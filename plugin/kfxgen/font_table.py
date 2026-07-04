"""Build embedded-font resources (@font-face) for native KFX output (#15).

Isolated from the generator so the risky parsing/matching logic is unit-tested
without Calibre. Calibre only appears in build_font_table (Task 4).
"""

import hashlib
import re
from dataclasses import dataclass

# TrueType/OpenType magic bytes. WOFF (wOFF) / WOFF2 (wOF2) are intentionally
# excluded — Kindle cannot embed them and decoding needs deps we don't carry.
_TTF_OTF_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")

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


def faces_from_rules(rules, manifest_lookup, log):
    """Turn @font-face rule dicts + a manifest byte-lookup into Face objects.

    Skips (with a warning) rules whose family is missing or whose only
    resolvable src bytes are not TTF/OTF. Dedups by resolved href.
    """
    faces = []
    taken_names = set()
    seen_hrefs = set()
    idx = 0
    for rule in rules:
        family = normalize_family(rule_field(rule, "font-family"))
        if not family:
            continue
        weight = parse_weight(rule_field(rule, "font-weight"))
        italic = parse_style(rule_field(rule, "font-style"))
        chosen_href = None
        chosen_data = None
        for href in extract_src_urls(rule_field(rule, "src")):
            data = manifest_lookup(href)
            if data and is_ttf_otf(data):
                chosen_href, chosen_data = href, data
                break
        if chosen_data is None:
            log.warn(
                f"  Skipping @font-face {family!r}: no embeddable TTF/OTF src "
                f"(WOFF/WOFF2/missing not supported)"
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
        """Resolve (family_list, bold, italic) to a face.

        Returns (emitted_family | None, weight_ok, style_ok). *_ok True means
        the chosen face already satisfies that axis, so the caller should NOT
        synthesize it. When no family is embedded, returns (None, False, False)
        so the caller reproduces today's synthetic behavior unchanged.
        """
        fam_faces = None
        for fam in family_list or []:
            if fam in self._by_family:
                fam_faces = self._by_family[fam]
                break
        if not fam_faces:
            return (None, False, False)

        want_bold = bool(bold)
        want_italic = bool(italic)
        # Exact face: weight side (>=600 == bold) and italic side both match.
        for f in fam_faces:
            if (
                f.weight >= BOLD_WEIGHT_THRESHOLD
            ) == want_bold and f.italic == want_italic:
                return (f.emitted_family, True, True)
        # Fallback: regular face (weight<600, upright), else any face.
        regular = next(
            (f for f in fam_faces if f.weight < BOLD_WEIGHT_THRESHOLD and not f.italic),
            fam_faces[0],
        )
        weight_ok = (regular.weight >= BOLD_WEIGHT_THRESHOLD) == want_bold
        style_ok = regular.italic == want_italic
        return (regular.emitted_family, weight_ok, style_ok)


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
