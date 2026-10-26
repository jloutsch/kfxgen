"""Build embedded-font resources (@font-face) for native KFX output (#15).

Isolated from the generator so the risky parsing/matching logic is unit-tested
without Calibre. Calibre only appears in build_font_table (Task 4).
"""

import hashlib
import os
import re
import struct
import zlib
from dataclasses import dataclass
from io import BytesIO


def _int_env(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


#: Per-face cap on embedded font bytes (#47). Blocks resource exhaustion from a
#: valid-magic but oversized "font" in an untrusted EPUB. 50 MB sits well above
#: any legitimate face — even full-coverage CJK fonts run ~10-20 MB — while
#: bounding the pathological case. Override via KFXGEN_MAX_FONT_BYTES.
MAX_FONT_BYTES = _int_env("KFXGEN_MAX_FONT_BYTES", 50 * 1024 * 1024)

# TrueType/OpenType magic bytes. WOFF (wOFF) / WOFF2 (wOF2) are not embedded
# as they are: they are unpacked to TrueType/OpenType first (#245).
_TTF_OTF_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")
_WOFF_MAGIC = (b"wOFF", b"wOF2")

#: Bytes before a WOFF's table directory, and in each of its entries.
_WOFF_HEADER_LEN = 44
_WOFF_ENTRY_LEN = 20

#: Where a WOFF's and a WOFF2's header holds the offsets and lengths of its
#: metadata and private blocks (metaOffset through privLength).
_WOFF_META_FIELDS = slice(24, 44)
_WOFF2_META_FIELDS = slice(28, 48)

#: CSS font-weight at or above which a face counts as "bold". Shared by face
#: matching, the $262 weight descriptor, and block-style capture so the cutoff
#: is defined once.
BOLD_WEIGHT_THRESHOLD = 600

#: Highest ASCII code point; family names with any char above this are non-ASCII
#: (CJK, Cyrillic, …) and need a hash suffix to stay distinct (see _slug).
_ASCII_MAX = 127

#: Hex chars of the family-name hash appended to non-ASCII slugs. 8 hex = 32
#: bits of space, ample to keep the handful of embedded families per book apart.
_SLUG_HASH_LEN = 8


@dataclass
class Face:
    css_family: str  # normalized (lowercased, unquoted) source family
    weight: int  # 100..900; bold->700, normal->400
    italic: bool
    data: bytes  # raw TTF/OTF bytes
    emitted_family: str  # internal slug, shared by $262.$11 and $157.$11
    location: str  # $418 fid, e.g. "resource/font0"


def is_ttf_otf(data):
    return bool(data) and data[:4] in _TTF_OTF_MAGIC


def is_woff(data):
    return bool(data) and data[:4] in _WOFF_MAGIC


def _over_cap(size, max_bytes):
    return ValueError(f"unpacks to {size} bytes, over the {max_bytes}-byte cap")


def _check_woff(data, max_bytes):
    """Inflate each compressed WOFF table no further than its declared length.

    fontTools inflates a whole table before comparing it with its length, so
    a small file could claim gigabytes; this bounds it first.
    """
    if len(data) < _WOFF_HEADER_LEN:
        raise ValueError("its WOFF header is cut short")
    (num_tables,) = struct.unpack_from(">H", data, 12)
    (total,) = struct.unpack_from(">L", data, 16)
    if total > max_bytes:
        raise _over_cap(total, max_bytes)
    declared = 0
    for i in range(num_tables):
        pos = _WOFF_HEADER_LEN + i * _WOFF_ENTRY_LEN
        if pos + _WOFF_ENTRY_LEN > len(data):
            raise ValueError("its WOFF table directory is cut short")
        tag, offset, length, orig, _sum = struct.unpack_from(">4sLLLL", data, pos)
        declared += orig
        if declared > max_bytes:
            raise _over_cap(declared, max_bytes)
        if length > orig:
            raise ValueError(f"its WOFF table {tag!r} is longer than its {orig} bytes")
        if length < orig:
            try:
                out = zlib.decompressobj().decompress(
                    data[offset : offset + length], orig + 1
                )
            except zlib.error as e:
                raise ValueError(f"its WOFF table {tag!r} is damaged") from e
            if len(out) != orig:
                raise ValueError(
                    f"its WOFF table {tag!r} does not inflate to its {orig} bytes"
                )


def _check_woff2(data, max_bytes):
    """Inflate a WOFF2's Brotli stream no further than its tables' lengths."""
    try:
        import brotli  # noqa: PLC0415
        from fontTools.ttLib.woff2 import (  # noqa: PLC0415
            WOFF2DirectoryEntry,
            woff2DirectorySize,
        )
    except ImportError as e:
        raise ValueError(f"WOFF2 needs {e.name}, which is not available") from e
    if len(data) < woff2DirectorySize:
        raise ValueError("its WOFF2 header is cut short")
    (num_tables,) = struct.unpack_from(">H", data, 12)
    total, compressed = struct.unpack_from(">LL", data, 16)
    if total > max_bytes:
        raise _over_cap(total, max_bytes)
    f = BytesIO(data)
    f.seek(woff2DirectorySize)
    expected = 0
    try:
        for _ in range(num_tables):
            entry = WOFF2DirectoryEntry()
            entry.fromFile(f)
            expected += entry.length
    except Exception as e:
        raise ValueError("its WOFF2 table directory is damaged") from e
    if expected > max_bytes:
        raise _over_cap(expected, max_bytes)
    start = f.tell()
    inflater = brotli.Decompressor()
    try:
        out = inflater.process(
            data[start : start + compressed], output_buffer_limit=expected + 1
        )
    except brotli.error as e:
        raise ValueError("its WOFF2 data is damaged") from e
    if len(out) != expected or not inflater.is_finished():
        raise ValueError(f"its WOFF2 data does not inflate to its {expected} bytes")


#: Tables every TrueType/OpenType font carries, and one of which holds its
#: outlines.
_SFNT_REQUIRED = {b"cmap", b"head", b"hhea", b"hmtx", b"maxp"}
_SFNT_OUTLINES = {b"glyf", b"CFF ", b"CFF2"}


def _check_sfnt(data):
    """Raise ValueError, saying why, when a TrueType/OpenType font's table
    directory doesn't hold up: cut short, a table past the file's end, or a
    table every font has missing. A file with a font's magic bytes and junk
    after them was embedded as a face (#471). A collection (ttcf) has a
    header of its own and isn't read here."""
    if data[:4] == b"ttcf":
        return
    if len(data) < 12:
        raise ValueError("its header is cut short")
    (num_tables,) = struct.unpack_from(">H", data, 4)
    if 12 + 16 * num_tables > len(data):
        raise ValueError("its table directory is cut short")
    tags = set()
    for i in range(num_tables):
        tag, _sum, offset, length = struct.unpack_from(">4sLLL", data, 12 + 16 * i)
        if offset + length > len(data):
            raise ValueError(f"its table {tag!r} runs past the file's end")
        tags.add(tag)
    missing = sorted(_SFNT_REQUIRED - tags)
    if missing or not tags & _SFNT_OUTLINES:
        raise ValueError(f"it has no {(missing or [b'glyf'])[0].decode()!r} table")


def sfnt_from_woff(data, max_bytes):
    """A WOFF or WOFF2 font unpacked to TrueType/OpenType bytes (#245).

    Raises ValueError, saying why, when the font is damaged, needs a module
    calibre lacks, or would unpack past `max_bytes`.
    """
    woff1 = data[:4] == b"wOFF"
    if woff1:
        _check_woff(data, max_bytes)
    else:
        _check_woff2(data, max_bytes)
    # fontTools inflates the metadata block, unbounded, while opening the
    # font, and it is not embedded: blank its fields so it is never read.
    fields = _WOFF_META_FIELDS if woff1 else _WOFF2_META_FIELDS
    data = (
        data[: fields.start] + bytes(fields.stop - fields.start) + data[fields.stop :]
    )
    from fontTools.ttLib import TTFont  # noqa: PLC0415

    try:
        font = TTFont(BytesIO(data), recalcBBoxes=False, recalcTimestamp=False)
        font.flavor = None
        out = BytesIO()
        font.save(out, reorderTables=None)  # no second pass
    except Exception as e:
        raise ValueError(f"it could not be unpacked ({type(e).__name__})") from e
    return out.getvalue()


def normalize_family(name):
    if not name:
        return ""
    return name.strip().strip("\"'").strip().lower()


def parse_weight(v):
    if v is None:
        return 400
    s = str(v).strip().lower()
    if s == "bold":
        return 700
    if s == "normal":
        return 400
    try:
        return int(s)
    except ValueError:
        return 400


def parse_style(v):
    return str(v or "").strip().lower() in ("italic", "oblique")


_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


def extract_src_urls(src):
    """Return ordered candidate hrefs from a CSS `src` (or a bare href)."""
    if not src:
        return []
    s = str(src)
    urls = _URL_RE.findall(s)
    if urls:
        return [u.strip() for u in urls]
    return [s.strip()]  # bare href (some Calibre versions pre-resolve src)


def _slug(name):
    norm = normalize_family(name)
    out = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    # Non-ASCII family names (CJK, Cyrillic, ...) lose all identity to the
    # ASCII-only slug, so distinct families (e.g. Korean 명조 serif vs 고딕 sans)
    # would collapse to the same base and rely only on emitted_name's dedup
    # counter to stay apart. Append a short, stable hash of the full name so
    # distinct families get distinct slug bases. Pure-ASCII names are unchanged.
    if any(ord(c) > _ASCII_MAX for c in norm):
        h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:_SLUG_HASH_LEN]
        out = f"{out}-{h}" if out else f"font-{h}"
    return out or "font"


def emitted_name(css_family, weight, italic, taken):
    base = f"{_slug(css_family)}-{weight}{'i' if italic else ''}"
    name = base
    n = 1
    while name in taken:
        name = f"{base}-{n}"
        n += 1
    taken.add(name)
    return name


def rule_field(rule, key):
    """Read an @font-face field from a css_parser CSSFontFaceRule OR a plain
    dict. Returns the string value or None."""
    style = getattr(rule, "style", None)
    if style is not None and hasattr(style, "getPropertyValue"):
        return style.getPropertyValue(key) or None
    if hasattr(rule, "get"):
        return rule.get(key)
    return None


def _face_key(rule):
    return (
        normalize_family(rule_field(rule, "font-family")),
        parse_weight(rule_field(rule, "font-weight")),
        parse_style(rule_field(rule, "font-style")),
    )


def faces_from_rules(rules, manifest_lookup, log, max_font_bytes=MAX_FONT_BYTES):
    """Turn @font-face rule dicts + a manifest byte-lookup into Face objects.

    Skips (with a warning) rules whose family is missing, whose only resolvable
    src bytes are not TTF/OTF, or whose font exceeds `max_font_bytes` (#47 —
    resource-exhaustion defense). Dedups by resolved href.
    """
    faces = []
    taken_names = set()
    seen_hrefs = set()
    # Each chapter repeats the book's rules: unpack each WOFF once.
    unpacked = {}
    # Faces some rule gives as TTF/OTF. A rule giving one of them only as
    # WOFF adds nothing: as before #245, the TTF/OTF rule embeds it.
    sfnt_faces = {
        _face_key(rule)
        for rule in rules
        if any(
            is_ttf_otf(manifest_lookup(href))
            for href in extract_src_urls(rule_field(rule, "src"))
        )
    }
    idx = 0
    for rule in rules:
        family = normalize_family(rule_field(rule, "font-family"))
        if not family:
            continue
        weight = parse_weight(rule_field(rule, "font-weight"))
        italic = parse_style(rule_field(rule, "font-style"))
        chosen_href = None
        chosen_data = None
        srcs = [
            (href, data)
            for href in extract_src_urls(rule_field(rule, "src"))
            for data in [manifest_lookup(href)]
            if is_ttf_otf(data) or is_woff(data)
        ]
        if _face_key(rule) in sfnt_faces and not any(is_ttf_otf(d) for _, d in srcs):
            continue
        # TTF/OTF first, in the author's order; a WOFF only when none fits.
        srcs.sort(key=lambda s: is_woff(s[1]))
        for href, data in srcs:
            if is_woff(data) and len(data) <= max_font_bytes:
                if href not in unpacked:
                    try:
                        unpacked[href] = sfnt_from_woff(data, max_font_bytes)
                    except ValueError as e:
                        unpacked[href] = e
                data = unpacked[href]
                if isinstance(data, ValueError):
                    log.warn(f"  Skipping @font-face {family!r} src {href!r}: {data}")
                    continue
            if len(data) > max_font_bytes:
                log.warn(
                    f"  Skipping @font-face {family!r} src {href!r}: "
                    f"{len(data)} bytes exceeds the {max_font_bytes}-byte cap "
                    f"(KFXGEN_MAX_FONT_BYTES)"
                )
                continue
            try:
                _check_sfnt(data)
            except ValueError as e:
                log.warn(f"  Skipping @font-face {family!r} src {href!r}: {e}")
                continue
            chosen_href, chosen_data = href, data
            break
        if chosen_data is None:
            log.warn(
                f"  Skipping @font-face {family!r}: no embeddable TTF, OTF, "
                f"WOFF or WOFF2 src"
            )
            continue
        if chosen_href in seen_hrefs:
            continue
        seen_hrefs.add(chosen_href)
        faces.append(
            Face(
                css_family=family,
                weight=weight,
                italic=italic,
                data=chosen_data,
                emitted_family=emitted_name(family, weight, italic, taken_names),
                location=f"resource/font{idx}",
            )
        )
        idx += 1
    return faces


class FontTable:
    """Embedded faces + face-matching for application (#15)."""

    def __init__(self, faces):
        self.faces = list(faces)
        self._by_family = {}
        for f in self.faces:
            self._by_family.setdefault(f.css_family, []).append(f)

    def match(self, family_list, bold, italic):
        """Resolve (family_list, bold, italic) to the emitted family of the best
        face, or None when no listed family is embedded.

        Prefers an exact (weight, style) face; otherwise falls back to the
        regular face (else the first). The caller sets the applied `$157`'s own
        `$13`/`$12` weight/style to the run's bold/italic so the Kindle resolves
        the same face the `$262` declares — a real bold/italic face is used when
        present, and synthesized on the fallback face when not (#50).
        """
        fam_faces = None
        for fam in family_list or []:
            if fam in self._by_family:
                fam_faces = self._by_family[fam]
                break
        if not fam_faces:
            return None

        want_bold = bool(bold)
        want_italic = bool(italic)
        # Exact face: weight side (>=600 == bold) and italic side both match.
        for f in fam_faces:
            if (
                f.weight >= BOLD_WEIGHT_THRESHOLD
            ) == want_bold and f.italic == want_italic:
                return f.emitted_family
        # Fallback: regular face (weight<600, upright), else any face.
        regular = next(
            (f for f in fam_faces if f.weight < BOLD_WEIGHT_THRESHOLD and not f.italic),
            fam_faces[0],
        )
        return regular.emitted_family


def build_manifest_lookup(oeb_book):
    """Return href -> bytes, with a basename fallback.

    Calibre `@font-face` `src` hrefs are usually relative to the CSS/spine item,
    so an exact manifest-href hit is not guaranteed; fall back to the basename.
    """
    by_href = {}
    by_base = {}
    for item in getattr(oeb_book, "manifest", []) or []:
        href = getattr(item, "href", "") or ""
        data = getattr(item, "data", None)
        if not href or not isinstance(data, (bytes, bytearray)):
            continue
        b = bytes(data)
        by_href[href] = b
        by_base[href.rsplit("/", 1)[-1]] = b

    def lookup(href):
        if not href:
            return None
        if href in by_href:
            return by_href[href]
        base = href.rsplit("/", 1)[-1]
        return by_base.get(base)

    return lookup


def build_stylizer(oeb_book, item):
    """Construct Calibre's Stylizer for one OEB spine item.

    Single home for the version-sensitive `Stylizer(tree, path, oeb, opts,
    profile)` signature (#41) — shared by @font-face extraction here and the
    per-element style resolver in converter.py, so a Calibre-side signature
    change only needs updating in one place. Raises if Calibre is unavailable;
    callers decide how to degrade.
    """
    from calibre.ebooks.oeb.stylizer import Stylizer  # noqa: PLC0415

    profile = getattr(getattr(oeb_book, "opts", None), "output_profile", None)
    return Stylizer(item.data, item.href, oeb_book, oeb_book.opts, profile)


def _default_stylizer_factory(oeb_book, log):
    def make(item):
        try:
            return build_stylizer(oeb_book, item)
        except Exception as e:
            log.warning(f"  Stylizer unavailable for fonts ({e})")
            return None

    return make


def build_font_table(oeb_book, log, stylizer_factory=None):
    """Aggregate @font-face rules across the spine and build a FontTable.

    Fonts declared in shared CSS repeat per spine item; faces_from_rules dedups
    by resolved href. `stylizer_factory(item)` is injectable for tests. Books
    with no embeddable fonts yield an empty FontTable, preserving today's output.
    """
    make = stylizer_factory or _default_stylizer_factory(oeb_book, log)
    all_rules = []
    for item in getattr(oeb_book, "spine", []) or []:
        if getattr(item, "data", None) is None:
            continue
        st = make(item)
        if st is None:
            continue
        all_rules.extend(getattr(st, "font_face_rules", []) or [])
    lookup = build_manifest_lookup(oeb_book)
    faces = faces_from_rules(all_rules, lookup, log)
    if faces:
        log.info(f"  Embedded fonts: {len(faces)} face(s)")
    return FontTable(faces)
