"""Build embedded-font resources (@font-face) for native KFX output (#15).

Isolated from the generator so the risky parsing/matching logic is unit-tested
without Calibre. Calibre only appears in build_font_table (Task 4).
"""

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
