"""Build embedded-font resources (@font-face) for native KFX output (#15).

Isolated from the generator so the risky parsing/matching logic is unit-tested
without Calibre. Calibre only appears in build_font_table (Task 4).
"""

import re
from dataclasses import dataclass

# TrueType/OpenType magic bytes. WOFF (wOFF) / WOFF2 (wOF2) are intentionally
# excluded — Kindle cannot embed them and decoding needs deps we don't carry.
_TTF_OTF_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


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
    out = re.sub(r"[^a-z0-9]+", "-", normalize_family(name)).strip("-")
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
