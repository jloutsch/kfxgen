"""Inline emphasis run/span computation for KFX styling (#9).

Pure, Calibre-independent: turns a paragraph's ordered (text, flags) segments
into whitespace-normalized text plus character spans, ready to become KFX $142
spans. See docs/superpowers/specs/2026-06-28-inline-emphasis-css-typography-design.md.
"""

import re

from .font_table import BOLD_WEIGHT_THRESHOLD, parse_style, parse_weight

FLAG_ITALIC = "italic"
FLAG_BOLD = "bold"
FLAG_SUPER = "super"
FLAG_SUB = "sub"

#: CSS length unit -> KFX $306 unit symbol.
_CSS_UNIT_TO_KFX = {
    "em": "$308",
    "rem": "$505",
    "%": "$314",
    "pt": "$318",
    "px": "$319",
    "mm": "$316",
}

_LENGTH_RE = re.compile(r"^\s*([+-]?[0-9]*\.?[0-9]+)\s*(em|rem|%|pt|px|mm)\s*$", re.I)


def normalize_runs(
    segments: list[tuple[str, frozenset]],
) -> tuple[str, list[tuple[int, int, frozenset]]]:
    """Collapse whitespace across (text, flags) segments and return
    (normalized_text, spans). Mirrors the converter's existing
    `" ".join(text.split())` rule: each run of ASCII whitespace becomes a
    single space and leading/trailing space is stripped. `spans` are maximal
    (start, length, flags) ranges with non-empty flags, offset into the text.
    """
    chars = []
    flags_per_char = []
    prev_space = True  # strip leading whitespace
    for text, flags in segments:
        for ch in text:
            if ch.isspace():
                if not prev_space:
                    chars.append(" ")
                    # a collapsed space carries its own segment's flags so
                    # "italic italic" stays one span rather than fragmenting.
                    flags_per_char.append(flags)
                    prev_space = True
            else:
                chars.append(ch)
                flags_per_char.append(flags)
                prev_space = False
    # strip trailing space
    while chars and chars[-1] == " ":
        chars.pop()
        flags_per_char.pop()

    text_out = "".join(chars)
    spans = []
    i = 0
    n = len(flags_per_char)
    while i < n:
        f = flags_per_char[i]
        if not f:
            i += 1
            continue
        j = i + 1
        while j < n and flags_per_char[j] == f:
            j += 1
        spans.append((i, j - i, f))
        i = j
    return text_out, spans


def parse_css_length(value):
    """Parse a CSS length string into (magnitude_str, kfx_unit_symbol).

    Returns None for empty/auto/inherit, unsupported units, a zero magnitude
    (no override needed), or a NEGATIVE magnitude. Negative text-indent is a
    hanging indent that the source pairs with a compensating margin-left; since
    margins are out of scope, honoring the negative indent alone pulls the first
    line off the left edge and clips leading characters (observed on Gutenberg
    front-matter metadata lists). Dropping it falls back to the default 0 indent.
    Magnitude is returned as a trimmed string so the caller can hand it to
    IonDecimal unchanged.
    """
    if not value:
        return None
    m = _LENGTH_RE.match(value)
    if not m:
        return None
    mag, unit = m.group(1), m.group(2).lower()
    try:
        if float(mag) <= 0.0:
            return None
    except ValueError:
        return None
    # Normalize "2.0" -> "2", "1.50" -> "1.5" without forcing a float repr.
    # (mag has no surrounding whitespace — the regex group excludes it.)
    if "." in mag:
        mag = mag.rstrip("0").rstrip(".")
    return (mag, _CSS_UNIT_TO_KFX[unit])


#: Marker for the link element of a run's flag set. A run's flags are a
#: frozenset, so carrying the target as ("link", target) means normalize_runs
#: splits and merges link runs by target for free — two adjacent <a>s to
#: different notes stay separate runs, one <a> split by a nested tag rejoins.
#: (#53)
LINK_FLAG = "link"


def make_link_flag(target):
    """Build the flag-set member that marks a run as a link to `target`."""
    return (LINK_FLAG, target)


def link_target(flags):
    """Return the link target carried by a run's flag set, or None."""
    for flag in flags:
        if isinstance(flag, tuple) and len(flag) == 2 and flag[0] == LINK_FLAG:
            return flag[1]
    return None


#: vertical-align keywords that mean "raise"/"lower" without a length.
_VALIGN_UP = {"super", "text-top"}
_VALIGN_DOWN = {"sub", "text-bottom"}

_VALIGN_LENGTH_RE = re.compile(
    r"^\s*([+-]?[0-9]*\.?[0-9]+)\s*(em|rem|%|pt|px|mm)\s*$", re.I
)


def parse_vertical_align(value):
    """Classify a CSS vertical-align value as "super", "sub", or None.

    Publisher EPUBs rarely use `<sup>`; they wrap the marker in a span whose
    class carries `vertical-align`, and the value is as often a raw length
    (`0.25em`) as the `super` keyword — so both forms have to be recognized.
    A positive offset raises, a negative one lowers. `baseline`/`middle`/
    `top`/`bottom` and a zero offset mean no shift. (#52)
    """
    if not value:
        return None
    raw = str(value).strip().lower()
    if raw in _VALIGN_UP:
        return "super"
    if raw in _VALIGN_DOWN:
        return "sub"
    m = _VALIGN_LENGTH_RE.match(raw)
    if not m:
        return None
    try:
        magnitude = float(m.group(1))
    except ValueError:
        return None
    if magnitude > 0:
        return "super"
    if magnitude < 0:
        return "sub"
    return None


#: CSS text-align keyword -> KFX $34 value symbol.
ALIGN_MAP = {"left": "$59", "right": "$61", "center": "$320", "justify": "$321"}


def _parse_font_family(value):
    """Split a CSS font-family value into normalized, lowercased names."""
    if not value:
        return []
    out = []
    for part in str(value).split(","):
        name = part.strip().strip("\"'").strip().lower()
        if name:
            out.append(name)
    return out


def compute_block_style(css):
    """Map a computed-CSS dict to kfxgen's block_style shape.

    `css` is a mapping that supports .get(prop) returning CSS strings (e.g. a
    Calibre Stylizer Style, or a plain dict in tests). Returns
    {"align": <keyword or None>, "indent"/"margin_left"/"margin_right":
    <(mag, unit_sym) or None>}. The align keyword is mapped to a symbol later,
    in build_fragment_157.
    """
    align = None
    raw_align = (css.get("text-align") or "").strip().lower()
    if raw_align in ALIGN_MAP:
        align = raw_align
    indent = parse_css_length(css.get("text-indent") or "")
    margin_left = parse_css_length(css.get("margin-left") or "")
    margin_right = parse_css_length(css.get("margin-right") or "")
    return {
        "align": align,
        "indent": indent,
        "margin_left": margin_left,
        "margin_right": margin_right,
        "font_family": _parse_font_family(css.get("font-family")),
        # Block-level emphasis: a paragraph made bold/italic via CSS
        # (e.g. `p.lead { font-weight: bold }`) rather than an inline tag.
        # Used to select the embedded bold/italic face for the whole block.
        "bold": parse_weight(css.get("font-weight")) >= BOLD_WEIGHT_THRESHOLD,
        "italic": parse_style(css.get("font-style")),
    }
