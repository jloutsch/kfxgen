"""
KFX Converter - Extract content from OEB and generate KFX

Uses NativeKFXGenerator to produce KFX with working TOC navigation.
"""

import bisect
import collections
import copy
import logging
import os
import posixpath
import re
import string
import unicodedata
from urllib.parse import unquote

from ._img_tokens import (
    IMG_TOKEN_DELIM as _IMG_TOKEN_DELIM,
    IMG_TOKEN_FIELD as _IMG_TOKEN_FIELD,
    IMG_TOKEN_RE as _IMG_TOKEN_RE,
    IMG_TOKEN_SPACE as _IMG_TOKEN_SPACE,
)
from .font_table import BOLD_WEIGHT_THRESHOLD, parse_style, parse_weight
from .image_optimize import (
    GIF_SIGNATURES,
    decodes,
    gif_to_png,
    optimize_images,
    svg_to_png,
)
from .inline_style import (
    FLAG_BOLD,
    FLAG_ITALIC,
    FLAG_NOT_BOLD,
    FLAG_NOT_ITALIC,
    FLAG_PRE,
    FLAG_PRE_LINE,
    FLAG_STRIKE,
    FLAG_SUB,
    FLAG_SUPER,
    FLAG_UNDERLINE,
    HARD_BREAK,
    LINE_START,
    NOTEREF_FLAG,
    _parse_font_family,
    BLACK,
    COLOUR_FLAG,
    SPACING_NORMAL,
    TYPOGRAPHY_FLAGS,
    compute_block_style,
    css_font_size,
    css_rgb,
    css_typography,
    flag_value,
    is_transparent,
    is_monospace,
    make_anchor_mark,
    make_link_flag,
    make_mono_flag,
    make_math_flag,
    make_ruby_flag,
    make_size_flag,
    mono_families,
    normalize_runs_with_anchors,
    parse_css_length,
    parse_vertical_align,
    run_font_size,
)
from .math_draw import draw as draw_math
from .native_generator import NativeKFXGenerator

_ITALIC_TAGS = {"em", "i"}
_BOLD_TAGS = {"strong", "b"}
#: The flag a run set back to normal carries, for each emphasis (#367 review).
_NOT_FLAG = {FLAG_BOLD: FLAG_NOT_BOLD, FLAG_ITALIC: FLAG_NOT_ITALIC}
_SUPER_TAGS = {"sup"}
_SUB_TAGS = {"sub"}
_UNDERLINE_TAGS = {"u", "ins"}
_STRIKE_TAGS = {"s", "strike", "del"}
#: Monospace without a stylizer; with one, calibre's UA sheet says so (#242).
_MONO_TAGS = {"code", "kbd", "samp", "tt"}

# Table cells run together without this. A table that is not laid out natively
# (#219, #251) has each row as one paragraph, its cells inline text within it, so the
# only thing separating two cells was whatever whitespace happened to sit
# between the tags in the source. Where an author wrote
# `</td><td>` with nothing between, adjacent values fused: `1801` and `8,893`
# came out as `18018,893`, a number that is not in the source and cannot be
# read back apart. 12 of the 77 corpus books contain adjacent cell pairs, and
# one fuses 2249 of its 4864 cells. (#128)
_CELL_TAGS = {"td", "th"}

# Native table layout (#219). A table the first version can't express
# correctly keeps 5.8.8's one paragraph per row instead.

#: Block-level tags in a cell. A cell holding two or more is a container of
#: paragraphs, one per block, as Kindle Previewer writes it (#261).
_CELL_BLOCK_TAGS = {
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    "section",
    "article",
    "figure",
}
#: Content a native table can't place inside a cell. An <img> is the
#: exception: a cell holding one is a container of paragraphs in which the
#: picture is a paragraph of its own (#262).
_NON_TEXT_TAGS = {
    "img",
    "svg",
    "image",
    "math",
    "video",
    "audio",
    "object",
    "embed",
    "iframe",
}
#: The longest cell entry a native table of two or more columns takes. Their
#: table viewer fits the whole table on one screen, where a longer cell can't
#: be read (Voyage 5.13.6, #348). A one-column table has no viewer and pages
#: through a long cell like any paragraph, so it takes any length.
_MAX_NATIVE_CELL_CHARS = 2000
#: The widest table checked on a device (#254). With the table-viewer
#: properties the Voyage (5.13.6) and the Oasis (5.18.2.1.1) read every table
#: up to 24 columns; without them both squeezed even 8. Wider tables are
#: untested, and their text would be too small to read.
_MAX_NATIVE_COLUMNS = 24

_security_log = logging.getLogger(__name__ + ".security")


def _computed_value(style, prop):
    """Return a Calibre Style's fully computed value for `prop`.

    Calibre's `Style.get(prop)` returns only the element's own specified value
    (None when the property is inherited from an ancestor). `Style[prop]`
    returns the computed value including inheritance and initial defaults. For
    inherited properties like font-family (commonly set on <body>), getitem is
    required; fall back to .get() if getitem is unavailable.
    """
    try:
        return style[prop]
    except Exception:
        return style.get(prop)


def _specified_inherited(stylizer, elem, prop):
    """The value `prop` was given on `elem` or its nearest ancestor that sets
    it, as written: CSS inheritance without calibre's computed conversion."""
    while elem is not None:
        value = stylizer.style(elem).get(prop)
        if value and str(value).strip().lower() != "inherit":
            return value
        elem = elem.getparent() if hasattr(elem, "getparent") else None
    return None


#: CSS px to the unit, for font-size. The em is 16px, as Kindle Previewer
#: measures it (24px is 1.5em, #241).
_FONT_PX = 16.0
_FONT_UNIT_PX = {
    "px": 1.0,
    "pt": 4 / 3,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96 / 2.54,
    "mm": 96 / 25.4,
    "q": 96 / 101.6,
}
_FONT_KEYWORD_PX = {
    "xx-small": 9.0,
    "x-small": 10.0,
    "small": 13.0,
    "medium": 16.0,
    "large": 18.0,
    "x-large": 24.0,
    "xx-large": 32.0,
    "xxx-large": 48.0,
}
_FONT_SIZE_RE = re.compile(r"^([0-9]*\.?[0-9]+)(em|rem|ex|ch|%|[a-z]+)$")


def _font_size_px(stylizer, elem, cache):
    """`elem`'s font size in CSS px, from each specified value up its
    ancestors. Calibre's computed size converts px at the output profile's
    DPI (24px came out 5.76pt on the Voyage profile), so it is not used.
    A value kfxgen can't read (`calc()`) is inherited."""
    if elem is None:
        return _FONT_PX
    if elem in cache:
        return cache[elem]
    inherited = _font_size_px(stylizer, elem.getparent(), cache)
    size = _css_size_px(
        stylizer.style(elem).get("font-size"),
        inherited,
        lambda: _root_size_px(stylizer, elem, cache),
    )
    cache[elem] = size
    return size


def _root_size_px(stylizer, elem, cache):
    """The root element's font size in CSS px, which `rem` is measured by."""
    root = elem.getroottree().getroot()
    return _FONT_PX if root is elem else _font_size_px(stylizer, root, cache)


def _css_size_px(value, inherited, root_px):
    """A font-size value in CSS px, against the `inherited` size, or the
    inherited size when there is none or kfxgen can't read it. `root_px()`
    gives the root's size, for rem."""
    value = str(value or "").strip().lower()
    if value in _FONT_KEYWORD_PX:
        return _FONT_KEYWORD_PX[value]
    if value == "smaller":
        # Previewer's step: <small> is 0.833333, <big> 1.2.
        return inherited / 1.2
    if value == "larger":
        return inherited * 1.2
    m = _FONT_SIZE_RE.match(value.replace(" ", ""))
    if not m or float(m.group(1)) <= 0:
        return inherited
    n, unit = float(m.group(1)), m.group(2)
    if unit == "em":
        return n * inherited
    if unit in ("ex", "ch"):
        return n * inherited / 2
    if unit == "%":
        return n * inherited / 100
    if unit == "rem":
        return n * root_px()
    if unit in _FONT_UNIT_PX:
        return n * _FONT_UNIT_PX[unit]
    return inherited


def _q_css(stylizer, q):
    """A <q>'s `quotes`, and its ::before and ::after `content`, as calibre's
    Stylizer has them: what Kindle Previewer prints in place of the
    language's marks (#503 QA). `quotes` inherits: the nearest element
    declaring it, up to one whose lang differs from its parent's, where a
    browser's own sheet sets that language's marks."""
    st = stylizer.style(q)
    pseudo = getattr(st, "pseudo_classes", None)
    rules = pseudo(()) if pseudo is not None else {}
    quotes = None
    for a in (q, *q.iterancestors()):
        value = (stylizer.style(a).get("quotes") or "").strip()
        if value and value.lower() != "inherit":
            quotes = value
            break
        if _starts_a_language(a):
            break
    return {
        "quotes": quotes,
        "before": dict(rules.get("before") or {}).get("content"),
        "after": dict(rules.get("after") or {}).get("content"),
    }


def _lang_of(elem):
    """The nearest `lang` at or above `elem`, or None. Kindle Previewer
    4.0.3 reads `lang` only, never `xml:lang`, for a <q>'s marks (#503 QA)."""
    for a in (elem, *elem.iterancestors()):
        if a.get("lang") is not None:
            return a.get("lang")
    return None


def _starts_a_language(elem):
    """Whether `elem` sets a lang its parent doesn't have: there a browser's
    own sheet gives that language's quotation marks (#503 QA). Compared as
    written: Previewer takes lang="EN" under "en" as a change."""
    own = elem.get("lang")
    if own is None:
        return False
    parent = elem.getparent()
    return own != (_lang_of(parent) if parent is not None else None)


def _first_letter_css(stylizer, elem, cache):
    """The paragraph's `::first-letter` CSS, its font size in CSS px, or None.
    calibre keeps the rule on the paragraph's style for KFX; it makes a span
    of the letter only for MOBI and DOCX (#240)."""
    # calibre's is a method: `pseudo_classes(filter_css)` copies the rules
    # without the properties named in `filter_css`.
    pseudo = getattr(stylizer.style(elem), "pseudo_classes", None)
    rule = pseudo(()).get("first-letter") if pseudo is not None else None
    if not rule:
        return None
    css = dict(rule)
    if css.get("font-size"):
        size = _css_size_px(
            css["font-size"],
            _font_size_px(stylizer, elem, cache),
            lambda: _root_size_px(stylizer, elem, cache),
        )
        css["font-size"] = round(size, 3)
    return css


_DECORATION_LINES = {"underline", "line-through"}


def _decoration_lines(stylizer, elem):
    """The decoration lines drawn through `elem`'s text, as one string, or
    None. A descendant's `none` can't take an ancestor's line away."""
    lines = set()
    while elem is not None:
        style = stylizer.style(elem)
        for prop in ("text-decoration", "text-decoration-line"):
            lines.update(str(style.get(prop) or "").lower().split())
        elem = elem.getparent() if hasattr(elem, "getparent") else None
    return " ".join(sorted(lines & _DECORATION_LINES)) or None


def _default_stylizer(oeb_book, item):
    # Single source of the Stylizer signature lives in font_table (#41).
    from .font_table import build_stylizer  # noqa: PLC0415

    return build_stylizer(oeb_book, item)


def _build_style_resolver(oeb_book, item, log, stylizer_factory=None):
    """Return a callable elem->computed-CSS-dict using Calibre's Stylizer, or
    None when Calibre/Stylizer is unavailable or construction fails. Never
    raises — failure degrades to no per-element block styling.

    `stylizer_factory(oeb_book, item)` is injectable for tests; the default
    builds Calibre's Stylizer."""
    try:
        make = stylizer_factory or _default_stylizer
        stylizer = make(oeb_book, item)
        sizes = {}
        fills = {}

        def behind(elem):
            # The fill of the nearest box kfxgen draws around `elem`, or
            # its own, as 0xRRGGBB, or None for the page: text colours are
            # measured against it (#383). Only a box draws a fill.
            if elem in fills:
                return fills[elem]
            fill = None
            if _local_tag(elem.tag) in _BOX_TAGS:
                fill = _box_background(stylizer.style(elem))
            if fill is None:
                parent = elem.getparent()
                if parent is not None and isinstance(parent.tag, str):
                    fill = behind(parent)
            else:
                fill &= 0xFFFFFF
            fills[elem] = fill
            return fill

        def resolve(elem):
            try:
                st = stylizer.style(elem)
                return {
                    # A number, in CSS px; the generator measures it
                    # against the book's main text size (#241).
                    "font-size": round(_font_size_px(stylizer, elem, sizes), 3),
                    # A drop cap: the paragraph's `::first-letter`, and a
                    # floated span's float (#240). Declared: neither
                    # inherits. Only a <p> is read for an initial, and
                    # calibre copies the rules on each call (#376 review).
                    "first-letter": (
                        _first_letter_css(stylizer, elem, sizes)
                        if _local_tag(elem.tag) == "p"
                        else None
                    ),
                    # A <q>'s marks as the book styles them (#503 QA).
                    "q-css": (
                        _q_css(stylizer, elem) if _local_tag(elem.tag) == "q" else None
                    ),
                    "float": st.get("float"),
                    # Declared: it doesn't inherit (#381).
                    "clear": st.get("clear"),
                    # text-align is INHERITED (set on <body> or a wrapper
                    # <div>), so read it inheritance-aware like font-family:
                    # Style.get() returns None when inherited; Style[prop]
                    # returns the computed value ('auto' when truly unset,
                    # which ALIGN_MAP ignores). text-indent/margins stay on
                    # .get(): margins don't inherit, and getitem drops the CSS
                    # unit on indent (returns computed px).
                    "text-align": _computed_value(st, "text-align"),
                    "text-indent": st.get("text-indent"),
                    "margin-left": st.get("margin-left"),
                    "margin-right": st.get("margin-right"),
                    # Read for a box's space above and below (#238).
                    "margin-top": st.get("margin-top"),
                    "margin-bottom": st.get("margin-bottom"),
                    # font-family/-weight/-style are also inherited, usually set
                    # on <body> and inherited by paragraphs.
                    "font-family": _computed_value(st, "font-family"),
                    # Not the computed value: calibre passes a numeric weight
                    # through a unit conversion (700 comes back as 299.197),
                    # which then read as normal (#312).
                    "font-weight": _specified_inherited(stylizer, elem, "font-weight"),
                    # Gathered from the element and its ancestors: a line is
                    # drawn through all the text inside the element that
                    # sets it, and calibre's `.get()` is the element's own
                    # (#237).
                    "text-decoration": _decoration_lines(stylizer, elem),
                    # Inherited, so read up the ancestors as written:
                    # calibre's computed letter-spacing is in points, and
                    # its computed value converts units on every lookup,
                    # 4 seconds on pg100 for these four (#239).
                    "letter-spacing": _specified_inherited(
                        stylizer, elem, "letter-spacing"
                    ),
                    "font-variant": _specified_inherited(
                        stylizer, elem, "font-variant"
                    ),
                    "text-transform": _specified_inherited(
                        stylizer, elem, "text-transform"
                    ),
                    "color": _specified_inherited(stylizer, elem, "color"),
                    "font-style": _computed_value(st, "font-style"),
                    # vertical-align does NOT inherit, so .get() is right here:
                    # it returns the declared value, or None when the element
                    # has no rule of its own. Read for inline runs (#52); the
                    # block path ignores it.
                    "vertical-align": st.get("vertical-align"),
                    # An <img>'s own size. Stylizer folds the width/height
                    # attributes into the element's CSS (and strips them),
                    # so this is the one place both spellings meet.
                    "width": st.get("width"),
                    "height": st.get("height"),
                    # A page drawn as a background layer carries its picture
                    # here and nowhere in the markup (#168). `background`
                    # shorthand is not consulted: Stylizer does not expand it,
                    # and no producer seen writes a page that way.
                    "background-image": st.get("background-image"),
                    # A box's fill (#238). Declared: it doesn't inherit.
                    "background-color": st.get("background-color"),
                    # Its colour when written as the shorthand (#464).
                    "background": st.get("background"),
                    # What the text is drawn on (#383).
                    "background-behind": behind(elem),
                    "background-repeat": st.get("background-repeat"),
                    # Where the book breaks the page: a contents entry
                    # there starts a section, one anywhere else is a place
                    # in a section (#363). Declared: they don't inherit, and
                    # calibre keeps each spelling as written.
                    "page-break-before": st.get("page-break-before"),
                    "page-break-after": st.get("page-break-after"),
                    "break-before": st.get("break-before"),
                    "break-after": st.get("break-after"),
                    # Computed, because it inherits: <code> inside <pre> is
                    # preformatted too, and calibre's UA sheet gives <pre>
                    # `white-space: pre`. (#202)
                    "white-space": _computed_value(st, "white-space"),
                    # A list item's marker (#201). list-style-type inherits
                    # from the list, and Calibre's UA sheet already maps
                    # `<ol type>` and nested lists onto it, so the computed
                    # value is the whole answer. The `list-style` shorthand
                    # is expanded by Stylizer. display does not inherit:
                    # the declared value is what can take the marker away.
                    "list-style-type": _computed_value(st, "list-style-type"),
                    "display": st.get("display"),
                    # Each side's computed border (style, width in points,
                    # colour), top, right, bottom, left (#264).
                    # Each side's declared padding, units kept: Kindle
                    # Previewer converts em and px differently, and the
                    # computed value is in points either way (#264).
                    "padding-sides": tuple(
                        st.get(f"padding-{side}") for side in _BORDER_SIDES
                    ),
                    "border-collapse": _computed_value(st, "border-collapse"),
                    "border-sides": tuple(
                        (
                            _computed_value(st, f"border-{side}-style"),
                            _computed_value(st, f"border-{side}-width"),
                            _computed_value(st, f"border-{side}-color"),
                        )
                        for side in _BORDER_SIDES
                    ),
                }
            except Exception:
                return None

        return resolve
    except Exception as e:
        log.warning(f"  Stylizer unavailable ({e}); skipping per-element CSS")
        return None


#: Each length unit as a share of the page width, as Kindle Previewer converts
#: a container's margin onto its paragraphs: 2em became 6.25 % (#238). A px is
#: 0.45pt and an em 12pt, the rates #296 measured for padding.
_UNIT_TO_PCT = {
    "$308": 3.125,  # em
    "$505": 3.125,  # rem
    "$319": 3.125 * 0.45 / 12,  # px
    "$318": 3.125 / 12,  # pt
    "$316": 3.125 * 72 / 25.4 / 12,  # mm
    "$314": 1.0,  # %
}
#: Table parts: a table's margins do not reach its rows (#219).
_NO_CARRY_TAGS = frozenset(
    {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup"}
)


#: The most of the page width that margins carried from containers may
#: leave unused: half (#238).
_MAX_CARRIED_MARGINS = 50.0


def _page_share(margin):
    """A (magnitude, unit) margin as a share of the page width, in %."""
    return float(margin[0]) * _UNIT_TO_PCT[margin[1]] if margin else 0.0


def _add_margin(own, extra):
    """A paragraph's margin with a container's added, as a share of the page
    width, which is how Kindle Previewer writes it: unlike an em margin, it
    doesn't grow with the reader's font size. Either may be None; with no
    container margin the paragraph's own is kept as it is."""
    if extra is None:
        return own
    total = _page_share(own) + _page_share(extra)
    return (f"{total:.4f}".rstrip("0").rstrip("."), "$314")


def _has_real_text(text):
    """True if `text` has any non-whitespace content once IMG tokens are removed.

    An image-only page (e.g. the EPUB's own cover.xhtml, which is just an
    <img>) reduces to empty here.
    """
    return bool(_IMG_TOKEN_RE.sub("", text or "").strip())


def _img_basename(href):
    """Basename of an image href — the key the generator matches resources by."""
    return (href or "").split("#", 1)[0].rsplit("/", 1)[-1]


def _page_shows_something(text, cover_href=None):
    """True when a page puts something in front of the reader: real text, or
    an image other than the cover.

    Image-only pages the TOC did not claim used to be discarded wholesale, on
    the strength of the one case that is right to drop — the EPUB's own
    cover.xhtml, whose picture the cover chapter already shows (#32). That
    rule took every page of a comic or picture book past the last TOC entry
    with it. The cover is the exception; a page is content.
    """
    if _has_real_text(text):
        return True
    cover = _img_basename(cover_href) if cover_href else None
    return any(
        _img_basename(m.group(1)) != cover for m in _IMG_TOKEN_RE.finditer(text or "")
    )


def _local_tag(tag):
    """Strip the lxml namespace prefix from a tag, returning the local name."""
    if not isinstance(tag, str):
        return None
    return tag.rsplit("}", 1)[-1]


#: The token's own delimiters, stripped from any value taken out of the book
#: before it is placed between them.
_IMG_TOKEN_CONTROLS = (_IMG_TOKEN_DELIM, _IMG_TOKEN_FIELD, _IMG_TOKEN_SPACE)


def _strip_token_controls(value):
    """Remove the token's delimiters from a value the book supplies.

    Every field of an EPUB is attacker-controlled (SECURITY.md), and href and
    alt are both written between delimiters. A `0x01` in either one opens a
    field that is not there: with the size field added, alt text of
    `photo<0x01>w=100%` parses as alt `photo` plus a size of 100% — an
    image width chosen by the book, and alt text silently truncated. A
    NUL ends the token early instead, leaving the remainder of the value
    in the text stream as raw control bytes, which is the #133 failure.
    """
    for ch in _IMG_TOKEN_CONTROLS:
        value = value.replace(ch, "")
    return value


def _make_img_token(href, alt, size=None, float_side=None):
    """Encode an <img> reference as a placeholder token string.

    Spaces in alt text are escaped to a control char so str.split() doesn't
    fragment the token during whitespace normalization. `size` is the hint
    from `_img_size_hint`, carried as an optional third field, and
    `float_side` ("left" or "right") as a fourth (#243).
    """
    safe_href = _strip_token_controls(href or "")
    escaped_alt = _strip_token_controls(alt or "").replace(" ", _IMG_TOKEN_SPACE)
    size_field = f"{_IMG_TOKEN_FIELD}{size or ''}" if size or float_side else ""
    if float_side:
        size_field += f"{_IMG_TOKEN_FIELD}{float_side}"
    return (
        f"{_IMG_TOKEN_DELIM}IMG{_IMG_TOKEN_FIELD}{safe_href}"
        f"{_IMG_TOKEN_FIELD}{escaped_alt}{size_field}{_IMG_TOKEN_DELIM}"
    )


#: A CSS length or percentage, as the size hint accepts it. A bare number is
#: an HTML attribute value in pixels.
_IMG_SIZE_RE = re.compile(r"^\d+(?:\.\d+)?(?:%|px|em|pt)?$")


def _img_size_hint(elem, style_resolver=None):
    """The size the markup asks for, as ``w=98%`` / ``w=200px`` / ``h=50%``,
    or None when the image is left at its natural size.

    Checked against Kindle Previewer 3.106 on five test books: an explicit
    width becomes the image's width, a height only counts when no width is
    given, and `max-width` / `max-height` are ignored altogether. Read from
    the computed CSS when a resolver is available (inside Calibre the
    Stylizer has already folded the attributes in), else from the attributes.
    """
    css = (style_resolver(elem) if style_resolver is not None else None) or {}
    for axis in ("width", "height"):
        raw = css.get(axis)
        if raw is None or raw == "":
            raw = elem.get(axis)
        raw = str(raw or "").strip()
        if raw and raw != "auto" and _IMG_SIZE_RE.match(raw):
            return f"{axis[0]}={raw}"
    return None


def _float_side(css):
    """ "left" or "right" for an element floated to that side, else None."""
    side = str((css or {}).get("float") or "").strip().lower()
    return side if side in ("left", "right") else None


def _clear_side(css):
    """ "left", "right" or "both" for an element set to clear floats on that
    side, else None (#381)."""
    side = str((css or {}).get("clear") or "").strip().lower()
    return side if side in ("left", "right", "both") else None


def _img_float(img, parent, size, style_resolver):
    """(float side or None, size hint) for an <img> (#243). Its own float
    wins; an image alone in a floated wrapper (`<p class="fl"><img/></p>`,
    a <span>) floats at the wrapper's width, as Kindle Previewer writes it.
    Previewer floats a wrapper of more as one container, which kfxgen
    doesn't write: its images stay unfloated.
    """
    if style_resolver is None:
        return None, size
    side = _float_side(style_resolver(img))
    if side or parent is None:
        return side, size
    pcss = style_resolver(parent) or {}
    side = _float_side(pcss)
    only_image = len(parent) == 1 and parent[0] is img
    if not side or not only_image or "".join(parent.itertext()).strip():
        return None, size
    width = str(pcss.get("width") or "").strip()
    if width and width != "auto" and _IMG_SIZE_RE.match(width):
        if size in (None, "w=100%"):
            size = f"w={width}"
    return side, size


#: Size hint for an image wrapped in <svg>. Amazon lays those out as a block
#: filling the page; a height of 100% is the same look in the reading flow.
SVG_IMAGE_SIZE = "h=100%"

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


#: SVG containers whose contents are definitions rather than drawing. An
#: <image> inside one is painted only where a <use> references it, so walking
#: into them paints pictures the publisher hid — and embeds the resource,
#: which #102 then cannot prune because an entry does display it.
#:
#: <use> is not resolved, so an image defined here and used elsewhere is
#: dropped rather than drawn in the wrong place. Dropping a referenced image
#: is the lesser error, and a reference file with one would be the thing to
#: change it on.
_SVG_NON_RENDERED_CONTAINERS = frozenset(
    {"defs", "symbol", "mask", "clippath", "pattern", "marker"}
)


#: `url(...)` in a CSS value, quoted or not.
_CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)""")


def _css_background_image(elem, css):
    """The href of a picture this element draws as its background, or None.

    Some print-to-EPUB chains put the scanned page in a `background-image` on
    an empty div and position the text over it. No `<img>`, no `<svg>`, so the
    page reached the reader with no picture at all — and a page-scan book with
    no text overlay reached it as a failed conversion, every spine item being
    empty (#168).

    Deliberately the narrowest rule that covers that shape, because the risk
    runs the other way: emitting for every `background-image` would *add*
    pictures to books that convert correctly today.

    * a `background-image` with a `url(...)`;
    * the element draws nothing else — no element children, no text of its
      own, so it is a picture rather than a decorated box;
    * `background-repeat: no-repeat`, which is the cheapest thing separating
      a page scan from a texture. A tiled background is decoration by
      definition, and the producers that lay pages out this way all set it.
    """
    if not css:
        return None
    if len(elem) or (elem.text or "").strip():
        return None
    if (css.get("background-repeat") or "").strip().lower() != "no-repeat":
        return None
    match = _CSS_URL_RE.search(css.get("background-image") or "")
    if not match:
        return None
    href = match.group(1).strip()
    return href or None


def _svg_image_refs(svg):
    """(href, alt) for every <image> an <svg> element actually draws.

    Publishers wrap full-page art in SVG — calibre's own cover page does —
    and Amazon renders it; here the page used to come through empty.

    Walks rather than using `iter()`, so a subtree that SVG does not paint can
    be skipped whole.
    """
    refs = []

    def walk(node):
        for child in node:
            local = _local_tag(child.tag)
            if not local or local.lower() in _SVG_NON_RENDERED_CONTAINERS:
                continue
            if local == "image":
                href = child.get(_XLINK_HREF) or child.get("href") or ""
                if href:
                    refs.append((href, ""))
            walk(child)

    walk(svg)
    return refs


# SVG parts that hold text no reader sees: its title and description, its
# stylesheet and scripts, and its metadata.
_SVG_TEXT_SKIP = _SVG_NON_RENDERED_CONTAINERS | {
    "title",
    "desc",
    "style",
    "script",
    "metadata",
    "foreignobject",
}


_SVG_CONDITIONS = ("systemLanguage", "requiredFeatures", "requiredExtensions")


def _svg_text_paragraphs(svg):
    """The words an <svg> draws as <text>: (text, <text> element) for each,
    in document order (#231). Picture books set a page's words over the art
    this way.

    A <tspan> or <textPath> with its own x or y starts a new line, so it is a
    word break; one without continues the run it sits in. A <switch> draws
    one child: its first without a condition (the fallback a reader shows),
    else its first."""
    out = []

    def run(node):
        s = node.text or ""
        for child in node:
            if _local_tag(child.tag) in ("tspan", "textPath", "a"):
                placed = child.get("x") is not None or child.get("y") is not None
                if placed and s and not s[-1].isspace():
                    s += " "
                s += run(child)
            s += child.tail or ""
        return s

    def visit(node):
        local = _local_tag(node.tag)
        if not local or local.lower() in _SVG_TEXT_SKIP:
            return
        if local == "text":
            text = " ".join(run(node).split())
            if text:
                out.append((text, node))
        elif local == "switch":
            kids = [c for c in node if _local_tag(c.tag)]
            plain = [c for c in kids if not any(c.get(a) for a in _SVG_CONDITIONS)]
            if plain or kids:
                visit((plain or kids)[0])
        else:
            for child in node:
                visit(child)

    for child in svg:
        visit(child)
    return out


_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _resolve_doc_path(base_href, href):
    """Resolve `href` against the document it appears in, as a book-internal key.

    Anchor keys used to be bare basenames, so `text/notes.xhtml` and
    `back/notes.xhtml` produced the same key and the first one silently won
    every link aimed at either. Keeping the directory makes them distinct. (#69)

    A leading `../` is a normal cross-folder link inside an EPUB, so it is
    resolved rather than rejected — but anything that escapes the book root, is
    absolute, or fails the shared traversal defenses still returns "". These
    keys are internal identifiers and never reach the filesystem; the escape
    check keeps them from naming anything outside the book regardless.
    """
    if href and _is_unsafe_href(href) and not href.startswith(".."):
        _security_log.warning("rejected unsafe href in _resolve_doc_path: %r", href)
        return ""
    base = (base_href or "").replace("\\", "/")
    # An empty href means "this document" — the file itself, not its folder.
    joined = (
        posixpath.join(posixpath.dirname(base), href.replace("\\", "/"))
        if href
        else base
    )
    resolved = posixpath.normpath(joined)
    if resolved.startswith("..") or resolved.startswith("/") or resolved == ".":
        return ""
    return resolved


def _resolve_img_src(base_href, src):
    """Resolve an `<img src>` to the container-relative href the manifest uses.

    A manifest href is relative to the container (`images/pic.jpg`); an
    `<img src>` is relative to the document holding it (`../images/pic.jpg`).
    Matching them by basename bridged that gap but collapsed two images that
    shared a filename onto one resource, so the reader saw the first picture
    twice. Resolving puts both sides in one namespace, the way `<a href>` has
    been since #69.

    Falls back to the raw `src` when resolution yields nothing (no base, an
    absolute path, an href that escapes the book root). The generator's
    basename fallback can still find the image from the raw value; an empty
    string would lose it.

    A remote, absolute or `data:` source is not a book-internal path, so it is
    returned untouched rather than passed to `_resolve_doc_path`, which would
    log it as a rejected security event. A leading `../` is not skipped here:
    that is ordinary cross-folder markup, and the resolver handles it quietly.
    """
    if not src or (_is_unsafe_href(src) and not src.startswith("..")):
        return src
    return _resolve_doc_path(base_href, src) or src


def _key_doc(base_href, href):
    """`_resolve_doc_path`, decoded: the file part of an anchor or link key.
    calibre keeps some file names percent-encoded (spaces) and others not
    (non-ASCII), and a link may spell either, so keys use the decoded name on
    both sides (#278). Picture lookups keep `_resolve_doc_path` as is."""
    return unquote(_resolve_doc_path(base_href, href))


def _drop_keys(blocks, keys):
    """Take `keys` off every block, box paragraph and table part holding
    them (#338)."""
    keys = set(keys)
    for b in blocks:
        parts = [b]
        tbl = b.get("table")
        if tbl:
            cells = [c for r in tbl["rows"] for c in r["cells"]]
            parts += [tbl, *tbl["rows"], *cells]
            parts += [p for c in cells for p in c.get("paragraphs") or ()]
        for part in parts:
            if part.get("anchor_keys"):
                part["anchor_keys"] = [k for k in part["anchor_keys"] if k not in keys]
            for k in keys & set(part.get("anchor_offsets") or ()):
                del part["anchor_offsets"][k]
        if b.get("type") == "box":
            _drop_keys(b["blocks"], keys)


def _prepend_keys(block, keys):
    """Put `keys` at the start of `block`, as a file's own key goes on its
    first block. A native table needs them on its own keys too, which its
    first row takes; from the block's keys alone they reach a later row."""
    block["anchor_keys"] = _dedupe_keep_order(
        keys + list(block.get("anchor_keys") or [])
    )
    block.setdefault("anchor_offsets", {}).update(dict.fromkeys(keys, 0))
    tbl = block.get("table")
    if tbl is not None:
        tbl["anchor_keys"] = _dedupe_keep_order(
            keys + list(tbl.get("anchor_keys") or [])
        )


def _resolve_link_target(href, base_href):
    """Normalize an in-book `<a href>` to a "<file>#<fragment>" anchor key.

    Returns None for anything that can't be an in-book target: empty hrefs,
    URL schemes, absolute paths, and traversal — all rejected by the shared
    `_normalize_href` defenses. A bare "#frag" resolves against `base_href`,
    the spine file the markup came from, so note markers in a chapter and
    the notes they point at agree on one key. (#53)
    """
    if not href:
        return None
    href = href.strip()
    if not href:
        return None
    # Any scheme at all (mailto:, tel:, http:, ...) means this leaves the book.
    # _is_unsafe_href only knows the schemes that are a security concern; an
    # in-book target simply never has one, so reject the whole shape here.
    if _URL_SCHEME_RE.match(href):
        return None
    # A fragment is percent-encoded in a URL but an id is written as-is, so
    # "#fn%3A1" names id="fn:1" (#278). Only the decoded form is matched: an
    # id that itself holds "%" (id="x%41", linked raw as "#x%41") now misses.
    # None of 1,373 library EPUBs has such an id.
    fragment = unquote(_href_fragment(href) or "")
    file_part = href.split("#", 1)[0]
    if file_part:
        target_file = _key_doc(base_href, file_part)
    else:
        # Same-file link: "#frag" is relative to the document it appears in.
        target_file = _key_doc(base_href, "") if base_href else ""
    if not target_file:
        return None
    return f"{target_file}#{fragment}" if fragment else target_file


_WS_FLAGS = frozenset({FLAG_PRE, FLAG_PRE_LINE})
_PRESERVING_WHITE_SPACE = {"pre", "pre-wrap", "break-spaces"}


def _white_space_flags(elem, style_resolver, inherited):
    """The white-space mode flag for `elem`, given its parent's (#202).

    The computed `white-space` decides when calibre supplies one; it already
    inherits, and calibre's UA sheet maps <pre>. Without a stylizer a <pre>
    is `pre` and anything else keeps what it inherited."""
    css = style_resolver(elem) if style_resolver is not None else None
    value = str((css or {}).get("white-space") or "").strip().lower()
    if not value:
        if _local_tag(elem.tag) == "pre":
            return frozenset({FLAG_PRE})
        return inherited & _WS_FLAGS
    if value in _PRESERVING_WHITE_SPACE:
        return frozenset({FLAG_PRE})
    if value == "pre-line":
        return frozenset({FLAG_PRE_LINE})
    return frozenset()


def _is_preformatted(elem, style_resolver):
    return FLAG_PRE in _white_space_flags(elem, style_resolver, frozenset())


def _css_emphasis(css):
    """(bold, italic, monospace families or None, font size or None,
    typography) from an element's computed CSS, or None without it. The
    typography is `css_typography`'s (#239).
    `bolder` is relative, but from any weight a book uses it lands on bold
    (`lighter` reads as normal already)."""
    if not css:
        return None
    weight = str(css.get("font-weight") or "").strip().lower()
    bold = weight == "bolder" or parse_weight(weight) >= BOLD_WEIGHT_THRESHOLD
    families = tuple(_parse_font_family(css.get("font-family")))
    mono = families if is_monospace(families) else None
    return (
        bold,
        parse_style(css.get("font-style")),
        mono,
        css_font_size(css),
        css_typography(css),
    )


def _first_letter_length(text):
    """How many characters `::first-letter` takes: any punctuation before
    the first letter, then the letter, as calibre and Previewer count it
    (a quote and its letter are 2). 0 when the text opens on no letter."""
    i = 0
    while i < len(text) and unicodedata.category(text[i])[0] in "PZ":
        i += 1
    if i == len(text) or unicodedata.category(text[i])[0] not in "LN":
        return 0
    return i + 1


def _with_initial(spans, n, add):
    """`spans` with the flags in `add` on the first `n` characters. A size in
    `add` replaces the characters' own."""
    head = [frozenset()] * n
    rest = []
    for s, length, flags in spans:
        for i in range(s, min(s + length, n)):
            head[i] = flags
        if s + length > n:
            start = max(s, n)
            rest.append((start, s + length - start, flags))
    if run_font_size(add) is not None:
        head = [frozenset(f for f in fl if run_font_size((f,)) is None) for fl in head]
    out = []
    for i, flags in enumerate(head):
        flags = flags | add
        if out and out[-1][0] + out[-1][1] == i and out[-1][2] == flags:
            out[-1] = (out[-1][0], out[-1][1] + 1, flags)
        else:
            out.append((i, 1, flags))
    if (
        rest
        and out
        and out[-1][0] + out[-1][1] == rest[0][0]
        and out[-1][2] == rest[0][2]
    ):
        out[-1] = (out[-1][0], out[-1][1] + rest[0][1], out[-1][2])
        rest = rest[1:]
    return out + rest


def _is_float_left(css):
    return str((css or {}).get("float") or "").strip().lower() == "left"


def _leading_float(node, style_resolver):
    """(the floated element `node`'s text opens on, whether the search is
    over): an element floated left with no text before it."""
    if (node.text or "").strip():
        return None, True
    for child in node:
        if isinstance(child.tag, str):
            if _local_tag(child.tag) in ("img", "svg"):
                return None, True
            if _is_float_left(style_resolver(child)):
                return child, True
            found, over = _leading_float(child, style_resolver)
            if over:
                return found, True
        if (child.tail or "").strip():
            return None, True
    return None, False


def _initial(elem, css, text, spans, style_resolver):
    """A paragraph's initial (#240), from its resolved `css`: its spans with
    the `::first-letter` CSS on the letter, and how many characters a drop
    cap takes, or None.

    A drop cap is an initial floated left: the paragraph's `::first-letter`,
    or an element its text opens on (`<span class="dropcap">O</span>nce`),
    whose run size the walker already gave it. The generator decides from
    the initial's size whether it drops. Not floated, an initial is only
    larger: a raised initial."""
    first = (css or {}).get("first-letter")
    if first:
        n = _first_letter_length(text)
        if not n:
            return spans, None
        bold, italic, _mono, size, typography = _css_emphasis(first)
        add = set()
        if size is not None:
            add.add(make_size_flag(size))
        # Its colour, and the rest, as a run of its own (#239). Black, the
        # reader's own, isn't.
        for kind, value in zip(TYPOGRAPHY_FLAGS, typography):
            if value is not None and value != BLACK:
                add.add((kind, value))
        if bold:
            add.add(FLAG_BOLD)
        if italic:
            add.add(FLAG_ITALIC)
        if add:
            spans = _with_initial(spans, n, frozenset(add))
        return spans, n if _is_float_left(first) else None
    found, _over = _leading_float(elem, style_resolver)
    if found is None:
        return spans, None
    letters = " ".join("".join(found.itertext()).split())
    if letters and text.startswith(letters):
        return spans, len(letters)
    return spans, None


#: In `_walk_inline(split_tables=True)`, stands in for a table nested in the
#: row being walked, as `(_TABLE_SPLIT, <table>)`; the caller splits there.
_TABLE_SPLIT = object()


def _is_link_flag(flag):
    """Whether a run flag is a link target (`make_link_flag`)."""
    return isinstance(flag, tuple) and flag[:1] == make_link_flag("")[:1]


#: `display` values that draw an element as a block of its own (#233).
_BLOCK_DISPLAYS = frozenset(
    {"block", "list-item", "table", "table-row", "table-caption", "flex", "grid"}
    | {"flow-root"}
)


def _draws_as_block(local, css):
    """Whether an element HTML treats as inline (a <span>, an <a>, an <em>)
    is drawn as a block by its CSS. A tag that is a block already keeps its
    own handling: calibre's sheet gives every <p> `display: block`, and a
    paragraph walked inline in a cell stays apart by a space (#279)."""
    if local in _CELL_BLOCK_TAGS or local in _CELL_TAGS or local in _BODY_BLOCK_TAGS:
        return False
    if local in _TABLE_PART_TAGS or local == "table":
        return False
    display = str((css or {}).get("display") or "").strip().lower()
    return display in _BLOCK_DISPLAYS


#: The quotation marks a <q> is printed with, by its lang as a whole tag:
#: (open, close, nested open, nested close). Measured from Kindle Previewer
#: 4.0.3's output for 4,422 tags, 66 languages with and without 75 regions
#: and scripts (#469, #503 QA); a tag not here, en-US and de-AT among them,
#: gets the plain pair Previewer writes for no lang at all.
_Q_MARKS = {
    "af": "\u201c\u201d\u2018\u2019",
    "ar": "\u201d\u201c\u2019\u2018",
    "az-cyrl": "\u00ab\u00bb\u2039\u203a",
    "bg": "\u201e\u201c\u201a\u2018",
    "bn": "\u201c\u201d\u2018\u2019",
    "br": "\u00ab\u00bb\u2039\u203a",
    "bs-cyrl": "\u201e\u201c\u201a\u2018",
    "ca": "\u201c\u201d\u00ab\u00bb",
    "cs": "\u201e\u201c\u201a\u2018",
    "da": "\u201c\u201d\u2018\u2019",
    "de": "\u201e\u201c\u201a\u2018",
    "de-ch": "\u00ab\u00bb\u2039\u203a",
    "el": "\u00ab\u00bb\u201c\u201d",
    "en": "\u201c\u201d\u2018\u2019",
    "en-gb": "\u201c\u201d\u2018\u2019",
    "es": "\u201c\u201d\u00ab\u00bb",
    "et": "\u201e\u201c\u201a\u2018",
    "eu": "\u201c\u201d\u00ab\u00bb",
    "fa": "\u00ab\u00bb\u2039\u203a",
    "fi": "\u201d\u201d\u2019\u2019",
    "fr": "\u00ab\u00bb\u00ab\u00bb",
    "fr-ca": "\u00ab\u00bb\u2039\u203a",
    "fr-ch": "\u00ab\u00bb\u2039\u203a",
    "hi": "\u201c\u201d\u2018\u2019",
    "hr": "\u201e\u201c\u201a\u2018",
    "hu": "\u201e\u201d\u00bb\u00ab",
    "id": "\u201c\u201d\u2018\u2019",
    "it": "\u00ab\u00bb\u201c\u201d",
    "ja": "\u300c\u300d\u300e\u300f",
    "ko": "\u201c\u201d\u2018\u2019",
    "lt": "\u201e\u201c\u201e\u201c",
    "lv": "\u201c\u201d\u2018\u2019",
    "mk": "\u201e\u201c\u201a\u2018",
    "ms": "\u201c\u201d\u2018\u2019",
    "nb": "\u00ab\u00bb\u2018\u2019",
    "nl": "\u201c\u201d\u2018\u2019",
    "nn": "\u00ab\u00bb\u2018\u2019",
    "pl": "\u201e\u201d\u00ab\u00bb",
    "pt": "\u201c\u201d\u2018\u2019",
    "pt-pt": "\u00ab\u00bb\u201c\u201d",
    "ro": "\u201e\u201d\u00ab\u00bb",
    "ru": "\u00ab\u00bb\u201e\u201c",
    "sk": "\u201e\u201c\u201a\u2018",
    "sl": "\u201e\u201c\u201a\u2018",
    "sq": "\u201e\u201c\u201a\u2018",
    "sr": "\u201e\u201c\u201a\u2018",
    "sr-latn": "\u201e\u201c\u201a\u2018",
    "sv": "\u201d\u201d\u2019\u2019",
    "sw": "\u201c\u201d\u2018\u2019",
    "ta": "\u201c\u201d\u2018\u2019",
    "te": "\u201c\u201d\u2018\u2019",
    "th": "\u201c\u201d\u2018\u2019",
    "tr": "\u201c\u201d\u2018\u2019",
    "uk": "\u00ab\u00bb\u201e\u201c",
    "ur": "\u201d\u201c\u2019\u2018",
    "vi": "\u201c\u201d\u2018\u2019",
    "zh": "\u201c\u201d\u2018\u2019",
    "zh-hant": "\u300c\u300d\u300e\u300f",
    "zu": "\u201c\u201d\u2018\u2019",
}
_Q_PLAIN = "\"\"''"


_CSS_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'')


def _css_strings(value):
    """The strings in a CSS value, escapes read ("\\201C" is a quotation
    mark)."""

    def unescape(text):
        text = re.sub(
            r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(int(m.group(1), 16)), text
        )
        return re.sub(r"\\(.)", r"\1", text)

    return [unescape(a or b) for a, b in _CSS_STRING_RE.findall(value or "")]


def _q_marks(q, css=None):
    """(open, close) for a <q>, as Kindle Previewer 4.0.3 prints them: its
    ::before and ::after content when the book sets one, else its `quotes`
    (none, or pairs by depth), else its nearest lang's pair (an empty lang is
    unknown, so plain); the nested pair for a <q> inside another (#469,
    #503 QA)."""
    css = css or {}
    depth = sum(1 for a in q.iterancestors() if _local_tag(a.tag) == "q")
    lang = _lang_of(q) or ""
    marks = _Q_MARKS.get(lang.lower(), _Q_PLAIN)
    pair = (marks[2], marks[3]) if depth else (marks[0], marks[1])
    quotes = (css.get("quotes") or "").strip().lower()
    if quotes == "none":
        pair = ("", "")
    elif strings := _css_strings(css.get("quotes")):
        pairs = list(zip(strings[0::2], strings[1::2]))
        if pairs:
            pair = pairs[min(depth, len(pairs) - 1)]

    def content(value, i):
        value = (value or "").strip()
        if value in ("", "normal"):
            return pair[i]
        if value == "none":
            return ""
        if value in ("open-quote", "close-quote"):
            return pair[i]
        return "".join(_css_strings(value))

    return content(css.get("before"), 0), content(css.get("after"), 1)


def _walk_inline(
    elem,
    flags=frozenset(),
    style_resolver=None,
    is_root=True,
    base_href=None,
    split_tables=False,
    parent_emphasis=None,
):
    """Yield (segment, flags) pairs for inline content, accumulating italic/
    bold from ancestor <em>/<i>/<strong>/<b> and superscript/subscript from
    <sup>/<sub> or a CSS `vertical-align`. <img> becomes an IMG token
    segment with empty flags so the generator still splits on it.

    `style_resolver` (elem -> css dict|None) is consulted only for inline
    descendants, never for the block element itself: a paragraph carrying
    `vertical-align` would otherwise turn its whole text into one raised
    run. (#52) The block's own computed bold and italic are read only to
    compare its children with: a child whose CSS weight or style differs
    from its parent's starts or ends that run. The block's own emphasis is
    the block's to write (#312). `parent_emphasis` is the parent's, passed
    down so each element is resolved once."""
    if not isinstance(elem.tag, str):
        # A comment or a processing instruction: lxml gives it a `.text`, but
        # it is markup, not content ("H2 anchor", a <?dp n="12"?> page
        # marker). Its `.tail` is real text, and the caller keeps it. (#252)
        # An unresolved entity node lands here too; calibre resolves entities
        # before the plugin sees the tree, so a conversion never meets one.
        return []
    if not is_root and _is_hidden(elem, style_resolver):
        # Hidden text inside a paragraph, a heading or a row: mostly page
        # markers (<a id="page12" hidden>12</a>), printed as "harbour12".
        # Its ids keep their place, since page lists and links point at
        # them; the caller keeps its tail, which is visible (#256).
        return [make_anchor_mark(aid) for aid in _subtree_anchor_ids(elem)]
    local = _local_tag(elem.tag)
    if local == "br":
        # A forced line break, which the normalizer turns into a newline. It
        # used to vanish, fusing "line one<br/>line two" into one word. (#202)
        return [make_anchor_mark(aid) for aid in _own_anchor_ids(elem)] + [
            (HARD_BREAK, frozenset(flags) - _WS_FLAGS)
        ]
    switch = _epub_switch_branch(elem)
    if switch is not None:
        ids, default = switch
        parts = [make_anchor_mark(aid) for aid in ids]
        if default is not None:
            parts.extend(
                _walk_inline(
                    default,
                    flags,
                    style_resolver,
                    is_root=False,
                    base_href=base_href,
                    split_tables=split_tables,
                    parent_emphasis=parent_emphasis,
                )
            )
        return parts
    cur = (set(flags) - _WS_FLAGS) | _white_space_flags(elem, style_resolver, flags)
    if local in _ITALIC_TAGS:
        cur.add(FLAG_ITALIC)
    if local in _BOLD_TAGS:
        cur.add(FLAG_BOLD)
    if local in _SUPER_TAGS:
        cur.add(FLAG_SUPER)
    if local in _SUB_TAGS:
        cur.add(FLAG_SUB)
    starts_link = False
    if local == "a":
        target = _resolve_link_target(elem.get("href"), base_href)
        if target:
            starts_link = True
            # A link inside a link (invalid, but the WordDumb plugin writes
            # it): the inner one wins, as a tap lands on the innermost
            # element. Keeping both left the run's target to the frozenset's
            # hash order, so it changed between runs (#317).
            cur = {f for f in cur if not _is_link_flag(f) and f != NOTEREF_FLAG}
            cur.add(make_link_flag(target))
            if "noteref" in _epub_types(elem):
                cur.add(NOTEREF_FLAG)
    css = None
    own_css = None
    emphasis = None
    if style_resolver is not None:
        own_css = style_resolver(elem)
        emphasis = _css_emphasis(own_css)
    if style_resolver is not None and "text-decoration" in (own_css or {}):
        # CSS decides, tags included: calibre's UA sheet gives <u>/<ins> and
        # <s>/<strike>/<del> their lines, and a book's `none` takes them
        # away (pg40739's `ins { text-decoration: none }`, #360 review).
        lines = str(own_css["text-decoration"] or "").split()
    else:
        lines = []
        if local in _UNDERLINE_TAGS:
            lines.append("underline")
        if local in _STRIKE_TAGS:
            lines.append("line-through")
    if "underline" in lines:
        cur.add(FLAG_UNDERLINE)
    if "line-through" in lines:
        cur.add(FLAG_STRIKE)
    if style_resolver is not None and not is_root:
        css = own_css
        if css:
            shift = parse_vertical_align(css.get("vertical-align"))
            if shift == "super":
                cur.add(FLAG_SUPER)
            elif shift == "sub":
                cur.add(FLAG_SUB)
        if parent_emphasis is None and elem.getparent() is not None:
            parent_emphasis = _css_emphasis(style_resolver(elem.getparent()))
        if emphasis is not None and parent_emphasis is not None:
            for flag, prop, tags, now, before in zip(
                (FLAG_BOLD, FLAG_ITALIC),
                ("font-weight", "font-style"),
                (_BOLD_TAGS, _ITALIC_TAGS),
                emphasis,
                parent_emphasis,
            ):
                if now and not before:
                    cur.add(flag)
                elif not now and (before or prop in own_css):
                    # Normal here: under bold, or a <b>/<i> whose CSS says
                    # normal (#362). With calibre the resolver always
                    # reports `prop`, and its UA sheet styles every <b>,
                    # <strong>, <i> and <em>; a test resolver that doesn't
                    # leaves tags alone. Bold the element shares with its
                    # parent stays the tag's: a no-font book writes no block
                    # bold (#34).
                    if flag in cur:
                        cur.discard(flag)
                    elif before:
                        # The emphasis is the paragraph's own, not a flag
                        # of this run's: say normal (#367 review).
                        cur.add(_NOT_FLAG[flag])
            if emphasis[2] != parent_emphasis[2]:
                # Monospace starts, ends, or changes family (#242).
                cur = {f for f in cur if mono_families((f,)) is None}
                if emphasis[2]:
                    cur.add(make_mono_flag(emphasis[2]))
            if emphasis[3] != parent_emphasis[3]:
                # A size of its own, or back to its parent's (#241).
                cur = {f for f in cur if run_font_size((f,)) is None}
                if emphasis[3] is not None:
                    cur.add(make_size_flag(emphasis[3]))
            # Small caps, text-transform, letter-spacing and colour: its own
            # when they differ from its parent's, "normal", "none" or 0
            # inside a paragraph that sets them, as Previewer writes them
            # (#239). A link's own colour is no run's: Previewer writes it
            # in the link's styles, and none in the run, not even the
            # colour of a span around it. Inside a link, a colour of its
            # own changes nothing either (#377 re-check).
            for kind, now, before, unset in zip(
                TYPOGRAPHY_FLAGS,
                emphasis[4],
                parent_emphasis[4],
                ("normal", "none", SPACING_NORMAL, None),
            ):
                if now == before or (kind == COLOUR_FLAG and now is None):
                    continue
                if kind == COLOUR_FLAG and any(_is_link_flag(f) for f in cur):
                    if starts_link:
                        cur = {f for f in cur if flag_value((f,), kind) is None}
                    continue
                cur = {f for f in cur if flag_value((f,), kind) is None}
                cur.add((kind, now if now is not None else unset))
    if local in _MONO_TAGS and "font-family" not in (own_css or {}):
        cur.add(make_mono_flag(("monospace",)))
    cur = frozenset(cur)
    parts = []
    # A <span> drawn as a block, one per line of verse, an address, a
    # letter's closing: its lines used to run together ("AB"). It is a line
    # of the paragraph, not a paragraph of its own: kfxgen puts a line of
    # space above an unindented paragraph, which would double-space the verse.
    # (#233)
    line = _draws_as_block(local, css)
    if line:
        parts.append(("", frozenset({LINE_START})))
    # Open a cell with a boundary as well as closing one, so a cell cannot fuse
    # onto whatever preceded it. Closing alone leaves the case where a cell
    # follows ordinary text rather than another cell — a nested table inside a
    # cell that already holds text ran `x<table>...<td>i` together as `xi`.
    # (#128)
    if local in _CELL_TAGS and not is_root:
        parts.append((" ", frozenset()))
    # Mark where this element starts before emitting its text, so an id can be
    # resolved to a character offset rather than just "somewhere in this
    # paragraph". Zero-length, so it changes no text and no span. (#79)
    for aid in _own_anchor_ids(elem):
        parts.append(make_anchor_mark(aid))
    if local == "ruby":
        parts.extend(
            _ruby_parts(
                elem,
                cur,
                lambda child: _walk_inline(
                    child,
                    cur,
                    style_resolver,
                    is_root=False,
                    base_href=base_href,
                    split_tables=split_tables,
                    parent_emphasis=emphasis,
                ),
            )
        )
        if line:
            parts.append(("", frozenset({LINE_START})))
        return parts
    if local == "math":
        parts.extend(_math_parts(elem, cur))
        if line:
            parts.append(("", frozenset({LINE_START})))
        return parts
    quote = (
        _q_marks(
            elem, (style_resolver(elem) or {}).get("q-css") if style_resolver else None
        )
        if local == "q"
        else None
    )
    if quote:
        parts.append((quote[0], cur))
    text = elem.text
    if text and local == "pre":
        # HTML ignores a newline straight after <pre>; the XML parser keeps it.
        text = text[2:] if text.startswith("\r\n") else text.removeprefix("\n")
    if text:
        parts.append((text, cur))
    for child in elem:
        clocal = _local_tag(child.tag)
        if split_tables and clocal == "table":
            # The caller writes it as a table of its own (#263).
            parts.append((_TABLE_SPLIT, child))
        elif clocal in ("img", "svg") and _is_hidden(child, style_resolver):
            # A hidden picture, as hidden text above: only its ids (#428).
            parts.extend(make_anchor_mark(aid) for aid in _subtree_anchor_ids(child))
        elif clocal == "img":
            for aid in _own_anchor_ids(child):
                parts.append(make_anchor_mark(aid))
            href = _resolve_img_src(base_href, child.get("src", "") or "")
            alt = child.get("alt", "") or ""
            size = _img_size_hint(child, style_resolver)
            side, size = _img_float(child, elem, size, style_resolver)
            # Only a link carries over: tapping the picture follows it (#355).
            link = frozenset(f for f in cur if _is_link_flag(f))
            parts.append((_make_img_token(href, alt, size, side), link))
        elif clocal == "svg":
            for aid in _own_anchor_ids(child):
                parts.append(make_anchor_mark(aid))
            for href, alt in _svg_image_refs(child):
                token = _make_img_token(href, alt, SVG_IMAGE_SIZE)
                parts.append((token, frozenset()))
            for text, node in _svg_text_paragraphs(child):
                parts.append((" ", frozenset()))
                for aid in _subtree_anchor_ids(node):
                    parts.append(make_anchor_mark(aid))
                parts.append((f"{text} ", frozenset()))
        elif clocal in _CELL_BLOCK_TAGS or clocal == "option":
            # A <select>'s options are separate words too (#477).
            # A block walked inline (a cell on the rows path, a native cell
            # mixing text and a block): its edges are a word boundary even
            # when the HTML puts no whitespace there.
            # <td>cellone<p>celltwo</p></td> read "cellonecelltwo" before.
            # Text inside one block is unchanged. A <dl>'s terms and
            # definitions are blocks too (#229). (#279)
            parts.append((" ", frozenset()))
            parts.extend(
                _walk_inline(
                    child,
                    cur,
                    style_resolver,
                    is_root=False,
                    base_href=base_href,
                    split_tables=split_tables,
                    parent_emphasis=emphasis,
                )
            )
            parts.append((" ", frozenset()))
        else:
            parts.extend(
                _walk_inline(
                    child,
                    cur,
                    style_resolver,
                    is_root=False,
                    base_href=base_href,
                    split_tables=split_tables,
                    parent_emphasis=emphasis,
                )
            )
        if child.tail:
            # The tail sits inside `elem`, so it carries `cur` — this element's
            # accumulated flags — not the incoming `flags`. Using `flags` here
            # dropped whatever `elem` itself contributed: text after a nested
            # tag inside <em> lost its italic, and text between styled spans
            # inside an <a> lost the link, leaving entries half-underlined and
            # half-dead. (#59)
            parts.append((child.tail, cur))
    if quote:
        parts.append((quote[1], cur))

    # Close a cell with a boundary so the next one cannot fuse onto it. Emitted
    # unconditionally rather than only when the source lacks whitespace —
    # normalization collapses runs of whitespace, so a redundant space costs
    # nothing and a missing one corrupts.
    #
    # Placed here, on the cell itself, rather than in the loop above where the
    # parent walks its children. Cells do not always arrive through that loop:
    # `extract_blocks_from_html._walk`'s container branch calls this function
    # on a child directly, so markup that omits `<tr>`/`<tbody>` — or puts a
    # `<tr>` straight under `<body>` — reached the parent loop never having
    # gone through a cell-aware step, and fused anyway. Both shapes are
    # malformed and an html5 parser would repair them, but the OEB shim parses
    # with plain `etree.fromstring`, which does not. (#128)
    if local in _CELL_TAGS and not is_root:
        parts.append((" ", frozenset()))
    if line:
        parts.append(("", frozenset({LINE_START})))
    return parts


def _ruby_parts(ruby, flags, walk):
    """A <ruby>'s base text, each run flagged with the <rt> after it (#236).

    The base is everything before an <rt> since the last one: text, an <rb>,
    a <span>. A reading is its <rt>'s text; Kindle Previewer 4 styles a
    reading's own markup, which kfxgen drops. An empty <rt> leaves its base
    plain. <rp> is the fallback for readers without ruby, and is dropped, as
    Previewer drops it. Previewer reads an <rtc> as ordinary text, and so
    does this: `walk` takes it like any other child."""
    out, base = [], []
    if ruby.text:
        base.append((ruby.text, flags))
    for child in ruby:
        local = _local_tag(child.tag) if isinstance(child.tag, str) else None
        if local == "rt":
            reading = " ".join("".join(child.itertext()).split())
            if reading:
                flag = make_ruby_flag(reading)
                base = [
                    (seg, fl | {flag}) if isinstance(seg, str) and seg else (seg, fl)
                    for seg, fl in base
                ]
            out.extend(base)
            out.extend(make_anchor_mark(aid) for aid in _subtree_anchor_ids(child))
            base = []
        elif local != "rp":
            base.extend(walk(child))
        if child.tail:
            base.append((child.tail, flags))
    return out + base


# MathML operators written with a space either side when they join two
# operands (#235); a leading one is a sign ("−b") and gets none.
_MATH_INFIX = frozenset("=≠<>≤≥≈≡∼≃≅∝≪≫→←↔⇒⇐⇔↦∈∉∋⊂⊃⊆⊇+−±∓×÷·⋅∗∘∪∩∧∨⊕⊗")
_MATH_OPEN = frozenset("([{⟨⌊⌈|‖")
# A one-letter accent over a single letter, as the combining mark that puts it
# there: x̄ rather than x then ¯.
_MATH_ACCENTS = {
    "¯": "\u0304",
    "‾": "\u0305",
    "^": "\u0302",
    "ˆ": "\u0302",
    "~": "\u0303",
    "˜": "\u0303",
    "˙": "\u0307",
    "¨": "\u0308",
    "→": "\u20d7",
    "\u20d7": "\u20d7",
}
_MATHML_MATH = "{http://www.w3.org/1998/Math/MathML}math"
_MATH_TOKENS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
_MATH_SCRIPTS = frozenset({"msup", "msub", "msubsup", "mover", "munder", "munderover"})
_MATH_SKIP = frozenset({"annotation", "annotation-xml", "mphantom"})
# Function application, invisible times, separator and plus: no glyph, and a
# Kindle font may draw one as a box.
_MATH_INVISIBLE = frozenset("\u2061\u2062\u2063\u2064")


def _math_parts(math, flags):
    """A MathML equation, drawn as Kindle Previewer 4 draws it: one
    placeholder character carrying its drawing, its alt text and its
    source (#235).

    One that can't be drawn is written as text a reader can follow:
    exponents and subscripts as raised and lowered runs, operators spaced,
    a fraction as a/b and a root as √x, with parentheses around a part
    longer than a single token. A single-letter identifier is italic, as
    maths sets it. Whitespace between MathML elements is source layout, not
    text; an <annotation> (the TeX source, mostly) is not text either. That
    text is also the alt text of a drawn equation without `alttext`."""
    parts = [make_anchor_mark(aid) for aid in _subtree_anchor_ids(math)]
    if _math_stray_text(math):
        text = _math_reading(math)
        return parts + ([(text, flags)] if text else [])
    body = _math_row(_math_kids(math), flags, False)
    text = "".join(seg for seg, _fl in body if isinstance(seg, str))
    alt = " ".join((math.get("alttext") or "").split())
    drawing = draw_math(math)
    if drawing is not None:
        from lxml import etree

        source = etree.tostring(math, encoding="unicode", with_tail=False)
        flag = make_math_flag(drawing, alt or " ".join(text.split()), source)
        return parts + [("\ufffc", flags | {flag})]
    if not text.strip():
        body = [(alt, flags)] if alt else []
    return parts + body


def _math_reading(math):
    """The text kfxgen writes for an equation it doesn't draw, without its
    runs: "a/b = 1"."""
    if _math_stray_text(math):
        # Text outside the token elements, invalid MathML: written as it
        # reads, as before, still without its annotations (#401 QA).
        text = "".join(c for c in "".join(_math_text(math)) if c not in _MATH_INVISIBLE)
        return " ".join(text.split())
    body = _math_row(_math_kids(math), frozenset(), False)
    return "".join(seg for seg, _fl in body if isinstance(seg, str))


#: How a TOC label writes a ruby reading, as `_ruby_heading_title` takes
#: it: as the source reads, <rp> and all, run into its base, left out, or
#: in parentheses (#236, #410).
_LABEL_READINGS = (None, "{}", "", "({})", "（{}）")


def _label_texts(elem):
    """A block holding an equation as a producer writes it into a TOC label:
    its source text run together ("The ab=1 rule", #402), each equation as
    its `alttext`, or each as the text kfxgen writes for it (#404); each of
    those with any ruby reading as `_LABEL_READINGS` writes it (#410). None
    for a block without an equation."""
    if next(elem.iter(_MATHML_MATH, "math"), None) is None:
        return None

    def written(e, maths, reading):
        out = [e.text or ""]
        for kid in e:
            local = _local_tag(kid.tag) if isinstance(kid.tag, str) else None
            if kid.tag in (_MATHML_MATH, "math"):
                out.append(maths(kid))
            elif reading is None and local is not None:
                out.append(written(kid, maths, reading))
            elif local == "rt":
                text = "".join(kid.itertext()).strip()
                out.append(reading.format(text) if text else "")
            elif local is not None and local != "rp":
                out.append(written(kid, maths, reading))
            out.append(kid.tail or "")
        return "".join(out)

    maths = (
        lambda m: "".join(m.itertext()),
        lambda m: m.get("alttext") or _math_reading(m),
        _math_reading,
    )
    return tuple(written(elem, m, r) for m in maths for r in _LABEL_READINGS)


def _math_stray_text(elem):
    """True when text sits outside a token element, and outside a skipped
    one: a Content MathML annotation holds text of its own."""
    local = _local_tag(elem.tag)
    if local in _MATH_TOKENS or local in _MATH_SKIP:
        return False
    if (elem.text or "").strip():
        return True
    return any(
        (k.tail or "").strip() or (isinstance(k.tag, str) and _math_stray_text(k))
        for k in elem
    )


def _math_text(elem):
    if not isinstance(elem.tag, str) or _local_tag(elem.tag) in _MATH_SKIP:
        return []
    out = [elem.text or ""]
    for k in elem:
        out += _math_text(k)
        out.append(k.tail or "")
    return out


def _math_kids(elem):
    return [
        k
        for k in elem
        if isinstance(k.tag, str) and _local_tag(k.tag) not in _MATH_SKIP
    ]


def _math_token(elem):
    return " ".join("".join(elem.itertext()).split())


def _math_is_atom(elem):
    """True when `elem` reads as one unit without parentheses: a token, a
    script on one, a root, or a row holding just one of those."""
    local = _local_tag(elem.tag)
    if local in _MATH_TOKENS:
        return True
    kids = _math_kids(elem)
    if local in _MATH_SCRIPTS:
        return bool(kids) and _math_is_atom(kids[0])
    if local in ("msqrt", "mroot", "mfenced"):
        return True
    return (
        local in ("mrow", "mstyle", "mpadded", "semantics")
        and len(kids) == 1
        and (_math_is_atom(kids[0]))
    )


def _math_group(elems, flags, script):
    """`elems` as one operand: in parentheses unless it is a single atom."""
    inner = _math_row(elems, flags, script)
    if len(elems) == 1 and _math_is_atom(elems[0]):
        return inner
    return [("(", flags), *inner, (")", flags)]


def _math_row(elems, flags, script):
    out = []
    prev = None  # what came before: None (start), "op" or "operand"
    for i, e in enumerate(elems):
        local = _local_tag(e.tag)
        if local == "mo":
            op = _math_token(e)
            if op in _MATH_INVISIBLE:
                name = elems[i - 1] if i else None
                if name is not None and _local_tag(name.tag) in _MATH_SCRIPTS:
                    name = (_math_kids(name) or [name])[0]  # sin²x: the name
                after = elems[i + 1] if i + 1 < len(elems) else None
                bracketed = after is not None and (
                    _local_tag(after.tag) == "mfenced"
                    or _math_token(after)[:1] in _MATH_OPEN
                )
                if (
                    op == "\u2061"
                    and name is not None
                    and len(_math_token(name)) > 1
                    and not bracketed
                ):
                    # "sin x", where the name runs into an argument with no
                    # bracket; "f(x)" and "fx" need no space.
                    out.append((" ", flags))
                continue
            op = "−" if op == "-" else op
            form = e.get("form")
            if op == ",":
                out.append((", " if not script else ",", flags))
            elif (
                op in _MATH_INFIX
                and not script
                and form != "prefix"
                and (prev == "operand" or form == "infix")
            ):
                out.append((f" {op} ", flags))
            else:
                out.append((op, flags))
            prev = (
                "operand"
                if op and op not in _MATH_INFIX and op not in _MATH_OPEN
                else "op"
            )
            continue
        out.extend(_math_elem(e, flags, script))
        prev = "operand"
    return out


def _math_elem(e, flags, script):
    local = _local_tag(e.tag)
    kids = _math_kids(e)
    if local in _MATH_TOKENS:
        text = _math_token(e)
        variant = e.get("mathvariant") or (
            "italic" if local == "mi" and len(text) == 1 else ""
        )
        if local == "ms":
            text = e.get("lquote", '"') + text + e.get("rquote", '"')
        fl = set(flags)
        if "italic" in variant:
            fl.add(FLAG_ITALIC)
        if "bold" in variant:
            fl.add(FLAG_BOLD)
        return [(text, frozenset(fl))] if text else []
    if local == "mspace":
        return [(" ", flags)]
    up, down = frozenset(flags | {FLAG_SUPER}), frozenset(flags | {FLAG_SUB})
    if local in _MATH_SCRIPTS and kids:
        base = _math_elem(kids[0], flags, script)
        if local == "mover" and len(kids) == 2:
            mark = _MATH_ACCENTS.get(_math_token(kids[1]))
            text = "".join(seg for seg, _fl in base)
            if mark and len(text) == 1:
                return [(seg + mark, fl) for seg, fl in base]
        if local in ("msub", "munder"):
            lows, highs = kids[1:2], []
        elif local in ("msup", "mover"):
            lows, highs = [], kids[1:2]
        else:
            lows, highs = kids[1:2], kids[2:3]
        out = list(base)
        for k in lows:
            out.extend(_math_elem(k, down, True))
        for k in highs:
            out.extend(_math_elem(k, up, True))
        return out
    if local == "mmultiscripts" and kids:
        # base (sub sup)* <mprescripts/> (sub sup)*: the prescripts are
        # written first, as an isotope reads, ¹⁴₆C (#402).
        tags = [_local_tag(k.tag) for k in kids]
        cut = tags.index("mprescripts") if "mprescripts" in tags else len(kids)
        out = []
        for scripts in (kids[cut + 1 :], None, kids[1:cut]):
            if scripts is None:
                out.extend(_math_elem(kids[0], flags, script))
                continue
            for i, k in enumerate(scripts):
                out.extend(_math_elem(k, up if i % 2 else down, True))
        return out
    if local == "mfrac" and len(kids) == 2:
        return [
            *_math_group(kids[:1], flags, script),
            ("/", flags),
            *_math_group(kids[1:], flags, script),
        ]
    if local == "msqrt":
        return [("√", flags), *_math_group(kids, flags, script)]
    if local == "mroot" and len(kids) == 2:
        return [
            *_math_elem(kids[1], up, True),
            ("√", flags),
            *_math_group(kids[:1], flags, script),
        ]
    if local == "mfenced":
        seps = "".join((e.get("separators") or ",").split()) or ","
        out = [(e.get("open", "("), flags)]
        for i, k in enumerate(kids):
            if i:
                sep = seps[min(i - 1, len(seps) - 1)]
                out.append((f"{sep} " if sep == "," else sep, flags))
            out.extend(_math_elem(k, flags, script))
        out.append((e.get("close", ")"), flags))
        return out
    if local == "mtable":
        out = []
        for r, row in enumerate(kids):
            if r:
                out.append(("; ", flags))
            cells = (
                _math_kids(row)
                if _local_tag(row.tag) in ("mtr", "mlabeledtr")
                else [row]
            )
            for c, cell in enumerate(cells):
                if c:
                    out.append((", ", flags))
                out.extend(_math_row(_math_kids(cell), flags, script))
        return out
    if local == "maction":
        return _math_elem(kids[0], flags, script) if kids else []
    return _math_row(kids, flags, script)


def _own_anchor_ids(elem):
    """Anchor ids declared directly on `elem`: its id, plus an <a name="...">."""
    ids = []
    eid = elem.get("id")
    if eid:
        ids.append(eid)
    if _local_tag(elem.tag) == "a":
        name = elem.get("name")
        if name:
            ids.append(name)
    return ids


def _subtree_anchor_ids(elem):
    """All anchor ids on `elem` and its descendants, in document order."""
    ids = []
    for e in elem.iter():
        ids.extend(_own_anchor_ids(e))
    return ids


_ROW_GROUP_TAGS = {"table", "thead", "tbody", "tfoot"}
#: What a table or row group may hold directly and still be written natively.
_TABLE_PART_TAGS = {"tr", "thead", "tbody", "tfoot", "caption", "col", "colgroup"}


def _is_empty_anchor(elem):
    """An <a> that only names a place: an id or name, and no content."""
    return (
        isinstance(elem.tag, str)
        and _local_tag(elem.tag) == "a"
        and bool(_own_anchor_ids(elem))
        and not (elem.text or "").strip()
        and len(elem) == 0
    )


def _anchors_follow_rows(elem):
    """True when a table's link targets sit after the row each one names.

    Calibre's MOBI→EPUB output lays out notes this way — `<tr>note 1</tr>
    <a id="…"/><tr>note 2</tr>…`, with no anchor before the first row and one
    after the last — and an anchor with no text of its own otherwise carries
    forward to the next block, which is the next note. Decided per row group
    from both ends, because the opposite layout (an anchor before each row)
    is carried forward correctly already. (#221, #223)

    An anchor *between* two rows is also required. Anchors that sit only
    after the last row usually name what comes next — the next section's
    link target just inside `</table>` — and moving one onto the last row put
    that row in the next chapter. A table holding a single note therefore
    keeps carrying forward, as every table did before. (#221 review)
    """
    if _local_tag(elem.tag) not in _ROW_GROUP_TAGS:
        return False
    kinds = "".join(
        "A" if _is_empty_anchor(c) else "R"
        for c in elem
        if _is_empty_anchor(c) or (isinstance(c.tag, str) and _local_tag(c.tag) == "tr")
    )
    # Starts with a row, ends with an anchor, and — once the trailing anchors
    # are set aside — still has an anchor, which then sits between two rows.
    return kinds[:1] == "R" and kinds.endswith("A") and "A" in kinds.rstrip("A")


#: A note marker in a notes table's first column: a number, a roman numeral
#: or a symbol, optionally bracketed and followed by a full stop or colon (#268).
_NOTE_MARKER_RE = re.compile(
    r"^[\[(]?(\d{1,4}"
    # A well-formed roman numeral only, so words such as "mild" and "civil"
    # are not taken for one (#273 review).
    r"|(?=[mdclxvi])m{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
    r"|[*\u2020\u2021\u00a7\u00b6#]+)[\])]?[.:]?$",
    re.IGNORECASE,
)
#: Share of a notes table's rows that the book must link to (#268). One
#: library notes table has a note nothing links to, so not all of them.
_NOTES_TABLE_LINKED_SHARE = 0.8


def _row_anchor_ids(row, follow):
    """Ids that name a table row: on it or inside it, plus the empty anchors
    beside it on the side its row group puts them (after the row when
    `follow`, as in calibre's notes layout, otherwise before), so each anchor
    names one row."""
    ids = set(_subtree_anchor_ids(row))
    sib = row.getnext() if follow else row.getprevious()
    while sib is not None and _is_empty_anchor(sib):
        ids.update(_own_anchor_ids(sib))
        sib = sib.getnext() if follow else sib.getprevious()
    return ids


def _is_notes_table(table, base_href, link_targets):
    """True when `table` is a notes section laid out as a table, written as one
    paragraph per note rather than as a Kindle table (#268).

    The shape is two cells a row, at least three rows, and a note marker
    alone in every first cell. What tells notes from a contents table or a
    numbered list of the same shape is that the book links into them: at
    least `_NOTES_TABLE_LINKED_SHARE` of the rows are link targets from
    somewhere in the book. A contents table's links point out (pg6133), and
    nothing links into a data table. `link_targets` is the set of
    "<file>#<id>" keys every in-book link resolves to."""
    if not link_targets or not base_href:
        return False
    doc = _key_doc(base_href, "")
    rows = [
        tr
        for tr in table.iter()
        if _local_tag(tr.tag) == "tr"
        and next((a for a in tr.iterancestors() if _local_tag(a.tag) == "table"), None)
        is table
        and any(_local_tag(c.tag) in _CELL_TAGS for c in tr)
    ]
    if len(rows) < 3:
        return False
    for row in rows:
        cells = [c for c in row if _local_tag(c.tag) in _CELL_TAGS]
        if len(cells) != 2:
            return False
        marker = " ".join("".join(cells[0].itertext()).split())
        if not _NOTE_MARKER_RE.match(marker):
            return False
    # The anchor layout is read once per row group: reading it for every row
    # made the check grow with rows times rows (#273 review).
    follows = {}
    linked = 0
    for row in rows:
        group = row.getparent()
        if group not in follows:
            follows[group] = _anchors_follow_rows(group)
        if any(
            f"{doc}#{aid}" in link_targets
            for aid in _row_anchor_ids(row, follows[group])
        ):
            linked += 1
    return linked >= _NOTES_TABLE_LINKED_SHARE * len(rows)


def _pictures_in_table_cells(oeb_book):
    """The manifest href of every `<img>` inside a `<td>` or `<th>`, spelled
    as `_resolve_img_src` spells a picture's token (#441 device check)."""
    found = set()
    for item in oeb_book.spine:
        try:
            data = item.data
        except Exception:
            continue
        if data is None or not hasattr(data, "iter"):
            continue
        base = getattr(item, "href", "") or ""
        for cell in data.iter():
            if not (isinstance(cell.tag, str) and _local_tag(cell.tag) in ("td", "th")):
                continue
            for img in cell.iter():
                if isinstance(img.tag, str) and _local_tag(img.tag) == "img":
                    found.add(_resolve_img_src(base, img.get("src", "") or ""))
    return found


def _book_link_targets(oeb_book):
    """Every "<file>#<id>" key an in-book link in the spine resolves to (#268)."""
    targets = set()
    for item in oeb_book.spine:
        try:
            data = item.data
        except Exception:
            continue
        if data is None or not hasattr(data, "iter"):
            continue
        base = getattr(item, "href", "") or ""
        for a in data.iter():
            if isinstance(a.tag, str) and _local_tag(a.tag) == "a" and a.get("href"):
                target = _resolve_link_target(a.get("href"), base)
                if target:
                    targets.add(target)
                    # An id such as "n:1" is linked as "#n%3A1" (#273 review).
                    targets.add(unquote(target))
    return targets


_LINE_TAGS = frozenset(
    {"p", "div", "li", "td", "th", "dd", "dt", "blockquote", "body", "caption"}
)


def _in_running_text(marker):
    """True when `marker` sits inside text, as a note marker does, rather
    than being the whole line, as a contents entry is (#225)."""
    line = marker.getparent()
    while line is not None and _local_tag(line.tag) not in _LINE_TAGS:
        line = line.getparent()
    if line is None:
        return False
    whole = "".join(line.itertext())
    own = "".join(marker.itertext())
    return bool(whole.replace(own, "", 1).strip())


def _note_pair_ids(oeb_book):
    """{file: ids} of notes and their markers, found by the links between
    them (#225): a marker carrying id R links to T, and a link in T's file
    links back to R. Then T is a note and R its marker. A target a link
    marked `epub:type="noteref"` (or `role="doc-noteref"`) names is a note
    too, back-link or not (#482).

    This is for a TOC calibre built from the book's links, which lists every
    marker and back-link as an entry: as chapters they split the notes and
    print their numbers as headings. A notes table and plain `<p>` or `<div>`
    notes carry no note markup, so `_note_target_ids` can't see them.

    A marker sits in running text; a contents entry is a whole line, and a
    contents page whose chapters link back to it would otherwise pair the
    same way when the chapter titles aren't headings.

    A contents page and its chapter headings link to each other the same way,
    so nothing in, on or around a heading counts. Detection never reads a
    label: chapters titled "1", "2", "3" are common."""
    links = []  # (file of the link, target key, keys of the ids it carries)
    noterefs = set()  # keys a link marked as a noteref names (#482)
    back = {}  # file -> keys its links point at
    heading = set()  # keys of ids in, on or holding a heading
    for item in oeb_book.spine:
        try:
            data = item.data
        except Exception:
            continue
        if data is None or not hasattr(data, "iter"):
            continue
        base = getattr(item, "href", "") or ""
        doc = _key_doc(base, "") if base else ""
        for elem in data.iter():
            if not isinstance(elem.tag, str):
                continue
            in_heading = any(
                isinstance(x.tag, str) and _local_tag(x.tag) in _HEADING_TAGS
                for x in elem.iter()
            ) or any(_local_tag(x.tag) in _HEADING_TAGS for x in elem.iterancestors())
            if in_heading:
                heading.update(f"{doc}#{aid}" for aid in _own_anchor_ids(elem))
            if _local_tag(elem.tag) != "a" or not elem.get("href"):
                continue
            target = _resolve_link_target(elem.get("href"), base)
            if not target or "#" not in target:
                continue
            if "noteref" in _epub_types(elem) or elem.get("role") == "doc-noteref":
                noterefs.add(target)
            if not in_heading:
                back.setdefault(doc, set()).add(target)
            carriers = list(_own_anchor_ids(elem))
            carrier = elem
            parent = elem.getparent()
            if (
                parent is not None
                and _local_tag(parent.tag) in ("sup", "span", "small")
                and len([c for c in parent if isinstance(c.tag, str)]) == 1
                and not (parent.text or "").strip()
                and not (elem.tail or "").strip()
            ):
                carriers += _own_anchor_ids(parent)
                carrier = parent
            if carriers and _in_running_text(carrier):
                links.append((doc, target, [f"{doc}#{aid}" for aid in carriers]))

    found = {}
    # What a noteref names is a note, though nothing else marks it (#482).
    for target in noterefs - heading:
        file, _, aid = target.partition("#")
        found.setdefault(file, set()).add(aid)
    for doc, target, carriers in links:
        if target in heading:
            continue
        target_doc = target.split("#", 1)[0]
        for marker in carriers:
            if marker in back.get(target_doc, ()) and marker not in heading:
                for key in (target, marker):
                    file, _, aid = key.partition("#")
                    found.setdefault(file, set()).add(aid)
    return found


def _table_is_native(table, style_resolver=None):
    """True when `table` can be written as a real KFX table (#219).

    Anything else keeps rows as paragraphs: a nested table, an object other
    than an <img> (#262), a cell or a paragraph in a cell over
    `_MAX_NATIVE_CELL_CHARS` in a table of two or more columns (#348), a cell
    outside a row, or no rows or cells at all. A cell holding
    several blocks is written as paragraphs (#261).

    Also anything the row path handles and the native walk would not: a
    hidden or contents-listing row, row group or cell (the row path drops it;
    native would show it), a row outside table/thead/tbody/tfoot, or text
    loose in the table, a row group or a row, before or between its children,
    or any other element directly in the table or a row group, such as a
    `<p>`, or in a row other than a cell or empty anchor (the row path keeps
    it; native would lose it). Likewise a hidden caption, which native would
    show, or a second caption, which native would drop. And a table wider
    than `_MAX_NATIVE_COLUMNS`, whose text would be too small to read.

    An <epub:switch>'s cases are not drawn, so they count for nothing (#315).
    Hidden means `_is_hidden`, CSS included, with `style_resolver` (#232).
    """
    table = _shown_tree(table)
    rows = 0
    cells = 0
    long_cell = False
    captions = 0
    for e in table.iter():
        tag = _local_tag(e.tag)
        if tag is None:
            continue
        if tag in _ROW_GROUP_TAGS or tag == "tr" or tag in _CELL_TAGS:
            if e is not table and (_is_hidden(e, style_resolver) or _is_nav_listing(e)):
                return False
        if tag in _ROW_GROUP_TAGS or tag == "tr":
            if (e.text or "").strip() or any((c.tail or "").strip() for c in e):
                return False
        if tag in _ROW_GROUP_TAGS and any(
            isinstance(c.tag, str)
            and _local_tag(c.tag) not in _TABLE_PART_TAGS
            and not _is_empty_anchor(c)
            for c in e
        ):
            return False
        if tag == "caption":
            # A hidden caption would be shown; only the first is kept.
            if _is_hidden(e, style_resolver):
                return False
            captions += 1
            if captions > 1:
                return False
        if tag == "tr":
            if _local_tag(e.getparent().tag) not in _ROW_GROUP_TAGS:
                return False
            # Only cells and empty anchors are read from a row.
            if any(
                isinstance(c.tag, str)
                and _local_tag(c.tag) not in _CELL_TAGS
                and not _is_empty_anchor(c)
                for c in e
            ):
                return False
            rows += 1
        elif tag == "table" and e is not table:
            return False
        elif tag in _NON_TEXT_TAGS and tag != "img":
            return False
        elif tag in _CELL_TAGS:
            if _local_tag(e.getparent().tag) != "tr":
                return False
            cells += 1
            long_cell = long_cell or _longest_cell_run(e) > _MAX_NATIVE_CELL_CHARS
    width = _table_width(table)
    if long_cell and width > 1:
        return False
    return rows > 0 and cells > 0 and width <= _MAX_NATIVE_COLUMNS


def _text_length(elem):
    """Characters `elem` writes: its text, plus one per <br>, which the
    converter turns into a newline."""
    return len("".join(elem.itertext())) + sum(
        1 for d in elem.iter() if _local_tag(d.tag) == "br"
    )


def _cell_paragraph_count(cell):
    """Block elements in a cell. Two or more make it a container of
    paragraphs (#261); fewer keep it one text entry."""
    return sum(
        1
        for d in cell.iter()
        if d is not cell and _local_tag(d.tag) in _CELL_BLOCK_TAGS
    )


def _longest_cell_run(cell):
    """The longest text entry a cell would be written as.

    A cell of plain text, or one block, is one entry. A cell of several blocks
    is one entry per block with no block inside it, plus its loose text; that
    is counted as one run, which can only overstate. In a table of two or
    more columns, a cell over `_MAX_NATIVE_CELL_CHARS` keeps the table out of
    the native format (#261, #348)."""
    if _cell_paragraph_count(cell) < 2:
        return _text_length(cell)
    leaves = [
        d
        for d in cell.iter()
        if d is not cell
        and _local_tag(d.tag) in _CELL_BLOCK_TAGS
        and not any(
            _local_tag(x.tag) in _CELL_BLOCK_TAGS for x in d.iter() if x is not d
        )
    ]
    leaf_lengths = [_text_length(d) for d in leaves]
    loose = _text_length(cell) - sum(leaf_lengths)
    return max(leaf_lengths + [loose])


def _cell_columns(table):
    """Each cell with the grid column it starts in, row by row: colspans, and
    the cells rowspan carries down from earlier rows of the same row group. A
    row with no cells is never written, so it ends no rowspan."""
    groups = [table] + [c for c in table if _local_tag(c.tag) in _ROW_GROUPS]
    for group in groups:
        carry = []
        for tr in group:
            if _local_tag(tr.tag) != "tr":
                continue
            row_cells = [c for c in tr if _local_tag(c.tag) in _CELL_TAGS]
            if not row_cells:
                continue
            col = 0
            for cell in row_cells:
                while col < len(carry) and carry[col]:
                    col += 1
                span = _span_attr(cell, "colspan")
                yield cell, col, span
                end = col + span
                carry.extend([0] * (end - len(carry)))
                carry[col:end] = [_span_attr(cell, "rowspan")] * (end - col)
                col = end
            carry = [max(n - 1, 0) for n in carry]


def _table_width(table):
    """Columns the widest row covers: its colspans plus the cells rowspan
    carries down from earlier rows of the same row group, as `_cell_columns`
    walks the grid. Stops counting once past `_MAX_NATIVE_COLUMNS`."""
    width = 0
    for _cell, col, span in _cell_columns(table):
        end = col + span
        if end > _MAX_NATIVE_COLUMNS:
            return end
        width = max(width, end)
    return width


def _width_pct(elem, style_resolver):
    """An element's width as a percentage, or None: its CSS width when it
    declares one, else its `width` attribute. Absolute widths count as none
    (#264): Kindle Previewer turns them into percentages by laying the page
    out."""
    css = style_resolver(elem) if style_resolver is not None else None
    declared = (css or {}).get("width")
    value = declared if declared is not None else elem.get("width")
    m = re.fullmatch(r"\s*([\d.]+)\s*%\s*", str(value or ""))
    return float(m.group(1)) if m else None


def _column_widths(table, style_resolver):
    """The table's column widths as Kindle Previewer writes them into $152
    (#264): ("col", [pct or None per column]) from <col>/<colgroup>, with
    `span` repeating a column; else ("cell", [...]) from the cells, the last
    row that gives a single-column cell a width winning; else None."""
    cols = []
    for child in table:
        tag = _local_tag(child.tag)
        members = [child] if tag == "col" else []
        if tag == "colgroup":
            members = [c for c in child if _local_tag(c.tag) == "col"]
        for col in members:
            cols.extend([_width_pct(col, style_resolver)] * _span_attr(col, "span"))
    if any(w is not None for w in cols):
        return ("col", cols)
    widths = []
    for cell, col, span in _cell_columns(table):
        widths.extend([None] * (col + span - len(widths)))
        if span == 1:
            pct = _width_pct(cell, style_resolver)
            if pct is not None:
                widths[col] = pct
    if any(w is not None for w in widths):
        return ("cell", widths)
    return None


_ROW_GROUPS = {"thead": "head", "tbody": "body", "tfoot": "foot"}
_ROW_GROUP_ORDER = {"head": 0, "body": 1, "foot": 2}


def _span_attr(cell, name):
    """colspan/rowspan as an int in 1..1000; anything malformed counts as 1."""
    try:
        n = int((cell.get(name) or "1").strip())
    except ValueError:
        return 1
    return min(max(n, 1), 1000)


def _row_align(tr, row_align, style_resolver):
    """A row written as one paragraph takes its cells' alignment when every
    cell with something in it agrees; otherwise the row's own (#224).

    A paragraph has one alignment, and the row's is only what its cells would
    inherit: a table centred around left-aligned cells, or cells each set
    `align="center"`, show otherwise. An empty spacer cell shows nothing, so
    it doesn't count."""
    aligns = set()
    for cell in tr:
        if _local_tag(cell.tag) not in _CELL_TAGS:
            continue
        if not "".join(cell.itertext()).strip() and not any(
            _local_tag(d.tag) in _NON_TEXT_TAGS for d in cell.iter()
        ):
            continue
        css = style_resolver(cell)
        aligns.add(compute_block_style(css)["align"] if css is not None else None)
    if len(aligns) == 1 and None not in aligns:
        return aligns.pop()
    return row_align


_BORDER_SIDES = ("top", "right", "bottom", "left")
#: Kindle Previewer writes a CSS border width at 0.45pt per pixel; calibre
#: computes it at 0.72pt per pixel. A keyword width is thin 1px, medium 3px,
#: thick 5px. (#264)
_PREVIEWER_PT_PER_CALIBRE_PT = 0.625
_BORDER_KEYWORD_PX = {"thin": 1, "medium": 3, "thick": 5}


def _border_colour(value):
    """A CSS colour as the ARGB integer Previewer writes, or None for black and
    for anything it can't read, which the device draws as its default black
    (#264)."""
    rgb = css_rgb(value)
    if not rgb:
        return None
    return 0xFF000000 | rgb


#: True for a colour that draws nothing: read as unreadable it would become
#: the default black (#292 review).
_is_transparent = is_transparent


def _css_border(css):
    """The visible sides of an element's computed CSS border, as
    {side: (style, width in Previewer's points, ARGB colour or None)}, or None
    when no side is drawn (#264)."""
    sides = (css or {}).get("border-sides")
    if not sides:
        return None
    out = {}
    for side, (kind, width, colour) in zip(_BORDER_SIDES, sides):
        kind = str(kind or "none").lower()
        if kind in ("none", "hidden") or _is_transparent(colour):
            continue
        try:
            pt = float(width)
        except (TypeError, ValueError):
            px = _BORDER_KEYWORD_PX.get(str(width or "").lower())
            if px is None:
                continue
            pt = px * 0.72
        if pt <= 0:
            continue
        out[side] = (
            kind,
            round(pt * _PREVIEWER_PT_PER_CALIBRE_PT, 4),
            _border_colour(colour),
        )
    return out or None


#: A cell's default padding, calibre's UA 1px, in Previewer's ems
#: (1px = 0.45pt, 12pt = 1em). kfxgen writes it for every cell already.
_DEFAULT_PADDING_EM = 0.45 / 12
_PADDING_EM_PER_UNIT = {"px": 0.45 / 12, "pt": 1 / 12, "em": 1.0, "rem": 1.0}


def _padding_em(value):
    """A declared padding as Kindle Previewer measures it: ems (1px = 0.45pt,
    12pt = 1em), ("%", n) for a percentage, or None when unreadable."""
    m = re.fullmatch(r"\s*([\d.]+)\s*(px|pt|em|rem|%)?\s*", str(value or ""))
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2)
    if unit == "%":
        return ("%", n)
    if unit is None:
        return 0.0 if n == 0 else None
    return n * _PADDING_EM_PER_UNIT[unit]


def _cell_padding(css):
    """A cell's padding per side (#264), or None when it is the default on
    every side, which the generator already writes. An unreadable side
    keeps the default."""
    sides = (css or {}).get("padding-sides")
    if not sides:
        return None
    out = {}
    for side, value in zip(_BORDER_SIDES, sides):
        em = _padding_em(value)
        out[side] = _DEFAULT_PADDING_EM if em is None else em
    if all(
        not isinstance(v, tuple) and abs(v - _DEFAULT_PADDING_EM) < 1e-9
        for v in out.values()
    ):
        return None
    return out


#: Containers drawn as a box when they have a border or a fill (#238).
_BOX_TAGS = frozenset({"div", "aside", "section", "blockquote", "article"})
#: Containers drawn as a box at their width when they hold a picture (#382).
_SIZED_BOX_TAGS = _BOX_TAGS | {"figure"}


def _box_width(css):
    """A wrapper's CSS width as a share of the page, in %, or None for none,
    `auto`, the full width or anything unreadable (#382). A px width is
    taken at Previewer's rate: 300px is 35.156 %."""
    width = parse_css_length(str((css or {}).get("width") or ""))
    share = _page_share(width) if width else 0.0
    return share if 0 < share < 100 else None


#: CSS px per unit, for a wrapper's width in a length (#384 review).
_CSS_PX = {"$319": 1.0, "$308": 16.0, "$505": 16.0, "$318": 4 / 3, "$316": 96 / 25.4}


def _box_width_px(css):
    """A wrapper's CSS width in CSS px when it is a length, else None: a
    picture's own px width inside it is then a share of it."""
    width = parse_css_length(str((css or {}).get("width") or ""))
    if not width or width[1] not in _CSS_PX:
        return None
    return float(width[0]) * _CSS_PX[width[1]]


def _background_parts(value):
    """A `background:` shorthand's parts: split at spaces, commas and slashes
    outside parentheses, so a function such as rgb(...), url(...) or a
    gradient holding rgb() stops stays one part (#464, #499 QA)."""
    parts, part, depth = [], "", 0
    for ch in value:
        depth += (ch == "(") - (ch == ")")
        if depth <= 0 and ch in " \t\n,/":
            if part:
                parts.append(part)
            part, depth = "", 0
        else:
            part += ch
    return parts + [part] if part else parts


def _box_background(css):
    """A box's fill as the ARGB integer Previewer writes ($70), or None for
    none, transparent, white (the page) or anything unreadable (#238)."""
    css = css or {}
    value = css.get("background-color")
    if not value or _is_transparent(value):
        # calibre leaves the shorthand unexpanded: its colour is the part
        # that reads as one, as Previewer takes it (#464).
        value = next(
            (
                part
                for part in _background_parts(css.get("background") or "")
                if css_rgb(part) is not None
            ),
            None,
        )
    if not value or _is_transparent(value):
        return None
    # Not _border_colour, which reads black as the default: a black fill is
    # drawn (#383).
    rgb = css_rgb(value)
    return None if rgb in (None, 0xFFFFFF) else 0xFF000000 | rgb


#: Space above a box when its CSS sets less: 1 line height (1.2em), what an
#: unindented paragraph gets above it. kfxgen's paragraphs have space above
#: and none below, so without it a border sat right under the line before
#: the box (device check, #309). The space below is the generator's: it
#: depends on the paragraph that follows (#238).
_BOX_SPACE_EM = 1.2


def _box_space(value, least=0.0):
    """A box's declared margin above or below, in ems (1px = 0.45pt,
    12pt = 1em, as for padding), or `least` when that is larger."""
    em = _padding_em(value)
    if isinstance(em, tuple) or em is None:
        em = 0.0
    return max(em, least)


#: An <hr>'s margins in calibre's own sheet, above and below (#230).
_RULE_SPACE_EM = 0.5
#: One blank line: what a spacer paragraph (`<p>&nbsp;</p>`) adds (#230).
_LINE_SPACE_EM = 1.2


def _margin_em(css, side):
    """An element's declared margin-top or -bottom in ems (1px = 0.45pt,
    12pt = 1em, as for padding); 0 when unset, negative or a percentage."""
    em = _padding_em((css or {}).get(f"margin-{side}"))
    return 0.0 if isinstance(em, tuple) or em is None else em


def _is_spacer(elem):
    """A paragraph that holds only a non-breaking space or a <br>: a blank
    line the book put in on purpose, often for a scene break (#230). An
    empty `<p> </p>` is not one: a browser draws it as nothing."""
    text = "".join(elem.itertext())
    if text.replace("\u00a0", "").strip():
        return False
    for d in elem.iter():
        if isinstance(d.tag, str) and _local_tag(d.tag) in ("img", "svg"):
            return False
    return "\u00a0" in text or any(
        isinstance(d.tag, str) and _local_tag(d.tag) == "br" for d in elem.iter()
    )


def _box_padding(css):
    """A box's padding per side, in Previewer's ems or ("%", n), or None when
    it has none. Unlike a cell, a box has no default padding (#238)."""
    sides = (css or {}).get("padding-sides")
    if not sides:
        return None
    out = {side: _padding_em(v) or 0.0 for side, v in zip(_BORDER_SIDES, sides)}
    if all(v == 0 for v in out.values()):
        return None
    return out


def _attr_border_px(table):
    """The `border` attribute's width as HTML reads it (leading digits; an
    empty value is 1), or 0."""
    border = table.get("border")
    if border is None:
        return 0
    return int(re.match(r"\s*(\d*)", border).group(1) or 1)


_VALIGN_ATTR_VALUES = {"top", "middle", "bottom", "baseline"}
_VALIGN_SCOPE = _CELL_TAGS | {"tr", "thead", "tbody", "tfoot"}


def _cell_valign(cell, style_resolver):
    """A cell's computed vertical-align: its own, else its row's, else its row
    group's (#269). None when nothing sets it, which only happens without a
    Stylizer.

    calibre's Stylizer computes the CSS but ignores the `valign` attribute,
    which calibre keeps in the markup, so it is read here: on each element
    a CSS value wins over the attribute, as an author rule beats a
    presentational hint. calibre's UA sheet gives td/tr `inherit`, so the
    walk goes on, and row groups and `table > tr` `middle`, which can't be
    told from an author's `middle`: there the attribute wins, as it does
    over the UA rule in a browser. Kindle Previewer 4 gives the same answer
    for every case in the #269 probe."""
    el = cell
    while el is not None and _local_tag(el.tag) in _VALIGN_SCOPE:
        css = style_resolver(el) if style_resolver is not None else None
        declared = ((css or {}).get("vertical-align") or "").strip().lower()
        attr = (el.get("valign") or "").strip().lower()
        if attr not in _VALIGN_ATTR_VALUES:
            attr = ""
        ua_middle = declared == "middle" and _local_tag(el.tag) not in _CELL_TAGS
        if declared and declared != "inherit" and not (ua_middle and attr):
            return declared
        if attr:
            return attr
        el = el.getparent()
    return None


def _table_cell(cell, style_resolver, base_href):
    text, spans, marks = normalize_runs_with_anchors(
        _walk_inline(cell, style_resolver=style_resolver, base_href=base_href)
    )
    ids = _dedupe_keep_order(_own_anchor_ids(cell) + list(marks))
    css = style_resolver(cell) if style_resolver is not None else None
    return {
        "text": text,
        "spans": spans,
        "anchor_ids": ids,
        "anchor_offsets": {aid: marks.get(aid, 0) for aid in ids},
        "block_style": compute_block_style(css) if css is not None else None,
        "header": _local_tag(cell.tag) == "th",
        # A heading written in a cell: the section's opening, as a heading
        # outside a table is (#492).
        "heading": any(
            isinstance(d.tag, str) and _local_tag(d.tag) in _HEADING_TAGS
            for d in cell.iter()
        ),
        "colspan": _span_attr(cell, "colspan"),
        "rowspan": _span_attr(cell, "rowspan"),
        "valign": _cell_valign(cell, style_resolver),
    }


def _paragraph_cell(cell, style_resolver, walk_cell):
    """A cell holding several blocks, as a container of paragraphs (#261).

    `walk_cell` walks the cell as the body is walked, so its headings, list
    items, loose text and anchors become the same blocks they would outside
    a table. Returns None when that leaves no text."""
    paragraphs = [p for p in walk_cell(cell) if p.get("text")]
    if not paragraphs:
        return None
    css = style_resolver(cell) if style_resolver is not None else None
    return {
        "text": " ".join(p["text"] for p in paragraphs),
        "spans": [],
        "anchor_ids": [],
        "anchor_offsets": {},
        "block_style": compute_block_style(css) if css is not None else None,
        "header": _local_tag(cell.tag) == "th",
        "colspan": _span_attr(cell, "colspan"),
        "rowspan": _span_attr(cell, "rowspan"),
        "valign": _cell_valign(cell, style_resolver),
        "paragraphs": paragraphs,
    }


def _cell_ids(cell):
    """Every anchor id a cell holds: its own and its paragraphs'."""
    return list(cell["anchor_ids"]) + [
        a for p in cell.get("paragraphs") or () for a in p["anchor_ids"]
    ]


def _table_block(table, style_resolver=None, base_href=None, walk_cell=None):
    """A native table as one block, plus its captions and any anchors left over.

    Returns (caption_elements, table_block, trailing_ids). The caller walks
    each caption with the ordinary block walker, so it becomes the same
    paragraphs the rows build writes (5.8.8), however many blocks it holds.
    Anchors between rows follow 5.8.8's rule (`_anchors_follow_rows`): in
    calibre's notes layout an anchor after a row belongs to that row,
    otherwise to the next row; anchors with no row left to take them carry
    past the table. (#219)

    With `walk_cell`, a cell holding two or more blocks becomes a container
    of paragraphs (`_paragraph_cell`). (#261)
    """
    rows, carry, captions = [], [], []
    # The table's own frame: its CSS border, else the `border` attribute's
    # outset grey frame of N x 0.45pt, as Kindle Previewer writes it (#264).
    table_css = style_resolver(table) if style_resolver is not None else None
    # Rows and row groups draw their own borders only when collapsed, as a
    # browser does and Kindle Previewer writes them (#264).
    collapse = str((table_css or {}).get("border-collapse") or "") == "collapse"

    def part_border(elem):
        if not collapse or style_resolver is None:
            return None
        return _css_border(style_resolver(elem))

    table_border = _css_border(table_css) or (
        dict.fromkeys(
            _BORDER_SIDES,
            ("outset", round(0.45 * _attr_border_px(table), 4), 0xFF808080),
        )
        if _attr_border_px(table)
        else None
    )

    attr_px = _attr_border_px(table)
    # The `border` attribute's inset rule round every cell (#264). A CSS
    # `border: none` on the table or a cell doesn't cancel it: calibre's
    # computed `none` can't be told from the UA default, so only a drawn CSS
    # border replaces the attribute's (#292 review; no library table has
    # both).
    attr_cell_border = (
        dict.fromkeys(_BORDER_SIDES, ("inset", 0.45, None)) if attr_px else None
    )

    def build_cell(cell):
        built = None
        # A cell with a picture is walked as the body is, so the picture is a
        # paragraph of its own, which the generator writes as an image (#262).
        # Only what an <epub:switch> draws counts (#315).
        shown = _shown_tree(cell)
        has_picture = any(_local_tag(d.tag) == "img" for d in shown.iter())
        if walk_cell is not None and (_cell_paragraph_count(shown) >= 2 or has_picture):
            built = _paragraph_cell(cell, style_resolver, walk_cell)
        if built is None:
            built = _table_cell(cell, style_resolver, base_href)
        # A cell's own CSS border wins over the attribute's (#264).
        css = style_resolver(cell) if style_resolver is not None else None
        built["border"] = _css_border(css) or attr_cell_border
        built["padding"] = _cell_padding(css)
        # A cell giving itself a width takes Previewer's border-box sizing
        # (#264).
        built["width_set"] = _width_pct(cell, style_resolver) is not None
        return built

    def clamp_rowspans(group_rows):
        """A rowspan ends at its row group's last row, as HTML ends it.
        Only rows with cells count: the generator writes no other row."""
        written = [r for r in group_rows if r["cells"]]
        for i, row in enumerate(written):
            for cell in row["cells"]:
                cell["rowspan"] = min(cell["rowspan"], len(written) - i)

    def take(container, group):
        nonlocal carry
        follow = _anchors_follow_rows(container)
        last = None
        run = []  # this row group's rows; rows loose in <table> form their own
        for child in container:
            tag = _local_tag(child.tag)
            if tag in _ROW_GROUPS:
                clamp_rowspans(run)
                run = []
                carry.extend(_own_anchor_ids(child))
                take(child, _ROW_GROUPS[tag])
                last = None
            elif tag == "caption":
                captions.append(child)
            elif tag == "tr":
                last = {
                    "group": group,
                    "border": part_border(child),
                    # Consecutive row groups of one kind become one group in
                    # the KFX, which takes its first row's group border; a
                    # second <tbody>'s own border-top is dropped (#296
                    # review: 2 such tables in the library, both unstyled).
                    "group_border": (
                        part_border(container)
                        if _local_tag(container.tag) in _ROW_GROUPS
                        else None
                    ),
                    "anchor_ids": carry
                    + _own_anchor_ids(child)
                    + [
                        a
                        for c in child
                        if _is_empty_anchor(c)
                        for a in _own_anchor_ids(c)
                    ],
                    "cells": [
                        build_cell(c) for c in child if _local_tag(c.tag) in _CELL_TAGS
                    ],
                }
                carry = []
                rows.append(last)
                run.append(last)
            elif _is_empty_anchor(child):
                ids = _own_anchor_ids(child)
                if follow and last is not None:
                    last["anchor_ids"].extend(ids)
                else:
                    carry.extend(ids)
        clamp_rowspans(run)

    take(table, "body")
    # Head, then body, then foot, whatever the source order: an HTML4-style
    # <tfoot> before <tbody> is drawn last by a browser. Stable, so rows of
    # one kind keep their order. Anchors were placed in source order and
    # travel with their rows. (#219)
    # `source_order` keeps the order the rows build wrote them in, which
    # the generator's chapter-title cut follows.
    for i, row in enumerate(rows):
        row["source_order"] = i
    rows.sort(key=lambda r: _ROW_GROUP_ORDER[r["group"]])
    own = _own_anchor_ids(table)
    every = _dedupe_keep_order(
        own
        + [a for r in rows for a in r["anchor_ids"]]
        + [a for r in rows for c in r["cells"] for a in _cell_ids(c)]
    )
    block = {
        "type": "table",
        "text": "\n".join(
            line
            for line in (
                " ".join(c["text"] for c in r["cells"] if c["text"]) for r in rows
            )
            if line
        ),
        "spans": [],
        "block_style": None,
        "anchor_ids": every,
        "anchor_offsets": dict.fromkeys(every, 0),
        "table": {
            "anchor_ids": own,
            "rows": rows,
            "border": table_border,
            "column_widths": _column_widths(table, style_resolver),
            # A percentage table width: without it the Kindle sizes the
            # table to its content and narrow columns' widths don't show.
            "width": _width_pct(table, style_resolver),
        },
    }
    return captions, block, carry


def _table_start_ids(table_block):
    """Ids that name a native table's start: its own, and those of its first
    row and that row's first cell. A cell-less row is skipped on output and
    its ids move to the next row, so they count too. (#219)"""
    tbl = table_block["table"]
    start = set(tbl["anchor_ids"])
    for row in tbl["rows"]:
        start.update(row["anchor_ids"])
        if row["cells"]:
            first = row["cells"][0]
            start.update(first["anchor_ids"])
            if first.get("paragraphs"):
                start.update(first["paragraphs"][0]["anchor_ids"])
            break
    return start


#: Semantics that make an element a note reference, a back-link, or one note,
#: by epub:type, ARIA role, or the classes Python-Markdown's footnotes
#: extension writes. A note *section* (epub:type footnotes/endnotes, a
#: "footnotes" div) is deliberately absent: it is a real contents target.
_NOTE_TYPES = {"noteref", "backlink", "footnote", "endnote", "rearnote"}
_NOTE_ROLES = {"doc-noteref", "doc-backlink", "doc-footnote", "doc-endnote"}
_NOTE_CLASSES = {"footnote-ref", "footnote-backref"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _epub_types(elem):
    return (elem.get(_EPUB_TYPE_ATTR) or elem.get("epub:type") or "").lower().split()


def _footnote_keys(oeb_book, log=None):
    """The key of every id on or inside an `epub:type="footnote"` element in
    the spine: the targets Kindle Previewer writes as pop-up notes when a
    noteref links to them (#445). Not endnotes or `role` notes, which
    Previewer leaves as ordinary links, and not a footnote CSS hides: its
    ids carry on to the next block shown, which is not a note (#446
    review). Its CSS is read with calibre's Stylizer when `log` is given."""
    keys = set()
    for item in oeb_book.spine:
        try:
            data = item.data
        except Exception:
            continue
        if data is None or not hasattr(data, "iter"):
            continue
        notes = [
            e
            for e in data.iter()
            if isinstance(e.tag, str) and "footnote" in _epub_types(e)
        ]
        if not notes:
            continue
        resolver = _build_style_resolver(oeb_book, item, log) if log else None
        doc = _key_doc(getattr(item, "href", "") or "", "")
        for elem in notes:
            if any(
                _is_hidden(a, resolver)
                for a in (elem, *elem.iterancestors())
                if isinstance(a.tag, str)
            ):
                continue
            for d in elem.iter():
                if isinstance(d.tag, str):
                    keys.update(f"{doc}#{aid}" for aid in _own_anchor_ids(d))
    return keys


def _is_note_element(elem):
    types = _epub_types(elem)
    if _NOTE_TYPES & set(types):
        return True
    if (elem.get("role") or "").strip().lower() in _NOTE_ROLES:
        return True
    return bool(_NOTE_CLASSES & set((elem.get("class") or "").split()))


def _note_target_ids(root):
    """Ids that name a note marker, a back-link, or a single note (#203).

    calibre generates a table of contents when the source has none, and it
    lists a footnote's marker and back-link as entries. Neither is a chapter:
    as chapters they split the text and print "1" and "↩" as headings.

    An id counts when its element is itself note-marked, wraps a note marker
    (`<sup id="fnref:1"><a class="footnote-ref">`), or is a list item in a
    Python-Markdown footnote block (`<div class="footnote"><ol><li id=...>`).
    A heading never counts, and neither does anything holding one, because a
    "Notes" chapter heading inside the notes section is a real contents entry.
    Detection is structural and never reads the label: chapters titled "1",
    "2", "3" are common.
    """
    ids = set()
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        own = _own_anchor_ids(elem)
        if not own:
            continue
        if _local_tag(elem.tag) in _HEADING_TAGS or any(
            isinstance(d.tag, str) and _local_tag(d.tag) in _HEADING_TAGS
            for d in elem.iter()
            if d is not elem
        ):
            continue
        note = _is_note_element(elem) or any(
            isinstance(c.tag, str) and _is_note_element(c) for c in elem
        )
        if not note and _local_tag(elem.tag) == "li":
            lst = elem.getparent()
            box = lst.getparent() if lst is not None else None
            note = box is not None and "footnote" in (box.get("class") or "").split()
        if note:
            ids.update(own)

    # Markers by what they link to. calibre strips the footnote-ref and
    # footnote-backref classes during conversion, so a Markdown marker arrives
    # as a bare `<sup id="fnref:1"><a href="#fn:1">`. It is an `<a>` pointing at
    # a note, or a sup/span wrapping nothing but one. A paragraph that merely
    # contains a marker is not one: its id can be a real chapter start.
    def _points_at_note(a):
        frag = _href_fragment(a.get("href") or "")
        return bool(frag) and (frag in ids or unquote(frag) in ids)

    markers = set()
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        own = _own_anchor_ids(elem)
        if not own or set(own) & ids:
            continue
        tag = _local_tag(elem.tag)
        if tag == "a":
            hit = _points_at_note(elem)
        elif tag in ("sup", "span", "small"):
            kids = [c for c in elem if isinstance(c.tag, str)]
            hit = (
                len(kids) == 1
                and _local_tag(kids[0].tag) == "a"
                and not (elem.text or "").strip()
                and not (kids[0].tail or "").strip()
                and _points_at_note(kids[0])
            )
        else:
            hit = False
        if hit:
            markers.update(own)
    return ids | markers


def _dedupe_keep_order(items):
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


#: epub:type values naming navigation that is never rendered as body text.
#: page-list is the big one: an EPUB 3 print-pagination nav holds one entry per
#: printed page, which arrived as several hundred bare page numbers. (#60)
#: Landmarks name a few places the reader's own menu offers. A list of
#: illustrations or tables (lot, loi, lov) is printed, as Kindle Previewer
#: prints it: a page of links to the book's figures (#392).
_NON_RENDERED_EPUB_TYPES = {"page-list", "landmarks"}
_EPUB_TYPE_ATTR = "{http://www.idpf.org/2007/ops}type"


#: Elements whose content a browser never shows: a <template>'s, and an
#: <iframe>'s fallback children, which HTML5 ignores (#477).
_NEVER_SHOWN_TAGS = {"template", "iframe"}


def _is_non_rendered(elem):
    """True when `elem` heads a subtree that is markup, not reading content.

    Three signals: the HTML5 `hidden` attribute (the general rule — the
    producer said not to display this), an element whose content a browser
    never shows (`_NEVER_SHOWN_TAGS`, #477), and an `epub:type` naming a
    navigation kind that is structural by definition, which catches producers
    that omit `hidden`.
    """
    if elem.get("hidden") is not None:
        return True
    if isinstance(elem.tag, str) and _local_tag(elem.tag) in _NEVER_SHOWN_TAGS:
        return True
    raw = elem.get(_EPUB_TYPE_ATTR) or elem.get("epub:type") or ""
    return any(part in _NON_RENDERED_EPUB_TYPES for part in raw.lower().split())


_INLINE_DISPLAY_NONE = re.compile(r"(?:^|;)\s*display\s*:\s*none\s*(?:;|$|!)", re.I)


def _is_hidden(elem, style_resolver):
    """`_is_non_rendered`, or hidden by CSS: `display: none`, computed by
    calibre's Stylizer when it is there, else read from the element's own
    `style` attribute (#232, #256). An element hidden by position and clip,
    a heading kept for navigation, is not hidden here."""
    if _is_non_rendered(elem):
        return True
    if style_resolver is not None:
        css = style_resolver(elem) or {}
        return str(css.get("display") or "").strip().lower() == "none"
    return bool(_INLINE_DISPLAY_NONE.search(elem.get("style") or ""))


_EPUB_NS = "{http://www.idpf.org/2007/ops}"


def _epub_switch_branch(elem):
    """For an <epub:switch>: (ids, default), else None.

    A reading system shows one branch of a switch. kfxgen supports none of
    the `required-namespace` cases (MathML, mostly), so it shows the
    <epub:default>, or nothing when there is none. `ids` are the switch's own
    and those in its cases, so a link aimed at a dropped case still lands on
    the equation the default draws. (#234)"""
    if elem.tag not in (_EPUB_NS + "switch", "epub:switch"):
        return None
    ids = _own_anchor_ids(elem)
    default = None
    for child in elem:
        if not isinstance(child.tag, str):
            continue
        if child.tag in (_EPUB_NS + "default", "epub:default"):
            if default is None:
                default = child
        else:
            ids.extend(_subtree_anchor_ids(child))
    return ids, default


def _shown_tree(elem):
    """`elem`, or a copy of it holding only the <epub:switch> branches the
    walkers draw: each switch keeps its default and none of its cases, nor
    the text between them. Checks that count a subtree's tags, blocks or
    text read this, so a dropped MathML case can't rule a table out. (#315)"""
    if not any(_epub_switch_branch(d) for d in elem.iter()):
        return elem
    elem = copy.deepcopy(elem)
    for switch in [d for d in elem.iter() if _epub_switch_branch(d)]:
        default = _epub_switch_branch(switch)[1]
        switch.text = None
        for child in list(switch):
            if child is not default:
                switch.remove(child)
        if default is not None:
            default.tail = None
    return elem


#: Marks a contents listing the book printed itself: a `class` token, an
#: `epub:type`, or the ARIA role. Gutenberg overwhelmingly uses `class="toc"`,
#: on the listing container in some books and on each entry paragraph in
#: others; EPUB 3 producers use `epub:type="toc"` / `role="doc-toc"`.
_TOC_CLASS_TOKEN = "toc"
_TOC_DOC_ROLE = "doc-toc"
#: Set on a file's root by `mark_source_classes`, and on each element whose
#: own class holds the `toc` token (#329).
_SOURCE_MARKED = "data-kfxgen-source"
_SOURCE_TOC = "data-kfxgen-toc"
#: Front matter a book marks by a `class` token or an `epub:type`, and by
#: an ARIA role: kept when it follows a contents listing that is rebuilt
#: (#393). Set on an element whose own class holds one of the tokens.
_FRONT_MATTER = frozenset(
    {"dedication", "epigraph", "copyright", "copyright-page", "colophon"}
)
_FRONT_MATTER_ROLES = frozenset({"doc-dedication", "doc-epigraph", "doc-colophon"})
#: Lists of illustrations, tables or videos, kept after a rebuilt listing like
#: front matter (#392). By `epub:type` only: a class named "lot" is anything.
_LIST_EPUB_TYPES = frozenset({"lot", "loi", "lov"})
_SOURCE_FRONT = "data-kfxgen-front"


def mark_source_classes(root):
    """Record which elements the book itself classes `toc`, before calibre
    renames the classes.

    calibre flattens the CSS before kfxgen sees the book, naming each class
    after the first element with that style: plain paragraphs styled like an
    unstyled `<p class="toc">` entry arrive as `class="toc"` and were
    dropped as a contents listing, and listing entries can be renamed away
    from it. calibre runs the output plugin's `specialize_css_for_output`
    on each file just before flattening it, which calls this (#329)."""
    root.set(_SOURCE_MARKED, "1")
    for elem in root.iter():
        if isinstance(elem.tag, str):
            tokens = (elem.get("class") or "").lower().split()
            if _TOC_CLASS_TOKEN in tokens:
                elem.set(_SOURCE_TOC, "1")
            if _FRONT_MATTER.intersection(tokens):
                elem.set(_SOURCE_FRONT, "1")


def _is_front_matter(elem):
    """True when the book marks `elem` as a dedication, an epigraph, a
    copyright page or a colophon (#393), or as a list of illustrations or
    tables (#392). The class is the book's own, before calibre renamed it
    (#329)."""
    if elem.getroottree().getroot().get(_SOURCE_MARKED):
        if elem.get(_SOURCE_FRONT):
            return True
    elif _FRONT_MATTER.intersection((elem.get("class") or "").lower().split()):
        return True
    raw = (elem.get(_EPUB_TYPE_ATTR) or elem.get("epub:type") or "").lower().split()
    if _FRONT_MATTER.intersection(raw) or _LIST_EPUB_TYPES.intersection(raw):
        return True
    return (elem.get("role") or "").strip().lower() in _FRONT_MATTER_ROLES


def _is_nav_listing(elem):
    """True when `elem` heads a contents listing printed in the source.

    KFX carries navigation of its own, and kfxgen rebuilds a contents page
    from the actual chapter titles, so a listing in the source is a duplicate
    wherever it sits. The discard used to key on the enclosing *chapter's*
    title, which only recognises a listing that is its own chapter; seven
    corpus books put one inside the front-matter page instead, or titled the
    chapter after the front matter, and printed the whole listing to the
    reader. Recognise the markup rather than the title. (#132)

    Class matching is by whitespace-separated token, never substring: "tocsin"
    is a word, and `class="toc-entry"` is a different name.
    """
    if elem.getroottree().getroot().get(_SOURCE_MARKED):
        # The book's own class, as it was before calibre renamed it (#329).
        if elem.get(_SOURCE_TOC):
            return True
    elif _TOC_CLASS_TOKEN in (elem.get("class") or "").split():
        return True
    raw = elem.get(_EPUB_TYPE_ATTR) or elem.get("epub:type") or ""
    if _TOC_CLASS_TOKEN in raw.lower().split():
        return True
    return (elem.get("role") or "").strip().lower() == _TOC_DOC_ROLE


def _attach_anchor_keys(blocks, base_href):
    """Qualify each block's anchor ids with the file they live in, so a link
    target normalized to "<file>#<id>" can be matched across spine files.
    Stays empty when the caller didn't say which file this is. (#53)

    `anchor_offsets` is re-keyed the same way, turning {id: offset} into
    {key: offset} so the generator can carry each offset into the anchor it
    builds. (#79)"""
    normalized = _key_doc(base_href, "") if base_href else ""

    def key_block(block):
        by_id = block.get("anchor_offsets") or {}
        block["anchor_keys"] = (
            [f"{normalized}#{aid}" for aid in block.get("anchor_ids", ())]
            if normalized
            else []
        )
        block["anchor_offsets"] = (
            {
                f"{normalized}#{aid}": by_id.get(aid, 0)
                for aid in block.get("anchor_ids", ())
            }
            if normalized
            else {}
        )
        if block.get("type") == "box":
            # Its blocks are the link targets, nested boxes and tables
            # included; the box only carries their ids for the chapter split
            # (#238, #310).
            for kid in block["blocks"]:
                key_block(kid)
        tbl = block.get("table")
        if tbl:
            cells = [c for r in tbl["rows"] for c in r["cells"]]
            paragraphs = [p for c in cells for p in c.get("paragraphs") or ()]
            for part in [tbl] + tbl["rows"] + cells + paragraphs:
                by_part = part.get("anchor_offsets") or {}
                ids = part.get("anchor_ids", ())
                part["anchor_keys"] = (
                    [f"{normalized}#{a}" for a in ids] if normalized else []
                )
                part["anchor_offsets"] = (
                    {f"{normalized}#{a}": by_part.get(a, 0) for a in ids}
                    if normalized
                    else {}
                )

    for block in blocks:
        key_block(block)
    # A TOC entry may link to a whole file with no fragment
    # (`<a href="about.xhtml">About the Author</a>`). Give the document's first
    # block a bare-filename key so such links have something to resolve to —
    # otherwise a file that declares no ids anywhere is unreachable and the
    # link is silently dropped. (#62)
    if normalized and blocks:
        blocks[0]["anchor_keys"] = [normalized] + blocks[0]["anchor_keys"]
        blocks[0]["anchor_offsets"][normalized] = 0
        if blocks[0].get("table"):
            blocks[0]["table"]["anchor_keys"] = [normalized] + blocks[0]["table"][
                "anchor_keys"
            ]
    return blocks


# ── List markers (#201) ──────────────────────────────────────────────────────
#
# kfxgen has no list structure: each <li> is a paragraph of its own. The
# number or bullet a reader draws in front of it is not in the text, so it
# used to be lost — for a bulleted list a formatting loss, for a numbered one
# a content loss ("step 2", endnote 14). The marker is written into the text
# instead, which survives any reader.

#: `<ol type>` / `<li type>` values, as the list-style-type each one means.
#: Case matters: "a" and "A" are different styles.
_LIST_TYPE_ATTR = {
    "1": "decimal",
    "a": "lower-alpha",
    "A": "upper-alpha",
    "i": "lower-roman",
    "I": "upper-roman",
    "disc": "disc",
    "circle": "circle",
    "square": "square",
}

#: One glyph for every bullet style. Readers ship "•" in every font; the
#: circle and square glyphs are not guaranteed, and a missing glyph is worse
#: than a disc where a circle was meant.
_BULLET_TYPES = frozenset({"disc", "circle", "square"})
_BULLET = "•"
#: An item that opens with its own bullet has typed its marker, as an item
#: that opens with "1." has typed its number. A shape glyph counts on its own
#: ("•item" too); a dash or asterisk only with a space after it, so
#: "-5 degrees" and "*emphasis*" stay prose. (Shapes-without-space from the
#: #206 author's follow-up, 294a691.)
_TYPED_BULLET_RE = re.compile(r"\s*(?:[•◦▪▫■□●○‣⁃·]|[-–—*]\s)")

_ROMAN = (
    (1000, "m"),
    (900, "cm"),
    (500, "d"),
    (400, "cd"),
    (100, "c"),
    (90, "xc"),
    (50, "l"),
    (40, "xl"),
    (10, "x"),
    (9, "ix"),
    (5, "v"),
    (4, "iv"),
    (1, "i"),
)
_GREEK = "αβγδεζηθικλμνξοπρστυφχψω"


def _to_roman(n):
    out = []
    for value, numeral in _ROMAN:
        while n >= value:
            out.append(numeral)
            n -= value
    return "".join(out)


def _to_alpha(n, letters):
    """Bijective base-N: a, b, …, z, aa, ab — the CSS alphabetic system."""
    out = []
    while n > 0:
        n, rem = divmod(n - 1, len(letters))
        out.append(letters[rem])
    return "".join(reversed(out))


def _format_ordinal(n, style):
    """`n` as the counter text of `style`. Outside a style's range (alphabetic
    and roman have no zero or negatives; roman stops at 3999) CSS falls back
    to decimal, and so does an unrecognised style name."""
    if style == "decimal-leading-zero":
        return f"{n:02d}" if n >= 0 else f"-{-n:02d}"
    if n > 0:
        if style in ("lower-alpha", "lower-latin"):
            return _to_alpha(n, string.ascii_lowercase)
        if style in ("upper-alpha", "upper-latin"):
            return _to_alpha(n, string.ascii_uppercase)
        if style == "lower-greek":
            return _to_alpha(n, _GREEK)
        if n < 4000:
            if style == "lower-roman":
                return _to_roman(n)
            if style == "upper-roman":
                return _to_roman(n).upper()
    return str(n)


def _parse_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _list_ordinals(list_elem, style_resolver=None):
    """{li: ordinal} for the items of one list, honouring `start`, `reversed`
    and `<li value>`. Hidden items take no number, as in a browser, CSS
    included (#232)."""
    items = [
        child
        for child in list_elem
        if _local_tag(child.tag) == "li" and not _is_hidden(child, style_resolver)
    ]
    is_reversed = list_elem.get("reversed") is not None
    start = _parse_int(list_elem.get("start"))
    if start is None:
        start = len(items) if is_reversed else 1
    step = -1 if is_reversed else 1
    ordinals = {}
    n = start
    for li in items:
        value = _parse_int(li.get("value"))
        if value is not None:
            n = value
        ordinals[li] = n
        n += step
    return ordinals


def _list_style_type(li, css):
    """The list-style-type that governs `li`, lowercased for keywords.

    Calibre's Stylizer resolves it fully — its UA sheet maps `<ol type>` and
    nested lists, and the property inherits from the list — so the computed
    value wins when there is one. Without a stylizer the markup decides: an
    `<ol>` counts in decimal (or its `type`), anything else is bulleted. A
    `type` on the item itself is a presentational hint the UA sheet does not
    cover, and it outranks what the item inherits."""
    own = li.get("type")
    if own is not None and own.strip() in _LIST_TYPE_ATTR:
        return _LIST_TYPE_ATTR[own.strip()]
    computed = (css or {}).get("list-style-type")
    if computed:
        computed = str(computed).strip()
        # A quoted string is itself the marker (CSS Lists 3).
        return computed if computed[:1] in "\"'" else computed.lower()
    parent = li.getparent()
    if parent is not None and _local_tag(parent.tag) == "ol":
        return _LIST_TYPE_ATTR.get((parent.get("type") or "").strip(), "decimal")
    return "disc"


def _already_numbered(text, n):
    """True when `text` opens with item number `n` written out by hand.

    Endnote lists commonly print the number in the text — often as the
    back-link, `<a>1</a>. The note…` — and prefixing another would read
    "1. 1. The note". Only the item's own number counts, so a sentence that
    merely starts with a figure is not mistaken for one. Letters and numerals
    need closing punctuation: "A man…" and "I went…" are prose."""
    head = text.lstrip()[:16]
    if re.match(rf"[(\[]?0*{n}(?:[.):\]]|\s|$)", head):
        return True
    if n <= 0:
        return False
    forms = {_to_alpha(n, string.ascii_lowercase)}
    if n < 4000:
        forms.add(_to_roman(n))
    lowered = head.lower()
    return any(re.match(rf"[(\[]?{re.escape(f)}[.)\]]", lowered) for f in forms)


# Elements a browser lays out as blocks that `block_tags` does not list. Next
# to text written straight into <body>, each stays its own paragraph rather
# than running into that text (#280).
_BODY_BLOCK_TAGS = frozenset(
    {
        "img",
        "svg",
        "address",
        "aside",
        "center",
        "details",
        "fieldset",
        "figcaption",
        "footer",
        "form",
        "header",
        "hgroup",
        "hr",
        "main",
        "menu",
        "nav",
        "summary",
    }
)


def _list_marker(li, css, ordinals):
    """(marker text, ordinal or None) for `li`, or None when it shows none."""
    display = str((css or {}).get("display") or "").strip().lower()
    if display and "list-item" not in display:
        return None  # `li { display: block }` suppresses the marker
    style = _list_style_type(li, css)
    if style == "none":
        return None
    if style[:1] in "\"'":
        return style[1:-1], None
    if style in _BULLET_TYPES:
        return f"{_BULLET} ", None
    n = ordinals.get(li)
    if n is None:
        return None
    return f"{_format_ordinal(n, style)}. ", n


def _prefix_marker(marker, text, spans, mark_offsets):
    """Put `marker` in front of a block's text, moving everything after it."""
    shift = len(marker)
    spans = [(start + shift, length, flags) for start, length, flags in spans]
    # An anchor at the very start keeps pointing at the start, which is now
    # the marker — a link to the item lands on its number.
    mark_offsets = {k: (v + shift if v else 0) for k, v in mark_offsets.items()}
    return marker + text, spans, mark_offsets


#: The least a gap set by margins alone must be to count as a scene break:
#: 1em is calibre's own paragraph margin, and a stanza's (#326).
_MARGIN_BREAK_EM = 1.5

#: The fields every block carries, for a rule or a space that holds no
#: text (#230).
_EMPTY_BLOCK = {
    "text": "",
    "spans": [],
    "block_style": None,
    "anchor_ids": [],
    "anchor_offsets": {},
}


#: A line of verse, an index entry, a cast list's name: at most this many
#: characters (#331). Real verse kept 89 % of its lines at 60, and a
#: library book of short prose paragraphs fell from 4,870 lines to 178.
_SHORT_LINE = 60
#: The share of a run's paragraphs that must be short lines (#331).
_SHORT_SHARE = 0.9


def _mark_tight_runs(blocks, gap_of):
    """Mark as "tight" the lines of each run of short, unindented paragraphs
    the book sets with no gap between them: verse one <p> per line, an
    index, a cast list (#331). kfxgen puts a line of space above every
    unindented paragraph, which double-spaced them; a browser draws them
    with none. A run's first line keeps its space, so a stanza break shows.

    A run is a paragraph followed by paragraphs with no gap above them, none
    a heading or indented. It is tight when nine in ten of its lines are
    short. Prose a book sets the same way, unindented with
    no gap, holds long paragraphs and keeps kfxgen's spacing: there the line
    of space is all that separates its paragraphs."""

    def line(b):
        style = b.get("block_style") or {}
        return (
            b.get("type") is None
            and not b.get("heading")
            # A table's rows written as paragraphs keep their space (#219).
            and not b.get("row")
            and not style.get("indent")
        )

    run = []
    for b in [*blocks, None]:
        if b is not None and line(b) and run and gap_of.get(id(b)) == 0.0:
            run.append(b)
            continue
        short = sum(1 for r in run if len(r.get("text") or "") <= _SHORT_LINE)
        if run and short >= _SHORT_SHARE * len(run):
            for r in run[1:]:
                r["tight"] = True
        run = [b] if b is not None and line(b) else []


#: `page-break-*` and `break-*` values that start a new page (#363).
_PAGE_BREAKS = frozenset({"always", "page", "left", "right", "recto", "verso"})


def _page_breaks(css):
    """Whether `css` breaks the page before its element, and after it."""
    if not css:
        return False, False

    def breaks(side):
        return any(
            str(css.get(prop) or "").strip().lower() in _PAGE_BREAKS
            for prop in (f"page-break-{side}", f"break-{side}")
        )

    return breaks("before"), breaks("after")


def _space_between(blocks, margins, measured=True):
    """`blocks` with the space a scene break puts above a paragraph, in ems,
    as "space_above" (#230, #326). The space blocks standing for spacers and
    invisible <hr>s are taken out.

    A paragraph's gap is the larger of its own margin-top, the margin-bottom
    before it and the margins of an invisible <hr> between, as margins
    collapse in a browser, plus a line for each spacer paragraph. The file's
    most common gap is its paragraph spacing, which kfxgen draws its own way.
    A gap is a break, and is written, when it is larger than that and:
    - a spacer or an invisible <hr> made it (#230); or
    - margins alone made it, and it stands out (#326): at least 1.5em and
      twice the usual gap, with usual gaps on both sides. Copying every
      margin changed 28,221 paragraphs in 186 of 200 library books; isolated
      gaps are 2,843 in 112, among them the `section-break`, `space-break`
      and `noindent` paragraphs that open a scene.

    A gap next to a heading is layout, not a break, and is not written
    (pg245 puts a spacer under each chapter heading). With `measured`, the
    margins are calibre's, and a run of short lines with no gap between them
    is marked tight (#331); without calibre's CSS no gap is known. A file's first
    paragraph starts a page or follows one in the same chapter, and takes
    none. Tables, boxes and rules keep their own spacing; a break before one
    is dropped."""
    out = []
    gaps = []  # (block, gap, how: "spacer", "margin" or None beside a heading)
    above = 0.0  # the larger margin waiting: the paragraph before's, an <hr>'s
    lines = 0.0
    on_purpose = False
    for b in blocks:
        kind = b.get("type")
        if kind == "space":
            above = max(above, b["margin"])
            lines += b["line"]
            on_purpose = True
            continue
        top, bottom = margins.get(id(b), (0.0, 0.0))
        if kind is None:
            if out:
                beside = b.get("heading") or out[-1].get("heading")
                how = None if beside else "spacer" if on_purpose else "margin"
                if out[-1].get("type") is not None:
                    # After a table, a box or a rule: a margin there is
                    # theirs to space.
                    how = how if how == "spacer" else None
                gaps.append((b, round(max(above, top) + lines, 4), how))
            above = bottom
        else:
            above = 0.0
        lines = 0.0
        on_purpose = False
        out.append(b)
    if gaps:
        counts = collections.Counter(g for _, g, _ in gaps)
        # The most common; of two as common, the smaller.
        usual = max(counts, key=lambda g: (counts[g], -g))
        gap_of = {id(b): gap for b, gap, _ in gaps}

        def ordinary(i):
            """No more than the usual gap above the paragraph at out[i], or
            nothing there to compare (the file's edge, a table, a box)."""
            if not 0 <= i < len(out):
                return True
            return gap_of.get(id(out[i]), usual) <= usual + 0.01

        if measured:
            _mark_tight_runs(out, gap_of)
        index = {id(b): i for i, b in enumerate(out)}
        for b, gap, how in gaps:
            if how is None or gap <= usual + 0.01:
                continue
            i = index[id(b)]
            following = out[i + 1] if i + 1 < len(out) else None
            if how == "margin" and not (
                gap >= _MARGIN_BREAK_EM
                and gap >= 2 * usual
                and ordinary(i - 1)
                and ordinary(i + 1)
                # Followed by a paragraph, not a heading or a table: a
                # margin before those is how they are set apart.
                and (
                    following is None
                    or (following.get("type") is None and not following.get("heading"))
                )
            ):
                continue
            b["space_above"] = gap
    return out


def extract_blocks_from_html(
    element,
    style_resolver=None,
    base_href=None,
    nav_listing_at=None,
    tables_seen=None,
    native_tables=False,
    toc_targets=None,
    link_targets=None,
    notes_seen=None,
    keep_nav_listings=False,
    trailing_ids=None,
):
    """Like extract_text_from_html but returns structured blocks:
    [{"text": str, "spans": [(start, length, frozenset)], "block_style": dict|None,
    "anchor_ids": list[str], "anchor_keys": list[str]}],
    preserving inline emphasis as spans and inline <img> as IMG tokens in `text`.
    When style_resolver is given, it is called per block element (elem -> css_dict|None)
    and the result is passed to compute_block_style to populate block_style.

    `nav_listing_at`, when given, collects one `(index, ids)` pair for each
    contents listing discarded: the index of the block that now follows it, and
    the anchor ids the listing itself held, so the caller can tell which
    chapter held it (#276). A listing that *was* the chapter's content has to be handed to the
    contents rebuild rather than simply deleted, or the book loses its
    contents page (#132).

    `tables_seen`, when given, collects the first block of each table that
    produced one, so the caller can say how many were written as rows of text
    rather than laid out (#219). It is the block itself, not its index, so the
    caller can tell whether it survived into the final chapters: a contents
    page is discarded after extraction, tables and all. A table nested inside
    a cell is part of that cell's row and is not counted separately.

    `native_tables`: when set, an eligible `<table>` becomes one
    `{"type": "table"}` block (#219); otherwise each row is its own paragraph.
    A table that is not eligible still falls back to rows, and only those
    count toward `tables_seen`.

    `toc_targets`: the fragment ids this file's TOC entries name. A chapter
    is a range of blocks and a native table is one block, so a TOC entry
    pointing past a table's start (its own id, an anchor just before it, its
    first row or first cell) cannot start a chapter there. Such a table falls
    back to rows, which keeps 5.8.8's chapters. (#219)

    `keep_nav_listings`: print contents listings as the book has them, for
    a book that gets no Contents page of kfxgen's (#335).

    `trailing_ids`, when given, collects the ids at the file's very end with
    no block after them. They still go on the last block, for chapter
    assembly; the caller moves their link keys to the next file (#338).

    `base_href` is the spine file this markup came from. It qualifies both
    sides of an in-book link: `anchor_keys` are the block's ids as
    "<file>#<id>", and a link run's target is normalized to the same form, so
    the generator can match them across files. Without it, links can't be
    resolved and `anchor_keys` stays empty. (#53)"""
    body = element.find(".//{http://www.w3.org/1999/xhtml}body")
    if body is None:
        body = element.find(".//body")
    if body is None:
        body = element

    ns = "{http://www.w3.org/1999/xhtml}"
    block_tags = set()
    for tag in (
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "li",
        # ol/ul must count as blocks, otherwise an <li> holding a nested list
        # looks childless and the whole sub-list is flattened into the parent's
        # paragraph — a Part heading and all its chapters on one line. (#58)
        "ol",
        "ul",
        # A term and its definition are paragraphs of their own, as a
        # browser draws them; they ran together as "TermDefinition" (#229).
        "dl",
        "dt",
        "dd",
        "section",
        "article",
        "figure",
        # A table that is not laid out natively (#251) falls back to rows:
        # each row is its own paragraph, so values that belong together stay
        # together. The row is the leaf; the rest are containers around it.
        # (#219)
        "table",
        "caption",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        # A scene break: a rule, or space when it draws nothing (#230).
        "hr",
        # The other browser blocks, <aside>, <header> and the rest: nested in
        # a <div>, their paragraphs ran into one (#387). Pictures are walked
        # on their own.
        *(_BODY_BLOCK_TAGS - {"img", "svg"}),
    ):
        block_tags.add(tag)
        block_tags.add(ns + tag)

    def _holds_blocks(elem):
        """A block child: one in `block_tags`, or an <epub:switch> whose
        default holds one, so the default's paragraphs stay apart (#234)."""
        if elem.tag in block_tags:
            return True
        switch = _epub_switch_branch(elem)
        return (
            switch is not None
            and switch[1] is not None
            and any(d.tag in block_tags for d in switch[1].iter())
        )

    blocks = []
    notes_tables = set()  # ids of tables written as notes paragraphs (#268)
    native_made = set()  # ids of tables written as native tables
    pending_ids = []  # anchors awaiting the next leaf block (containers, standalone <a>)
    # List markers awaiting the next text block: an item's number belongs on
    # the first text it holds, which may sit in a nested <p> (#201). An entry
    # is (marker, ordinal or None).
    pending_markers = []
    list_ordinals = {}  # list element -> {li: ordinal}
    # Space between paragraphs (#230): each block's own margin-top and
    # -bottom, in ems, with its containers' added at its edges. A spacer
    # paragraph or an <hr> that draws nothing is a {"type": "space"} block
    # until the end, where the space goes above the next paragraph.
    margins = {}  # id(block) -> [top, bottom]
    # The index from which the next block shown follows a page break, set by
    # an element's `page-break-before` or the one before it's `-after` (#363).
    break_at = []
    # (index, side): the next block shown from that index starts below the
    # floats on that side, set by an element's `clear`. An empty clearing
    # element passes it on, as Kindle Previewer does (#381).
    clear_at = []

    def _take_marker(text, spans, mark_offsets):
        """Prefix the pending list marker, if any, to a text block."""
        # A picture is not text: an image-only item stays an image block,
        # and the marker waits for text (or is dropped with the item).
        if not pending_markers or not _has_real_text(text):
            return text, spans, mark_offsets
        entries = pending_markers[:]
        pending_markers.clear()
        marker, n = entries[-1]
        if n is not None and _already_numbered(text, n):
            marker = ""
        elif n is None and marker == f"{_BULLET} " and _TYPED_BULLET_RE.match(text):
            # A typed bullet is the marker; a second one read "• ◦ …". Only a
            # bullet yields: in a numbered list the number is content.
            marker = ""
        marker = "".join(m for m, _ in entries[:-1]) + marker
        if not marker:
            return text, spans, mark_offsets
        return _prefix_marker(marker, text, spans, mark_offsets)

    def _emit_image_block(elem, href=None, alt=None, size=None):
        ids = pending_ids[:]
        pending_ids.clear()
        ids.extend(_own_anchor_ids(elem))
        block_ids = _dedupe_keep_order(ids)
        side = None
        if href is None:
            href = _resolve_img_src(base_href, elem.get("src", "") or "")
            alt = elem.get("alt", "") or ""
            size = _img_size_hint(elem, style_resolver)
            side, size = _img_float(elem, elem.getparent(), size, style_resolver)
        blocks.append(
            {
                "text": _make_img_token(href, alt, size, side),
                "spans": [],
                "block_style": None,
                "anchor_ids": block_ids,
                "anchor_offsets": dict.fromkeys(block_ids, 0),
            }
        )

    def _emit_svg_blocks(elem):
        """Each <image> an <svg> draws becomes an image block of its own, and
        the words it draws as <text> follow as paragraphs: their place on the
        picture is lost, their reading order is not (#231)."""
        for href, alt in _svg_image_refs(elem):
            _emit_image_block(elem, href, alt, SVG_IMAGE_SIZE)
        drew_picture = bool(_svg_image_refs(elem))
        for text, node in _svg_text_paragraphs(elem):
            ids = []
            if not drew_picture:
                # No picture took the <svg>'s own ids or those carried to
                # it: the first paragraph does.
                ids = pending_ids[:] + _own_anchor_ids(elem)
                pending_ids.clear()
                drew_picture = True
            ids = _dedupe_keep_order(ids + _subtree_anchor_ids(node))
            blocks.append(
                {
                    "text": text,
                    "spans": [],
                    "block_style": None,
                    "anchor_ids": ids,
                    "anchor_offsets": dict.fromkeys(ids, 0),
                }
            )

    def _discard_listing(elem):
        """Drop a contents listing's text, keeping the two things in it that
        are not navigation.

        Images: a plate printed inside the section is content. The reason to
        discard a listing is that its *text* duplicates navigation KFX already
        carries, which says nothing about pictures — that distinction is #117,
        and dropping the subtree wholesale would reintroduce it.

        Anchors: a TOC entry often points at the listing container itself. An
        anchor that vanishes takes every link aimed at it with it (#51/#53), so
        ids carry forward to the next real block, exactly as an empty anchor
        block already does. Anchors are paragraph-granular, so the link lands
        just past where it used to.
        """
        if _is_non_rendered(elem):
            return
        if _is_hidden(elem, style_resolver):
            # Hidden by CSS: its picture goes (#428), its ids carry on as
            # they did when it was walked. Ids inside a listing should name
            # the listing instead (#433).
            pending_ids.extend(_subtree_anchor_ids(elem))
            return
        if _local_tag(elem.tag) == "img":
            _emit_image_block(elem)
            return
        if _local_tag(elem.tag) == "svg":
            _emit_svg_blocks(elem)
            return
        pending_ids.extend(_own_anchor_ids(elem))
        for child in elem:
            _discard_listing(child)

    def _walk_cell(cell):
        """A table cell's blocks, walked as the body is (#261). The walker's
        pending ids and list markers are set aside first and put back after,
        so nothing carries into the cell or out of it. Ids left pending at
        the cell's end go on its last block."""
        saved_ids, saved_markers = pending_ids[:], pending_markers[:]
        saved_break, saved_clear = break_at[:], clear_at[:]
        pending_ids.clear()
        pending_markers.clear()
        break_at.clear()
        clear_at.clear()
        start = len(blocks)
        listings = len(nav_listing_at) if nav_listing_at is not None else 0
        _walk_element(cell)
        out = blocks[start:]
        del blocks[start:]
        if nav_listing_at is not None:
            # A contents listing in a cell was recorded at an index inside
            # the cell, whose blocks are taken out; it belongs to the table,
            # which goes where the cell's blocks began.
            nav_listing_at[listings:] = [
                (start, ids) for _, ids in nav_listing_at[listings:]
            ]
        if pending_ids and out:
            last = out[-1]
            for aid in pending_ids:
                if aid not in last["anchor_ids"]:
                    last["anchor_ids"].append(aid)
                    last["anchor_offsets"][aid] = 0
        pending_ids[:] = saved_ids
        pending_markers[:] = saved_markers
        break_at[:] = saved_break
        clear_at[:] = saved_clear
        return out

    def _walk_split_row(row):
        """A row holding a table, on the rows path (#263). The row's text
        before and after each table is a row paragraph, styled as the row;
        each table is walked as a block of its own, so it can be native.
        Nesting a table in a table cell is never tried on a device; nothing
        is nested here."""
        bstyle = None
        if style_resolver is not None:
            css = style_resolver(row)
            if css is not None:
                bstyle = compute_block_style(css)
            if bstyle is not None:
                bstyle["align"] = _row_align(row, bstyle["align"], style_resolver)
        preformatted = _is_preformatted(row, style_resolver)
        segment = []

        def flush():
            text, spans, mark_offsets = normalize_runs_with_anchors(segment)
            segment.clear()
            ids = pending_ids[:]
            pending_ids.clear()
            ids.extend(mark_offsets)
            if not text.strip():
                # Ids with no text of their own name what follows: the next
                # piece, or the table's start.
                pending_ids.extend(ids)
                return
            text, spans, mark_offsets = _take_marker(text, spans, mark_offsets)
            block_ids = _dedupe_keep_order(ids)
            blocks.append(
                {
                    "text": text,
                    "spans": spans,
                    "block_style": dict(bstyle) if bstyle else bstyle,
                    "preformatted": preformatted,
                    "anchor_ids": block_ids,
                    "heading": False,
                    "row": True,
                    "anchor_offsets": {
                        aid: mark_offsets.get(aid, 0) for aid in block_ids
                    },
                }
            )

        for part in _walk_inline(
            row, style_resolver=style_resolver, base_href=base_href, split_tables=True
        ):
            if part[0] is _TABLE_SPLIT:
                flush()
                _walk(part[1])
            else:
                segment.append(part)
        flush()

    def _is_block_at_body(child):
        if not isinstance(child.tag, str):
            return True
        if _local_tag(child.tag) in _BODY_BLOCK_TAGS:
            return True
        css = style_resolver(child) if style_resolver is not None else None
        display = str((css or {}).get("display") or "").strip().lower()
        return (
            display.startswith(("block", "list-item", "table", "flex", "grid"))
            or _is_nav_listing(child)
            or any(d.tag in block_tags for d in child.iter())
        )

    def _walk(elem):
        """Walk `elem`, then mark the first block shown after a page break
        it or one before it set (#363)."""
        css = (
            style_resolver(elem)
            if style_resolver is not None and isinstance(elem.tag, str)
            else None
        )
        before, after = _page_breaks(css)
        if before and not break_at:
            break_at.append(len(blocks))
        # A <br> is inline and never walked here: its clear is nothing, as
        # in Previewer's book.
        side = _clear_side(css)
        if side and not clear_at:
            clear_at.append((len(blocks), side))
        start = len(blocks)
        _walk_flow(elem)
        if isinstance(elem.tag, str) and _is_front_matter(elem):
            # Kept if a contents rebuild replaces its chapter (#393).
            for b in blocks[start:]:
                b["front_matter"] = True
        if after and not break_at:
            break_at.append(len(blocks))
        if break_at:
            for b in blocks[break_at[0] :]:
                if b.get("type") != "space":
                    b["break_before"] = True
                    break_at.clear()
                    break
        if clear_at:
            for b in blocks[clear_at[0][0] :]:
                if b.get("type") != "space":
                    b.setdefault("clear", clear_at[0][1])
                    clear_at.clear()
                    break

    def _walk_flow(elem):
        if _local_tag(elem.tag) != "li" or _is_hidden(elem, style_resolver):
            start = len(blocks)
            _walk_element(elem)
            if (
                _local_tag(elem.tag) == "table"
                and len(blocks) > start
                # Not "no table among its blocks": a table written as rows
                # can hold a native one (#263).
                and id(elem) not in native_made
            ):
                # Notes written as paragraphs on purpose are counted apart
                # from tables that could not be laid out (#268).
                if id(elem) in notes_tables:
                    if notes_seen is not None:
                        notes_seen.append(blocks[start])
                elif tables_seen is not None:
                    tables_seen.append(blocks[start])
            return
        parent = elem.getparent()
        if parent not in list_ordinals:
            list_ordinals[parent] = _list_ordinals(parent, style_resolver)
        css = style_resolver(elem) if style_resolver is not None else None
        entry = _list_marker(elem, css, list_ordinals[parent])
        if entry is None:
            _walk_element(elem)
            return
        # An outer item that has shown no text yet (`<li><ol><li>…`) keeps its
        # marker ahead of this one rather than losing it.
        before = pending_markers[:]
        pending_markers.append(entry)
        _walk_element(elem)
        if pending_markers:
            # The item held no text to carry its marker (an image, or
            # nothing); it does not leak onto whatever follows the list.
            pending_markers[:] = before

    def _walk_element(elem):
        if not isinstance(elem.tag, str):
            # A comment or a processing instruction between blocks is markup,
            # not a paragraph (#252). Also an unresolved entity node, which a
            # calibre tree never holds.
            return
        if _is_hidden(elem, style_resolver):
            # Not printed, but links into it still land: its ids name the
            # next block shown (#232).
            pending_ids.extend(_subtree_anchor_ids(elem))
            return
        switch = _epub_switch_branch(elem)
        if switch is not None:
            ids, default = switch
            pending_ids.extend(ids)
            if default is not None:
                _walk(default)
            return
        if not keep_nav_listings and _is_nav_listing(elem):
            if nav_listing_at is not None:
                # The listing's own ids travel with its position: a TOC entry
                # aimed at one of them is what makes the chapter starting
                # after the listing its owner (#276).
                nav_listing_at.append(
                    (len(blocks), frozenset(_subtree_anchor_ids(elem)))
                )
            _discard_listing(elem)
            return
        background = _css_background_image(
            elem, style_resolver(elem) if style_resolver is not None else None
        )
        if background:
            # The element *is* the picture — no children, no text — so it is
            # handled here rather than in either branch below, both of which
            # would see an empty block and drop it. Its box is sized to the
            # scan in every book seen with this shape, so the size hint comes
            # from the same place it would for an <img>. (#168)
            _emit_image_block(
                elem, background, "", _img_size_hint(elem, style_resolver)
            )
            return

        if _local_tag(elem.tag) == "hr":
            _emit_rule(elem)
            return

        native = (
            native_tables
            and _local_tag(elem.tag) == "table"
            and _table_is_native(elem, style_resolver)
        )
        if native and _is_notes_table(elem, base_href, link_targets):
            # A notes section reads better as paragraphs, one per note (#268).
            # Decided before the paragraph-cell path, so notes running to
            # several paragraphs still become paragraphs (#267 review).
            notes_tables.add(id(elem))
            native = False
        if native:
            listings = len(nav_listing_at) if nav_listing_at is not None else 0
            captions, table, trailing = _table_block(
                elem, style_resolver, base_href, walk_cell=_walk_cell
            )
            past_start = set(table["anchor_ids"]) - _table_start_ids(table)
            native = not (toc_targets and past_start & set(toc_targets))
            if not native and nav_listing_at is not None:
                # Drop what the native attempt recorded: the rows walk below
                # records its own, and keeps a listing inside a cell as the
                # row's text, recording none (#282).
                del nav_listing_at[listings:]
        if native:
            # The caption is walked like any block, as the rows build walks
            # it: each block in it is its own paragraph, with its style and
            # ids, and anchors carried from before the table land on its
            # first paragraph. An empty caption leaves its ids pending for
            # the table's start. (#219)
            if captions:
                # The table's own ids are pending when the rows build walks
                # its caption, so they name the caption's first paragraph: a
                # TOC entry to the table starts its chapter there. They come
                # back to the table if the caption holds no text.
                own = table["table"]["anchor_ids"]
                pending_ids.extend(own)
                table["table"]["anchor_ids"] = []
                table["anchor_ids"] = [a for a in table["anchor_ids"] if a not in own]
            for caption in captions:
                _walk(caption)
            if pending_ids:
                # Anchors carried from before the table name its start.
                table["table"]["anchor_ids"] = _dedupe_keep_order(
                    pending_ids + table["table"]["anchor_ids"]
                )
                table["anchor_ids"] = _dedupe_keep_order(
                    pending_ids + table["anchor_ids"]
                )
                table["anchor_offsets"] = dict.fromkeys(table["anchor_ids"], 0)
                pending_ids.clear()
            blocks.append(table)
            native_made.add(id(elem))
            pending_ids.extend(trailing)
            return

        # An equation walked as a block is a paragraph of its own; its
        # children are MathML, not blocks to walk one by one (#235).
        is_block = elem.tag in block_tags or _local_tag(elem.tag) == "math"
        has_block_child = any(_holds_blocks(child) for child in elem)

        if (
            native_tables
            and _local_tag(elem.tag) == "tr"
            and not has_block_child
            and any(
                isinstance(d.tag, str) and _local_tag(d.tag) == "table"
                for d in elem.iterdescendants()
            )
        ):
            _walk_split_row(elem)
            return

        if is_block and not has_block_child:
            text, spans, mark_offsets = normalize_runs_with_anchors(
                _walk_inline(elem, style_resolver=style_resolver, base_href=base_href)
            )
            ids = pending_ids[:]
            pending_ids.clear()
            ids.extend(_subtree_anchor_ids(elem))
            # Only non-breaking spaces is a spacer, not a paragraph (#230, #244).
            if text.strip():
                text, spans, mark_offsets = _take_marker(text, spans, mark_offsets)
                bstyle = None
                if style_resolver is not None:
                    css = style_resolver(elem)
                    if css is not None:
                        bstyle = compute_block_style(css)
                    if bstyle is not None and _local_tag(elem.tag) == "tr":
                        bstyle["align"] = _row_align(
                            elem, bstyle["align"], style_resolver
                        )
                    if bstyle is not None and _local_tag(elem.tag) == "p":
                        spans, drop = _initial(elem, css, text, spans, style_resolver)
                        if drop:
                            bstyle["drop_cap"] = drop
                if _local_tag(elem.tag) == "math" and elem.get("display") == "block":
                    # Centred, as Previewer sets a display equation (#235).
                    bstyle = {**(bstyle or compute_block_style({})), "align": "center"}
                block_ids = _dedupe_keep_order(ids)
                blocks.append(
                    {
                        "text": text,
                        "spans": spans,
                        "block_style": bstyle,
                        "preformatted": _is_preformatted(elem, style_resolver),
                        "anchor_ids": block_ids,
                        # A chapter with no TOC entry takes its title from
                        # its file's first heading (#304).
                        "heading": _local_tag(elem.tag) in _HEADING_TAGS,
                        # A table row written as a paragraph (#219).
                        "row": _local_tag(elem.tag) == "tr",
                        # Its text as a producer writes it into a TOC label:
                        # the heading match reads it (#402, #404).
                        **(
                            {"label_texts": labels}
                            if (labels := _label_texts(elem)) is not None
                            else {}
                        ),
                        # Ids inherited from an enclosing container point at
                        # this block's start; only ids declared inside it have
                        # a position of their own.
                        "anchor_offsets": {
                            aid: mark_offsets.get(aid, 0) for aid in block_ids
                        },
                    }
                )
                _note_margins(elem, len(blocks) - 1)
                # Text written straight in a bordered or filled <div> is a
                # box around that one paragraph (#310).
                _make_box(elem, len(blocks) - 1, leaf=True)
            else:
                pending_ids.extend(ids)  # empty anchor block: carry ids forward
                if _is_spacer(elem):
                    _emit_space(line=_LINE_SPACE_EM)
            return

        # No `parent_is_block` guard. It used to be here to avoid emitting an
        # image a `_walk_inline` had already consumed, but no call site can
        # deliver such an element: the leaf-block branch above returns without
        # recursing, and the container branch below only dispatches block
        # children and images into `_walk` — everything it hands to
        # `_walk_inline` stays there. The guard was therefore never true when it
        # mattered, and when it *was* true it silently discarded the image,
        # which is exactly what #113 was. Removed rather than left dead so a
        # future call site cannot resurrect the silent drop.
        if _local_tag(elem.tag) == "img":
            _emit_image_block(elem)
            return
        if _local_tag(elem.tag) == "svg":
            _emit_svg_blocks(elem)
            return
        pending_ids.extend(_own_anchor_ids(elem))

        start = len(blocks)
        _walk_container(elem)
        if not _make_box(elem, start):
            _carry_container_margins(elem, start)
            _note_margins(elem, start)

    def _emit_space(margin=0.0, line=0.0):
        """Space for the next paragraph to take: a spacer's blank line, or an
        <hr> that draws nothing, whose margins collapse with the paragraphs'
        around it. A cell keeps only blocks with text, so in a cell it is
        dropped, as before."""
        blocks.append({**_EMPTY_BLOCK, "type": "space", "margin": margin, "line": line})

    def _emit_rule(elem):
        """An <hr>: a rule, as Kindle Previewer writes it ($596), with its
        margins as space above and below, or just space when its CSS draws
        no border (#230). Its ids name the paragraph after it."""
        pending_ids.extend(_own_anchor_ids(elem))
        css = style_resolver(elem) if style_resolver is not None else None
        if css is not None and _css_border(css) is None:
            # An empty block's own margins collapse into one, the larger.
            _emit_space(margin=max(_margin_em(css, "top"), _margin_em(css, "bottom")))
            return

        def space(side):
            if not (css or {}).get(f"margin-{side}"):
                return _RULE_SPACE_EM
            return _margin_em(css, side)

        blocks.append(
            {
                **_EMPTY_BLOCK,
                "type": "rule",
                "rule": {"space_above": space("top"), "space_below": space("bottom")},
            }
        )

    def _note_margins(elem, start):
        """Record `elem`'s margin-top on the first paragraph it produced and
        its margin-bottom on the last, keeping a larger one already there:
        margins that touch collapse to the larger, as in a browser (#230)."""
        if style_resolver is None:
            return
        css = style_resolver(elem)
        top, bottom = _margin_em(css, "top"), _margin_em(css, "bottom")
        if not (top or bottom):
            return
        mine = [b for b in blocks[start:] if b.get("type") not in ("space", "rule")]
        if not mine:
            return
        first = margins.setdefault(id(mine[0]), [0.0, 0.0])
        first[0] = max(first[0], top)
        last = margins.setdefault(id(mine[-1]), [0.0, 0.0])
        last[1] = max(last[1], bottom)

    def _make_box(elem, start, leaf=False):
        """Replace the blocks a bordered or filled container produced with
        one box block holding them, drawn as Kindle Previewer draws it: a
        `$270` with the border, fill, padding and side margins, around the
        paragraphs (#238). Tables, pictures and other boxes inside are
        nested in it, as Previewer nests them (#310). A TOC entry past the
        box's first block keeps it flat, as for a native table (#219).
        `leaf`: the container is itself the one paragraph (text written
        straight in a bordered <div>); its margins move to the box.
        Returns whether it made a box."""
        tag = _local_tag(elem.tag)
        if style_resolver is None or tag not in _SIZED_BOX_TAGS:
            return False
        css = style_resolver(elem) or {}
        border, fill = _css_border(css), _box_background(css)
        if tag not in _BOX_TAGS:
            border = fill = None
        kids = blocks[start:]
        # A wrapper around a picture is drawn at its width (#382); one of
        # only a floated picture keeps #243's floated picture instead.
        width = _box_width(css)
        side = _float_side(css)
        if width is not None and not any(
            _IMG_TOKEN_RE.search(b.get("text") or "") for b in kids
        ):
            width = None
        if width is not None and side and len(kids) == 1:
            m = _IMG_TOKEN_RE.fullmatch(kids[0].get("text") or "")
            if m and m.group(4):
                width = None
        if border is None and fill is None and width is None:
            return False
        if not kids or not any(
            b.get("type") in ("table", "box") or (b.get("text") or "").strip()
            for b in kids
        ):
            return False
        if leaf:
            style = dict(kids[0].get("block_style") or {})
            style["margin_left"] = style["margin_right"] = None
            kids[0]["block_style"] = style
        past_start = {aid for b in kids[1:] for aid in b.get("anchor_ids") or ()}
        if toc_targets and past_start & set(toc_targets):
            return False
        left = parse_css_length(css.get("margin-left") or "")
        right = parse_css_length(css.get("margin-right") or "")
        framed = border is not None or fill is not None
        ids = _dedupe_keep_order([a for b in kids for a in b.get("anchor_ids") or ()])
        # A clear set on the container reached its first block; it is the
        # box's (#381).
        first = next((b for b in kids if b.get("type") != "space"), {})
        clear = first.pop("clear", None)
        blocks[start:] = [
            {
                "type": "box",
                "break_before": any(b.get("break_before") for b in kids),
                **({"clear": clear} if clear else {}),
                "text": "\n\n".join(b["text"] for b in kids if b["text"]),
                "spans": [],
                "block_style": None,
                "blocks": kids,
                "anchor_ids": ids,
                "anchor_offsets": dict.fromkeys(ids, 0),
                "box": {
                    "border": border,
                    "background": fill,
                    "padding": _box_padding(css),
                    "margin_left": _page_share(left) or None,
                    "margin_right": _page_share(right) or None,
                    "space_above": _box_space(
                        css.get("margin-top"), _BOX_SPACE_EM if framed else 0.0
                    ),
                    "space_below": _box_space(css.get("margin-bottom")),
                    "width": width,
                    "width_px": _box_width_px(css) if width is not None else None,
                    "float": side if width is not None else None,
                    "centred": width is not None
                    and str(css.get("margin-left")).strip() == "auto"
                    and str(css.get("margin-right")).strip() == "auto",
                },
            }
        ]
        return True

    def _carry_container_margins(elem, start):
        """Add a container's side margins to each text block it produced, as
        Kindle Previewer does: an indented <div> or <blockquote> indents its
        paragraphs (#238). Run after the children, so nested containers add
        up. A table's margins stay off its rows (#219); native tables and
        pictures keep their own.

        Only margins calibre leaves reach this: its "remove fake margins"
        step, on by default, strips some container margins first (three
        nested 3em divs carry nothing; its log says "Removing level ...
        left margin")."""
        if style_resolver is None or _local_tag(elem.tag) in _NO_CARRY_TAGS:
            return
        css = style_resolver(elem) or {}
        left = parse_css_length(css.get("margin-left") or "")
        right = parse_css_length(css.get("margin-right") or "")
        if left is None and right is None:
            return
        for b in blocks[start:]:
            if b.get("type") == "box":
                # The box takes them, not its paragraphs.
                box = b["box"]
                box["margin_left"] = (box["margin_left"] or 0) + _page_share(
                    left
                ) or None
                box["margin_right"] = (box["margin_right"] or 0) + _page_share(
                    right
                ) or None
                continue
            if b.get("type") == "table" or not _has_real_text(b.get("text") or ""):
                continue
            style = dict(b.get("block_style") or compute_block_style({}))
            new_left = _add_margin(style.get("margin_left"), left)
            new_right = _add_margin(style.get("margin_right"), right)
            if _page_share(new_left) + _page_share(new_right) > _MAX_CARRIED_MARGINS:
                # Nested indents would leave a narrow column of text: keep the
                # paragraph's own margins. 15 paragraphs in 290 books.
                continue
            style["margin_left"], style["margin_right"] = new_left, new_right
            b["block_style"] = style

    def _walk_container(elem, is_block=None):
        """`elem`'s children in order: block children walked as blocks, each
        run of inline content between them (its own text, inline children,
        tails) one paragraph. `is_block` overrides which children count as
        blocks."""
        # A container can hold its own inline content alongside block children —
        # `<li>Part<ol>…</ol></li>`, `<div>lead-in<p>…</p></div>`. That inline
        # content is real text and used to be dropped on the floor, because this
        # branch only recursed into children. Flush each run of inline siblings
        # as its own block, in document order. (#58)
        inline_parts = []

        def _flush_inline():
            if not inline_parts:
                return
            # `normalize_runs_with_anchors`, matching the leaf-block branch.
            # The plain version silently discards the anchor marks
            # `_walk_inline` emits, which is why an `id` on a table cell used
            # to vanish while the same id on a `<p>` or `<li>` survived:
            # tables were walked here rather than there, and a link into a cell
            # resolved to nothing at all (#130). Rows are blocks now (#219),
            # but a cell with no `<tr>` around it still arrives here.
            text, spans, mark_offsets = normalize_runs_with_anchors(inline_parts)
            inline_parts.clear()
            if not text.strip():
                # An anchor contributing no visible text carries forward to the
                # next block, exactly as an empty leaf block already does.
                pending_ids.extend(mark_offsets)
                return
            text, spans, mark_offsets = _take_marker(text, spans, mark_offsets)
            ids = pending_ids[:]
            pending_ids.clear()
            ids.extend(mark_offsets)
            block_ids = _dedupe_keep_order(ids)
            blocks.append(
                {
                    "text": text,
                    "spans": spans,
                    "block_style": None,
                    "anchor_ids": block_ids,
                    # Cells fuse into one block (#128), so an offset is what
                    # makes a link land on the right cell rather than at the
                    # start of the row. Ids inherited from an enclosing
                    # container point at the block's start (#79).
                    "anchor_offsets": {
                        aid: mark_offsets.get(aid, 0) for aid in block_ids
                    },
                }
            )

        ws = _white_space_flags(elem, style_resolver, frozenset())
        if elem.text:
            inline_parts.append((elem.text, ws))
        # In a table whose link targets follow their rows, an anchor after a
        # row belongs to that row, at its start — not to the next one. A row
        # that produced no block leaves `row_block` unset, and the anchor then
        # carries forward as usual.
        follow = _anchors_follow_rows(elem)
        row_block = None
        for child in elem:
            if follow and row_block is not None and _is_empty_anchor(child):
                tbl = row_block.get("table")
                for aid in _own_anchor_ids(child):
                    if aid not in row_block["anchor_ids"]:
                        row_block["anchor_ids"].append(aid)
                        row_block["anchor_offsets"][aid] = 0
                    # A row that opens with a native table (#263): its first
                    # row takes the table's own ids; from the block's alone
                    # they reach a later row.
                    if tbl is not None and aid not in tbl["anchor_ids"]:
                        tbl["anchor_ids"].append(aid)
            elif (
                is_block(child)
                if is_block is not None
                else _holds_blocks(child) or _local_tag(child.tag) in ("img", "svg")
            ):
                _flush_inline()
                start = len(blocks)
                _walk(child)
                if follow and _local_tag(child.tag) == "tr":
                    row_block = blocks[start] if len(blocks) > start else None
            else:
                # A hidden child gives only its ids (#256).
                inline_parts.extend(
                    _walk_inline(
                        child,
                        ws,
                        style_resolver,
                        is_root=False,
                        base_href=base_href,
                    )
                )
            if child.tail:
                inline_parts.append((child.tail, ws))
        _flush_inline()

    # The spine document is itself an SVG when the <body> lookup above fell
    # through to the root element. EPUB 3 fixed-layout producers emit these —
    # the page is an .svg file rather than an XHTML page containing one — and
    # walking their children finds <image>, which no branch of `_walk` claims,
    # so the page came through empty. (#165)
    is_svg_document = _local_tag(body.tag) == "svg"
    if is_svg_document:
        _emit_svg_blocks(body)
    elif (body.text or "").strip() or any((c.tail or "").strip() for c in body):
        # Text written straight into <body>, outside any paragraph, is kept
        # the way it is inside a <div> (#280). A body without any keeps the
        # walk below, child by child. Any child that walk would treat as more
        # than a run of text still goes to it: a <nav> listing is discarded
        # there, and an <aside> or <header> holding paragraphs keeps them.
        _walk_container(body, is_block=_is_block_at_body)
    else:
        for child in body:
            _walk(child)

    if nav_listing_at:
        # Each listing's index counts the space blocks before it, which
        # `_space_between` takes out.
        spaces = [i for i, b in enumerate(blocks) if b.get("type") == "space"]
        nav_listing_at[:] = [
            (at - bisect.bisect_left(spaces, at), ids) for at, ids in nav_listing_at
        ]
    blocks[:] = _space_between(blocks, margins, measured=style_resolver is not None)

    # Trailing anchors (e.g. <a id="eof"/> after the last leaf block) never
    # reach a subsequent block to flush to; snap them onto the last emitted
    # block instead of dropping them silently. Not onto a rule, which holds
    # no anchor (#230).
    if trailing_ids is not None and blocks:
        trailing_ids.extend(pending_ids)
    if pending_ids and blocks:
        last_block = next(
            (b for b in reversed(blocks) if b.get("type") != "rule"), blocks[-1]
        )
        last_block["anchor_ids"] = _dedupe_keep_order(
            last_block["anchor_ids"] + pending_ids
        )
        # A native table's chunks declare only its own, its rows' and its
        # cells' keys, so the ids go on its last row, where 5.8.8 put them.
        # (#219)
        tbl = last_block.get("table")
        if tbl and tbl["rows"]:
            last_row = tbl["rows"][-1]
            last_row["anchor_ids"] = _dedupe_keep_order(
                last_row["anchor_ids"] + pending_ids
            )
        if last_block.get("type") == "box":
            # Its last paragraph, not the box (#238).
            last_kid = last_block["blocks"][-1]
            last_kid["anchor_ids"] = _dedupe_keep_order(
                last_kid["anchor_ids"] + pending_ids
            )

    if blocks:
        return _attach_anchor_keys(blocks, base_href)

    if is_svg_document:
        # An SVG document's text nodes are not reading content: they are the
        # stylesheet, the RDF metadata the drawing program left behind, the
        # ids inside <defs>. The flat-text fallback below scoops all of it
        # into the body — 1,894 characters of CSS from the cover of the
        # IDPF/epub3-samples book, 220 of RDF from two of its pages. A vector
        # page that draws no bitmap and no <text> contributes nothing instead;
        # its <text> was read above (#231).
        return []

    # Fallback: no block elements — flat extraction, no spans (unchanged rule).
    text = body.xpath("string()")
    lines = [line.strip() for line in text.split("\n")]
    text = " ".join(line for line in lines if line)
    if not text:
        return []
    return _attach_anchor_keys(
        [
            {
                "text": text,
                "spans": [],
                "block_style": None,
                "anchor_ids": _dedupe_keep_order(pending_ids),
            }
        ],
        base_href,
    )


def extract_text_from_html(element):
    """Plain-text extraction (IMG tokens preserved). Now derived from
    extract_blocks_from_html so the two never diverge."""
    return "\n\n".join(
        b["text"] for b in extract_blocks_from_html(element) if b["text"]
    )


def _iso639_1(code):
    """calibre's two-letter form of an ISO 639 code, or None."""
    try:
        from calibre.utils.localization import lang_as_iso639_1
    except ImportError:
        return None
    return lang_as_iso639_1(code)


def _kfx_language(lang):
    """The book's language as Kindle Previewer 4.0.3 writes it (#453): as the
    book gives it, region and spelling kept ("en-GB", "pt_BR"); a three-letter
    code in its two-letter form where one exists ("eng" is "en"), else as it
    is ("haw"), never cut into another language's code; and "en" for none
    (calibre's "und"). calibre hands over its own three-letter code with no
    region, so in a conversion the region is already gone."""
    primary, rest = re.match(r"([^-_]*)(.*)", lang).groups()
    if not primary or primary.lower() == "und":
        return "en"
    if len(primary) == 3:
        primary = _iso639_1(primary) or primary
    return primary + rest


def extract_metadata(oeb_book, log):
    """
    Extract metadata from OEB book.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger

    Returns:
        dict: Metadata dictionary with title, author, language
    """
    metadata = {
        "title": "Untitled",
        "author": "Unknown",
        "language": "en",
        "publisher": "kfxgen",
        "issue_date": None,
    }

    try:
        if oeb_book.metadata.title:
            metadata["title"] = str(oeb_book.metadata.title[0])

        if oeb_book.metadata.creator:
            metadata["author"] = ", ".join(str(c) for c in oeb_book.metadata.creator)

        if oeb_book.metadata.language:
            metadata["language"] = _kfx_language(str(oeb_book.metadata.language[0]))

        if hasattr(oeb_book.metadata, "publisher") and oeb_book.metadata.publisher:
            metadata["publisher"] = str(oeb_book.metadata.publisher[0])

        if hasattr(oeb_book.metadata, "date") and oeb_book.metadata.date:
            raw_date = str(oeb_book.metadata.date[0])
            metadata["issue_date"] = raw_date[:10]  # YYYY-MM-DD from ISO format

    except Exception as e:
        log.error(f"Error extracting metadata: {e}")

    return metadata


_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


def _is_unsafe_href(href):
    """True if href looks like path traversal, an absolute path, a URL scheme,
    or a Windows drive letter. Percent-encoded forms (%2e%2e, %2f) are decoded
    iteratively until stable before checking, so multi-pass encodings like
    %252e%252e are caught at any depth. See SECURITY.md (#44, #60)."""
    if not href:
        return False
    # Iterate unquote until the string stabilises. The 16-iteration bound is
    # generous (no realistic href ever needs more than 2-3 layers) and is
    # only there to prevent pathological inputs from looping unboundedly.
    # The length-bound rejects malformed inputs that grow during decode —
    # legitimate percent-decoding monotonically shrinks.
    prev = href
    original_len = len(href)
    for _ in range(16):
        nxt = unquote(prev)
        if nxt == prev:
            break
        if len(nxt) > max(original_len, 1) * 4:
            # Decode shouldn't expand. Treat as malformed.
            return True
        prev = nxt
    decoded = prev
    candidates = {href, decoded}
    for candidate in candidates:
        if candidate.startswith(("/", "\\")):
            return True
        # Tightened drive-letter check: require an ASCII alpha first character.
        # The previous `candidate[1] == ':'` alone matched any 2-char prefix
        # ending in ':' (e.g. emoji + ':' or '0:'), which over-flagged.
        if _DRIVE_LETTER_RE.match(candidate):
            return True
        lowered = candidate.lower()
        if "://" in lowered or lowered.startswith(("javascript:", "data:", "file:")):
            return True
        parts = candidate.replace("\\", "/").split("/")
        if any(p == ".." for p in parts):
            return True
    return False


def _normalize_href(href):
    """Normalize an href by removing anchors and extracting filename.

    Returns "" for hrefs containing path traversal, absolute paths, URL schemes,
    or drive letters. The basename strip below already neutralises today's
    callers, but raw hrefs flow through chunk text in memory and may reach
    future code paths that resolve them — fail closed at the source. (#44)
    """
    if not href:
        return ""
    href = href.split("#")[0]
    if _is_unsafe_href(href):
        _security_log.warning("rejected unsafe href in _normalize_href: %r", href)
        return ""
    # Strip directory components. Split on both separators so a Windows-style
    # backslash can't survive into the returned "basename" and be re-read as a
    # path separator by a downstream resolver (#125 fuzz finding). Backslash
    # traversal (..\..) is already rejected by _is_unsafe_href above; this
    # neutralises the non-traversal residue (e.g. '....\' -> '').
    basename = re.split(r"[\\/]", href)[-1]
    # Re-check the extracted basename. Stripping directories can *surface* a
    # scheme/drive fragment that sat mid-path and so passed the full-href check
    # above (e.g. 'foo/javascript:alert(1)' -> 'javascript:alert(1)', or
    # '..../file:' -> 'file:'). The full-scheme-with-'//' forms are already
    # caught by the '://' check, but bare scheme prefixes are not. Fail closed
    # so the result is always a clean basename (#125 fuzz finding).
    if _is_unsafe_href(basename):
        _security_log.warning(
            "rejected unsafe basename in _normalize_href: %r (from %r)", basename, href
        )
        return ""
    return basename


def _href_fragment(href):
    """Return the fragment after '#', or '' when there is none."""
    return href.split("#", 1)[1] if href and "#" in href else ""


def _anchor_block_index(blocks):
    """Map each anchor id to the index of the FIRST block that carries it."""
    out = {}
    for i, b in enumerate(blocks):
        for aid in b.get("anchor_ids", ()):
            if aid not in out:
                out[aid] = i
    return out


#: calibre stamps the contents page it generates for MOBI/AZW3 output with
#: this body id. It is navigation, not the publisher's contents — KFX carries
#: its own nav pane — and left in, it was rebuilt as the contents page: for a
#: comic that is every page, "Page 1" to "Page 93", printed at the end.
CALIBRE_INLINE_TOC_ID = "calibre_generated_inline_toc"


def _is_calibre_inline_toc(element):
    """True for a spine document that is calibre's generated inline TOC."""
    body = element.find(".//{http://www.w3.org/1999/xhtml}body")
    if body is None:
        body = element.find(".//body")
    return body is not None and body.get("id") == CALIBRE_INLINE_TOC_ID


# Max length for treating a leading front-matter block's text as its own
# heading/title; longer reads as body prose, so fall back to a neutral label
# rather than dumping a paragraph into the nav entry.
_LEADING_TITLE_MAX_LEN = 60

#: Label used when front matter has no usable heading of its own. kfxgen
#: invents this string; no book contains it. Anything that would put it in
#: front of a reader — a rendered heading, a contents entry — is a leak of
#: the same kind as #107's "Half Title Page". (#133)
LEADING_TITLE_FALLBACK = "Front Matter"


def _leading_chapter_title(head_blocks):
    """Title for front matter that precedes the first TOC anchor.

    Use the first block's text when it is short enough to be a heading,
    otherwise a neutral 'Front Matter' label.

    Image tokens are stripped before the guards run. The guards alone let one
    through — `_make_img_token` emits a single run with its spaces escaped, so
    a bare cover image reads as a brief, tidy heading. The chapter was then
    *titled* with a picture, and because `_rebuild_contents_page` skips
    chapters by matching literal strings, no entry in `CONTENTS_SKIP_TITLES`
    could match it: the cover came back as a contents entry. Worse, a title is
    emitted as a heading chunk without token resolution, so both copies reached
    the reader as raw control characters — 116 of them across the corpus, and a
    device crashed paging over them. (#133)

    Strip rather than reject the whole block. An image sits beside real words
    often enough that rejecting on sight would answer #133 by discarding good
    titles: `<h2><img/>Preface</h2>` is one block, and the chapter is called
    Preface. What is left after stripping is the text a reader sees, so the
    length and newline bounds are measured against that rather than against the
    token's overhead."""
    if head_blocks:
        t = _IMG_TOKEN_RE.sub("", head_blocks[0].get("text") or "").strip()
        if 0 < len(t) <= _LEADING_TITLE_MAX_LEN and "\n" not in t:
            return t
    return LEADING_TITLE_FALLBACK


def _is_file_name_label(label, href):
    """Whether a TOC label is only its target file's name, with or without
    its extension, letters and digits both: "split_003" for
    split_003.xhtml, left by a conversion that
    had no titles (#285). A word such as "Copyright" on Copyright.xhtml is a
    real name, and so is a number: "12" on 12.xhtml is chapter 12."""
    name = posixpath.basename(_normalize_href(href) or "")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    label = (label or "").strip()
    return (
        bool(stem)
        and label.lower() in (stem.lower(), name.lower())
        and any(c.isdigit() for c in label)
        and any(c.isalpha() for c in label)
    )


def _retitle_from_heading(ch):
    """A chapter whose TOC label is only a file name (#285): never print the
    label; name the chapter after its first heading when it opens with one,
    as a chapter with no TOC entry is (#304), else keep the label for the
    navigation pane, since an entry with a poor name still beats none
    (#143)."""
    ch["_omit_title_heading"] = True
    first = (ch.get("blocks") or [{}])[0]
    heading = first.get("heading") and " ".join((first.get("text") or "").split())
    if heading:
        ch["title"] = heading
        # The title is the file's: front-matter rules keyed on titles don't
        # apply to it.
        ch["_from_spine"] = True


def _entry_key(spine_item, frag):
    """The anchor key a contents entry into `spine_item` names, or None for
    one naming the whole file. An id the file doesn't declare (the entry was
    snapped to a block) goes on that block, so the entry still lands. (#363)"""
    if not frag:
        return None
    doc = _key_doc(spine_item["href"], "")
    return f"{doc}#{frag}"


#: Pages kfxgen replaces or restyles by their contents title: a section of
#: their own each, so nothing else is replaced with them (#363).
def _rewritten_page(title):
    t = _normalize_title(title)
    return (
        t in CONTENTS_SKIP_TITLES or t in SMALL_TEXT_CHAPTERS or t in _CONTENTS_TITLES
    )


def _section_heads(coords, flat, spine_items, nav_flat, file_offset):
    """Which contents entries start a section (#363).

    An entry starts one at the start of its file, or where the book breaks
    the page between the entry before it and its own block. Any other entry
    is a place in the section before it, still listed in the contents, as a
    browser keeps a title page's lines on one page.

    Every entry starts one when the file's CSS wasn't read (no stylizer), or
    when kfxgen rewrites the section: a page it replaces by title, and one
    holding a contents listing it rebuilds (#132). Text merged into those
    would be lost with them."""
    heads = []
    for k, (fi, si, title, _key, _level) in enumerate(coords):
        if (
            k == 0
            or coords[k - 1][1] != si
            or not spine_items[si].get("breaks_known")
            or _rewritten_page(title)
            or _rewritten_page(coords[k - 1][2])
        ):
            heads.append(True)
            continue
        prev = coords[k - 1][0]
        heads.append(any(b.get("break_before") for b in flat[prev + 1 : fi + 1]))
    # A merged section holding a listing splits at every entry again.
    starts = [k for k, h in enumerate(heads) if h] + [len(coords)]
    for a, b in zip(starts, starts[1:]):
        if b - a < 2:
            continue
        lo, si = coords[a][0], coords[a][1]
        hi = (
            coords[b][0]
            if b < len(coords)
            else file_offset[si] + len(spine_items[si].get("blocks") or [])
        )
        if any(lo <= i < hi for i in nav_flat):
            heads[a:b] = [True] * (b - a)
    return heads


def _assemble_chapters_by_coordinate(
    spine_items_ordered, toc_entries, log, cover_href=None
):
    """Resolve each TOC entry to a (spine_index, block_index) coordinate and
    slice content between consecutive coordinates into chapters. Returns None
    when no TOC entry resolves to a spine item (caller falls back).

    `cover_href` names the cover image, so that a page showing nothing but
    the cover — which the cover chapter already shows — is not repeated."""
    spine_blocks = [s.get("blocks") or [] for s in spine_items_ordered]
    spine_anchor = [_anchor_block_index(b) for b in spine_blocks]
    spine_order = [_normalize_href(s["href"]) for s in spine_items_ordered]

    file_offset = []
    acc = 0
    for b in spine_blocks:
        file_offset.append(acc)
        acc += len(b)

    flat = []
    for b in spine_blocks:
        flat.extend(b)

    # Collect all anchor fragments referenced in the TOC (even non-monotonic ones)
    # so we can detect head blocks that belong to a skipped TOC entry.
    toc_anchors: set[str] = set()
    for _e in toc_entries:
        _f = _href_fragment(_e["href"])
        if _f:
            toc_anchors.add(_f)

    # (flat_index, spine_index, title, anchor key or None, contents level)
    coords = []
    last_block_in_file = {}
    prev_flat = -1
    listing_entry = None  # the listing ids the entry before named
    empty_listing = {}  # coord -> its listing's keys, for a page with no blocks (#433)
    same_spot = {}  # coord -> the entry before it, on the same block (#459)
    for entry in toc_entries:
        norm = _normalize_href(entry["href"])
        try:
            si = spine_order.index(norm)
        except ValueError:
            log.warn(f"  TOC entry {entry['title']!r} dropped: not in spine")
            continue
        frag = _href_fragment(entry["href"])
        amap = spine_anchor[si]
        # calibre hands TOC fragments percent-encoded (`fn%3a1`); the anchor
        # map holds the raw id (`fn:1`). (#203)
        if frag and frag not in amap and unquote(frag) in amap:
            frag = unquote(frag)
        note_ids = spine_items_ordered[si].get("note_ids", ())
        if frag and (frag in note_ids or unquote(frag) in note_ids):
            log.info(
                f"  TOC entry {entry['title']!r} points at a footnote, not a "
                "chapter; skipped"
            )
            continue
        if frag and frag in amap:
            bi = amap[frag]
        elif frag:
            bi = last_block_in_file.get(si, -1) + 1
            log.warn(
                f"  TOC anchor #{frag} not found in {norm}; snapping to block {bi}"
            )
        else:
            bi = 0
        bi = min(bi, len(spine_blocks[si]) - 1) if spine_blocks[si] else 0
        fi = file_offset[si] + bi
        names_listing = next(
            (
                ids
                for _, ids in spine_items_ordered[si].get("nav_listing_at") or ()
                if frag and frag in ids
            ),
            None,
        )
        if fi == prev_flat and listing_entry and not names_listing:
            # The entry before named a listing that ends where this one
            # starts: it keeps the listing as a page with no blocks, for the
            # rebuild, rather than taking this entry's (#433). The listing's
            # ids, carried onto this entry's block, are the page's (#436).
            # A second entry into the listing is a place on that page (#459).
            page = len(coords) - 1
            while page in same_spot:
                page = same_spot[page]
            empty_listing[page] = [
                _entry_key(spine_items_ordered[coords[-1][1]], i)
                for i in sorted(listing_entry)
            ]
        elif fi == prev_flat:
            # A second entry on the same block: a place in its twin's
            # section, landing where the twin does (#459).
            same_spot[len(coords)] = len(coords) - 1
        elif fi < prev_flat:
            log.warn(
                f"  TOC entry {entry['title']!r} out of document order; skipping split"
            )
            continue
        key = _entry_key(spine_items_ordered[si], frag)
        if (
            key
            and frag not in amap
            and key not in flat[fi].setdefault("anchor_keys", [])
        ):
            flat[fi]["anchor_keys"].append(key)
            flat[fi].setdefault("anchor_offsets", {})[key] = 0
        coords.append((fi, si, entry["title"], key, entry.get("level", 0)))
        prev_flat = fi
        listing_entry = names_listing
        last_block_in_file[si] = bi

    if not coords:
        return None

    # Flat indices at which a contents listing was discarded, so the chapter
    # covering one can be handed to the contents rebuild (#132).
    # A discarded listing occupies no block, so it records the index its first
    # block *would* have had — that is, the index of the block now following
    # it. A listing at the end of its file records one past the last block,
    # which is also the next chapter's first index; left as-is, the boundary
    # is ambiguous and the chapter *after* the listing gets flagged too. Since
    # the rebuild takes the first heading-sized flagged chapter, that replaces
    # a real short chapter's content with a contents page. Clamp such an index
    # back onto the file's last block so every listing lands strictly inside
    # the chapter that held it.
    #
    # A listing that ends where the next chapter starts records that chapter's
    # first index too. It belongs to that chapter only when the chapter's TOC
    # entry names the listing itself or an id inside it (`<div id="toc"
    # class="toc">`); otherwise it belongs to the chapter holding the block
    # before it. A listing at the start of its file stays with that file. (#276)
    starts = {}
    for fi, si, _title, _key, _level in coords:
        starts[fi] = si
    toc_frag_at = {}
    for entry in toc_entries:
        frag = _href_fragment(entry["href"])
        if frag:
            toc_frag_at.setdefault(_normalize_href(entry["href"]), set()).update(
                (frag, unquote(frag))
            )
    nav_flat = set()
    # Where each listing actually sat: the index of the block after it, before
    # the clamping above. What is printed above it is kept (#369).
    listing_at = {}
    for si, s_item in enumerate(spine_items_ordered):
        last = len(spine_blocks[si]) - 1
        if last < 0:
            continue
        frags_here = toc_frag_at.get(_normalize_href(s_item["href"]), set())
        for local, ids in s_item.get("nav_listing_at") or ():
            fi = file_offset[si] + min(local, last)
            raw = file_offset[si] + local
            if fi in starts and 0 < local <= last and not (ids & frags_here):
                fi -= 1
            nav_flat.add(fi)
            listing_at.setdefault(fi, raw)

    def _flag_nav(ch, start, end):
        """Mark a chapter that held a discarded listing. Half-open, matching
        the slice the chapter was built from."""
        here = [listing_at[i] for i in nav_flat if start <= i < end]
        if ch and here:
            ch["_had_nav_listing"] = True
            # The first listing's place in the chapter's blocks (#369).
            ch["_nav_listing_at"] = max(0, min(here) - start)
        return ch

    def _mk(title, block_slice):
        text = "\n\n".join(b["text"] for b in block_slice if b.get("text"))
        # A slice showing nothing but the cover — the EPUB's own cover page,
        # listed in the TOC or not — is already shown by the cover chapter
        # (#32). As a chapter of its own it would be a blank page: the
        # generator resolves the cover image only through that chapter.
        if not _page_shows_something(text, cover_href):
            return None
        return {"title": title, "text": text, "blocks": list(block_slice)}

    chapters = []

    first_fi = coords[0][0]
    # A head block that carries an anchor referenced (even non-monotonically) in
    # the TOC is NOT front matter — fold it into the first chapter instead.
    # NOTE: Known limitation — a malformed EPUB where genuine front matter
    # carries an `id` that duplicates a TOC fragment would be folded into
    # chapter 1 rather than emitted as a separate leading chapter.
    head_has_toc_anchor = first_fi > 0 and any(
        a in toc_anchors for b in flat[0:first_fi] for a in (b.get("anchor_ids") or [])
    )
    if first_fi > 0 and not head_has_toc_anchor:
        head = flat[0:first_fi]
        head_text = "\n\n".join(b["text"] for b in head if b.get("text"))
        if not _page_shows_something(head_text, cover_href):
            log.info("  Skipping cover-only head before first TOC anchor")
        else:
            ch = _flag_nav(_mk(_leading_chapter_title(head), head), 0, first_fi)
            if ch:
                if ch["title"] == LEADING_TITLE_FALLBACK:
                    # No title of its own, and not in the source TOC either —
                    # this chapter exists precisely because these blocks
                    # precede the first TOC anchor. Listing an invented label
                    # adds navigation the publisher chose not to provide. Only
                    # the nav entry goes; the content still ships (#143).
                    #
                    # Narrow on purpose: a head whose first block *does* read
                    # as a heading keeps its entry. That label is the book's
                    # own words rather than ours, and dropping it would remove
                    # a usable destination to fix a naming problem.
                    ch["_omit_from_toc"] = True
                    # Invented label, not the book's own words — suppress it
                    # as a heading so it cannot print onto the page. Set only
                    # on this synthetic path: a book whose own TOC names a
                    # chapter "Front Matter" comes through `coords` and keeps
                    # its heading. (#133)
                    ch["_omit_title_heading"] = True
                chapters.append(ch)

    heads = _section_heads(coords, flat, spine_items_ordered, nav_flat, file_offset)
    for k in same_spot:
        heads[k] = False
    for k, (fi, si, title, _key, _level) in enumerate(coords):
        if not heads[k]:
            continue
        nxt = next((j for j in range(k + 1, len(coords)) if heads[j]), None)
        if nxt is not None:
            end = coords[nxt][0]
        else:
            # The entries after it are all in its file.
            end = file_offset[si] + len(spine_blocks[si])
        start = 0 if (k == 0 and head_has_toc_anchor) else fi
        if k in empty_listing:
            ch = {
                "title": title,
                "text": "",
                "blocks": [],
                "_had_nav_listing": True,
                "_listing_keys": empty_listing[k],
            }
        else:
            ch = _flag_nav(_mk(title, flat[start:end]), start, end)
        if ch:
            if _is_file_name_label(title, spine_items_ordered[si]["href"]):
                _retitle_from_heading(ch)
            # Its depth in the book's contents, for the nesting (#313).
            ch["toc_level"] = coords[k][4]
            subs = []
            for j in range(k + 1, nxt if nxt is not None else len(coords)):
                twin = j
                while twin in same_spot:
                    twin = same_spot[twin]
                # On the section's own block: it opens the section (None).
                key = None if twin == k else coords[twin][3]
                subs.append({"title": coords[j][2], "key": key, "level": coords[j][4]})
            if subs:
                ch["sub_entries"] = subs
            chapters.append(ch)

    last_si = coords[-1][1]
    # Tail orphans use filename-stem titles; TOC titles drove the inline splits
    # above so they are not available to reassign here (intentional).
    for si in range(last_si + 1, len(spine_items_ordered)):
        item = spine_items_ordered[si]
        if not _page_shows_something(item["text"], cover_href):
            log.info(f"  Skipping cover-only orphan {_normalize_href(item['href'])}")
            continue
        norm = _normalize_href(item["href"])
        stem = norm.rsplit(".", 1)[0] if "." in norm else norm
        ch = _flag_nav(
            _mk(stem or f"Section {si + 1}", spine_blocks[si]),
            file_offset[si],
            file_offset[si] + len(spine_blocks[si]),
        )
        if ch:
            # Same rule as the head above, and the reason the filename title
            # is tolerable: these spine items sit past the last TOC coordinate,
            # so the source never listed them. Reviewing real books, what lands
            # here is back matter the publisher deliberately left out of its
            # own TOC — a note to the reader, a "stay in touch" page, an image
            # wrapper. One corpus book was 835 of 994 nav entries this way,
            # each an 85-character filename (#143).
            ch["_omit_from_toc"] = True
            # The filename title is ours, not the book's: never print it as
            # the page's heading either, as for the head above (#133, #275).
            ch["_omit_title_heading"] = True
            chapters.append(ch)

    return chapters


def extract_chapters_from_oeb(
    oeb_book,
    log,
    metadata=None,
    cover_href=None,
    native_tables=False,
    keep_nav_listings=False,
):
    """
    Extract structured chapters from OEB book by mapping TOC to spine items.

    Produces a list of {'title': str, 'text': str} dicts suitable for
    NativeKFXGenerator.generate_full_book(chapters=...).

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger
        metadata: Optional dict with 'title' and 'author'; front-matter rules run only with a title
        cover_href: Manifest href of the cover image, when one was found
        native_tables: Write eligible tables as one table block each (#219)
        keep_nav_listings: Print contents listings rather than drop them;
            set when a first pass finds the book gets no Contents page (#335)

    Returns:
        list: List of chapter dicts with 'title' and 'text' keys
    """
    # Build spine item map: normalized href -> text
    spine_map = {}
    spine_items_ordered = []
    table_blocks = []  # (href, first block) per table, for the #219 warning
    notes_blocks = []  # (href, first block) per notes table (#268)
    # Every in-book link target, before any chapter is walked: whether a
    # table is a notes section depends on links from other files (#268).
    link_targets = _book_link_targets(oeb_book) if native_tables else None
    # Only a TOC calibre built itself lists the book's links (#225).
    note_pairs = (
        _note_pair_ids(oeb_book)
        if getattr(oeb_book, "auto_generated_toc", False)
        else {}
    )

    toc_entries = _extract_toc_with_hrefs(oeb_book, log)
    # Fragment ids the TOC names, per file, matched the way chapter assembly
    # matches them, so a table holding one past its start keeps rows. (#219)
    toc_targets = {}
    for entry in toc_entries:
        frag = _href_fragment(entry["href"])
        if frag:
            toc_targets.setdefault(_normalize_href(entry["href"]), set()).update(
                (frag, unquote(frag))
            )

    log.info(f"Processing {len(oeb_book.spine)} spine items...")

    # Keys of spine files that show nothing (only `<div id="e"></div>`): the
    # file's own key and its ids. Without a block they went nowhere and links
    # to them were plain text; they go on the next shown block (#278). If
    # that block is later dropped (a "Contents", title or half-title page's
    # first block), they are lost with it: 2 of 66 such keys in the library
    # sample, with no link to either.
    orphan_keys = []
    svg_text_files = 0  # files whose SVG draws words, said once (#231)

    for i, item in enumerate(_spine_in_order(oeb_book, toc_entries)):
        # Per-item try/except (#73): a single bad item logs a warning and
        # the loop continues. The previous shape wrapped the whole loop in
        # a bare except Exception, which aborted iteration on the first
        # parse error and silently lost every subsequent item.
        #
        # Scope covers the `.data` access too: some OEB-shaped sources
        # (e.g. our test EpubAsOeb shim) parse XHTML eagerly on .data
        # access, so a malformed-XHTML spine item raises before we ever
        # call extract_text_from_html.
        try:
            if not hasattr(item, "data") or item.data is None:
                continue
            if _is_calibre_inline_toc(item.data):
                href_for_log = getattr(item, "href", "") or "<unknown>"
                log.info(f"  Skipping calibre's generated inline TOC ({href_for_log})")
                continue
            resolver = _build_style_resolver(oeb_book, item, log)
            nav_listing_at = []
            tables_seen = []
            notes_seen = []
            trailing = []
            item_href = getattr(item, "href", "") or ""
            note_ids = _note_target_ids(item.data) | note_pairs.get(
                _key_doc(item_href, "") if item_href else "", set()
            )
            blocks = extract_blocks_from_html(
                item.data,
                style_resolver=resolver,
                base_href=getattr(item, "href", "") or "",
                nav_listing_at=nav_listing_at,
                tables_seen=tables_seen,
                native_tables=native_tables,
                link_targets=link_targets,
                notes_seen=notes_seen,
                keep_nav_listings=keep_nav_listings,
                trailing_ids=trailing,
                # Assembly skips entries naming a footnote; so does this.
                toc_targets=toc_targets.get(
                    _normalize_href(getattr(item, "href", "") or ""), set()
                )
                - note_ids,
            )
            text = "\n\n".join(b["text"] for b in blocks if b.get("text"))
            svg_text_files += any(
                _svg_text_paragraphs(e)
                for e in item.data.iter()
                if _local_tag(e.tag) == "svg"
            )
        except Exception as e:
            href_for_log = getattr(item, "href", "") or "<unknown>"
            log.warn(f"  Spine item {i + 1} parse failed ({href_for_log}): {e}")
            continue

        if not text or len(text.strip()) == 0:
            empty_href = getattr(item, "href", "") or ""
            doc_key = _key_doc(empty_href, "") if empty_href else ""
            if doc_key:
                orphan_keys.append(doc_key)
                orphan_keys.extend(
                    f"{doc_key}#{aid}"
                    for elem in item.data.iter()
                    if isinstance(elem.tag, str)
                    for aid in _own_anchor_ids(elem)
                )
            continue

        if orphan_keys:
            _prepend_keys(blocks[0], orphan_keys)
            orphan_keys = []

        # Get the href for this spine item
        href = getattr(item, "href", "") or ""
        norm_href = _normalize_href(href)

        spine_map[norm_href] = text
        spine_map[href] = text
        spine_items_ordered.append(
            {
                "href": href,
                "text": text,
                "blocks": blocks,
                "nav_listing_at": nav_listing_at,
                "note_ids": note_ids,
                # Where the pages break is known only from the CSS (#363).
                "breaks_known": resolver is not None,
            }
        )
        table_blocks.extend((href, b) for b in tables_seen)
        notes_blocks.extend((href, b) for b in notes_seen)
        log.info(f"  Spine item {i + 1}: {len(text)} chars ({norm_href})")
        if trailing and href:
            # Ids at the file's very end name what follows: Gutenberg ends a
            # file with the next chapter's <p><a id="link2HCH0001"></a></p>,
            # and its link opened the end of the chapter before (#338). Their
            # link keys go to the next shown block, as an empty file's do;
            # the ids stay here for chapter assembly. With nothing after,
            # the book's last block takes them back.
            moved = [f"{_key_doc(href, '')}#{aid}" for aid in trailing]
            _drop_keys(blocks, moved)
            orphan_keys.extend(moved)

    if svg_text_files:
        log.warn(
            f"  Words drawn in SVG pictures in {svg_text_files} "
            f"file{'s' if svg_text_files != 1 else ''} are written after the "
            "picture, not over it (#231)"
        )

    if orphan_keys and spine_items_ordered:
        # Nothing shown followed: the book's last block takes them.
        last = spine_items_ordered[-1]["blocks"][-1]
        last["anchor_keys"] = _dedupe_keep_order(
            list(last.get("anchor_keys") or []) + orphan_keys
        )
        last.setdefault("anchor_offsets", {}).update(
            {k: len(last.get("text") or "") for k in orphan_keys}
        )

    if not spine_items_ordered:
        # Raise instead of returning a "No content extracted." sentinel (#72).
        # The sentinel was non-empty so it bypassed generate_full_book's
        # documented "Raises ValueError if empty or None" validation,
        # producing silent success for inputs that had nothing convertible.
        # Calibre's plugin runtime surfaces ValueError as a conversion error.
        raise ValueError(
            "No spine items with extractable text — EPUB has no convertible content"
        )

    if toc_entries:
        chapters = _assemble_chapters_by_coordinate(
            spine_items_ordered, toc_entries, log, cover_href=cover_href
        )
        if chapters:
            log.info(f"Assembled {len(chapters)} chapters from TOC coordinates")
            if _listing_without_contents(chapters, keep_nav_listings):
                return _with_listings_kept(
                    oeb_book, log, metadata, cover_href, native_tables
                )
            _replace_title_page(chapters, metadata, log, keep_nav_listings)
            _warn_flattened_tables(
                chapters, table_blocks, log, native_tables, notes_blocks
            )
            return chapters
        log.info("TOC produced no chapters; using spine items as chapters")

    # Fallback: use each spine item as a chapter. A file that opens with a
    # heading is titled by it, so the TOC names the chapter as the book does;
    # any other is "Section N". Neither title is printed: the first is
    # already on the page and the second is no part of the book (#304).
    chapters = []
    for i, item in enumerate(spine_items_ordered):
        first = (item.get("blocks") or [{}])[0]
        heading = first.get("heading") and " ".join((first.get("text") or "").split())
        chapter = {"title": heading or f"Section {i + 1}", "text": item["text"]}
        # The title is the file's, not a TOC label: front-matter rules keyed
        # on titles ("Contents", "Title Page") don't apply to it.
        chapter["_from_spine"] = True
        # The book's own heading prints, as written: its italics, its CSS and
        # its line breaks, which a title printed by kfxgen would lose. The
        # title only names the chapter in the TOC, on one line.
        chapter["_omit_title_heading"] = True
        if item.get("blocks"):
            chapter["blocks"] = item["blocks"]
        if item.get("nav_listing_at"):
            chapter["_had_nav_listing"] = True
        chapters.append(chapter)

    log.info(f"Using {len(chapters)} spine items as chapters (no TOC mapping)")
    if _listing_without_contents(chapters, keep_nav_listings):
        return _with_listings_kept(oeb_book, log, metadata, cover_href, native_tables)
    _replace_title_page(chapters, metadata, log, keep_nav_listings)
    _warn_flattened_tables(chapters, table_blocks, log, native_tables, notes_blocks)
    return chapters


def _listing_without_contents(chapters, kept):
    """Whether the book dropped a contents listing but will get no Contents
    page: none titled so, and no page left heading-sized to be rebuilt as
    one (#335). pg2701 prints its listing on the title page with the title
    and byline; dropping it left the book with no contents in the text."""
    if kept or not any(ch.get("_had_nav_listing") for ch in chapters):
        return False
    titled = any(
        _normalize_title(c["title"]) in _CONTENTS_TITLES and not c.get("_from_spine")
        for c in chapters
    )
    return not titled and _nav_listing_contents_chapter(chapters) is None


def _with_listings_kept(oeb_book, log, metadata, cover_href, native_tables):
    """The chapters again, with the book's own contents listings printed and
    their links working, for a book that gets no Contents page (#335)."""
    log.info("  No Contents page to rebuild: keeping the book's own listing")
    return extract_chapters_from_oeb(
        oeb_book,
        log,
        metadata=metadata,
        cover_href=cover_href,
        native_tables=native_tables,
        keep_nav_listings=True,
    )


def _container_parser(oeb_book):
    """A function parsing a file of the folder calibre unpacked the book
    into, or None when there is no folder. It returns None for a file out of
    the folder (a `..` path or a link) or one that can't be read."""
    root = getattr(getattr(oeb_book, "container", None), "rootdir", None)
    if not root:
        return None
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = os.path.realpath(root)

    def parse(path):
        full = os.path.realpath(os.path.join(root, path))
        try:
            if os.path.commonpath([root, full]) != root:
                _security_log.warning("rejected path outside the book: %r", path)
                return None
            return etree.parse(full, parser)
        except (OSError, ValueError, etree.Error):
            return None

    return parse


def _spine_in_order(oeb_book, toc_entries):
    """calibre's spine, with the files it added in TOC order (#253). When a
    book's spine leaves out files its pages link to, calibre appends them in
    a different order on every conversion, as non-linear files after all the
    book's own. Those of them its own OPF spine doesn't list are the added
    ones. Unchanged when that OPF can't be read."""
    spine = list(oeb_book.spine)
    parse = _container_parser(oeb_book)
    name = getattr(oeb_book.container, "opfname", None) if parse else None
    opf = parse(name) if name else None
    if opf is None:
        return spine

    def file(href):
        # Names only, compared: an OPF in a subfolder may write "../Text/x".
        return posixpath.basename(unquote((href or "").split("#")[0]))

    hrefs = {
        i.get("id"): i.get("href")
        for i in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']")
    }
    listed = {
        file(hrefs.get(r.get("idref")))
        for r in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']")
    }
    toc = {}
    for n, entry in enumerate(toc_entries):
        toc.setdefault(file(entry.get("href")), n)
    tail = len(spine)
    while tail and not getattr(spine[tail - 1], "linear", True):
        tail -= 1
    own = spine[:tail] + [i for i in spine[tail:] if file(i.href) in listed]

    # A link written "C2.XHTML" for c2.xhtml makes calibre, on a file system
    # that ignores case, add the same file again: it is left out (#476).
    # Two added files differing only in case are one too: the first in the
    # sorted order is kept, so the same one every time (#506 QA).
    def folded(i):
        return unquote((i.href or "").split("#")[0]).lower()

    seen = {folded(i) for i in own}
    added = []
    for i in sorted(
        (i for i in spine[tail:] if file(i.href) not in listed),
        key=lambda i: (toc.get(file(i.href), len(toc)), file(i.href)),
    ):
        if folded(i) not in seen:
            seen.add(folded(i))
            added.append(i)
    return own + added


def _has_media_overlays(oeb_book):
    """Whether the book pairs its pages with read-aloud narration: a
    `media-overlay` link on a manifest item of its own OPF. calibre's
    manifest drops those links and the SMIL files, so this reads the OPF
    calibre unpacked into the container folder (#248)."""
    parse = _container_parser(oeb_book)
    if parse is None:
        return False
    container = parse(os.path.join("META-INF", "container.xml"))
    if container is None:
        return False
    # Each OPF is read on its own: one missing or broken doesn't hide the next.
    for full_path in container.xpath("//*[local-name()='rootfile']/@full-path"):
        opf = parse(full_path)
        if opf is not None and opf.xpath("//*[local-name()='item'][@media-overlay]"):
            return True
    return False


def _warn_media(oeb_book, log):
    """Say once per book that its narration, audio and video are left out:
    kfxgen writes neither (#248). A clip's fallback text is written."""
    clips = files = 0
    for item in getattr(oeb_book, "spine", None) or ():
        # A page that doesn't parse is skipped, as in extraction (#73).
        try:
            n = sum(
                isinstance(e.tag, str) and _local_tag(e.tag) in ("audio", "video")
                for e in item.data.iter()
            )
        except Exception:
            continue
        clips += n
        files += bool(n)
    what = []
    if _has_media_overlays(oeb_book):
        what.append("this book's read-aloud narration")
    if clips:
        what.append(
            f"{clips} audio or video clip{'s' if clips != 1 else ''} "
            f"in {files} file{'s' if files != 1 else ''}"
        )
    if what:
        log.warn(
            "  Left out, since kfxgen writes no audio or video: "
            + " and ".join(what)
            + ". The text and pictures are converted"
            + (", with each clip's fallback text" if clips else "")
            + " (#248)"
        )


def _warn_flattened_tables(chapters, table_blocks, log, native_tables, notes_blocks=()):
    """Say once per book how many tables were written as rows of text (#219),
    and why: native tables were turned off, or these tables fell back.

    Counted against the final chapters, not at extraction: a contents page, a
    title page or a cover-only page is dropped after its blocks were extracted,
    and many books print their contents listing as a table. Counting those
    warned about tables the book never contained — in 28 of 63 corpus books,
    every counted table had been discarded. A table counts when its first
    block is still in a chapter.
    """
    kept = {id(b) for ch in chapters for b in ch.get("blocks") or ()}
    written = [href for href, block in table_blocks if id(block) in kept]
    notes = sum(1 for _, block in notes_blocks if id(block) in kept)
    if notes:
        log.info(
            f"  {notes} notes table{'s' if notes != 1 else ''} written as one "
            "paragraph per note (#268)"
        )
    native = sum(
        1 for ch in chapters for b in ch.get("blocks") or () if b.get("type") == "table"
    )
    if native:
        s = "s" if native != 1 else ""
        log.info(f"  {native} table{s} written as native Kindle table{s} (#219)")
    if not written:
        return
    # Said once per book, not per table: a book with tables usually has
    # dozens.
    n, f = len(written), len(set(written))
    if not native_tables:
        why = "native tables are turned off"
    elif n == 1:
        why = (
            "it could not be laid out as a Kindle table (for example over 24 columns, or an "
            "image or a nested table in a cell)"
        )
    else:
        why = (
            "they could not be laid out as Kindle tables (for example over 24 columns, or an "
            "image or a nested table in a cell)"
        )
    log.warn(
        f"  {n} table{'s' if n != 1 else ''} in {f} file{'s' if f != 1 else ''} "
        f"written as one paragraph per row, so columns do not line up: {why} (#219)"
    )


# Chapter titles come from a book's TOC, where a label is routinely typeset
# with trailing punctuation ("CONTENTS.") or wrapped in brackets. Every lookup
# below is exact membership against a set of literal strings, so a single such
# character defeats it: one corpus book printed its entire 65-block contents
# listing into the body because "contents." is not "contents". #107 patched the
# same failure mode one string at a time. Normalise once, here, and route every
# set lookup through it.
#
# Edge-of-string only, deliberately. Interior punctuation is part of the label,
# and stripping it could turn a distinct chapter title into a matching one —
# a title that merely *contains* a label must not start behaving like it. (#135)
_TITLE_EDGE_CHARS = string.punctuation + string.whitespace


def _normalize_title(title):
    """Canonical form of a chapter title, for label-set lookups.

    Lowercases, collapses internal whitespace, and strips punctuation and
    whitespace from both ends. (#135)"""
    return " ".join((title or "").split()).strip(_TITLE_EDGE_CHARS).lower()


SMALL_TEXT_CHAPTERS = {
    "copyright",
    "copyright page",
    "also by",
    "also by the author",
    "about the author",
    "about the authors",
    "dedication",
    "epigraph",
    "acknowledgments",
    "acknowledgements",
    "colophon",
    "credits",
}

SMALL_FONT_SIZE = 0.75

#: TOC labels that denote a contents listing of the book's own.
_CONTENTS_TITLES = frozenset({"contents", "table of contents"})

#: Title given to a contents page rebuilt from markup rather than from a
#: recognised chapter title, replacing whatever the source called the listing.
CONTENTS_PAGE_TITLE = "Contents"

# TOC labels that denote the full title page (book title + author).
TITLE_PAGE_TITLES = frozenset({"title page", "title"})

# TOC labels that denote a half-title (a.k.a. bastard title) page. Print
# convention shows ONLY the book title — no author, no subtitle. The
# label itself ("Half Title Page") is structural navigation metadata and
# must never render as visible heading text; one observed book leaked the
# literal words "Half Title Page" onto the page because this set did not
# recognise the variant. Keep the spelling variants in sync with
# CONTENTS_SKIP_TITLES below. (#107)
HALF_TITLE_TITLES = frozenset(
    {
        "half title",
        "half-title",
        "half title page",
        "half-title page",
        "halftitle",
        "halftitle page",
        "bastard title",
    }
)


#: A chapter that held a discarded listing is treated as the book's contents
#: page only when what remains of it is heading-sized. Anything longer is a
#: page that merely *carried* a listing, and its text is content that must
#: never be overwritten by a generated one. Reuses the leading-title bound,
#: which draws the same heading-versus-prose line. (#132)
_NAV_REMNANT_MAX_LEN = _LEADING_TITLE_MAX_LEN


def _nav_listing_contents_chapter(chapters):
    """The chapter whose discarded listing should become the contents page.

    None when the book already builds one from a titled chapter — a book must
    not end up with two — or when no flagged chapter is heading-sized.
    """
    if any(
        _normalize_title(c["title"]) in _CONTENTS_TITLES and not c.get("_from_spine")
        for c in chapters
    ):
        return None
    for ch in chapters:
        if not ch.get("_had_nav_listing"):
            continue
        remnant = _IMG_TOKEN_RE.sub("", ch.get("text") or "").strip()
        if len(remnant) > _NAV_REMNANT_MAX_LEN:
            continue
        # At most one block of its own, its heading. A short page of two or
        # more is something else that also carried the listing: pg2160 and
        # pg2701 print theirs on the title page, whose title and byline the
        # rebuild would replace (#252/#276 review). A box counts its own
        # paragraphs: a part page's heading and subtitle in one shaded box
        # are still two (#319).
        if _count_text_blocks(ch.get("blocks") or ()) <= 1:
            return ch
    return None


#: What a listing's own heading says, which kfxgen's Contents page replaces:
#: the titles it rebuilds by, a navigation document's label, and the usual
#: word for it in other languages. Anything else above a listing is the
#: book's text and is kept (#369).
_LISTING_HEADINGS = _CONTENTS_TITLES | frozenset(
    {
        "navigation",
        "toc",
        "inhalt",
        "inhaltsverzeichnis",
        "table des matières",
        "sommaire",
        "índice",
        "indice",
        "contenido",
        "contenidos",
        "sumário",
        "inhoud",
        "innehåll",
        "indhold",
        "innhold",
        "sisällys",
        "spis treści",
        "obsah",
        "содержание",
        "оглавление",
        "目次",
        "目录",
        "目錄",
        "차례",
        "목차",
    }
)


def _split_text_above_listing(ch):
    """The text printed above `ch`'s listing, as a chapter of its own, or None.

    The rebuild replaces the chapter holding a listing, whose one paragraph
    is taken to be the listing's heading. When that paragraph sits above the
    listing and isn't a contents heading, it's the book's own text: pg3011's
    title page ends with its date, "1883", which the NCX names and the
    listing follows (#369). It keeps the chapter's contents entry; the
    rebuilt page follows it under its own."""
    blocks = ch.get("blocks") or []
    at = ch.get("_nav_listing_at")
    if not at:
        return None
    above, rest = blocks[:at], blocks[at:]
    shown = [b for b in above if (b.get("text") or "").strip()]
    if not shown or _normalize_title(shown[0]["text"]) in _LISTING_HEADINGS:
        return None
    lead = {
        k: v
        for k, v in ch.items()
        if k not in ("blocks", "text", "_had_nav_listing", "_nav_listing_at")
    }
    lead["blocks"] = above
    lead["text"] = "\n\n".join(b["text"] for b in above if b.get("text"))
    ch["blocks"] = rest
    ch["text"] = "\n\n".join(b["text"] for b in rest if b.get("text"))
    ch.pop("sub_entries", None)
    return lead


def _split_front_matter_below_listing(ch):
    """The front matter printed after `ch`'s listing, as a chapter of its
    own, or None.

    The rebuild replaces the chapter holding a listing. A dedication or an
    epigraph after the listing with no TOC entry of its own is part of that
    chapter: pg64317's were replaced with it (#393). Only blocks the book
    marks as front matter are kept (`_is_front_matter`): kept as a whole,
    the text after a listing was most often another copy of the contents
    (#389). They follow the rebuilt page, out of the navigation and with no
    heading, as front matter before the first TOC entry is (#143). The rest,
    pictures included (#117), is left for the rebuild."""
    blocks = ch.get("blocks") or []
    at = ch.get("_nav_listing_at")
    if at is None:
        return None
    kept = [b for b in blocks[at:] if b.get("front_matter")]
    if not _count_text_blocks(kept):
        return None
    left = blocks[:at] + [b for b in blocks[at:] if not b.get("front_matter")]
    ch["blocks"] = left
    ch["text"] = "\n\n".join(b["text"] for b in left if b.get("text"))
    return {
        "title": LEADING_TITLE_FALLBACK,
        "text": "\n\n".join(b["text"] for b in kept if b.get("text")),
        "blocks": kept,
        "toc_level": ch.get("toc_level", 0),
        "_omit_from_toc": True,
        "_omit_title_heading": True,
    }


def _count_text_blocks(blocks):
    """Blocks with text, counting each paragraph inside a box (#319) and
    each paragraph in a native table's cells (#330)."""

    def count(b):
        if b.get("type") == "box":
            return _count_text_blocks(b["blocks"])
        if b.get("type") == "table":
            return sum(
                _count_text_blocks(cell.get("paragraphs") or [cell])
                for row in b["table"]["rows"]
                for cell in row["cells"]
            )
        return bool(_IMG_TOKEN_RE.sub("", b.get("text") or "").strip())

    return sum(count(b) for b in blocks)


def _block_keys(ch):
    """The anchor keys of a chapter's blocks, which hold their parts' own."""
    return [k for b in ch.get("blocks") or () for k in b.get("anchor_keys") or ()]


def _keep_illustrations(ch):
    """Move the pictures off a chapter whose text is about to be replaced.

    The contents listing becomes kfxgen's own (#132), because its *text*
    duplicates the navigation KFX carries itself. That is no reason to drop
    what the publisher printed there (#117). Title pages, which were rewritten
    too until #448, are now kept as written.

    Tokens only, never the blocks they came from: the surrounding text is
    exactly what the caller is replacing. `preserved_images` is read outside
    the generator's `toc_links` branch, so it works for a page with no links.
    """
    return [
        m.group(0)
        for b in (ch.get("blocks") or [])
        for m in _IMG_TOKEN_RE.finditer(b.get("text") or "")
    ]


def _replace_title_page(chapters, metadata, log, listings_kept=False):
    """Label title pages, rebuild contents, and set font sizes for front/back matter.

    `listings_kept`: the book keeps its own listing, as it has no Contents
    page to rebuild (#335). A chapter titled "Contents" then got the name
    from the kept listing's own heading (one the TOC named is rebuilt before
    this pass), and its pages after the listing are the book's text: it is
    kept as the book has it (#387)."""
    if not metadata:
        return
    title = metadata.get("title", "")
    if not title:
        return
    # A listing recognised by markup rather than by title still has to become
    # kfxgen's contents page; discarding it alone leaves the book with none
    # (#132). Resolved before the loop so the "already has one" test sees
    # every chapter, not just those visited so far.
    nav_contents_ch = _nav_listing_contents_chapter(chapters)
    if nav_contents_ch is not None:
        lead = _split_text_above_listing(nav_contents_ch)
        if lead is not None:
            chapters.insert(chapters.index(nav_contents_ch), lead)
            log.info(f"  Kept the text above the contents listing: {lead['title']}")
    # Front matter after a listing about to be replaced is split off before
    # any rebuild, whose links name chapters by their place in the list
    # (#393). Only a chapter that dropped a listing has a place for it.
    for ch in list(chapters):
        if ch is nav_contents_ch or _normalize_title(ch["title"]) in _CONTENTS_TITLES:
            after = _split_front_matter_below_listing(ch)
            if after is not None:
                chapters.insert(chapters.index(ch) + 1, after)
                log.info("  Kept the front matter after the contents listing")
    for ch in chapters:
        if ch is nav_contents_ch:
            _rebuild_contents_page(ch, chapters, log)
            ch.pop("blocks", None)
            ch["font_size"] = SMALL_FONT_SIZE
            # Rename rather than suppress. The source label named the listing
            # that was replaced — "Navigation" is structural metadata and must
            # not print (#60/#107) — but omitting the heading instead leaves a
            # bare list of links under no header, where the title-keyed path
            # shows one. The page is now kfxgen's contents page, in the body
            # heading and in the reader's navigation alike, so name it that.
            log.info(f"  Rebuilt contents from listing markup: {ch['title']}")
            ch["title"] = CONTENTS_PAGE_TITLE
            if ch.get("_from_spine"):
                # Named now, so the name prints (#304).
                ch.pop("_omit_title_heading", None)
            continue
        if ch.get("_from_spine"):
            continue
        ch_title = _normalize_title(ch["title"])
        if ch_title in TITLE_PAGE_TITLES | HALF_TITLE_TITLES:
            # Printed as the book has it; only the TOC label ("Title Page")
            # is left off, as structural metadata (#33, #107). Its text used
            # to be replaced with the book's title and author, which deleted
            # whatever else its chapter held, such as a contents listing or a
            # page with no TOC entry of its own (#448).
            ch["_omit_title_heading"] = True
        elif ch_title in _CONTENTS_TITLES and not listings_kept:
            # Rebuild contents page from actual chapter titles
            _rebuild_contents_page(ch, chapters, log)
            ch.pop("blocks", None)
            ch["font_size"] = SMALL_FONT_SIZE

        if ch_title in SMALL_TEXT_CHAPTERS:
            ch["font_size"] = SMALL_FONT_SIZE


# Chapter titles to exclude from the generated contents listing.
# Built from the shared title/half-title sets so a new spelling variant
# only has to be added in one place. (#107)
CONTENTS_SKIP_TITLES = (
    TITLE_PAGE_TITLES
    | HALF_TITLE_TITLES
    | {
        "cover",
        "contents",
        # Invented by _leading_chapter_title, never the book's own text (#133).
        LEADING_TITLE_FALLBACK.lower(),
        "table of contents",
        "copyright",
        "copyright page",
    }
)


def _rebuild_contents_page(contents_ch, all_chapters, log):
    """Rebuild a Contents chapter with underlined, linked entries."""
    toc_links = []
    for i, ch in enumerate(all_chapters):
        if ch is contents_ch:
            # A contents page never lists itself. The title-keyed path got
            # this for free — "contents" is in the skip set — but a listing
            # recognised by markup sits under whatever title the book gave
            # it ("Navigation"), which matches nothing. (#132)
            continue
        ch_lower = _normalize_title(ch["title"])
        if ch_lower in CONTENTS_SKIP_TITLES:
            continue
        if ch.get("_omit_from_toc"):
            # Left out of the navigation pane (#143), so left off this page
            # too: its title is a label kfxgen made up, such as a file name.
            # (#284 review: 835 of pg22210's 992 entries.)
            continue
        toc_links.append(
            {
                "text": ch["title"],
                "target_chapter_idx": i,
                "level": ch.get("toc_level", 0),
            }
        )
        # Places in the section the contents name, after it (#363).
        toc_links.extend(
            {
                "text": sub["title"],
                "target_chapter_idx": i,
                "target_key": sub["key"],
                "level": sub.get("level", 0),
            }
            for sub in ch.get("sub_entries") or ()
        )
    # Indented by depth, counted from the shallowest entry listed: a book's
    # contents often sit under one wrapper entry (its title) that this page
    # leaves out (#313).
    top = min((link["level"] for link in toc_links), default=0)
    for link in toc_links:
        link["level"] -= top

    # Illustrations that happen to sit in this section are content, and are
    # kept even though the text around them is not. The reason to discard a
    # source contents section is that its *text* duplicates the navigation KFX
    # carries itself; that says nothing about pictures printed there. Two
    # corpus books lose decorative plates this way — a plate between the
    # Contents heading and the next heading vanished with the section. (#117)
    #
    # Tokens only, not the blocks they came from: the surrounding text is
    # exactly what this function exists to replace.
    preserved_images = _keep_illustrations(contents_ch)

    # Links into the page kfxgen replaces land on it (#436): the keys of the
    # blocks it drops (its file's, its heading's, the listing's), and those
    # of a listing with no block of its own, from the block they were
    # carried to.
    moved = contents_ch.pop("_listing_keys", [])
    for ch in all_chapters:
        _drop_keys(ch.get("blocks") or (), moved)
    contents_ch["anchor_keys"] = _dedupe_keep_order(_block_keys(contents_ch) + moved)

    # Build display text (header + entries)
    lines = ["Contents"]
    for link in toc_links:
        lines.append(link["text"])
    contents_ch["text"] = "\n\n".join(lines)
    contents_ch.pop("blocks", None)

    # Structured link data for the native generator
    contents_ch["toc_links"] = toc_links
    if preserved_images:
        # A separate key rather than appended to `text`, because the generator
        # ignores `text` entirely once `toc_links` is set and emits one chunk
        # per link — images smuggled into the text would be dropped again,
        # silently and in a harder place to find.
        contents_ch["preserved_images"] = preserved_images
        log.info(
            f"  Kept {len(preserved_images)} illustration(s) from the "
            "discarded contents section"
        )
    log.info(f"  Rebuilt contents page with {len(toc_links)} linked entries")


def _extract_toc_with_hrefs(oeb_book, log):
    """
    Extract TOC entries preserving href targets for chapter mapping.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger

    Returns:
        list: List of {'title': str, 'href': str, 'level': int} dicts
    """
    toc_entries = []

    try:
        if not hasattr(oeb_book, "toc") or not oeb_book.toc:
            log.info("No TOC found in source book")
            return toc_entries

        log.info("Extracting TOC with hrefs...")

        def process_toc_node(node, level=0):
            if hasattr(node, "title") and node.title:
                href = getattr(node, "href", "") or ""
                entry = {"title": str(node.title), "href": href, "level": level}
                toc_entries.append(entry)
                log.info(
                    f"  {'  ' * level}[{level}] {node.title} -> {_normalize_href(href)}"
                )

            for child in node:
                process_toc_node(child, level + 1)

        for node in oeb_book.toc:
            process_toc_node(node)

        if toc_entries:
            log.info(f"Extracted {len(toc_entries)} TOC entries with hrefs")
        else:
            log.info("TOC found but no entries extracted")

    except Exception as e:
        log.error(f"Error extracting TOC: {e}")

    return toc_entries


def extract_images_from_oeb(
    oeb_book, log, exclude_hrefs=None, display_dims=None, own_size=()
):
    """
    Walk OEB manifest for image/* items and return their raw bytes.

    Used by Phase 4 to emit $164 + $417 resource pairs. The cover image is
    typically excluded (handled separately via extract_cover_image), so callers
    pass its href in `exclude_hrefs`.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger
        exclude_hrefs: Optional iterable of normalized hrefs to skip
        display_dims: Optional dict, filled with {href: (width, height)} for
            each SVG drawn at twice its size: the size to show it at (#440)
        own_size: hrefs of SVGs to draw at their own size: those shown in a
            table cell, where a picture has no style and its pixels are its
            size (#441 device check)

    Returns:
        dict: { href: bytes } for every image manifest item that's not excluded
    """
    images = {}
    skipped_unsupported = 0
    converted_gifs = 0
    converted_svgs = 0
    excluded = {_normalize_href(h) for h in (exclude_hrefs or [])}
    try:
        for item in oeb_book.manifest:
            media = (getattr(item, "media_type", "") or "").lower()
            if "image" not in media:
                continue
            data = getattr(item, "data", None)
            href = getattr(item, "href", "") or ""
            if media == "image/svg+xml":
                if not href or _normalize_href(href) in excluded:
                    continue
                # calibre hands an SVG over parsed, not as bytes, so the
                # check below dropped every one without a word (#438).
                raw = getattr(item, "bytes_representation", None)
                scale = 1 if {href, unquote(href)} & set(own_size) else 2
                drawn = svg_to_png(raw, log, scale) if raw else None
                if drawn is None:
                    skipped_unsupported += 1
                    log.warn(f"  Skipping image {href!r}: SVG not rasterised")
                    continue
                log.info(f"  Rasterised SVG to PNG: {href!r}")
                converted_svgs += 1
                images[href], own = drawn
                if own and display_dims is not None:
                    display_dims[href] = own
                continue
            if not isinstance(data, (bytes, bytearray)) or len(data) <= 100:
                continue
            if not href or _normalize_href(href) in excluded:
                continue
            # Pre-filter to formats the generator handles (JPEG, PNG). The
            # generator silently drops unrecognized magic bytes; doing the
            # check here lets us log the skip with the offending href.
            if data[:3] != b"\xff\xd8\xff" and data[:4] != b"\x89PNG":
                # A GIF is re-encoded rather than dropped. Four books in a
                # 226-EPUB library store every page as one and reach the reader
                # with no pictures at all (#177) — the text arrives, the scans
                # do not. GIF has a symbol (`$286`, in the generator's own
                # format comment) but nothing emits it and no device has been
                # asked whether it would render; PNG is lossless like GIF, so
                # nothing is lost in the change, and it is a format this
                # project has watched render on hardware.
                converted = (
                    gif_to_png(bytes(data), log)
                    if bytes(data[:6]) in GIF_SIGNATURES
                    else None
                )
                if converted is None:
                    skipped_unsupported += 1
                    log.warn(
                        f"  Skipping image {href!r}: unsupported format "
                        f"(magic bytes {bytes(data[:4]).hex()}); only JPEG and PNG "
                        f"are emitted as KFX resources"
                    )
                    continue
                log.info(
                    f"  Converted GIF to PNG: {href!r} "
                    f"({len(data):,} -> {len(converted):,} bytes)"
                )
                converted_gifs += 1
                images[href] = converted
                continue
            images[href] = bytes(data)
    except Exception as e:
        log.warn(f"Error walking manifest for images: {e}")
    summary = f"  Extracted {len(images)} body image(s) from manifest"
    if converted_gifs:
        summary += f" ({converted_gifs} GIF converted to PNG)"
    if converted_svgs:
        summary += f" ({converted_svgs} SVG rasterised to PNG)"
    if skipped_unsupported:
        summary += f" ({skipped_unsupported} skipped — unsupported format)"
    log.info(summary)
    return images


def _get_cover_image_data(item, log):
    """Get binary cover-image data from a manifest item.

    Validates JPEG/PNG magic bytes before returning — Calibre identifies
    images by manifest media-type only, so we don't trust the label and
    sniff the actual bytes (#46). Mismatched or unrecognized formats are
    rejected here so garbage never reaches the binary serializer.

    Validation is magic-byte-only by design; structurally invalid JPEGs
    (correct header, corrupt body) flow through and are rejected by Kindle
    at render time.

    `log` is passed explicitly (rather than captured via closure) so this
    helper is independently testable and reusable across discovery paths.
    """
    if not item or not hasattr(item, "data"):
        return None
    data = item.data
    if not isinstance(data, bytes) or len(data) <= 100:
        return None
    if data[:3] != b"\xff\xd8\xff" and data[:4] != b"\x89PNG":
        href = getattr(item, "href", "") or "<unknown>"
        log.warn(
            f"  Skipping cover candidate {href!r}: unsupported format "
            f"(magic bytes {bytes(data[:4]).hex()}); only JPEG and PNG "
            f"are accepted as cover images"
        )
        return None
    return data


def _drop_undecodable(cover, cover_href, images, log):
    """The cover and pictures without those calibre can't decode, each named
    in a warning. Written, they reached the book with no size, or a size
    read from garbage (4294967295 x 4294967295) (#471)."""
    if cover is not None and not decodes(cover):
        log.warn(f"  Skipping the cover {cover_href!r}: it can't be decoded")
        cover = None
    kept = {}
    for href, data in images.items():
        if decodes(data):
            kept[href] = data
        else:
            log.warn(f"  Skipping image {href!r}: it can't be decoded")
    return cover, kept


def extract_cover_image(oeb_book, log):
    """
    Extract cover image binary data from OEB book.

    Calibre's oeb_book.metadata.cover returns a manifest item ID (not an href).
    We need to find the manifest item by ID, then get its image data.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger

    Returns:
        tuple: (bytes, href) or (None, None). The href is needed by the body
        image pipeline to skip the cover (avoid double-emit as $164 cover_img +
        $164 img_N) regardless of which discovery method located it.
    """
    # Method 1: metadata.cover → manifest item ID → image data
    try:
        if oeb_book.metadata.cover:
            cover_id = str(oeb_book.metadata.cover[0])
            log.info(f"  metadata.cover ID: {cover_id}")

            for item in oeb_book.manifest:
                if getattr(item, "id", None) == cover_id:
                    media_type = getattr(item, "media_type", "") or ""
                    if "image" in media_type:
                        data = _get_cover_image_data(item, log)
                        if data:
                            href = getattr(item, "href", "") or ""
                            log.info(
                                f"  Cover image: {len(data):,} bytes from manifest ID '{cover_id}'"
                            )
                            return data, href

            # Also try as href (some books use href in metadata.cover)
            cover_item = oeb_book.manifest.hrefs.get(cover_id)
            if cover_item:
                data = _get_cover_image_data(cover_item, log)
                if data:
                    href = getattr(cover_item, "href", "") or cover_id
                    log.info(
                        f"  Cover image: {len(data):,} bytes from manifest href '{cover_id}'"
                    )
                    return data, href
    except Exception as e:
        log.warn(f"Could not extract cover from metadata: {e}")

    # Method 2: guide entries with type='cover'
    try:
        if hasattr(oeb_book, "guide") and oeb_book.guide:
            # A calibre Guide maps types to references: iterating it gives
            # the type strings, so this found nothing (#495).
            for ref in oeb_book.guide.values():
                ref_type = getattr(ref, "type", "") or ""
                if ref_type.lower() in ("cover", "other.ms-coverimage-standard"):
                    href = getattr(ref, "href", "")
                    if href:
                        item = oeb_book.manifest.hrefs.get(href)
                        data = _get_cover_image_data(item, log)
                        if data:
                            log.info(f"  Cover image: {len(data):,} bytes from guide")
                            return data, href
    except Exception as e:
        log.warn(f"Could not extract cover from guide: {e}")

    # Method 3: scan manifest for items with 'cover' in ID or href + image type
    try:
        for item in oeb_book.manifest:
            item_id = (getattr(item, "id", "") or "").lower()
            item_href = (getattr(item, "href", "") or "").lower()
            media_type = (getattr(item, "media_type", "") or "").lower()
            if "image" in media_type and ("cover" in item_id or "cover" in item_href):
                data = _get_cover_image_data(item, log)
                if data:
                    href = getattr(item, "href", "") or ""
                    log.info(
                        f"  Cover image: {len(data):,} bytes from manifest scan (id={item_id})"
                    )
                    return data, href
    except Exception as e:
        log.warn(f"Could not scan manifest for cover: {e}")

    log.info("  No cover image found")
    return None, None


def _ensure_oeb_opts(oeb_book, opts):
    """Attach the conversion `opts` to the OEB when it lacks them.

    Calibre's OutputFormatPlugin.convert() receives `opts` as a separate
    argument; the OEBBook itself has no `.opts` on this pipeline. Both the
    per-element style resolver (#9) and @font-face extraction (#15) build a
    Stylizer, which needs opts + output_profile, and read them off
    `oeb_book.opts`. Without this, Stylizer construction raises and both
    silently degrade (no block CSS, no embedded fonts). Never overwrite an
    existing `.opts`; tolerate objects that reject attribute assignment.
    """
    if getattr(oeb_book, "opts", None) is not None:
        return
    try:
        oeb_book.opts = opts
    except (AttributeError, TypeError):
        pass


def _font_table_for(oeb_book, opts, log):
    """Build the embedded-font table, or an empty one when the user opted out of
    embedding via the `kfxgen_disable_font_embedding` output option (#15 escape
    hatch).

    Opt-out (default False) rather than a default-on toggle: Calibre renders a
    default-True boolean's checkbox unchecked and inverts its CLI flag, which is
    confusing. An empty FontTable means no `$262`/`$418` fragments, no `$11` on
    `$157` styles, and `override_kindle_font=False` — so the font
    installed/selected on the Kindle is used. Absent option (opts None or
    lacking the attr) embeds, preserving behavior for non-plugin callers.
    """
    from .font_table import FontTable, build_font_table  # noqa: PLC0415

    if getattr(opts, "kfxgen_disable_font_embedding", False):
        log.info("  Font embedding disabled (kfxgen_disable_font_embedding=True)")
        return FontTable([])
    return build_font_table(oeb_book, log)


def convert_oeb_to_kfx(oeb_book, output_path, opts, log):
    """
    Convert Calibre OEB book to KFX format using native generator.

    Args:
        oeb_book: Calibre OEB book object
        output_path: Path to write KFX file
        opts: Conversion options
        log: Calibre logger

    Returns:
        None (writes to output_path)
    """
    from . import __version__ as _kfxgen_version

    # Make the conversion opts reachable via oeb_book.opts so Stylizer-based
    # CSS resolution (#9) and @font-face font extraction (#15) can construct.
    _ensure_oeb_opts(oeb_book, opts)

    log.info("=" * 70)
    log.info(f"kfxgen v{_kfxgen_version} - Native KFX Generator")
    log.info("=" * 70)

    # Extract metadata
    log.info("Extracting metadata...")
    metadata = extract_metadata(oeb_book, log)
    log.info(f"  Title: {metadata['title']}")
    log.info(f"  Author: {metadata['author']}")
    log.info(f"  Language: {metadata['language']}")
    log.info(f"  Publisher: {metadata['publisher']}")
    if metadata["issue_date"]:
        log.info(f"  Date: {metadata['issue_date']}")

    # Extract cover image (and the href it was located at, so we can skip it
    # in body-image extraction regardless of which method found it).
    log.info("Extracting cover image...")
    cover_image, cover_href = extract_cover_image(oeb_book, log)

    # ISSUE-4 INVESTIGATION (image rendering): re-enable body image
    # emission with the new dedicated image style. Diagnostic build only
    # — do not merge until Kindle device-test confirms images render.
    log.info("Extracting body images... (#4 image-rendering investigation)")
    display_dims = {}
    images = extract_images_from_oeb(
        oeb_book,
        log,
        exclude_hrefs=[cover_href] if cover_href else [],
        display_dims=display_dims,
        own_size=_pictures_in_table_cells(oeb_book),
    )

    cover_image, images = _drop_undecodable(cover_image, cover_href, images, log)

    # Optimize over-size images unless the user opted to embed originals (#11).
    if getattr(opts, "kfxgen_embed_original_images", False):
        log.info("  Image optimization disabled (embed original images)")
    else:
        cover_image, images = optimize_images(cover_image, images, log)

    # Extract structured chapters
    log.info("Extracting chapters...")
    native_tables = not getattr(opts, "kfxgen_disable_native_tables", False)
    if not native_tables:
        log.info("  Native tables disabled (kfxgen_disable_native_tables=True)")
    chapters = extract_chapters_from_oeb(
        oeb_book,
        log,
        metadata=metadata,
        cover_href=cover_href,
        native_tables=native_tables,
    )
    _warn_media(oeb_book, log)
    total_chars = sum(len(ch["text"]) for ch in chapters)
    log.info(f"  Chapters: {len(chapters)}")
    log.info(f"  Total content: {total_chars:,} characters")

    # Build embedded-font table (#15), unless the user disabled embedding.
    font_table = _font_table_for(oeb_book, opts, log)

    # Generate KFX
    log.info("Generating KFX file...")
    gen = NativeKFXGenerator()
    gen.generate_full_book(
        title=metadata["title"],
        author=metadata["author"],
        chapters=chapters,
        output_path=output_path,
        cover_image=cover_image,
        images=images,  # ISSUE-4 INVESTIGATION (image rendering)
        display_dims=display_dims,
        footnote_keys=_footnote_keys(oeb_book, log),
        language=metadata["language"],
        publisher=metadata["publisher"],
        issue_date=metadata.get("issue_date"),
        font_table=font_table,
        # Every <img src> went through _resolve_img_src, so a miss means the
        # file is absent, not that the href is spelled differently. (#195)
        resolved_image_refs=True,
    )

    if os.path.isfile(output_path):
        size = os.path.getsize(output_path)
        log.info("=" * 70)
        log.info(f"KFX generated: {output_path} ({size:,} bytes)")
        log.info("=" * 70)
    else:
        raise Exception("KFX generation failed - no output file created")
