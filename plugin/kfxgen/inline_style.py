"""Inline emphasis run/span computation for KFX styling (#9).

Pure, Calibre-independent: turns a paragraph's ordered (text, flags) segments
into whitespace-normalized text plus character spans, ready to become KFX $142
spans. See docs/superpowers/specs/2026-06-28-inline-emphasis-css-typography-design.md.
"""

import colorsys
import functools
import itertools
import math
import re

from .font_table import BOLD_WEIGHT_THRESHOLD, parse_style, parse_weight

FLAG_ITALIC = "italic"
FLAG_BOLD = "bold"
FLAG_SUPER = "super"
FLAG_SUB = "sub"
#: Text decoration lines (#237). Kindle Previewer writes them as $23 and $27.
FLAG_UNDERLINE = "underline"
FLAG_STRIKE = "line-through"
#: A run its CSS sets back to normal inside a bold or italic paragraph: the
#: paragraph's own emphasis is no flag of the run's to take away, so the
#: run says normal instead (#367 review).
FLAG_NOT_BOLD = "not-bold"
FLAG_NOT_ITALIC = "not-italic"

#: White-space modes, carried on a segment's flags by the converter and read
#: by `normalize_runs_with_anchors`, which removes them again: they choose how
#: the text is normalized and never become a style span. (#202)
#:
#: FLAG_PRE: `white-space: pre / pre-wrap / break-spaces`. Newlines are line
#: breaks and spaces are kept.
#: FLAG_PRE_LINE: `white-space: pre-line`. Newlines are line breaks; spaces
#: collapse as usual.
FLAG_PRE = "ws-pre"
FLAG_PRE_LINE = "ws-pre-line"
_WS_FLAGS = frozenset({FLAG_PRE, FLAG_PRE_LINE})

#: A forced line break (`<br>`) in a segment stream. KFX keeps a paragraph's
#: text preformatted, so a line break is a newline character in the content;
#: KFX Input turns it back into <br>. (#202)
HARD_BREAK = "\u2028"

#: An element drawn as a block (a `display: block` span) inside a paragraph:
#: what follows it, and what follows the text before it, starts a new line. A
#: segment of its own, ("", {LINE_START}). Unlike HARD_BREAK it never makes an
#: empty line: a <br> next to it gives one break, not two. (#233)
LINE_START = "line-start"

NBSP = "\u00a0"
#: The whitespace HTML collapses: ASCII only. A non-breaking space, an em
#: space or an ideographic space is the book's own and is kept, indentation
#: included (#244).
_COLLAPSIBLE = frozenset(" \t\n\r\f")
_TAB_STOP = 8

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


#: Marker for an anchor's position within a paragraph's segment stream. The
#: mark carries no text, so it passes through whitespace collapsing without
#: shifting a single character, and comes back out as the character offset of
#: whatever follows it. That offset is what a `$266` anchor puts in `$143` so
#: a return link lands on the marker instead of the paragraph's first line. (#79)
ANCHOR_MARK = "anchor-mark"


def make_anchor_mark(anchor_id):
    """Build the zero-length segment that marks where `anchor_id` is declared."""
    return ("", frozenset({(ANCHOR_MARK, anchor_id)}))


def anchor_mark_id(flags):
    """Return the anchor id carried by an anchor-mark segment, or None."""
    for flag in flags:
        if isinstance(flag, tuple) and len(flag) == 2 and flag[0] == ANCHOR_MARK:
            return flag[1]
    return None


def normalize_runs(
    segments: list[tuple[str, frozenset]],
) -> tuple[str, list[tuple[int, int, frozenset]]]:
    """Collapse whitespace across (text, flags) segments and return
    (normalized_text, spans). Mirrors the converter's existing
    `" ".join(text.split())` rule: each run of ASCII whitespace becomes a
    single space and leading/trailing space is stripped. `spans` are maximal
    (start, length, flags) ranges with non-empty flags, offset into the text.
    """
    text, spans, _anchors = normalize_runs_with_anchors(segments)
    return text, spans


def normalize_runs_with_anchors(
    segments: list[tuple[str, frozenset]],
) -> tuple[str, list[tuple[int, int, frozenset]], dict]:
    """`normalize_runs`, plus {anchor_id: character offset} for any anchor
    marks in `segments`.

    An id declared twice in one paragraph keeps its first position, matching
    the browser rule that the first occurrence wins. An anchor sitting after
    the paragraph's last visible character clamps to the end rather than
    pointing past it. (#79)
    """
    chars = []
    flags_per_char = []
    anchors = {}
    pre_spaces = []  # indexes of spaces kept in preformatted text
    prev_space = True  # strip leading whitespace
    line_started = False  # just past a LINE_START, no text since

    def _drop_last():
        chars.pop()
        flags_per_char.pop()
        # A dropped kept space must lose its mark too. Otherwise the next
        # character appended at that position is taken for a kept space and
        # overwritten with a non-breaking one: a code line ending in spaces
        # ate the first letters of the line after it.
        while pre_spaces and pre_spaces[-1] >= len(chars):
            pre_spaces.pop()

    def _break(flags):
        # Trailing spaces before a line break are invisible; drop them. A
        # break before any text is dropped too: a paragraph never opens on an
        # empty line.
        while chars and chars[-1] == " ":
            _drop_last()
        if chars:
            chars.append("\n")
            flags_per_char.append(flags)

    for text, flags in segments:
        marked = anchor_mark_id(flags)
        if marked is not None:
            anchors.setdefault(marked, len(chars))
            continue
        if LINE_START in flags:
            if chars and chars[-1] != "\n":
                _break(frozenset())
            prev_space = True
            # A <br> straight after a block's edge adds no line: Standard
            # Ebooks puts one after each verse span and hides it with CSS.
            line_started = True
            continue
        pre = FLAG_PRE in flags
        pre_line = FLAG_PRE_LINE in flags
        if pre or pre_line:
            flags = flags - _WS_FLAGS
        for ch in text:
            if ch == HARD_BREAK or ((pre or pre_line) and ch == "\n"):
                if not line_started:
                    _break(flags)
                line_started = False
                prev_space = True
            elif pre and ch in " \t":
                if ch == "\t":
                    line_start = len(chars) - 1
                    while line_start >= 0 and chars[line_start] != "\n":
                        line_start -= 1
                    width = _TAB_STOP - (len(chars) - line_start - 1) % _TAB_STOP
                else:
                    width = 1
                for _ in range(width):
                    pre_spaces.append(len(chars))
                    chars.append(" ")
                    flags_per_char.append(flags)
                prev_space = False
            elif pre and ch == "\r":
                continue
            elif ch in _COLLAPSIBLE:
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
                line_started = False
    # strip trailing space and line breaks
    while chars and chars[-1] in (" ", "\n"):
        _drop_last()

    # A kept space that starts a line or sits beside another one would be
    # collapsed or stripped by a reader; a non-breaking space is not. A single
    # space between words stays breakable. This is the rule KFX Input uses in
    # reverse when it turns KFX text back into HTML. (#202)
    kept = set(pre_spaces)
    n_chars = len(chars)
    for i in pre_spaces:
        if i >= n_chars:
            continue
        at_line_start = i == 0 or chars[i - 1] in ("\n", NBSP)
        beside_space = (i > 0 and (i - 1) in kept) or (i + 1) in kept
        if at_line_start or beside_space:
            chars[i] = NBSP

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
    if anchors:
        limit = len(text_out)
        anchors = {k: min(v, limit) for k, v in anchors.items()}
    return text_out, spans, anchors


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


#: A ruby's base text carries ("ruby", (serial, reading)) (#236). The serial
#: keeps two neighbouring rubies with one reading apart; it is unique within a
#: conversion, and the generator numbers rubies in reading order.
RUBY_FLAG = "ruby"
_ruby_serials = itertools.count()


def make_ruby_flag(reading):
    """Build the flag-set member that gives a run of base text `reading`."""
    return (RUBY_FLAG, (next(_ruby_serials), reading))


def ruby_of(flags):
    """A run's ruby as (serial, reading), or None."""
    return flag_value(flags, RUBY_FLAG)


def without_ruby(flags):
    """A run's flags less its ruby, which is written as an event of its own."""
    return frozenset(
        f for f in flags if not (isinstance(f, tuple) and f[0] == RUBY_FLAG)
    )


MONO_FLAG = "mono"

#: Monospace families a book names without the generic `monospace` after them.
_MONOSPACE_NAMES = frozenset(
    {
        "courier",
        "courier new",
        "consolas",
        "menlo",
        "monaco",
        "lucida console",
        "lucida sans typewriter",
        "andale mono",
        "dejavu sans mono",
        "liberation mono",
        "source code pro",
        "inconsolata",
        "fira mono",
        "fira code",
        "ubuntu mono",
        "roboto mono",
        "noto sans mono",
        "droid sans mono",
        "sf mono",
        "pt mono",
    }
)


def is_monospace(families):
    """Whether a parsed font-family list draws monospace text: it names the
    generic `monospace`, or its first family is a known monospace face."""
    families = list(families or [])
    return "monospace" in families or (
        bool(families) and families[0] in _MONOSPACE_NAMES
    )


def make_mono_flag(families):
    """The flag-set member marking a run monospace in `families` (#242). The
    families travel with it, so an embedded face can still be matched."""
    return (MONO_FLAG, tuple(families))


def mono_families(flags):
    """The families a run's monospace flag carries, or None."""
    for flag in flags:
        if isinstance(flag, tuple) and len(flag) == 2 and flag[0] == MONO_FLAG:
            return flag[1]
    return None


SIZE_FLAG = "size"


def css_font_size(css):
    """An element's font size in CSS px, as the resolver reports it, or None
    without one (#241)."""
    size = (css or {}).get("font-size")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        return None
    return size


def make_size_flag(size):
    """The flag-set member giving a run its own font size (#241), in CSS px.
    The generator writes it against the book's main text size."""
    return (SIZE_FLAG, size)


def run_font_size(flags):
    """The size a run's size flag carries, or None."""
    for flag in flags:
        if isinstance(flag, tuple) and len(flag) == 2 and flag[0] == SIZE_FLAG:
            return flag[1]
    return None


#: Small caps, text-transform, letter-spacing and colour (#239): each a run's
#: own when it differs from its parent's, carried as (flag, value). The
#: value of the first two is the CSS keyword ("normal" and "none" inside a
#: paragraph that sets them); of letter-spacing a KFX length (mag, unit),
#: ("0", "$308") for `normal`; of colour the ARGB integer.
SMALL_CAPS_FLAG = "small-caps"
TRANSFORM_FLAG = "transform"
SPACING_FLAG = "spacing"
COLOUR_FLAG = "colour"
TYPOGRAPHY_FLAGS = (SMALL_CAPS_FLAG, TRANSFORM_FLAG, SPACING_FLAG, COLOUR_FLAG)
#: Their block_style keys, in the same order.
TYPOGRAPHY_KEYS = ("small_caps", "text_transform", "letter_spacing", "colour")
SPACING_NORMAL = ("0", "$308")
_TRANSFORMS = frozenset({"uppercase", "lowercase", "capitalize"})


def flag_value(flags, kind):
    """The value a run's (kind, value) flag carries, or None."""
    for flag in flags:
        if isinstance(flag, tuple) and len(flag) == 2 and flag[0] == kind:
            return flag[1]
    return None


def css_small_caps(value):
    """ "small-caps" for a `font-variant` naming it, else None. Previewer
    reads `font-variant` only, not `font-variant-caps` (#239)."""
    return "small-caps" if "small-caps" in str(value or "").lower().split() else None


def css_text_transform(value):
    """An uppercase, lowercase or capitalize `text-transform`, else None
    (`full-width` too, which Previewer drops)."""
    value = str(value or "").strip().lower()
    return value if value in _TRANSFORMS else None


def css_letter_spacing(value):
    """A widening `letter-spacing` as the KFX length Previewer writes, or
    None: em, rem and pt as written, a px as 0.45pt (#239). Previewer drops
    a negative spacing and a percentage."""
    parsed = parse_css_length(str(value or ""))
    if parsed is None or parsed[1] == "$314" or parsed[1] == "$316":
        return None
    mag, unit = parsed
    if unit == "$319":
        mag, unit = f"{float(mag) * 0.45:.4g}", "$318"
    return mag, unit


#: The CSS named colours, all 148 (CSS Color 4). Previewer reads them all
#: but `rebeccapurple`, which kfxgen reads as a browser does. System colours
#: (`windowtext`) are unread, as in Previewer (#383).
_CSS_COLOURS = {
    "aliceblue": 0xF0F8FF, "antiquewhite": 0xFAEBD7, "aqua": 0x00FFFF,
    "aquamarine": 0x7FFFD4, "azure": 0xF0FFFF, "beige": 0xF5F5DC, "bisque": 0xFFE4C4,
    "black": 0x000000, "blanchedalmond": 0xFFEBCD, "blue": 0x0000FF,
    "blueviolet": 0x8A2BE2, "brown": 0xA52A2A, "burlywood": 0xDEB887,
    "cadetblue": 0x5F9EA0, "chartreuse": 0x7FFF00, "chocolate": 0xD2691E,
    "coral": 0xFF7F50, "cornflowerblue": 0x6495ED, "cornsilk": 0xFFF8DC,
    "crimson": 0xDC143C, "cyan": 0x00FFFF, "darkblue": 0x00008B, "darkcyan": 0x008B8B,
    "darkgoldenrod": 0xB8860B, "darkgray": 0xA9A9A9, "darkgreen": 0x006400,
    "darkgrey": 0xA9A9A9, "darkkhaki": 0xBDB76B, "darkmagenta": 0x8B008B,
    "darkolivegreen": 0x556B2F, "darkorange": 0xFF8C00, "darkorchid": 0x9932CC,
    "darkred": 0x8B0000, "darksalmon": 0xE9967A, "darkseagreen": 0x8FBC8F,
    "darkslateblue": 0x483D8B, "darkslategray": 0x2F4F4F, "darkslategrey": 0x2F4F4F,
    "darkturquoise": 0x00CED1, "darkviolet": 0x9400D3, "deeppink": 0xFF1493,
    "deepskyblue": 0x00BFFF, "dimgray": 0x696969, "dimgrey": 0x696969,
    "dodgerblue": 0x1E90FF, "firebrick": 0xB22222, "floralwhite": 0xFFFAF0,
    "forestgreen": 0x228B22, "fuchsia": 0xFF00FF, "gainsboro": 0xDCDCDC,
    "ghostwhite": 0xF8F8FF, "gold": 0xFFD700, "goldenrod": 0xDAA520, "gray": 0x808080,
    "green": 0x008000, "greenyellow": 0xADFF2F, "grey": 0x808080, "honeydew": 0xF0FFF0,
    "hotpink": 0xFF69B4, "indianred": 0xCD5C5C, "indigo": 0x4B0082, "ivory": 0xFFFFF0,
    "khaki": 0xF0E68C, "lavender": 0xE6E6FA, "lavenderblush": 0xFFF0F5,
    "lawngreen": 0x7CFC00, "lemonchiffon": 0xFFFACD, "lightblue": 0xADD8E6,
    "lightcoral": 0xF08080, "lightcyan": 0xE0FFFF, "lightgoldenrodyellow": 0xFAFAD2,
    "lightgray": 0xD3D3D3, "lightgreen": 0x90EE90, "lightgrey": 0xD3D3D3,
    "lightpink": 0xFFB6C1, "lightsalmon": 0xFFA07A, "lightseagreen": 0x20B2AA,
    "lightskyblue": 0x87CEFA, "lightslategray": 0x778899, "lightslategrey": 0x778899,
    "lightsteelblue": 0xB0C4DE, "lightyellow": 0xFFFFE0, "lime": 0x00FF00,
    "limegreen": 0x32CD32, "linen": 0xFAF0E6, "magenta": 0xFF00FF, "maroon": 0x800000,
    "mediumaquamarine": 0x66CDAA, "mediumblue": 0x0000CD, "mediumorchid": 0xBA55D3,
    "mediumpurple": 0x9370DB, "mediumseagreen": 0x3CB371, "mediumslateblue": 0x7B68EE,
    "mediumspringgreen": 0x00FA9A, "mediumturquoise": 0x48D1CC,
    "mediumvioletred": 0xC71585, "midnightblue": 0x191970, "mintcream": 0xF5FFFA,
    "mistyrose": 0xFFE4E1, "moccasin": 0xFFE4B5, "navajowhite": 0xFFDEAD,
    "navy": 0x000080, "oldlace": 0xFDF5E6, "olive": 0x808000, "olivedrab": 0x6B8E23,
    "orange": 0xFFA500, "orangered": 0xFF4500, "orchid": 0xDA70D6,
    "palegoldenrod": 0xEEE8AA, "palegreen": 0x98FB98, "paleturquoise": 0xAFEEEE,
    "palevioletred": 0xDB7093, "papayawhip": 0xFFEFD5, "peachpuff": 0xFFDAB9,
    "peru": 0xCD853F, "pink": 0xFFC0CB, "plum": 0xDDA0DD, "powderblue": 0xB0E0E6,
    "purple": 0x800080, "rebeccapurple": 0x663399, "red": 0xFF0000,
    "rosybrown": 0xBC8F8F, "royalblue": 0x4169E1, "saddlebrown": 0x8B4513,
    "salmon": 0xFA8072, "sandybrown": 0xF4A460, "seagreen": 0x2E8B57,
    "seashell": 0xFFF5EE, "sienna": 0xA0522D, "silver": 0xC0C0C0, "skyblue": 0x87CEEB,
    "slateblue": 0x6A5ACD, "slategray": 0x708090, "slategrey": 0x708090,
    "snow": 0xFFFAFA, "springgreen": 0x00FF7F, "steelblue": 0x4682B4, "tan": 0xD2B48C,
    "teal": 0x008080, "thistle": 0xD8BFD8, "tomato": 0xFF6347, "turquoise": 0x40E0D0,
    "violet": 0xEE82EE, "wheat": 0xF5DEB3, "white": 0xFFFFFF, "whitesmoke": 0xF5F5F5,
    "yellow": 0xFFFF00, "yellowgreen": 0x9ACD32,
}  # fmt: skip

#: A channel is an integer or a percentage (#394); the alpha is a number. A
#: percentage p is floor(p x 2.56), capped at 255, as Previewer reads it.
_CHANNEL = r"\s*(\d+(?:\.\d+)?%|\d+)\s*"
_RGB = re.compile(rf"rgba?\({_CHANNEL},{_CHANNEL},{_CHANNEL}(?:,\s*([\d.]+)\s*)?\)")
_HSL = re.compile(
    r"hsla?\(\s*([\d.]+)(?:deg)?\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*"
    r"(?:,\s*([\d.]+)\s*)?\)"
)


def _rgb_alpha(value):
    """A CSS colour as (0xRRGGBB, alpha from 0 to 1), or None when it can't be
    read. A percentage alpha isn't read: Previewer draws no fill for one."""
    v = str(value or "").strip().lower()
    if v in _CSS_COLOURS:
        return _CSS_COLOURS[v], 1.0
    if re.fullmatch(r"#[0-9a-f]{3}", v):
        return int("".join(c * 2 for c in v[1:]), 16), 1.0
    if re.fullmatch(r"#[0-9a-f]{6}", v):
        return int(v[1:], 16), 1.0
    m = _RGB.fullmatch(v)
    if m:
        r, g, b = (
            min(int(float(x[:-1]) * 256 // 100) if x.endswith("%") else int(x), 255)
            for x in m.groups()[:3]
        )
        rgb = (r << 16) | (g << 8) | b
    else:
        m = _HSL.fullmatch(v)
        if not m:
            return None
        h, sat, light = (float(x) for x in m.groups()[:3])
        r, g, b = (
            round(c * 255)
            for c in colorsys.hls_to_rgb(
                (h % 360) / 360, min(light, 100) / 100, min(sat, 100) / 100
            )
        )
        rgb = (r << 16) | (g << 8) | b
    alpha = m.group(4)
    return rgb, 1.0 if alpha is None else float(alpha)


def css_rgb(value):
    """A CSS colour as a 0xRRGGBB integer, or None when it can't be read.

    A translucent colour is blended onto the white page, as a browser shows
    it there, and written opaque (#383). Previewer writes the alpha in the
    ARGB value instead; whether a Kindle draws it isn't known, and the
    blended colour is the same where it is and readable where it isn't."""
    got = _rgb_alpha(value)
    if got is None:
        return None
    rgb, alpha = got
    if alpha >= 1:
        return rgb
    out = 0
    for shift in (16, 8, 0):
        c = (rgb >> shift) & 0xFF
        out |= round(c * alpha + 255 * (1 - alpha)) << shift
    return out


def is_transparent(value):
    """True for a colour that draws nothing: `transparent`, or an alpha of 0."""
    v = str(value or "").strip().lower()
    if v == "transparent":
        return True
    got = _rgb_alpha(v)
    return got is not None and got[1] <= 0


def _luminance(rgb):
    """WCAG relative luminance of (r, g, b) channels, 0 to 255."""

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


BLACK = 0xFF000000

#: The luminance of 4.5:1 contrast against white: (1 + 0.05) / 4.5 - 0.05.
_MAX_LUMINANCE = 1.05 / 4.5 - 0.05


def _scaled(ch, target, darken):
    """`ch` scaled, keeping its hue, to the luminance `target`: down to it
    when `darken`, else up to it, as far as the brightest channel allows.
    Black lightens as grey."""
    if darken:
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            if _luminance(tuple(c * mid for c in ch)) > target:
                hi = mid
            else:
                lo = mid
        return tuple(int(c * lo) for c in ch)
    base = ch if max(ch) else (1, 1, 1)
    lo, hi = (1.0 if max(ch) else 0.0), 255 / max(base)
    if _luminance(tuple(c * hi for c in base)) < target:
        lo = hi
    for _ in range(30):
        mid = (lo + hi) / 2
        if _luminance(tuple(c * mid for c in base)) < target:
            lo = mid
        else:
            hi = mid
    return tuple(min(255, math.ceil(c * hi - 1e-9)) for c in base)


@functools.lru_cache(maxsize=1024)
def css_text_colour(value, background=None):
    """A text colour as the ARGB integer Previewer writes, or None when it
    draws nothing or can't be read (#239).

    `background` is the 0xRRGGBB fill of the box drawn behind the text, or
    None for the white page. Previewer measures the colour against it: with
    less than 4.5:1 contrast, the colour moves toward whichever of black or
    white contrasts more with the background, keeping its hue, until it has
    4.5:1 (#383). On the page, white is written 0xFF767676 and yellow
    0xFF7A7A00; on a #333333 box white is kept and black becomes about
    0xFF9C9C9C. A colour that can't get lighter without changing its hue
    (pure red) is kept."""
    if is_transparent(value):
        return None
    rgb = css_rgb(value)
    if rgb is None:
        return None
    ch = ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
    bg = 0xFFFFFF if background is None else background
    lum = _luminance(((bg >> 16) & 0xFF, (bg >> 8) & 0xFF, bg & 0xFF))
    if background is None:
        if _luminance(ch) > _MAX_LUMINANCE:
            ch = _scaled(ch, _MAX_LUMINANCE, True)
    else:
        mine = _luminance(ch)
        if (max(mine, lum) + 0.05) / (min(mine, lum) + 0.05) < 4.5:
            # Black contrasts more with a light background, white with a
            # dark one.
            darken = (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05)
            target = (lum + 0.05) / 4.5 - 0.05 if darken else 4.5 * (lum + 0.05) - 0.05
            ch = _scaled(ch, target, darken)
    return 0xFF000000 | (ch[0] << 16) | (ch[1] << 8) | ch[2]


def css_typography(css):
    """(small caps, text-transform, letter-spacing, colour) from an element's
    computed CSS, each as its run flag carries it, or None unset (#239)."""
    css = css or {}
    return (
        css_small_caps(css.get("font-variant")),
        css_text_transform(css.get("text-transform")),
        css_letter_spacing(css.get("letter-spacing")),
        # Unset is black. A string, for the cache. Measured against the box
        # behind the text, or the page (#383).
        css_text_colour(str(css.get("color") or "black"), css.get("background-behind")),
    )


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
# A table cell's vertical-align as $633, as Kindle Previewer 4 writes it. Any
# other value (baseline, super, text-top, a length) gets no $633 there. (#269)
VALIGN_MAP = {"top": "$58", "middle": "$320", "bottom": "$60"}


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
        # `bolder` is bold, as it is for a run (#367 review).
        "bold": str(css.get("font-weight") or "").strip().lower() == "bolder"
        or parse_weight(css.get("font-weight")) >= BOLD_WEIGHT_THRESHOLD,
        "italic": parse_style(css.get("font-style")),
        # In CSS px; the generator measures it against the book's main
        # text size (#241).
        "font_size": css_font_size(css),
        **_block_typography(css),
    }


def _block_typography(css):
    """A paragraph's small caps, text-transform, letter-spacing and colour,
    each only when set: black, the reader's own, isn't (#239)."""
    return {
        key: value
        for key, value in zip(TYPOGRAPHY_KEYS, css_typography(css))
        if value is not None and not (key == "colour" and value == BLACK)
    }
