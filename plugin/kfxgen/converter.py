"""
KFX Converter - Extract content from OEB and generate KFX

Uses NativeKFXGenerator to produce KFX with working TOC navigation.
"""

import bisect
import collections
import copy
import itertools
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
    cmyk_to_rgb,
    decodes,
    gif_to_png,
    optimize_images,
    svg_to_png,
)
from .inline_style import (
    BIDI_FLAG,
    LANG_FLAG,
    page_bidi,
    FLAG_BOLD,
    FLAG_ITALIC,
    FLAG_NOT_BOLD,
    FLAG_NOT_ITALIC,
    FLAG_PRE,
    FLAG_PRE_LINE,
    FLAG_STRIKE,
    FLAG_SUB,
    FLAG_SUPER,
    FLAG_TEXT_BOTTOM,
    FLAG_TEXT_TOP,
    FLAG_UNDERLINE,
    HARD_BREAK,
    BASELINE_SHIFT,
    LINE_START,
    NOTEREF_FLAG,
    SHIFT_FLAGS,
    _parse_font_family,
    BLACK,
    COLOUR_FLAG,
    LINK_COLOUR_FLAG,
    SPACING_NORMAL,
    TYPOGRAPHY_FLAGS,
    compute_block_style,
    css_font_size,
    css_link_colour,
    css_rgb,
    css_typography,
    flag_value,
    is_transparent,
    is_monospace,
    make_anchor_mark,
    make_bidi_flag,
    EXTERNAL_LINK_PREFIX,
    make_link_flag,
    make_mono_flag,
    make_math_flag,
    make_tcy_flag,
    make_turn_flag,
    make_ruby_flag,
    make_size_flag,
    mono_families,
    nested_shift,
    normalize_runs_with_anchors,
    parse_css_length,
    parse_vertical_align,
    run_font_size,
)
from .math_draw import draw as draw_math
from .native_generator import NativeKFXGenerator
from .page_scale import VERTICAL_PT, HORIZONTAL_PT, em_percent, page_pt, page_width

_ITALIC_TAGS = {"em", "i"}
_BOLD_TAGS = {"strong", "b"}
#: The flag a run set back to normal carries, for each emphasis (#367 review).
_NOT_FLAG = {FLAG_BOLD: FLAG_NOT_BOLD, FLAG_ITALIC: FLAG_NOT_ITALIC}
_SUPER_TAGS = {"sup"}
_SUB_TAGS = {"sub"}


def _nest_shift(cur, inherited, own):
    """`cur` with the shift this element sets (`own`) inside one it inherits
    recorded as a nested one, "sub@2", as Kindle Previewer 4.0.3 layers it on
    the outer run (#536). The outer shifts stay as they were."""
    depth = sum(1 for f in inherited if f in SHIFT_FLAGS or nested_shift(f))
    if not depth or not own:
        return cur
    # Superscript wins over another shift, as it does on one run.
    shift = next(f for f in (*SHIFT_FLAGS, BASELINE_SHIFT) if f in own)
    # Its parent's size, `font-size: 100%`: the layer keeps it (#638).
    size = run_font_size(inherited)
    keeps = "=" if size is not None and run_font_size(cur) == size else ""
    nested = f"{shift}@{depth + 1}{keeps}"
    return (set(cur) - (set(own) - set(inherited))) | {nested}


def _whole_baseline_dropped(parts, depth):
    """`parts` of a shifted element at `depth`, without its children's
    baseline shift when that is all of its text: Kindle Previewer 4.0.3
    layers a baseline over part of a shifted element only, so a note marker
    set back to the baseline inside its <sup> stays a superscript. Each
    element is judged on its own, an exponent beside it apart (QA on #641)."""

    def baseline(flags):
        return next(
            (
                f
                for f in flags
                if (n := nested_shift(f))
                and n[0] == BASELINE_SHIFT
                and n[1] == depth + 1
            ),
            None,
        )

    # A space counts as its text, as Previewer counts it (QA on #641).
    texts = [flags for text, flags in parts if text]
    if not texts or not all(baseline(flags) for flags in texts):
        return parts
    return [
        (text, frozenset(f for f in flags if f != baseline(flags)))
        for text, flags in parts
    ]


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


def _book_height(stylizer, elem):
    """An <hr>'s height as the book sets it, or None. calibre's UA sheet
    gives every <hr> `height: 2px`, which Kindle Previewer, reading the book
    itself, doesn't have; any height the book sets counts there, even 1px
    (QA on #649). calibre rewrites the book's CSS from computed styles
    before an output plugin sees it: a plain <hr> and one the book gives
    2px share one class, so exactly 2px is taken as calibre's."""
    value = stylizer.style(elem).get("height")
    return None if value is None or str(value).strip() == "2px" else value


def _specified_inherited(stylizer, elem, prop):
    """The value `prop` was given on `elem` or its nearest ancestor that sets
    it, as written: CSS inheritance without calibre's computed conversion."""
    while elem is not None:
        value = stylizer.style(elem).get(prop)
        if value and str(value).strip().lower() != "inherit":
            return value
        elem = elem.getparent() if hasattr(elem, "getparent") else None
    return None


#: A `:link` or `:visited` in a selector. Kindle Previewer 4.0.3 reads both
#: as always true: such a rule sets a link's colour as the same rule without
#: them would, by specificity and then order (P733, #733).
_LINK_STATE = re.compile(r":(?:link|visited)(?![\w-])", re.I)
#: The other states and pseudo-elements calibre's Stylizer keeps apart from
#: an element's style. Previewer takes no colour from `:hover`, `:active` or
#: `:focus`, alone or after a `:link` rule (P733 L05, L20, L26).
_OTHER_STATE = re.compile(
    r":{1,2}(?:active|after|before|checked|disabled|enabled|first-letter"
    r"|first-line|focus|focus-visible|focus-within|hover|target|target-within)"
    r"(?![\w-])",
    re.I,
)
#: The text `a:visited` in a selector. Previewer drops any rule whose
#: selector has it, in any position (`a:visited`, `p a:visited`,
#: `a:visited.x`), and keeps `:visited`, `*:visited`, `a.x:visited` and
#: `a[href]:visited` (P741m/n/o, #741 QA). It reads tag names and
#: pseudo-classes in any case but class names as written: `.a:visited`,
#: `#a:visited` and `.xa:visited` are dropped, `.A:VISITED` kept (P741r).
_BARE_A_VISITED = re.compile(r"(?:a|(?<![^\s>+~,(])A):(?i:visited)(?![\w-])")
_INLINE_COLOUR = re.compile(r"(?:^|;)\s*color\s*:", re.I)
_INHERITED_COLOUR = frozenset({"inherit", "currentcolor"})
#: A link's colour from a `:link` or `:visited` rule, as written, set by
#: `mark_link_state_colours` before calibre flattens the CSS (#733).
_SOURCE_LINK_COLOUR = "data-kfxgen-link-colour"


def mark_link_state_colours(root, stylizer, select=None):
    """Record on each link the colour its `:link` or `:visited` rule gives
    it, where that rule wins (#733).

    calibre's Stylizer keeps those rules apart from the element's style, and
    its flattened CSS keeps them as `:link` and `:visited` classes, without
    their place in the cascade. Before flattening, its `rules` are the
    book's, in cascade order: the last colour rule matching a link decides,
    and only a `:link` or `:visited` one is recorded. A colour in the link's
    `style` attribute wins over both (P733 L07, L17), and a link without an
    href is no link (L21). A rule with a bare `a:visited` counts for nothing,
    as Previewer drops it (P741m/n/o). calibre runs the output plugin's
    `specialize_css_for_output` on each file just before flattening it,
    which calls this. `select(selector)` lists the elements a selector
    matches; calibre's own by default."""
    rules = []
    for rule in getattr(stylizer, "rules", None) or ():
        cssdict, text = rule[2], str(rule[3])
        if _BARE_A_VISITED.search(text):
            continue
        if cssdict.get("color") and not _OTHER_STATE.search(text):
            rules.append((text, str(cssdict["color"]), bool(_LINK_STATE.search(text))))
    if not any(state for _, _, state in rules):
        return
    links = [
        e
        for e in root.iter()
        if isinstance(e.tag, str)
        and _local_tag(e.tag) == "a"
        and e.get("href") is not None
        and not _INLINE_COLOUR.search(e.get("style") or "")
    ]
    if not links:
        return
    if select is None:
        # calibre's selector engine, as its Stylizer matches the rules.
        from css_selectors import Select  # noqa: PLC0415

        select = Select(root, ignore_inappropriate_pseudo_classes=True)
    found = {}
    wanted = set(links)
    for text, value, state in rules:
        try:
            matched = wanted.intersection(select(text))
        except Exception:
            continue
        for e in matched:
            found[e] = value if state else None
    for e, value in found.items():
        if value is not None:
            e.set(_SOURCE_LINK_COLOUR, value)


#: On each element of a vertical-rl file (#247): the margin and padding
#: properties the book sets, and the user agent's own, as "prop:value;...".
_V_AUTHOR = "data-kfxgen-v-author"
_V_UA = "data-kfxgen-v-ua"
#: On the first horizontal-tb element of a vertical-rl file's branch (#247).
_HORIZONTAL = "data-kfxgen-horizontal"
#: On the first vertical-rl element inside such a horizontal one (#247,
#: QA's P749 Q09c, probe PSB R01-R07).
_VERTICAL = "data-kfxgen-vertical"
_SIDES = ("top", "right", "bottom", "left")
_BOX_PROPS = tuple(f"{k}-{s}" for k in ("margin", "padding") for s in _SIDES)
_STYLE_DECL = re.compile(
    r"(?:^|;)\s*(margin|padding)(?:-(top|right|bottom|left))?\s*:", re.I
)


def mark_vertical_margins(root, stylizer, select=None):
    """Record, on each element of a vertical-rl file, which of its margins
    and paddings the book sets and what the user agent's are (#247).

    Kindle Previewer 4.0.3 lays them out as a browser does (QA's P747 on
    #747, and P247g): the user agent's margins are logical, before and after
    a block, which in vertical-rl text is right and left, and its list
    padding is at the start of the line, the top; the book's own are
    physical, its margin-top the top. So a book's margin-top is added to a
    <p>'s default space before it, not put in its place, and a book's
    margin-right is the space before it, which collapses with its
    neighbour's. calibre folds the user agent's margins into its own classes
    when it flattens the CSS, so they are told apart here, before: a rule's
    sort key starts 0 for the user agent's and 1 for the book's, and a
    `style` attribute is the book's. calibre runs the output plugin's
    `specialize_css_for_output` on each file just before flattening it,
    which calls this."""
    body = next(
        (
            e
            for e in root.iter()
            if isinstance(e.tag, str) and _local_tag(e.tag) == "body"
        ),
        None,
    )
    if body is None:
        return
    # The body's own, else its <html>'s, which it inherits (P247a, P247d).
    mode = next(
        (
            str(stylizer.style(e).get("writing-mode") or "").strip().lower()
            for e in (body, body.getparent())
            if e is not None and stylizer.style(e).get("writing-mode")
        ),
        "",
    )
    if mode != "vertical-rl":
        return
    if select is None:
        from css_selectors import Select  # noqa: PLC0415

        select = Select(root, ignore_inappropriate_pseudo_classes=True)
    authored, ua = {}, {}
    # In cascade order: a later user agent rule's value wins.
    for rule in getattr(stylizer, "rules", None) or ():
        props = [p for p in _BOX_PROPS if p in rule[2]]
        if not props or not rule[0]:
            continue
        try:
            matched = select(str(rule[3]))
        except Exception:
            continue
        for e in matched:
            if rule[0][0] == 0:
                ua.setdefault(e, {}).update((p, str(rule[2][p])) for p in props)
            else:
                authored.setdefault(e, set()).update(props)
    # Each element's writing mode: its own, else its parent's. vertical-lr,
    # which kfxgen doesn't write, counts as unset (_WRITING_MODES): inside a
    # horizontal block it is horizontal, as in Previewer's (QA's P749 Q09b).
    # A horizontal-tb element and all inside it are laid out horizontally,
    # with physical margins: none of them is marked, and the first of them
    # is marked as the block Previewer wraps in a horizontal container
    # (P247h).
    modes = {body: "vertical-rl"}
    for e in body.iter():
        if not isinstance(e.tag, str):
            continue
        if e is not body:
            own = str(stylizer.style(e).get("writing-mode") or "").strip().lower()
            above = modes.get(e.getparent(), "vertical-rl")
            modes[e] = own if own in _WRITING_MODES else above
            if modes[e] == "horizontal-tb":
                if above != "horizontal-tb":
                    e.set(_HORIZONTAL, "1")
                continue
            if above == "horizontal-tb":
                # Vertical again inside a horizontal block: Previewer writes
                # it in a vertical container of its own (PSB R01-R03).
                e.set(_VERTICAL, "1")
        own = set(authored.get(e, ()))
        for m in _STYLE_DECL.finditer(e.get("style") or ""):
            kind, side = m.group(1).lower(), m.group(2)
            own.update(f"{kind}-{s}" for s in ((side.lower(),) if side else _SIDES))
        e.set(_V_AUTHOR, " ".join(p for p in _BOX_PROPS if p in own))
        e.set(_V_UA, ";".join(f"{p}:{v}" for p, v in (ua.get(e) or {}).items()))


#: In vertical-rl text, the physical side each of kfxgen's slots takes: its
#: top and bottom, the space before and after a block, are the right and
#: left; its left and right, the line's start and end, the top and bottom
#: (#247). The generator writes each slot on that side.
_VERTICAL_SLOTS = {"top": "right", "bottom": "left", "left": "top", "right": "bottom"}


def _vertical_box(css, st, elem):
    """`css`'s margins and paddings, in a vertical-rl file, put in kfxgen's
    slots (#247): each slot takes the book's value of the physical side it
    stands for where the book sets one, else the user agent's own for that
    slot, logical. Unchanged outside a vertical-rl file."""
    authored = elem.get(_V_AUTHOR)
    if authored is None or elem.get(_VERTICAL) is not None:
        # A vertical block inside a horizontal one is laid out in that
        # block: its own margins are physical, as a horizontal block's are
        # (PSB R06: its top and bottom meet the horizontal block's, its left
        # and right stay on it).
        return css
    authored = set(authored.split())
    ua = dict(
        item.split(":", 1) for item in (elem.get(_V_UA) or "").split(";") if ":" in item
    )

    def value(kind, slot):
        side = _VERTICAL_SLOTS[slot]
        if f"{kind}-{side}" in authored:
            return st.get(f"{kind}-{side}")
        return ua.get(f"{kind}-{slot}")

    for slot in _SIDES:
        css[f"margin-{slot}"] = value("margin", slot)
    css["padding-sides"] = tuple(value("padding", slot) for slot in _BORDER_SIDES)
    return css


def _specified_colour(stylizer, elem):
    """`_specified_inherited`'s colour, with a link's `:link` or `:visited`
    colour in place of calibre's where it wins (#733). `inherit` and
    `currentColor` there take the colour around the link (P733 L14, L15)."""
    while elem is not None:
        value = elem.get(_SOURCE_LINK_COLOUR) if hasattr(elem, "get") else None
        if value is None:
            value = stylizer.style(elem).get("color")
            if value and str(value).strip().lower() != "inherit":
                return value
        elif value.strip().lower() not in _INHERITED_COLOUR:
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
    value = stylizer.style(elem).get("font-size")
    size = _calibre_keyword_px(
        stylizer, value, lambda: _root_size_px(stylizer, elem, cache)
    )
    if size is None:
        size = _css_size_px(
            value, inherited, lambda: _root_size_px(stylizer, elem, cache)
        )
    cache[elem] = size
    return size


def _calibre_keyword_px(stylizer, value, root_px):
    """The CSS px of the font-size keyword calibre turned into `value`, as
    Kindle Previewer 4.0.3 sizes it, or None (#729).

    calibre writes `small` as the input profile's size for it over the
    profile's base size, in rem to one decimal: 9 / 12 is 0.8rem
    on the default profile, which kfxgen read as 12.8 px where Previewer
    writes 13 px; `xx-large` came to 1.7rem, 27.2 px, for Previewer's 32.
    A value two keywords share (xx-small and x-small are both 0.8rem on the
    Kindle profiles) and `medium` (1rem, as authors write it) are left as
    they are.

    Only where the root is 16 px, `root_px()`: under a root of another size
    (`html { font-size: 62.5% }`) an author's own 1.1rem is 11 px in
    Previewer, and in QA's library 12 books had such a root and a rem of
    calibre's form against 1 with such a root and a keyword (QA on #731).
    A keyword there keeps calibre's rem."""
    table = _calibre_keyword_table(stylizer)
    text = str(value or "").strip().lower()
    if text not in table or abs(root_px() - _FONT_PX) > 0.01:
        return None
    return table[text]


def _calibre_keyword_table(stylizer):
    """{the rem value calibre writes for a keyword: Previewer's px}, for the
    input profile calibre rewrote keywords with, cached on `stylizer`."""
    table = getattr(stylizer, "_kfxgen_keyword_px", None)
    if table is not None:
        return table
    # calibre rewrites the keywords with the input profile, before the
    # output side reads the book: with --input-profile kindle the values
    # were that profile's, whatever the output profile.
    opts = getattr(stylizer, "opts", None)
    profile = getattr(opts, "input_profile", None)
    names = getattr(profile, "fnames", None) or {}
    try:
        base = float(getattr(profile, "fbase", 0) or 0)
    except (TypeError, ValueError):
        base = 0.0
    seen = {}
    for keyword, pt in names.items():
        if keyword not in _FONT_KEYWORD_PX or keyword == "medium" or not base:
            continue
        written = f"{float(pt) / base:.1f}rem"
        seen.setdefault(written, []).append(keyword)
    table = {
        written: _FONT_KEYWORD_PX[kws[0]]
        for written, kws in seen.items()
        if len(kws) == 1
    }
    try:
        stylizer._kfxgen_keyword_px = table
    except AttributeError:
        pass
    return table


def _line_height_factor(stylizer, elem, cache):
    """`elem`'s line height as a factor of its own font size, from the
    nearest element declaring one: a unitless value is inherited as that
    factor, a length or a % as the length it comes to where it is declared;
    normal, or none set, is 1.2 (#463). calibre raises one below 1.2 to 1.2
    before kfxgen sees it (its minimum line height)."""
    own = _font_size_px(stylizer, elem, cache)
    for a in (elem, *elem.iterancestors()):
        if not isinstance(a.tag, str):
            continue
        value = str(stylizer.style(a).get("line-height") or "").strip().lower()
        if not value or value == "inherit":
            continue
        if value == "normal":
            return 1.2
        try:
            return round(float(value), 4)
        except ValueError:
            pass
        m = _FONT_SIZE_RE.match(value.replace(" ", ""))
        if not m or not own:
            return None
        px = _css_size_px(
            value,
            _font_size_px(stylizer, a, cache),
            lambda a=a: _root_size_px(stylizer, a, cache),
        )
        return round(px / own, 4)
    return 1.2


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


_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

#: CSS unicode-bidi as Kindle Previewer 4.0.3 writes it in `$674` (#246).
_UNICODE_BIDI = {
    "embed": "embed",
    "isolate": "isolate",
    "bidi-override": "bidi-override",
    "isolate-override": "isolate-override",
    "plaintext": "plaintext",
    # Written too, and nearer than an ancestor's dir (Previewer probe W246).
    "normal": "normal",
}


def _bidi(elem, style_resolver=None):
    """An element's writing direction, unicode-bidi and language, as Kindle
    Previewer 4.0.3 writes them (#246): {"direction", "unicode_bidi",
    "lang"}, each only when set, or {} for none.

    Direction follows the CSS cascade with `dir` as a declaration on its own
    element: walking up from `elem`, CSS `direction` set on an element beats
    that element's `dir`, and either beats an ancestor's (QA's Q50-Q54).
    `dir="auto"` takes the direction of the element's first letter that has
    one, left to right when none does (#710's K19).
    unicode-bidi is the nearest element's, itself or an ancestor, that has
    its own unicode-bidi CSS or a `dir` (isolate, as browsers' sheets give
    `[dir]`); on one element the CSS beats the `dir` (QA's Q67, Q68; my
    V246 Q81-Q83). plaintext keeps the direction (W246 Q90-Q94). The
    language is the nearest `xml:lang` or `lang`, lower-cased.

    "set_here" names those `elem` sets itself: Previewer writes them on its
    entry even where the container around it has the same (P605, P605c:
    E01, E02, E04, E05, E06)."""
    out = {}
    here = []
    direction = None
    bidi = None
    for e in (elem, *elem.iterancestors()):
        if not isinstance(e.tag, str):
            continue
        attr = (e.get("dir") or "").strip().lower()
        has_dir = attr in ("rtl", "ltr", "auto")
        if direction is None or bidi is None:
            css = style_resolver(e) if style_resolver is not None else None
            if direction is None:
                own = str((css or {}).get("own-direction") or "").strip().lower()
                if own in ("rtl", "ltr"):
                    direction = own
                elif has_dir:
                    direction = _first_strong(e) if attr == "auto" else attr
                if direction is not None and e is elem:
                    here.append("direction")
            if bidi is None:
                bidi = _UNICODE_BIDI.get(
                    str((css or {}).get("own-unicode-bidi") or "").strip().lower()
                ) or ("isolate" if has_dir else None)
                if bidi is not None and e is elem:
                    here.append("unicode_bidi")
        if "lang" not in out:
            lang = e.get(_XML_LANG) or e.get("lang")
            if lang and lang.strip():
                out["lang"] = lang.strip().lower()
                if e is elem:
                    here.append("lang")
        if direction is not None and bidi is not None and "lang" in out:
            break
    if bidi:
        out["unicode_bidi"] = bidi
    if direction:
        out["direction"] = direction
    if here:
        out["set_here"] = tuple(here)
    return out


def _first_strong(elem):
    """`dir="auto"`'s direction: that of the first letter in `elem` that has
    one, skipping a `<bdi>` and anything with its own `dir`, as HTML has it;
    left to right when no letter has one. Kindle Previewer 4.0.3 reads it so
    on blocks, spans and `<bdi>` (#710's K01, K06, K07, K19)."""

    def texts(e):
        yield e.text or ""
        for child in e:
            if isinstance(child.tag, str) and not (
                _local_tag(child.tag) in ("bdi", "script", "style") or child.get("dir")
            ):
                yield from texts(child)
            yield child.tail or ""

    for text in texts(elem):
        for ch in text:
            kind = unicodedata.bidirectional(ch)
            if kind == "L":
                return "ltr"
            if kind in ("R", "AL"):
                return "rtl"
    return "ltr"


def _inline_bidi(elem, css):
    """An inline element's own direction, unicode-bidi and language, as
    Kindle Previewer 4.0.3 writes them on its run (#710): {"direction",
    "unicode_bidi", "lang"}, each only when the element sets it, or {}.

    Unlike a block's, nothing comes from its parent, and a value equal to
    the paragraph's is written too (I10, I23). CSS `direction` beats `dir`
    (K03, K32, K34) and gives no unicode-bidi of its own (I06). The CSS
    unicode-bidi wins (I17, K30, K31); without it `<bdo>` is
    isolate-override (I13), and a `dir` or a `<bdi>` isolate. `<bdi>` with
    no `dir`, and `dir="auto"`, take the text's direction (K01, K04, K06)."""
    local = _local_tag(elem.tag)
    attr = (elem.get("dir") or "").strip().lower()
    out = {}
    own = str((css or {}).get("own-direction") or "").strip().lower()
    if own in ("rtl", "ltr"):
        out["direction"] = own
    elif attr in ("rtl", "ltr"):
        out["direction"] = attr
    elif attr == "auto" or (local == "bdi" and not attr):
        out["direction"] = _first_strong(elem)
    bidi = _UNICODE_BIDI.get(
        str((css or {}).get("own-unicode-bidi") or "").strip().lower()
    )
    if bidi is None and local == "bdo":
        bidi = "isolate-override"
    elif bidi is None and (attr in ("rtl", "ltr", "auto") or local == "bdi"):
        bidi = "isolate"
    if bidi:
        out["unicode_bidi"] = bidi
    lang = elem.get(_XML_LANG) or elem.get("lang")
    if lang and lang.strip():
        out["lang"] = lang.strip().lower()
    return out


#: Tells one inline element's direction run from another's (#710).
_BIDI_SERIAL = itertools.count()


def _pictures_among_words(elem, holds_blocks, set_apart=lambda img: False):
    """`elem`'s <img> children that share a run of loose content with words:
    between two block children (or an end), some text or an inline element
    with text (#757); or with another picture, as Kindle Previewer 4.0.3
    writes pictures side by side, one paragraph (#762, probe PSS P11),
    unless one of them is set apart (`display: block`: each its own block,
    as Previewer writes it, QA's Q766 Z38). The
    elements themselves, not their `id()`: lxml makes a new proxy for each
    access once the last one is gone (#712)."""
    found, run, words = set(), [], bool((elem.text or "").strip())
    for child in elem:
        if not isinstance(child.tag, str):
            words = words or bool((child.tail or "").strip())
            continue
        if holds_blocks(child) or _local_tag(child.tag) == "svg":
            # A block, or an <svg>, which `_walk_container` writes as
            # pictures of its own: the run ends.
            if words or _side_by_side(run, set_apart):
                found.update(run)
            run, words = [], False
        elif _local_tag(child.tag) == "img":
            run.append(child)
        elif "".join(child.itertext()).strip():
            words = True
        if (child.tail or "").strip():
            words = True
    if words or _side_by_side(run, set_apart):
        found.update(run)
    return found


def _side_by_side(pictures, set_apart):
    """Whether a run of pictures alone is written side by side, one
    paragraph: two or more, none set to display as a block (#762)."""
    return len(pictures) > 1 and not any(set_apart(p) for p in pictures)


def _with_bidi(block_style, elem, style_resolver, own=True):
    """`block_style` with `elem`'s direction and language in it (#246).
    `own=False` for text loose in `elem`, between or after its blocks: it
    has none of its own to repeat, and Previewer writes them on the box
    only (QA's P735b F03, F04)."""
    bidi = _bidi(elem, style_resolver)
    if not own:
        bidi.pop("set_here", None)
    if not bidi:
        return block_style
    return {**(block_style or {}), "bidi": bidi}


def _start_end_align(block_style, css):
    """`text-align: start` is right and `end` left in a right-to-left
    paragraph, as Kindle Previewer 4.0.3 writes them; left to right they
    stay left and right (#515, #246). The direction is the paragraph's own,
    from `dir` or CSS, not the book's page direction."""
    raw = str((css or {}).get("text-align") or "").strip().lower()
    if raw not in ("start", "end") or not block_style:
        return block_style
    if (block_style.get("bidi") or {}).get("direction") != "rtl":
        return block_style
    return {**block_style, "align": "right" if raw == "start" else "left"}


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
        size = _calibre_keyword_px(
            stylizer, css["font-size"], lambda: _root_size_px(stylizer, elem, cache)
        )
        if size is None:
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


#: The writing modes kfxgen writes (#247). vertical-lr, used for
#: Mongolian, isn't probed and is left horizontal.
_WRITING_MODES = frozenset({"vertical-rl", "horizontal-tb"})


def _file_writing_mode(root, style_resolver):
    """A spine file's writing mode from its <body>, which inherits its
    <html>'s: "vertical-rl", "horizontal-tb", or None when it sets none
    (#247)."""
    if style_resolver is None:
        return None
    body = next(
        (
            e
            for e in root.iter()
            if isinstance(e.tag, str) and _local_tag(e.tag) == "body"
        ),
        root,
    )
    mode = str((style_resolver(body) or {}).get("writing-mode") or "").strip().lower()
    return mode if mode in _WRITING_MODES else None


def _book_writing_mode(language, rtl):
    """The book's writing mode as Kindle Previewer 4.0.3 sets it:
    "vertical-rl" when its spine turns pages right to left and its
    `dc:language` is Japanese, whatever its CSS; else "horizontal-tb" (QA's
    P746 on #746: m1, m3-m6, m8, m9 vertical; m7, an English rtl book, and
    m10, `xml:lang="ja"` under `dc:language` en, not). Other languages
    aren't probed. `language` is as `_kfx_language` gives it: "ja", not
    calibre's "jpn"."""
    base = re.split(r"[-_]", str(language or ""), maxsplit=1)[0].lower()
    return "vertical-rl" if rtl and base == "ja" else "horizontal-tb"


_PIXEL_VIEWPORT = re.compile(r"(?:^|[\s,;])width\s*=\s*\d", re.I)


def _fixed_layout(oeb_book):
    """Whether the book is fixed-layout: most of its spine pages carry a
    viewport sized in pixels ("width=1200, height=1600"), as a
    pre-paginated page does. calibre drops `rendition:layout` from the
    metadata and keeps the viewport. Such a book isn't made vertical: the
    #711 manga test book (ja, rtl) passed on the Paperwhite written
    horizontal with `$192: $375`, and Previewer makes a MOBI of it, so there
    is no evidence for vertical (QA of #746)."""
    pages = list(getattr(oeb_book, "spine", None) or ())
    sized = 0
    for item in pages:
        root = getattr(item, "data", None)
        if root is None or not hasattr(root, "iter"):
            continue
        sized += any(
            isinstance(m.tag, str)
            and _local_tag(m.tag) == "meta"
            and str(m.get("name") or "").lower() == "viewport"
            and _PIXEL_VIEWPORT.search(str(m.get("content") or ""))
            for m in root.iter()
        )
    return bool(pages) and sized * 2 > len(pages)


def _drop_vertical_marks(root):
    """`mark_vertical_margins`' marks taken off a file, so it is read as
    horizontal text: its margins in their own places, no 縦中横 (#752)."""
    for e in root.iter():
        if isinstance(e.tag, str):
            for mark in (_V_AUTHOR, _V_UA, _HORIZONTAL, _VERTICAL):
                e.attrib.pop(mark, None)


def _split_by_writing_mode(chapters):
    """Each chapter cut where its blocks' writing mode changes: a file left
    out of the TOC is joined to the chapter before it, and Previewer gives a
    file set otherwise its own section (QA's P746 t1, #247). The parts after
    the first are no TOC entry and print no title. A contents page kfxgen
    built (`toc_links`) is left whole."""
    out = []
    for ch in chapters:
        blocks = ch.get("blocks") or []
        groups = []
        for b in blocks:
            mode = b.get("writing_mode")
            if groups and mode in (None, groups[-1][0]):
                groups[-1][1].append(b)
            else:
                groups.append([mode, [b]])
        if len(groups) < 2 or ch.get("toc_links"):
            out.append(ch)
            continue
        for n, (_mode, part) in enumerate(groups):
            text = "\n\n".join(b["text"] for b in part if b.get("text"))
            if n == 0:
                out.append({**ch, "blocks": part, "text": text})
            else:
                out.append(
                    {
                        "title": ch.get("title", ""),
                        "text": text,
                        "blocks": part,
                        "_omit_from_toc": True,
                        "_omit_title_heading": True,
                    }
                )
    return out


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
            # A table cell draws its fill too (#502).
            if _local_tag(elem.tag) in _BOX_TAGS | _CELL_TAGS:
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
                css = {
                    # A number, in CSS px; the generator measures it
                    # against the book's main text size (#241).
                    "font-size": round(_font_size_px(stylizer, elem, sizes), 3),
                    # A factor of its own font size, for $42 (#463).
                    "line-height": _line_height_factor(stylizer, elem, sizes),
                    # As written, or None when no element sets it: the
                    # factor says 1.2 for both `normal` and unset (#668).
                    "line-height-declared": _specified_inherited(
                        stylizer, elem, "line-height"
                    ),
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
                    # The element's own, declared on it: a header cell keeps
                    # its own alignment, even its row's (#601).
                    "own-text-align": st.get("text-align"),
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
                    # Inherited; Previewer writes upright and sideways on
                    # their run (#247).
                    "text-orientation": _specified_inherited(
                        stylizer, elem, "text-orientation"
                    ),
                    # Inherited: the marks beside the characters (#247).
                    "text-emphasis-style": _specified_inherited(
                        stylizer, elem, "text-emphasis-style"
                    ),
                    # The element's own: 縦中横 (#247).
                    "text-combine-upright": st.get("text-combine-upright"),
                    # A file's writing mode, from its <body> or <html>
                    # (P247a, P247d). calibre reads -epub-writing-mode as
                    # writing-mode (P247e). Read on those two only.
                    "writing-mode": (
                        _specified_inherited(stylizer, elem, "writing-mode")
                        if _local_tag(elem.tag) in ("html", "body")
                        else None
                    ),
                    "color": _specified_colour(stylizer, elem),
                    "font-style": _computed_value(st, "font-style"),
                    # vertical-align does NOT inherit, so .get() is right here:
                    # it returns the declared value, or None when the element
                    # has no rule of its own. Read for inline runs (#52); the
                    # block path ignores it.
                    "vertical-align": st.get("vertical-align"),
                    # Set on the element itself, not inherited: `dir` on an
                    # element beats an ancestor's CSS direction (#246).
                    # calibre's UA sheet sets neither.
                    "own-direction": st.get("direction"),
                    "own-unicode-bidi": st.get("unicode-bidi"),
                    # An <img>'s own size. Stylizer folds the width/height
                    # attributes into the element's CSS (and strips them),
                    # so this is the one place both spellings meet.
                    "width": st.get("width"),
                    "height": (
                        _book_height(stylizer, elem)
                        if _local_tag(elem.tag) == "hr"
                        else st.get("height")
                    ),
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
                    # Each side's border width as declared: calibre's computed
                    # value turns an em into points (#722).
                    "border-widths": tuple(
                        st.get(f"border-{side}-width") for side in _BORDER_SIDES
                    ),
                    "border-sides": tuple(
                        (
                            _computed_value(st, f"border-{side}-style"),
                            _computed_value(st, f"border-{side}-width"),
                            _computed_value(st, f"border-{side}-color"),
                        )
                        for side in _BORDER_SIDES
                    ),
                }
                # In vertical-rl text, margins and paddings by the book's
                # sides and the user agent's (#247).
                return _vertical_box(css, st, elem)
            except Exception:
                return None

        return resolve
    except Exception as e:
        log.warning(f"  Stylizer unavailable ({e}); skipping per-element CSS")
        return None


#: The length units a margin is converted from: as Kindle Previewer converts a
#: container's margin onto its paragraphs, 2em became 6.25 % (#238), 5% in a
#: vertical book (P247j). A px is 0.45pt and an em 12pt, the rates #296
#: measured for padding.
_UNIT_TO_PCT = frozenset({"$308", "$505", "$319", "$318", "$316", "$314"})


def _unit_percent(unit):
    """One `unit` as a % of the page, in the arithmetic main used at 384pt,
    so a horizontal book's values are unchanged to the last digit."""
    em = em_percent()
    return {
        "$308": em,
        "$505": em,
        "$319": em * 0.45 / 12,
        "$318": em / 12,
        "$316": em * 72 / 25.4 / 12,
        "$314": 1.0,
    }[unit]


#: Table parts: a table's margins do not reach its rows (#219).
_NO_CARRY_TAGS = frozenset(
    {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup"}
)


#: The most of the page width that margins carried from containers may
#: leave unused: half (#238).
_MAX_CARRIED_MARGINS = 50.0


def _page_share(margin):
    """A (magnitude, unit) margin as a share of the page width, in %."""
    if not margin:
        return 0.0
    return float(margin[0]) * _unit_percent(margin[1])


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

    def name(href):
        # In any capitals or %-encoding: one file to a reader (#550 QA).
        return unquote(_img_basename(href)).lower()

    cover = name(cover_href) if cover_href else None
    return any(name(m.group(1)) != cover for m in _IMG_TOKEN_RE.finditer(text or ""))


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


def _make_img_token(href, alt, size=None, float_side=None, extra=None):
    """Encode an <img> reference as a placeholder token string.

    Spaces in alt text are escaped to a control char so str.split() doesn't
    fragment the token during whitespace normalization. `size` is the hint
    from `_img_size_hint`, carried as an optional third field,
    `float_side` ("left" or "right") as a fourth (#243), and `extra` from
    `_img_inline_extras` as a fifth (#565).
    """
    safe_href = _strip_token_controls(href or "")
    escaped_alt = _strip_token_controls(alt or "").replace(" ", _IMG_TOKEN_SPACE)
    fields = [size or "", float_side or "", extra or ""]
    while fields and not fields[-1]:
        fields.pop()
    size_field = "".join(f"{_IMG_TOKEN_FIELD}{f}" for f in fields)
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


#: vertical-align keywords Kindle Previewer 4.0.3 keeps on a picture inside
#: a paragraph's words (#565).
_INLINE_VALIGNS = frozenset(
    {"middle", "top", "bottom", "text-top", "text-bottom", "baseline", "super", "sub"}
)
#: A vertical-align length, which Previewer writes as a baseline shift
#: (#565): a signed number with a unit, or 0.
_INLINE_VALIGN_LENGTH_RE = re.compile(
    r"^[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:px|pt|em|rem|%)$|^[-+]?0(?:\.0+)?$"
)


def _img_inline_extras(elem, style_resolver=None):
    """What a picture inside a paragraph's words keeps besides its
    `_img_size_hint`, as ``h=1em;va=middle``, or None (#565): its height,
    which Previewer stretches it to when a width is also given, and its
    vertical-align, a keyword or a length."""
    css = (style_resolver(elem) if style_resolver is not None else None) or {}
    out = []
    raw = str(css.get("height") or elem.get("height") or "").strip()
    if _IMG_SIZE_RE.match(raw):
        out.append(f"h={raw}")
    valign = str(css.get("vertical-align") or "").strip().lower()
    if valign in _INLINE_VALIGNS or _INLINE_VALIGN_LENGTH_RE.match(valign):
        out.append(f"va={valign}")
    return ";".join(out) or None


def _displays_as_block(css):
    """Whether an element's CSS sets `display: block`."""
    return str((css or {}).get("display") or "").strip().lower() == "block"


def _lone_block_picture(elem, style_resolver):
    """Whether a block holds nothing but one <img> set to `display: block`:
    Previewer writes it as a block picture in a vertical book's horizontal
    block, where a plain one is inline in a container (PVA H10, P247k K10)."""
    if style_resolver is None or (elem.text or "").strip():
        return False
    kids = [c for c in elem if isinstance(c.tag, str)]
    return (
        len(kids) == 1
        and _local_tag(kids[0].tag) == "img"
        and not (kids[0].tail or "").strip()
        and _displays_as_block(style_resolver(kids[0]))
    )


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


#: SVG elements that draw a shape. A <use> draws what it references.
_SVG_SHAPES = frozenset(
    {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "use"}
)
_SVG_LENGTH_RE = re.compile(r"^\s*(\d+(?:\.\d+)?|\.\d+)\s*([a-z%]*)\s*$")


def _svg_own_style(svg):
    """{property: value} of an <svg>'s own `style` attribute."""
    out = {}
    for decl in (svg.get("style") or "").split(";"):
        key, _sep, value = decl.partition(":")
        if key.strip() and value.strip():
            out[key.strip().lower()] = value.strip().lower()
    return out


def _svg_draws_shapes_only(svg):
    """Whether an <svg> draws shapes and nothing kfxgen already takes from
    it: no <image> (#154) and no <text> (#231). Formulas set as glyph
    outlines are drawn so, and were dropped (#709). One its own style or
    attributes hide is not drawn: calibre computes nothing for an <svg>,
    so `_is_hidden` doesn't see it (QA on #727). Shapes in an <svg> inside
    it don't count: Qt doesn't draw a nested <svg>."""
    if _svg_image_refs(svg) or _svg_text_paragraphs(svg):
        return False
    style = _svg_own_style(svg)
    display = style.get("display") or (svg.get("display") or "").strip().lower()
    visibility = style.get("visibility") or (svg.get("visibility") or "").lower()
    if display == "none" or visibility.strip() in ("hidden", "collapse"):
        return False

    def shapes(node):
        for child in node:
            local = _local_tag(child.tag)
            if not local or local.lower() in _SVG_NON_RENDERED_CONTAINERS:
                continue
            if local == "svg":
                continue
            if local in _SVG_SHAPES or shapes(child):
                return True
        return False

    return shapes(svg)


#: An <svg> length in ems for kfxgen's size hint: Qt and the hint read em
#: but not ex or rem. An ex is half an em, CSS's own fallback; a rem the
#: root's 16 px, an em in the book's main text.
_SVG_EM_UNITS = {"ex": 0.5, "rem": 1.0}


def _svg_hint_length(value):
    """`value` with an ex or rem length written in em (QA on #727: MathJax
    sizes its formulas in ex), else as it is."""
    m = _SVG_SIGNED_LENGTH_RE.match(value or "")
    if m and m.group(2) in _SVG_EM_UNITS:
        return f"{float(m.group(1)) * _SVG_EM_UNITS[m.group(2)]:g}em"
    return value


def _svg_box_css(svg):
    """An <svg>'s width, height and vertical-align, from its own `style`
    and then its width and height attributes, an ex or rem in em. calibre's
    Stylizer computes an <svg> as width auto with no vertical-align, so a
    formula lost the size and shift its markup gives it (#709)."""
    style = _svg_own_style(svg)
    out = {k: style[k] for k in ("width", "height", "vertical-align") if k in style}
    for axis in ("width", "height"):
        if axis not in out and (svg.get(axis) or "").strip():
            out[axis] = svg.get(axis).strip().lower()
    return {k: _svg_hint_length(v) for k, v in out.items()}


_SVG_SIGNED_LENGTH_RE = re.compile(
    r"^\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*([a-z%]*)\s*$"
)
#: CSS px in one of each unit, to draw an <svg> at: Qt reads a pt as a px
#: and no em, ex or rem at all (QA on #727).
_SVG_UNIT_PX = {
    "": 1.0,
    "px": 1.0,
    "pt": 96 / 72,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96 / 2.54,
    "mm": 96 / 25.4,
    "em": 16.0,
    "rem": 16.0,
    "ex": 8.0,
}


def _svg_length_px(value):
    """An <svg> width or height in CSS px, or None for a % or an unknown
    unit."""
    m = _SVG_LENGTH_RE.match(value or "")
    if not m or m.group(2) not in _SVG_UNIT_PX:
        return None
    return float(m.group(1)) * _SVG_UNIT_PX[m.group(2)]


def _svg_used_elsewhere(svg):
    """The elements outside `svg` that its <use>s draw, and those theirs
    draw. MathJax keeps every glyph in one hidden <svg> of its own, and a
    sprite sheet does the same; the copy drawn alone had none of them and
    came out blank (QA on #727)."""
    inside = {e.get("id") for e in svg.iter() if isinstance(e.tag, str)}
    found, seen, todo = [], set(), [svg]
    by_id = None
    while todo:
        for e in todo.pop().iter():
            if not (isinstance(e.tag, str) and _local_tag(e.tag) == "use"):
                continue
            ref = e.get(_XLINK_HREF) or e.get("href") or ""
            key = ref[1:] if ref.startswith("#") else None
            if not key or key in inside or key in seen:
                continue
            seen.add(key)
            if by_id is None:
                by_id = {
                    x.get("id"): x
                    for x in svg.getroottree().iter()
                    if isinstance(x.tag, str) and x.get("id")
                }
            target = by_id.get(key)
            if target is not None:
                found.append(target)
                todo.append(target)
    return found


def _svg_picture(svg, scale=1):
    """The <svg> element as an SVG file of its own, to rasterise (#709).

    - What its <use>s draw from outside it is copied into its <defs>.
    - Its width and height go in CSS px, as Qt reads neither em, ex nor
      rem, and reads a pt as a px. A width with no height gets the height
      its viewBox gives, and the other way round; a viewBox alone gives
      both.
    - One with no viewBox gets one of its own size: its shapes are in px.
    - `scale` multiplies its size. Qt reads a size as whole pixels, so a
      formula 8.8pt high drawn at 8 px and scaled up came out 9% too
      short; the viewBox keeps its drawing filling it (QA on #727).

    Returns (bytes, the scale drawn at): 1 when its size can't be told."""
    from lxml import etree

    own = copy.deepcopy(svg)
    own.tail = None
    used = _svg_used_elsewhere(svg)
    if used:
        ns = etree.QName(own).namespace
        defs = etree.Element(f"{{{ns}}}defs" if ns else "defs")
        defs.extend(copy.deepcopy(u) for u in used)
        for u in defs:
            u.tail = None
        own.insert(0, defs)
    try:
        # Numbers apart by spaces, commas or both (QA on #727).
        box = re.split(r"[\s,]+", (own.get("viewBox") or "").strip())
        _x, _y, vw, vh = (float(v) for v in box)
    except ValueError:
        vw = vh = 0
    has_box = vw > 0 and vh > 0
    w, h = _svg_length_px(own.get("width")), _svg_length_px(own.get("height"))
    if has_box:
        if w and not h:
            h = w * vh / vw
        elif h and not w:
            w = h * vw / vh
        elif not w and not h and not own.get("width") and not own.get("height"):
            w, h = vw, vh
    elif w and h:
        own.set("viewBox", f"0 0 {w:g} {h:g}")
    if not (w and h):
        return etree.tostring(own, with_tail=False), 1
    own.set("width", f"{w * scale:g}")
    own.set("height", f"{h * scale:g}")
    return etree.tostring(own, with_tail=False), scale


def _svg_picture_href(svg):
    """The href kfxgen gives an <svg> drawn as a picture of its own (#709):
    named by its markup, so the picture extraction and the text walk reach
    the same name from the same element, and two copies of one formula
    share one picture."""
    import hashlib

    digest = hashlib.sha1(_svg_picture(svg)[0]).hexdigest()[:16]
    return f"{_INLINE_SVG_DIR}/{digest}.svg"


#: The folder name of kfxgen's pictures of inline <svg>s. No book file is
#: there.
_INLINE_SVG_DIR = "kfxgen-inline-svg"
#: How many times its own size an inline <svg> is drawn. Qt reads a
#: formula's 24pt as 24 px, so at twice that a 2em formula had 24 px to
#: an em, where a Paperwhite shows an em at about 40-50 (#709). Its shown
#: size comes from its markup, so this only adds sharpness.
_INLINE_SVG_SCALE = 4


def _inline_svg_pictures(oeb_book):
    """{href: <svg> element} for every <svg> in the spine's pages drawn as
    a picture of its own (#709)."""
    found = {}
    for item in oeb_book.spine:
        try:
            data = item.data
        except Exception:
            continue
        if data is None or not hasattr(data, "iter"):
            continue
        if isinstance(data.tag, str) and _local_tag(data.tag) == "svg":
            continue  # a page that is an SVG file, not one inside a page
        for svg in data.iter():
            if not (isinstance(svg.tag, str) and _local_tag(svg.tag) == "svg"):
                continue
            parent = svg.getparent()
            if parent is not None and _local_tag(parent.tag) == "svg":
                continue
            if _svg_draws_shapes_only(svg):
                found.setdefault(_svg_picture_href(svg), svg)
    return found


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
                if part.get("type") == "box":
                    _drop_keys(part["blocks"], keys)  # (#645)
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


_EXTERNAL_LINK_RE = re.compile(r"^(?:https?|mailto):", re.I)


def _external_link_target(href):
    """An http, https or mailto `<a href>` as a link target, which Kindle
    Previewer keeps as a link (#474); None for anything else, javascript:
    and tel: among them."""
    href = (href or "").strip()
    return EXTERNAL_LINK_PREFIX + href if _EXTERNAL_LINK_RE.match(href) else None


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
    typography, colour as given) from an element's computed CSS, or None
    without it. The typography is `css_typography`'s (#239); the colour as
    given, not darkened for contrast, is what a link's is compared by
    (#728).
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
        css_link_colour(str(css.get("color") or "black")),
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
        bold, italic, _mono, size, typography, _given = _css_emphasis(first)
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


#: The brightest channel of a link colour Previewer 4.0.3 writes none for on
#: the page: up to #1a1a1a none, from #202020 on written, any channel
#: (QA on #732, P732d; 0x1B-0x1F not probed).
_NEAR_BLACK = 0x1A


def _near_black(argb):
    """Whether an ARGB colour is black or near enough for Previewer to write
    no link colour for it on the page."""
    return argb is not None and all(
        (argb >> shift) & 0xFF <= _NEAR_BLACK for shift in (16, 8, 0)
    )


#: Inline tags the text around a link is looked for through; any other is
#: the edge of its block (#728).
_LINK_INLINE_TAGS = frozenset(
    "a abbr b bdi bdo big cite code del dfn em font i ins kbd label mark q s"
    " samp small span strike strong sub sup time tt u var".split()
)

#: Inline elements a vertical book can set the other way from their line,
#: as an inline container of their own (#247, probe PSB): those that hold
#: text and nothing else drawn.
_TURN_TAGS = _LINK_INLINE_TAGS - {"sub", "sup"}
#: Display values that make an element a block, not a span to turn: it keeps
#: its own lines, as on main (QA's Q765 Y06; Previewer makes it a block).
_TURN_BLOCK_DISPLAYS = frozenset({"block", "list-item", "table", "flex", "grid"})


#: The whitespace CSS collapses: a no-break space is not (#728, P732u U01).
_COLLAPSING = " \t\n\r\f"
#: Inline elements that show something with no text: a link's leading space
#: after one stays (P732u U04).
_LINK_PICTURE_TAGS = frozenset({"img", "svg", "math", "object", "video"})


def _last_shown(elem, style_resolver):
    """What `elem` shows last, as `_text_before` reads it: a space for a
    <br>, an object mark for a picture, else its last text; "" for nothing.
    A <br> or a picture at its end counts (QA on #732, P732v V01, V02)."""
    if not isinstance(elem.tag, str) or _is_hidden(elem, style_resolver):
        return ""
    local = _local_tag(elem.tag)
    if local == "br":
        return " "
    if local in _LINK_PICTURE_TAGS:
        return "\ufffc"
    for child in reversed(elem):
        if child.tail:
            return child.tail
        text = _last_shown(child, style_resolver)
        if text:
            return text
    return elem.text or ""


def _text_before(node, style_resolver):
    """The nearest text before `node` in its block, as written: "" at the
    block's start, a space after a <br>, where a line starts, and an object
    mark after a picture. Hidden elements are skipped."""
    while True:
        sib = node.getprevious()
        if sib is not None:
            if sib.tail:
                return sib.tail
            text = _last_shown(sib, style_resolver)
            if text:
                return text
            node = sib
            continue
        parent = node.getparent()
        if parent is not None and parent.text:
            return parent.text
        if parent is None or _local_tag(parent.tag) not in _LINK_INLINE_TAGS:
            return ""
        node = parent


def _is_all_its_link_shows(elem, style_resolver):
    """Whether `elem` is all the link around it shows. Previewer then gives
    the link no colour of its own when `elem` is black (QA on #732, P732p,
    P732q, P732t, P732u; P728f): `<a red><b black>12</b></a>` is drawn
    black, and so is `<a red><span hidden>Chapter</span> <span
    black>12</span></a>`.

    From the link down to `elem`, each element shows one element, hidden
    ones left out, and no text but leading whitespace that collapses: after
    an ordinary space, a <br>, or the block's start. A no-break space, a
    space after a letter or a picture, and any trailing whitespace keep the
    link's colour (T05, Q05, U01, U04, U10), and so do a picture beside
    `elem` or an element inside it (Q02, Q10)."""
    link = next(
        (
            up
            for up in elem.iterancestors()
            if isinstance(up.tag, str) and _local_tag(up.tag) == "a"
        ),
        None,
    )
    if link is None:
        return False
    if any(isinstance(k.tag, str) and not _is_hidden(k, style_resolver) for k in elem):
        return False
    if not ((elem.text or "") + "".join(k.tail or "" for k in elem)).strip():
        return False
    ahead = _text_before(link, style_resolver)
    lead_goes = not ahead or ahead[-1] in _COLLAPSING
    node = link
    while node is not elem:
        shows = [
            k
            for k in node
            if isinstance(k.tag, str) and not _is_hidden(k, style_resolver)
        ]
        if len(shows) != 1:
            return False
        (child,) = shows
        if child is not elem and elem not in child.iterdescendants():
            return False
        texts = [(node.text, True)]
        after = False
        for k in node:
            after = after or k is child
            texts.append((k.tail, not after))
        for text, leading in texts:
            if text and (text.strip(_COLLAPSING) or not (leading and lead_goes)):
                return False
        node = child
    return True


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


_CONTENT_TOKEN_RE = re.compile(
    r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|([a-zA-Z-]+)\(([^)]*)\)|([a-zA-Z-]+)'
)


def _q_pair(q, css, depth):
    """The (open, close) pair a quote keyword prints at `depth`: the <q>'s
    `quotes` (none, or pairs by depth), else its nearest lang's, the second
    pair at any depth past the first (#469, #503 QA)."""
    lang = _lang_of(q) or ""
    marks = _Q_MARKS.get(lang.lower(), _Q_PLAIN)
    pair = (marks[2], marks[3]) if depth else (marks[0], marks[1])
    quotes = (css.get("quotes") or "").strip().lower()
    if quotes == "none":
        return ("", "")
    if strings := _css_strings(css.get("quotes")):
        pairs = list(zip(strings[0::2], strings[1::2]))
        if pairs:
            return pairs[min(depth, len(pairs) - 1)]
    return pair


def _q_content(q, css, side, depth):
    """What a <q>'s ::before or ::after prints, and the quote depth after it,
    as Kindle Previewer 4.0.3 counts it (#504): open-quote raises the depth
    and close-quote lowers it, never below 0, as in CSS; a keyword prints
    its mark only alone, open-quote in ::before and close-quote in ::after.
    Strings and attr() print; counter() and counters() print nothing."""
    value = (css.get(side) or "").strip()
    if value in ("", "normal"):
        value = "open-quote" if side == "before" else "close-quote"
    if value == "none":
        return "", depth
    tokens = _CONTENT_TOKEN_RE.findall(value)
    alone = len(tokens) == 1
    out = []
    for dq, sq, fn, arg, word in tokens:
        if fn:
            if fn.lower() == "attr":
                out.append(q.get(arg.strip()) or "")
        elif word:
            word = word.lower()
            if word in ("open-quote", "no-open-quote"):
                if word == "open-quote" and alone and side == "before":
                    out.append(_q_pair(q, css, depth)[0])
                depth += 1
            elif word in ("close-quote", "no-close-quote") and depth:
                depth -= 1
                if word == "close-quote" and alone and side == "after":
                    out.append(_q_pair(q, css, depth)[1])
        else:
            # A string, its escapes read.
            out.extend(_css_strings(f"'{sq}'" if sq else f'"{dq}"'))
    return "".join(out), depth


def _document_q_marks(root, css_of):
    """{q: (open, close)} for every <q> in `root`'s document, the quote depth
    running on across it in reading order, as Previewer counts it (#504)."""
    from lxml import etree

    marks, depth = {}, 0
    for event, q in etree.iterwalk(root, events=("start", "end")):
        if not isinstance(q.tag, str) or _local_tag(q.tag) != "q":
            continue
        css = css_of(q) or {}
        if event == "start":
            text, depth = _q_content(q, css, "before", depth)
            marks[q] = [text, ""]
        else:
            marks[q][1], depth = _q_content(q, css, "after", depth)
    return {q: tuple(m) for q, m in marks.items()}


# The last document's marks: a document's blocks are walked in turn, with one
# resolver for the document (#504).
_Q_DOCUMENT = {}


def _q_marks(q, style_resolver=None):
    """(open, close) for a <q>, as Kindle Previewer 4.0.3 prints them: its
    ::before and ::after content, else its `quotes`, else its nearest lang's
    pair, at the quote depth its document has reached (#469, #503 QA,
    #504)."""
    root = q.getroottree().getroot()
    cache = _Q_DOCUMENT
    if cache.get("root") is not root or cache.get("resolver") is not style_resolver:
        cache.update(
            root=root,
            resolver=style_resolver,
            marks=_document_q_marks(
                root,
                lambda e: (
                    (style_resolver(e) or {}).get("q-css") if style_resolver else None
                ),
            ),
        )
    marks = cache["marks"].get(q)
    if marks is None:
        # Not in its document's walk (a copy): counted on its own.
        marks = _document_q_marks(q, lambda e: None).get(q, ("", ""))
    return marks


def _turned_parts(elem, cur, style_resolver, base_href, emphasis, mode):
    """An inline element set the other way from its line (#247): its ids,
    then one placeholder carrying its text and its own runs, written as an
    inline container holding a paragraph, as Kindle Previewer 4.0.3 writes
    a horizontal-tb span in vertical text (probe PSB S01-S12) and a
    vertical-rl one in a horizontal block (R05). Its runs are inside it,
    as Previewer writes them, a link round it too; the placeholder takes
    none. None when it holds something its paragraph can't (a picture, a
    ruby, an equation): it stays in the line, as before."""
    if any(
        isinstance(d.tag, str) and _local_tag(d.tag) in ("img", "svg", "math", "ruby")
        for d in elem.iterdescendants()
    ):
        # A picture is walked by the element holding it, so it would be lost
        # from the text; a ruby or an equation is an entry its paragraph
        # can't hold: these stay in the line.
        return None
    for d in elem.iterdescendants():
        # An element turned again inside it is read as its text, in this
        # one's direction: Previewer nests a container there, which this
        # one's paragraph can't hold (QA's Q765 Y01).
        if isinstance(d.tag, str):
            d.attrib.pop(_HORIZONTAL, None)
            d.attrib.pop(_VERTICAL, None)
    quote = _q_marks(elem, style_resolver) if _local_tag(elem.tag) == "q" else None
    inner = [(quote[0], cur)] if quote else []
    inner.append((elem.text, cur))
    for child in elem:
        inner.extend(
            _walk_inline(
                child,
                cur,
                style_resolver,
                is_root=False,
                base_href=base_href,
                parent_emphasis=emphasis,
            )
        )
        inner.append((child.tail, cur))
    if quote:
        inner.append((quote[1], cur))
    inner = [(t, f) for t, f in inner if isinstance(f, frozenset) and t is not None]
    text, spans, _marks = normalize_runs_with_anchors(inner)
    if not text.strip():
        return None
    raw = "".join(t for t, _f in inner)
    if raw[-1:] in _COLLAPSING and not text.endswith(" "):
        # Previewer keeps a space it ends with, inside it: the space between
        # it and the next word (QA's Q765 Y08, Y12).
        text += " "
    ids = [aid for d in elem.iterdescendants() for aid in _own_anchor_ids(d)]
    return [
        *(make_anchor_mark(aid) for aid in ids),
        ("\ufffc", frozenset({make_turn_flag(mode, text, spans)})),
    ]


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
    own_shifts = []  # the shifts this element sets itself (#536)
    if local in _SUPER_TAGS:
        cur.add(FLAG_SUPER)
        own_shifts.append(FLAG_SUPER)
    if local in _SUB_TAGS:
        cur.add(FLAG_SUB)
        own_shifts.append(FLAG_SUB)
    starts_link = False
    if local == "a":
        target = _resolve_link_target(
            elem.get("href"), base_href
        ) or _external_link_target(elem.get("href"))
        if target:
            starts_link = True
            # A link inside a link (invalid, but the WordDumb plugin writes
            # it): the inner one wins, as a tap lands on the innermost
            # element. Keeping both left the run's target to the frozenset's
            # hash order, so it changed between runs (#317).
            cur = {f for f in cur if not _is_link_flag(f) and f != NOTEREF_FLAG}
            # Nor the outer link's colour: Previewer gives an inner link
            # none of its own (P728e E02, #728).
            cur = {f for f in cur if flag_value((f,), LINK_COLOUR_FLAG) is None}
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
            elif shift == "text-top":
                cur.add(FLAG_TEXT_TOP)
            elif shift == "text-bottom":
                cur.add(FLAG_TEXT_BOTTOM)
            if shift in SHIFT_FLAGS:
                own_shifts.append(shift)
            elif str(css.get("vertical-align") or "").strip().lower() == "baseline":
                own_shifts.append(BASELINE_SHIFT)  # inside a shift only (#638)
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
            # (#239). A link's own colour is no run's `$19`: Previewer
            # writes none in the run, not even the colour of a span around
            # it (#377 re-check). It writes the link's colour, and a colour
            # set inside the link, as the link's colour, `$576` (#728).
            for kind, now, before, unset in zip(
                TYPOGRAPHY_FLAGS,
                emphasis[4],
                parent_emphasis[4],
                ("normal", "none", SPACING_NORMAL, None, "mixed", "none"),
            ):
                if kind == COLOUR_FLAG and any(_is_link_flag(f) for f in cur):
                    # Compared as given: on a dark box black and #333333
                    # darken to the box text's own colour (QA on #732, P732
                    # B03, B04, B14).
                    if now is None or emphasis[5] == parent_emphasis[5]:
                        continue
                    link_colour = emphasis[5]
                    if own_css.get("background-behind") is None and _near_black(
                        link_colour
                    ):
                        # On the page, black and near-black are no link
                        # colour: the link is drawn in its paragraph's (QA on
                        # #732, P732 B05, P732d). In a box, every colour is.
                        link_colour = None
                        if not _is_all_its_link_shows(elem, style_resolver) and any(
                            flag_value((f,), LINK_COLOUR_FLAG) is not None for f in cur
                        ):
                            # A black word inside a coloured link takes the
                            # link's colour (QA on #732, P732 B09, P732k K10).
                            # Holding all of the link's text, it takes the
                            # link's colour away (P732p X05).
                            continue
                    if starts_link:
                        cur = {f for f in cur if flag_value((f,), kind) is None}
                    # With the colour as text, for a link whose target isn't
                    # found: Previewer writes it as `$19`, darkened as text,
                    # black too (P732b, P732j).
                    cur = {f for f in cur if flag_value((f,), LINK_COLOUR_FLAG) is None}
                    cur.add((LINK_COLOUR_FLAG, (link_colour, now)))
                    continue
                if now == before or (kind == COLOUR_FLAG and now is None):
                    continue
                cur = {f for f in cur if flag_value((f,), kind) is None}
                cur.add((kind, now if now is not None else unset))
    if local in _MONO_TAGS and "font-family" not in (own_css or {}):
        cur.add(make_mono_flag(("monospace",)))
    if not is_root:
        keys = _inline_bidi(elem, own_css)
        if keys.keys() - {"lang"}:
            # Its own direction: a run over its whole text, its language in
            # it (#710's P02).
            depth = 1 + sum(
                1 for f in cur if isinstance(f, tuple) and f[0] == BIDI_FLAG
            )
            cur.add(make_bidi_flag(next(_BIDI_SERIAL), depth, keys))
        elif keys:
            # A language alone is a run style, as bold is (P01, P03).
            cur = {f for f in cur if flag_value((f,), LANG_FLAG) is None}
            cur.add((LANG_FLAG, keys["lang"]))
    cur = frozenset(_nest_shift(cur, flags, own_shifts))
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
    tcy = (
        " ".join("".join(elem.itertext()).split())
        if not is_root
        # In a vertical-rl file only, which mark_vertical_margins marked:
        # Previewer isn't probed on it in horizontal text.
        and elem.get(_V_AUTHOR) is not None
        and str((own_css or {}).get("text-combine-upright") or "").strip().lower()
        == "all"
        else ""
    )
    if tcy:
        # Set across the line (#247): one placeholder carrying its text, an
        # inline container of its own, as an equation is. Its style is the
        # one all its text shares: Previewer writes a bold run over the
        # container of `<span class="tcy"><b>56</b></span>` (QA's P748 X06),
        # and a normal-weight one where a span inside it undoes a bold
        # around it (P748d D1).
        inner = [(elem.text, cur)]
        for child in elem:
            inner.extend(
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
            inner.append((child.tail, cur))
        worded = [
            f
            for t, f in inner
            if isinstance(t, str) and t.strip() and isinstance(f, frozenset)
        ]
        own = frozenset.intersection(*worded) if worded else cur
        parts.extend(
            make_anchor_mark(aid)
            for d in elem.iterdescendants()
            for aid in _own_anchor_ids(d)
        )
        parts.append(("\ufffc", own | {make_tcy_flag(tcy)}))
        return parts
    turn = (
        "horizontal"
        if elem.get(_HORIZONTAL) is not None
        else "vertical"
        if elem.get(_VERTICAL) is not None
        else None
    )
    if (
        turn
        and not is_root
        and local in _TURN_TAGS
        and str((own_css or {}).get("display") or "").strip().lower()
        not in _TURN_BLOCK_DISPLAYS
        # Previewer doesn't turn one in a table cell (QA's Q765 Y04).
        and not any(
            isinstance(a.tag, str) and _local_tag(a.tag) in _CELL_TAGS
            for a in elem.iterancestors()
        )
    ):
        turned = _turned_parts(elem, cur, style_resolver, base_href, emphasis, turn)
        if turned is not None:
            parts.extend(turned)
            return parts
    quote = _q_marks(elem, style_resolver) if local == "q" else None
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
            extra = _img_inline_extras(child, style_resolver)
            # Only a link carries over: tapping the picture follows it (#355).
            link = frozenset(f for f in cur if _is_link_flag(f))
            parts.append((_make_img_token(href, alt, size, side, extra), link))
        elif clocal == "svg":
            for aid in _own_anchor_ids(child):
                parts.append(make_anchor_mark(aid))
            if _svg_draws_shapes_only(child):
                # Drawn only with shapes, a formula as glyph outlines: a
                # picture of its own, sized and aligned as an <img> (#709).
                link = frozenset(f for f in cur if _is_link_flag(f))
                box = _svg_box_css(child)
                token = _make_img_token(
                    _svg_picture_href(child),
                    "",
                    _img_size_hint(child, lambda _e: box),
                    None,
                    _img_inline_extras(child, lambda _e: box),
                )
                parts.append((token, link))
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
    if own_shifts:
        parts = _whole_baseline_dropped(
            parts, sum(1 for f in cur if f in SHIFT_FLAGS or nested_shift(f))
        )
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


def _is_contentless_anchor(elem):
    """An <a> with no content, whatever its attributes: a table ignores it
    between its rows and cells, as Kindle Previewer 4.0.3 does (#702). One
    with an id still names a place; that is `_is_empty_anchor`'s."""
    return (
        isinstance(elem.tag, str)
        and _local_tag(elem.tag) == "a"
        and not (elem.text or "").strip()
        and len(elem) == 0
    )


def _foster_parents(table):
    """`table`, its row groups and its rows: where content that isn't a
    table part sits outside every cell."""
    groups = [c for c in table if _local_tag(c.tag) in _ROW_GROUP_TAGS - {"table"}]
    rows = [r for g in [table, *groups] for r in g if _local_tag(r.tag) == "tr"]
    return [table, *groups, *rows]


def _fostered_content(table):
    """What sits in `table` outside its cells, in document order: elements
    (an empty <a> excepted, #702) and loose text, as `(element, None)` or
    `(None, text)`. Kindle Previewer 4.0.3 moves it all in front of the
    table, as HTML's parser does, and keeps the table (#704)."""
    parents = _foster_parents(table)
    out = []
    for e in table.iter():
        if e not in parents:
            continue
        if (e.text or "").strip():
            out.append((None, e.text))
        for c in e:
            if not isinstance(c.tag, str):
                pass
            elif _local_tag(c.tag) in _TABLE_PART_TAGS | _CELL_TAGS:
                pass
            elif not _is_contentless_anchor(c):
                out.append((c, None))
            if (c.tail or "").strip():
                out.append((None, c.tail))
    return out


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
                elif (
                    isinstance(img.tag, str)
                    and _local_tag(img.tag) == "svg"
                    and _svg_draws_shapes_only(img)
                ):
                    found.add(_svg_picture_href(img))
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
        # Text and elements outside the cells are moved in front of the
        # table, as Previewer does, or ignored when an empty <a> (#702, #704).
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


def _loose_cell_text(cell):
    """Whether text is written straight in the cell beside its blocks: a
    paragraph of its own, as Kindle Previewer 4.0.3 writes it, not joined to
    the block after it (#681)."""
    if (cell.text or "").strip():
        return True
    return any(
        (d.tail or "").strip()
        for d in cell
        if isinstance(d.tag, str) and _local_tag(d.tag) in _CELL_BLOCK_TAGS
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


def _padding_em(value, ratio=1.0):
    """A declared padding as Kindle Previewer measures it: ems (1px = 0.45pt,
    12pt = 1em), ("%", n) for a percentage, or None when unreadable. An em
    is the element's own: `ratio` is its font size against the 16px root
    (#647)."""
    m = re.fullmatch(r"\s*([\d.]+)\s*(px|pt|em|rem|%)?\s*", str(value or ""))
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2)
    if unit == "%":
        return ("%", n)
    if unit is None:
        return 0.0 if n == 0 else None
    return n * _PADDING_EM_PER_UNIT[unit] * (ratio if unit == "em" else 1.0)


def _font_ratio(css):
    """An element's font size against the 16px root: Kindle Previewer 4.0.3
    measures an em margin or padding in the element's own size, and a px,
    pt, rem or % one against the root, whatever the book's main size (#647)."""
    px = css_font_size(css)
    return px / 16.0 if px else 1.0


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


#: Containers drawn as a box when they have a border or a fill (#238), and a
#: <p> or <pre>, as Kindle Previewer 4.0.3 draws a code listing (#643).
_BOX_TAGS = frozenset({"div", "aside", "section", "blockquote", "article", "p", "pre"})
#: Containers drawn as a box at their width when they hold a picture (#382).
_SIZED_BOX_TAGS = _BOX_TAGS | {"figure"}


def _has_content(elem):
    """Whether an element shows anything: text, or a picture, rule or table
    in it."""
    if not isinstance(elem.tag, str):
        return False
    if "".join(elem.itertext()).strip():
        return True
    return any(
        _local_tag(d.tag) in ("img", "svg", "hr", "table")
        for d in elem.iter()
        if isinstance(d.tag, str)
    )


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


#: An <hr>'s margins in calibre's own sheet, above and below (#230).
_RULE_SPACE_EM = 0.5
#: One blank line: what a spacer paragraph (`<p>&nbsp;</p>`) adds (#230).
_LINE_SPACE_EM = 1.2
#: An <hr> that draws nothing: Kindle Previewer 4.0.3 reads its lengths in
#: CSS units (1em = 16px = 12pt, 100% = 74em) and keeps 0.6 of them (#643).
_HIDDEN_RULE_EM_PER_UNIT = {
    "px": 1 / 16,
    "pt": 1 / 12,
    "em": 1.0,
    "rem": 1.0,
    "%": 0.74,
}
_HIDDEN_RULE_SHARE = 0.6


#: calibre's own <hr> border: 1px inset on every side, in the text colour.
#: Kindle Previewer writes no border for it, and the device draws its own
#: rule (#722).
_UA_RULE_SIDE = ("inset", 0.72, "currentcolor")


def _rule_look(css):
    """How an <hr> is drawn, as Kindle Previewer 4.0.3 writes it on its
    `$596` (my probes, #722), or {} for calibre's plain rule:
    - "width": (n, "%") from % or px (px at 0.45pt, 100% = 384pt), or
      (n, "em");
    - "align": "left" or "right" when it has a width and a margin that
      isn't auto, else "centre"; "margin_left"/"margin_right": a margin as a share of the
      page, when it isn't 0 or auto, only the aligned side's with a width;
    - "border": {side: (style, width pt or (n, "em"), ARGB or None)}, its
      drawn sides, unless it is calibre's own;
    - "fill" (ARGB) and "height" ((n, "pt") from px, or (n, "em")): a
      borderless rule drawn as a filled bar.
    Its `color` is not written: calibre gives every <hr> grey, and
    Previewer wrote grey for `color: red` too."""
    css = css or {}
    look = {}
    width = parse_css_length(str(css.get("width") or ""))
    if width and width[1] in ("$314", "$319"):
        look["width"] = (_page_share(width), "%")
    elif width and width[1] == "$308":
        look["width"] = (float(width[0]), "em")
    margins = {}
    for side in ("left", "right"):
        value = str(css.get(f"margin-{side}") or "").strip().lower()
        length = parse_css_length(value)
        if length and float(length[0]) > 0 and length[1] in _UNIT_TO_PCT:
            look[f"margin_{side}"] = _page_share(length)
        margins[side] = value
    if "width" in look:
        # Placed from one side: only that side's margin is written. A
        # right-placed rule's left margin is auto, so has none.
        if margins["left"] not in ("", "auto"):
            look["align"] = "left"
            look.pop("margin_right", None)
        elif margins["right"] not in ("", "auto"):
            look["align"] = "right"
        else:
            # Both auto: centred. Previewer writes it for `margin: auto`
            # but not for calibre's own auto, which kfxgen can't tell apart;
            # written for both, so it doesn't rest on the device's default
            # (the user's choice, QA on #724).
            look["align"] = "centre"
    sides = css.get("border-sides") or ()
    ua = len(sides) == 4 and all(
        (str(k).lower(), _float_or_none(w), str(c).lower()) == _UA_RULE_SIDE
        for k, w, c in sides
    )
    border = None if ua else _css_border(css)
    if border:
        # A black the book sets is written, as Previewer writes it; the
        # text colour (`currentColor`) is not.
        raw = dict(zip(_BORDER_SIDES, (c for _k, _w, c in sides)))
        # A width in em stays in em, in the rule's own size, as Previewer
        # writes it (QA on #724: 0.1em was written 0.75pt).
        declared = dict(zip(_BORDER_SIDES, css.get("border-widths") or ()))
        ratio = _font_ratio(css)

        def width(side, pt):
            em = parse_css_length(str(declared.get(side) or ""))
            if em and em[1] == "$308":
                return (round(float(em[0]) * ratio, 4), "em")
            return pt

        look["border"] = {
            side: (
                kind,
                width(side, pt),
                0xFF000000 if css_rgb(raw[side]) == 0 else colour,
            )
            for side, (kind, pt, colour) in border.items()
        }
    height = str(css.get("height") or "").strip()
    length = parse_css_length(height)
    if height == "0":
        look["height"] = (0.0, "pt")
    elif length and length[1] == "$319":
        look["height"] = (round(float(length[0]) * 0.45, 4), "pt")
    elif length and length[1] == "$308":
        look["height"] = (float(length[0]), "em")
    fill = _box_background(css)
    if fill is not None and look.get("height", (0,))[0] > 0:
        look["fill"] = fill
    return look


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _margin_em(css, side, share=1.0):
    """An element's declared margin-top or -bottom in the 16px root's ems
    (1px = 0.45pt, 12pt = 1em, as for padding; an em is the element's own,
    #647); a percentage on Kindle Previewer 4.0.3's scale, 100% = 384pt =
    32em (#640), 480pt = 40em in a vertical book (P247j), of `share` of the
    page: inside a box, the box's width (#652); negative as the book sets it
    (#640); 0 when unset."""
    value = str((css or {}).get(f"margin-{side}") or "").strip()
    sign = -1.0 if value.startswith("-") else 1.0
    em = _padding_em(value.lstrip("-"), _font_ratio(css))
    if isinstance(em, tuple):
        return sign * em[1] * (page_pt() / 1200) * share
    return 0.0 if em is None else sign * em


def _hidden_rule_em(css):
    """(margin-top, height, margin-bottom) of an <hr> that draws nothing, in
    the 16px root's ems, as Kindle Previewer 4.0.3 spaces it on the
    paragraph after (my probes, #643): 0.6 of each, on its own scale. An em
    is the rule's own. A 5% margin is 1.85 lh, where a paragraph's is 1.333.
    With no height its margins collapse into one, with each other and the
    paragraphs' around it; with a height the book sets, even 1px, each
    collapses with its own side's (QA on #649)."""
    ratio = _font_ratio(css)

    def em(value):
        m = re.fullmatch(r"\s*(-?[\d.]+)\s*(px|pt|em|rem|%)?\s*", str(value or ""))
        if not m or m.group(2) is None:
            return 0.0
        unit = m.group(2)
        n = float(m.group(1)) * _HIDDEN_RULE_EM_PER_UNIT[unit]
        return n * ratio if unit == "em" else n

    top, bottom = em(css.get("margin-top")), em(css.get("margin-bottom"))
    height = max(0.0, em(css.get("height")))
    share = _HIDDEN_RULE_SHARE
    if not height:
        return _collapse(top, bottom) * share, 0.0, 0.0
    return top * share, height * share, bottom * share


#: The line a spacer paragraph stands for in Kindle Previewer 4.0.3 when no
#: element sets a line height, at any font size (my probes, #663, #668).
_SPACER_LINE_PX = 19.0


def _spacer_em(css):
    """(margin-top, height, margin-bottom) of a spacer paragraph, in the 16px
    root's ems, as Kindle Previewer 4.0.3 spaces it (#663): as an <hr> that
    draws nothing with a height, its own margins and its line, in whole px
    (my probes, #668): 19px when no element sets a line height; 4/3 of its
    font size for a `normal` the book writes (21px at 16px); else its own
    line height (QA on #667: 1.5, 2, 3 and 40px; 1.2 at 32px is 38px)."""
    own = css_font_size(css) or 16.0
    factor = css.get("line-height")
    declared = str(css.get("line-height-declared") or "").strip().lower()
    if declared == "normal":
        line = round(own * 4 / 3)
    elif isinstance(factor, (int, float)) and (declared or abs(factor - 1.2) > 1e-6):
        line = round(factor * own)
    else:
        line = _SPACER_LINE_PX
    return _hidden_rule_em(
        {
            "margin-top": css.get("margin-top"),
            "margin-bottom": css.get("margin-bottom"),
            "font-size": css.get("font-size"),
            "height": f"{line}px",
        }
    )


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
    """A box's padding per side, in its own ems or ("%", n), or None when it
    has none: a px, pt or rem padding is the 16px root's, as Kindle Previewer
    4.0.3 measures it (#647). Unlike a cell, a box has no default padding
    (#238)."""
    sides = (css or {}).get("padding-sides")
    if not sides:
        return None
    ratio = _font_ratio(css)

    def own(value):
        em = _padding_em(value, ratio) or 0.0
        return em if isinstance(em, tuple) else em / ratio

    out = {side: own(v) for side, v in zip(_BORDER_SIDES, sides)}
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


def _only_block_child(cell):
    """The one block element a cell holds and nothing beside it, or None."""
    kids = [k for k in cell if isinstance(k.tag, str)]
    if len(kids) != 1 or _local_tag(kids[0].tag) not in (
        {"p", "div", "blockquote"} | _HEADING_TAGS
    ):
        return None
    if (cell.text or "").strip() or (kids[0].tail or "").strip():
        return None
    return kids[0]


def _table_dir(cell):
    """The `dir` of a cell's table, or of what holds it: a cell's or a row's
    own `dir` doesn't count, as Kindle Previewer 4.0.3 has it (#570)."""
    table = next(
        (a for a in cell.iterancestors() if _local_tag(a.tag) == "table"), cell
    )
    return next(
        (a.get("dir") for a in (table, *table.iterancestors()) if a.get("dir")), ""
    )


def _cell_block_style(cell, css, block_style=None, style_resolver=None):
    """A cell's, or a cell paragraph's, block style with its alignment: the
    book's, else none, as Kindle Previewer 4.0.3 writes a cell in a page with
    a DOCTYPE; a Paperwhite then shows it justified, as Previewer's (#599).
    In a right-to-left table too: Previewer writes the direction (`$682`)
    and no alignment there (#605's P605, which replaced #479's quirks-mode
    right). A header cell is left unset,
    so it stays centred, unless it sets its own alignment (#589)."""
    if block_style is None:
        block_style = compute_block_style(css) if css is not None else None
    if _local_tag(cell.tag) == "th" and style_resolver is not None:
        block_style = block_style or compute_block_style({})

        # A header cell's alignment counts only when it sets its own, as
        # Kindle Previewer 4.0.3 has it (#589), even one equal to its row's
        # (#601); otherwise it is centred. A data cell inherits as usual, in
        # a page with a DOCTYPE.
        def raw(elem, key="text-align"):
            css_of = style_resolver(elem) if elem is not None else None
            return str((css_of or {}).get(key) or "").strip().lower()

        mine = raw(cell)
        own = raw(cell, "own-text-align")
        own_paragraph = (
            block_style["align"] != compute_block_style({"text-align": mine})["align"]
        )
        if not own_paragraph:
            # Centred, written out: a header cell's paragraphs otherwise
            # take the book's alignment.
            # Keywords as Previewer reads them on a header cell (#616):
            # inherit is the nearest alignment set in CSS above it, not by an
            # `align=` attribute, else left (#621); initial left; start
            # centre. In a right-to-left table initial is right and end left.
            if own == "inherit":
                own = next(
                    (
                        a_own
                        for a in cell.iterancestors()
                        if (a_own := raw(a, "own-text-align"))
                        and a_own != "inherit"
                        and a_own
                        != str(a.get(_SOURCE_ALIGN) or a.get("align") or "")
                        .strip()
                        .lower()
                    ),
                    "left",
                )
            rtl = _table_dir(cell).lower() == "rtl"
            own = {
                "initial": "right" if rtl else "left",
                "start": "center",
                **({"end": "left"} if rtl else {}),
            }.get(own, own)
            align = compute_block_style({"text-align": own})["align"]
            block_style = {**block_style, "align": align or "center"}
        # `<th><p style="text-align: right">`: the paragraph's own, as for a
        # data cell (#532), Previewer's too.
        inner = _only_block_child(cell)
        if inner is not None and raw(inner) != mine:
            own = compute_block_style({"text-align": raw(inner)})["align"]
            if own:
                block_style = {**block_style, "align": own}
    if _local_tag(cell.tag) == "th" or (block_style or {}).get("align"):
        return block_style
    inner = _only_block_child(cell)
    if inner is not None and style_resolver is not None:
        # `<td><p style="text-align: right">`: the paragraph's own, as
        # Previewer writes it (QA on #532).
        own = compute_block_style(style_resolver(inner) or {})["align"]
        if own:
            return {**(block_style or compute_block_style({})), "align": own}
    return block_style


def _cell_start_end(block_style, cell, css):
    """A data cell's `text-align: start/end` from its own direction, as a
    paragraph's (QA's Q60, Q61; #515). A header cell's start stays centred
    (#616)."""
    if _local_tag(cell.tag) == "th":
        return block_style
    return _start_end_align(block_style, css)


def _header_not_bold(cell, css):
    """A header cell whose weight comes to normal, as `<th style="font-weight:
    normal">`: not bold, as Kindle Previewer 4.0.3 writes it (#597)."""
    weight = str((css or {}).get("font-weight") or "").strip().lower()
    # `bolder` reads as 400 here; the cell's block style makes it bold.
    return (
        _local_tag(cell.tag) == "th"
        and bool(weight)
        and parse_weight(weight) < BOLD_WEIGHT_THRESHOLD
    )


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
        "block_style": _cell_start_end(
            _with_bidi(
                _cell_block_style(cell, css, style_resolver=style_resolver),
                cell,
                style_resolver,
            ),
            cell,
            css,
        ),
        "header": _local_tag(cell.tag) == "th",
        "not_bold": _header_not_bold(cell, css),
        # A heading written in a cell: the section's opening, as a heading
        # outside a table is (#492).
        "heading": any(
            isinstance(d.tag, str) and _local_tag(d.tag) in _HEADING_TAGS
            for d in cell.iter()
        ),
        "colspan": _span_attr(cell, "colspan"),
        "rowspan": _span_attr(cell, "rowspan"),
        "valign": _cell_valign(cell, style_resolver),
        # Its fill, as Kindle Previewer writes it on the cell (#502).
        "background": _box_background(css),
    }


def _paragraph_cell(cell, style_resolver, walk_cell):
    """A cell holding several blocks, as a container of paragraphs (#261).

    `walk_cell` walks the cell as the body is walked, so its headings, list
    items, loose text and anchors become the same blocks they would outside
    a table. Returns None when that leaves no text."""

    def in_cell(p):
        if p.get("type") == "box":
            # Its paragraphs are the cell's text, aligned as the cell's
            # (#645): a <th>'s centred.
            return {**p, "blocks": [in_cell(k) for k in p["blocks"]]}
        return {
            **p,
            "block_style": _cell_block_style(
                cell, None, p.get("block_style"), style_resolver
            ),
        }

    paragraphs = [in_cell(p) for p in walk_cell(cell) if p.get("text")]
    if not paragraphs:
        return None
    css = style_resolver(cell) if style_resolver is not None else None
    return {
        "text": " ".join(p["text"] for p in paragraphs),
        "spans": [],
        "anchor_ids": [],
        "anchor_offsets": {},
        "block_style": _cell_start_end(
            _with_bidi(
                _cell_block_style(cell, css, style_resolver=style_resolver),
                cell,
                style_resolver,
            ),
            cell,
            css,
        ),
        "header": _local_tag(cell.tag) == "th",
        "not_bold": _header_not_bold(cell, css),
        "colspan": _span_attr(cell, "colspan"),
        "rowspan": _span_attr(cell, "rowspan"),
        "valign": _cell_valign(cell, style_resolver),
        "background": _box_background(css),
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

    def holds_box(cell):
        """Whether a block in the cell is bordered or filled: walked as the
        body is, it is a box in the cell, as Kindle Previewer 4.0.3 draws
        it; one cell entry lost it (#645)."""
        if style_resolver is None:
            return False
        for d in cell.iter():
            if d is cell or _local_tag(d.tag) not in _BOX_TAGS:
                continue
            css = style_resolver(d) or {}
            if _css_border(css) is not None or _box_background(css) is not None:
                return True
        return False

    def spaced_block(cell):
        """Whether the cell's one block has margins of its own: Kindle
        Previewer 4.0.3 writes them in the cell, as it does a cell of
        several blocks' (#681: a cell of one <p>, 0.833 above and below)."""
        if style_resolver is None:
            return False
        for d in cell.iter():
            if d is cell or _local_tag(d.tag) not in _CELL_BLOCK_TAGS:
                continue
            css = style_resolver(d) or {}
            if _margin_em(css, "top") or _margin_em(css, "bottom"):
                return True
        return False

    def build_cell(cell):
        built = None
        # A cell with a picture is walked as the body is, so the picture is a
        # paragraph of its own, which the generator writes as an image (#262).
        # Only what an <epub:switch> draws counts (#315).
        shown = _shown_tree(cell)
        has_picture = any(_local_tag(d.tag) == "img" for d in shown.iter())
        if walk_cell is not None and (
            _cell_paragraph_count(shown) >= 2
            or has_picture
            or holds_box(shown)
            or (_cell_paragraph_count(shown) and _loose_cell_text(shown))
            or spaced_block(shown)
        ):
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

    def own_bidi(elem):
        """`elem`'s direction and language when it sets one itself, else
        None: a row or group that sets none passes its table's through."""
        bidi = _bidi(elem, style_resolver)
        return bidi if bidi.get("set_here") else None

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
                    # A row's or row group's own direction and language, on
                    # its entry as Previewer writes them (P605 T04, T05).
                    "bidi": own_bidi(child),
                    "group_bidi": (
                        own_bidi(container)
                        if _local_tag(container.tag) in _ROW_GROUPS
                        else None
                    ),
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
            # Its direction and language: its cells repeat only theirs that
            # differ, as Kindle Previewer 4.0.3 writes them (#246).
            "bidi": _bidi(table, style_resolver),
            # A horizontal-tb table in vertical-rl text: Previewer sets it
            # horizontal on the table itself, with no container (QA's P749
            # Q02, #247).
            **({"horizontal": True} if table.get(_HORIZONTAL) is not None else {}),
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
#: An element's `align=` attribute, which calibre turns into a class's
#: text-align when it flattens the CSS (#621).
_SOURCE_ALIGN = "data-kfxgen-align"


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
            if elem.get("align"):
                elem.set(_SOURCE_ALIGN, elem.get("align").strip().lower())


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
            for part in paragraphs:
                if part.get("type") == "box":
                    # A box in a cell (#645): its blocks are the targets.
                    for kid in part["blocks"]:
                        key_block(kid)

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


def _collapse(a, b):
    """Two touching margins as one, as in a browser: the largest positive
    plus the most negative (#640)."""
    return max(0.0, a, b) + min(0.0, a, b)


def _takes_margin(block):
    """Whether kfxgen writes the book's margins on `block`: a paragraph, a
    heading (#658) or a box, as Kindle Previewer 4.0.3 spaces a box (#643),
    not a table or a rule (#553)."""
    return block.get("type") in (None, "box")


def _spaces_itself(block):
    """Whether kfxgen writes `block`'s space above its own way: a rule
    (#230). The paragraph before it then keeps no space below, or the gap
    was both (QA on #639). A heading did too, a line above it, until it took
    the book's margins as Previewer does (#658)."""
    return block.get("type") == "rule"


def _ems_own(block):
    """How many of the 16px root's ems are one of `block`'s own: its font
    size against 16px. A box's is its container's (#647)."""
    px = block.get("font_px") if block.get("type") == "box" else None
    return (px or (block.get("block_style") or {}).get("font_size") or 16.0) / 16.0


def _place_margin(
    prev,
    b,
    above,
    top,
    lines,
    on_purpose,
    fixed=0.0,
    pad_up=0.0,
    pad_own=0.0,
    outer_up=0.0,
    outer_own=0.0,
):
    """The gap above `b`, as Kindle Previewer 4.0.3 writes it (#553, #538):
    the larger of the margin waiting above (`above`, the block before's
    margin-bottom or an invisible <hr>'s) and `b`'s own margin-top, as margins
    collapse in a browser, plus the spacer paragraphs' lines (in ems). `b` takes it
    when it has a margin-top of its own; otherwise the block before keeps it
    as its space below and `b` takes none. Margins and padding come in the
    16px root's ems, as Previewer compares them, and are written in the
    block's own (#647). A wrapper's padding above `b`
    (`pad_own`) or below the block before (`pad_up`) keeps the margins on
    its two sides apart: the wrapper's own margin (`outer_own`, `outer_up`)
    collapses with the outside, and the paragraph's is added inside (#640,
    QA on #642). `fixed` is space margins don't collapse through: a rule
    with a height between (QA on #649)."""
    if prev is None:
        if _takes_margin(b):
            # An invisible <hr> before it keeps its margin below, collapsed
            # with the margin it touches: a padded wrapper's, else `b`'s own.
            # A box has none, and lost it (#659, QA on #662).
            if pad_own:
                space = _collapse(above, outer_own) + pad_own + top
            else:
                space = outer_own + _collapse(above, top)
            b["margin_above"] = (space + fixed) / _ems_own(b) + lines
        return
    up, own = above, top
    if _spaces_itself(b):
        # The margin waiting above meets the rule's own, as Kindle Previewer
        # 4.0.3 writes the rule's `$47`; a padding below the block before
        # stays inside it, as for a paragraph. The block before keeps none
        # (QA on #639), and the rule had only its own (QA on #720).
        rule = b["rule"]
        if pad_up:
            gap = _collapse(outer_up, rule["space_above"]) + up + pad_up
        else:
            gap = _collapse(up, rule["space_above"])
        rule["space_above"] = gap + fixed
        return
    if pad_up or pad_own:
        outside = _collapse(outer_up if pad_up else up, outer_own if pad_own else own)
        inside_up = up + pad_up if pad_up else 0.0
        inside_own = pad_own + own if pad_own else 0.0
        gap = outside + inside_up + inside_own + fixed
        # As without padding: `b` takes it with a margin or padding of its
        # own above, else the block before keeps it below.
        if _takes_margin(b) and (
            pad_own or top or outer_own or not _takes_margin(prev)
        ):
            b["margin_above"] = gap / _ems_own(b) + lines
        elif _takes_margin(prev) and not _spaces_itself(b):
            prev["margin_below"] = gap / _ems_own(prev)
            if _takes_margin(b):
                b["margin_above"] = lines
        return
    gap = _collapse(up, own) + fixed
    if top != 0 or on_purpose or not _takes_margin(prev):
        if _takes_margin(b):
            b["margin_above"] = (gap + pad_up + pad_own) / _ems_own(b) + lines
    else:
        if not _spaces_itself(b):
            prev["margin_below"] = (above + pad_up) / _ems_own(prev)
        if _takes_margin(b):
            b["margin_above"] = pad_own / _ems_own(b)
    if (
        not _takes_margin(b)
        and not _spaces_itself(b)
        and _takes_margin(prev)
        and (up > own or fixed)
    ):
        # Before a table: the paragraph above keeps it, as Previewer writes it,
        # with a spacer's or a sized rule's space between (QA on #667).
        prev["margin_below"] = (above + fixed) / _ems_own(prev)


class _ById:
    """A mapping keyed by object identity, for blocks, which are dicts and
    can't be keys. It holds each key, so an address is never given to a new
    block while the mapping is in use: keyed by `id()` alone, a freed
    block's address went to a new one, which took its margins, and spacing
    changed from one conversion to the next (#712)."""

    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        hit = self._d.get(id(key))
        return default if hit is None else hit[1]

    def setdefault(self, key, value):
        return self._d.setdefault(id(key), (key, value))[1]

    def __getitem__(self, key):
        return self._d[id(key)][1]

    def __setitem__(self, key, value):
        self._d[id(key)] = (key, value)


def _space_between(blocks, margins, measured=True, paddings=None):
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
    fixed = 0.0  # space past a rule with a height (QA on #649)
    # A padded block's padding, and the margin inside it, which the spacers
    # after it don't reach (#685).
    held = 0.0
    on_purpose = False
    for b in blocks:
        kind = b.get("type")
        if kind == "space":
            prev_pads = (paddings or _ById()).get(out[-1]) if out else None
            if measured and not on_purpose and prev_pads and prev_pads[1]:
                # After a padded block the spacer meets the margin outside
                # its padding; the padding, and the margin inside it, stay
                # where they are, as Previewer spaces them (#685).
                held = above + prev_pads[1]
                above = prev_pads[3]
                prev_pads[3] = 0.0
                ratio = out[-1].get("outer_em_ratio")
                if ratio and b["margin"]:
                    # In the size of the element that set it (QA on #688).
                    above /= ratio
            if (
                b["margin"]
                and not on_purpose
                and not held
                and out
                and out[-1].get("bottom_in_em")
                and out[-1].get("own_bottom_em")
            ):
                # The margin-bottom waiting above, in em or rem, meets the
                # first spacer's or rule's as Previewer reads it: in the
                # block's own size, an h3's 1em as 1em, not 1.17, and a 0.8em
                # paragraph's as 1em, when the block's own margin is in em or
                # rem too; a px or % one as before (#669, QA on #673).
                above /= _ems_own(out[-1])
            if b.get("height"):
                # Margins don't collapse through it: the one above it meets
                # the waiting margin, the one below it the next block's.
                fixed += _collapse(above, b["margin"]) + b["height"]
                above = b["margin_after"]
            else:
                above = max(above, b["margin"])
            lines += b["line"]
            on_purpose = True
            continue
        top, bottom = margins.get(b, (0.0, 0.0))
        if measured:
            pads = paddings or _ById()
            up_pads = (pads.get(out[-1]) if out else None) or (0.0,) * 4
            own_pads = pads.get(b) or (0.0,) * 4
            _place_margin(
                out[-1] if out else None,
                b,
                above,
                top,
                lines,
                on_purpose,
                fixed=fixed + held,
                pad_up=0.0 if held else up_pads[1],
                pad_own=own_pads[0],
                outer_up=0.0 if held else up_pads[3],
                outer_own=own_pads[2],
            )
            if kind == "box":
                # Its paragraphs are spaced the same way, inside it (#640).
                b["blocks"] = _space_between(b["blocks"], margins, measured, paddings)
                last = b["blocks"][-1] if b["blocks"] else None
                tail = last.pop("trailing_space", 0.0) if last else 0.0
                # A vertical block's blocks end at its left (QA's U03, U16).
                vertical = bool(b["box"].get("vertical"))
                end_side = "left" if vertical else "bottom"
                edge = (b["box"].get("border") or {}).get(end_side) or (
                    b["box"].get("padding") or {}
                ).get(end_side)
                keep = last.get("padding_below", 0.0) if last is not None else 0.0
                if (
                    not edge
                    and last is not None
                    and (last.get("margin_below") or 0.0) > keep
                    and _has_real_text(last.get("text"))
                ):
                    # Nothing at its bottom edge: the space below its last
                    # block leaves the box, as Previewer writes it; its own
                    # padding stays, and a picture's spacer is dropped, as
                    # Previewer drops it (QA on #682).
                    tail = max(tail, (last["margin_below"] - keep) * _ems_own(last))
                    last["margin_below"] = keep
                if tail and edge:
                    # Spacers after a table that ends the box: below it,
                    # inside the box, in its padding (#675).
                    pad = dict(
                        b["box"].get("padding") or dict.fromkeys(_BORDER_SIDES, 0.0)
                    )
                    pad["bottom"] = (pad.get("bottom") or 0.0) + tail / _ems_own(b)
                    b["box"]["padding"] = pad
                elif tail and vertical:
                    # Along its own axis: its space after it, its left.
                    b["box"]["end"] = _collapse(b["box"].get("end") or 0.0, tail)
                elif tail:
                    # It collapses with the box's margin below, where the run
                    # around the box meets it, the next block's or its own end
                    # (#680, QA on #682).
                    mine = margins.setdefault(b, [0.0, 0.0])
                    mine[1] = bottom = _collapse(mine[1], tail)
        if kind is None:
            if out:
                beside = b.get("heading") or out[-1].get("heading")
                how = None if beside else "spacer" if on_purpose else "margin"
                if out[-1].get("type") is not None:
                    # After a table, a box or a rule: a margin there is
                    # theirs to space.
                    how = how if how == "spacer" else None
                pads = paddings or _ById()
                padding = (pads.get(out[-1]) or (0.0,) * 4)[1] + (
                    pads.get(b) or (0.0,) * 4
                )[0]
                gaps.append(
                    (b, round(_collapse(above, top) + fixed + lines + padding, 4), how)
                )
        # A box's margin-bottom waits for the next block, as a paragraph's.
        above = bottom if kind in (None, "box") else 0.0
        lines = 0.0
        fixed = 0.0
        held = 0.0
        on_purpose = False
        out.append(b)
    if measured and out and on_purpose and not _takes_margin(out[-1]):
        # Spacers after a table or a picture that ends the run: their space,
        # for a box to keep inside it (#675).
        out[-1]["trailing_space"] = fixed + above + lines
    if measured and out and _takes_margin(out[-1]):
        # A file's or a box's last paragraph keeps its margin-bottom (#553),
        # met by the spacers and invisible <hr>s after it: their space stays
        # below it, inside the box, as Previewer writes it (#670).
        last_pads = (paddings or _ById()).get(out[-1]) or (0.0,) * 4
        own = _ems_own(out[-1])
        if on_purpose:
            below = (fixed + above) / own + lines
        else:
            below = margins.get(out[-1], (0, 0))[1] / own
        out[-1]["margin_below"] = (
            max(out[-1].get("margin_below") or 0.0, below)
            + (last_pads[1] + last_pads[3]) / own
        )
        # Its own padding, which stays with it in an open box (QA on #682).
        out[-1]["padding_below"] = last_pads[1] / own
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
    rule_ids = set()  # an <hr>'s own ids: they name the rule (QA on #720)
    # List markers awaiting the next text block: an item's number belongs on
    # the first text it holds, which may sit in a nested <p> (#201). An entry
    # is (marker, ordinal or None).
    pending_markers = []
    list_ordinals = {}  # list element -> {li: ordinal}
    # Space between paragraphs (#230): each block's own margin-top and
    # -bottom, in ems, with its containers' added at its edges. A spacer
    # paragraph or an <hr> that draws nothing is a {"type": "space"} block
    # until the end, where the space goes above the next paragraph.
    margins = _ById()  # block -> [top, bottom]
    paddings = _ById()  # block -> [its wrappers' padding-top, -bottom] (#640)
    # The index from which the next block shown follows a page break, set by
    # an element's `page-break-before` or the one before it's `-after` (#363).
    break_at = []
    # Cells walked as a paragraph when they hold no blocks (#565).
    leaf_cells = set()
    # (index, side): the next block shown from that index starts below the
    # floats on that side, set by an element's `clear`. An empty clearing
    # element passes it on, as Kindle Previewer does (#381).
    clear_at = []

    def _emit_fostered(table):
        """What sits in `table` outside its cells, in front of it, as Kindle
        Previewer 4.0.3 writes it (#704): loose text and inline elements run
        together into one paragraph, a block element is walked as its own.
        The ids carried from before the table name the first of them."""
        parts = []

        def flush():
            if not parts:
                return
            text, spans, mark_offsets = normalize_runs_with_anchors(parts)
            parts.clear()
            if not text.strip():
                pending_ids.extend(mark_offsets)
                return
            ids = _dedupe_keep_order(pending_ids + list(mark_offsets))
            pending_ids.clear()
            blocks.append(
                {
                    "text": text,
                    "spans": spans,
                    # The language and direction around the table, not the
                    # table's own, as Kindle Previewer 4.0.3 writes it (#718,
                    # QA on #720).
                    "block_style": _with_bidi(
                        None,
                        table if table.getparent() is None else table.getparent(),
                        style_resolver,
                    ),
                    "anchor_ids": ids,
                    "anchor_offsets": {aid: mark_offsets.get(aid, 0) for aid in ids},
                }
            )

        for el, text in _fostered_content(table):
            if el is None:
                parts.append((text, frozenset()))
            elif _local_tag(el.tag) == "img":
                # A picture runs with the words, as Previewer writes it. Its
                # token is built here: `_walk_inline` builds one for a child.
                if _is_hidden(el, style_resolver):
                    parts.extend(make_anchor_mark(a) for a in _subtree_anchor_ids(el))
                    continue
                parts.extend(make_anchor_mark(a) for a in _own_anchor_ids(el))
                token = _make_img_token(
                    _resolve_img_src(base_href, el.get("src", "") or ""),
                    el.get("alt", "") or "",
                    _img_size_hint(el, style_resolver),
                    None,
                    _img_inline_extras(el, style_resolver),
                )
                parts.append((token, frozenset()))
            elif el.tag in block_tags:
                flush()
                _walk(el)
            else:
                parts.extend(
                    _walk_inline(
                        el,
                        style_resolver=style_resolver,
                        is_root=False,
                        base_href=base_href,
                    )
                )
        flush()

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
        side = extra = None
        if href is None:
            href = _resolve_img_src(base_href, elem.get("src", "") or "")
            alt = elem.get("alt", "") or ""
            size = _img_size_hint(elem, style_resolver)
            side, size = _img_float(elem, elem.getparent(), size, style_resolver)
            # Its height too, which sizes a picture in vertical text (#247).
            extra = _img_inline_extras(elem, style_resolver)
        block = {
            "text": _make_img_token(href, alt, size, side, extra),
            "spans": [],
            "block_style": None,
            "anchor_ids": block_ids,
            "anchor_offsets": dict.fromkeys(block_ids, 0),
        }
        css = style_resolver(elem) if style_resolver is not None else None
        align = compute_block_style(css)["align"] if css is not None else None
        if align:
            # The text-align it inherits from its block: a picture in a
            # horizontal block of vertical text is placed by it, as Kindle
            # Previewer 4.0.3 writes it (#247, S755).
            block["picture_align"] = align
        if _displays_as_block(css):
            # Set to display as a block: a block picture in a vertical book's
            # horizontal block too, as Previewer writes it (PVA H10).
            block["picture_block"] = True
        blocks.append(block)

    def _emit_svg_blocks(elem):
        """Each <image> an <svg> draws becomes an image block of its own, and
        the words it draws as <text> follow as paragraphs: their place on the
        picture is lost, their reading order is not (#231)."""
        if elem.getparent() is not None and _svg_draws_shapes_only(elem):
            # Drawn only with shapes: a picture of its own (#709). A page
            # that is an SVG file is left as #231 leaves it.
            box = _svg_box_css(elem)
            _emit_image_block(
                elem,
                _svg_picture_href(elem),
                "",
                _img_size_hint(elem, lambda _e: box),
            )
            return
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
                    "block_style": _with_bidi(None, node, style_resolver),
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
        # A cell of words and pictures is one paragraph, as a <div> is in
        # the body, so a picture inside its words stays in them (#565). One
        # holding blocks is still walked block by block.
        leaf_cells.add(id(cell))
        _walk_element(cell)
        leaf_cells.discard(id(cell))
        out = blocks[start:]
        del blocks[start:]
        if style_resolver is not None:
            # The cell's paragraphs and boxes are spaced by their margins, as
            # outside a table, with its spacers' space (#645, #655, #675):
            # Kindle Previewer 4.0.3 writes each paragraph's margins in the
            # cell. A cell of one paragraph is written flat, as before.
            out = _space_between(out, margins, measured=True, paddings=paddings)
        else:
            out = [b for b in out if b.get("type") not in ("space", "rule")]
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
        """A row holding a table, on the rows path (#263). Each cell is
        walked as the body is: its paragraphs with their margins, its own
        text a paragraph of its own, each table a block of its own, so it can
        be native, as Kindle Previewer 4.0.3 splits the outer table round the
        inner one (#681: its cell paragraphs keep 0.833 above and below).
        Before, the row's text was run together, cell into cell, as lines
        with no space. Nesting a table in a table cell is never tried on a
        device; nothing is nested here."""
        align = None
        if style_resolver is not None:
            css = style_resolver(row)
            if css is not None:
                align = _row_align(
                    row, compute_block_style(css)["align"], style_resolver
                )
        # The row's own ids name its first piece.
        pending_ids.extend(_own_anchor_ids(row))
        start = len(blocks)
        for cell in row:
            if not isinstance(cell.tag, str) or _local_tag(cell.tag) not in (
                "td",
                "th",
            ):
                continue
            pending_ids.extend(_own_anchor_ids(cell))
            first = len(blocks)
            _walk_container(cell)
            weight = (
                str(
                    ((style_resolver(cell) if style_resolver else None) or {}).get(
                        "font-weight"
                    )
                    or ""
                )
                .strip()
                .lower()
            )
            if weight in ("bold", "bolder") or (
                weight.isdigit() and int(weight) >= 600
            ):
                # A header cell's bold, which its own text, written straight
                # in it, has no paragraph style to carry (QA on #692).
                for b in blocks[first:]:
                    style = b.get("block_style") or {}
                    if b.get("type") is None and "bold" not in style:
                        b["block_style"] = {**style, "bold": True}
        for b in blocks[start:]:
            if b.get("type") is not None:
                continue
            # A table's rows written as paragraphs keep their space (#219),
            # and the row's alignment where they set none of their own.
            b["row"] = True
            if align and not (b.get("block_style") or {}).get("align"):
                b["block_style"] = {**(b.get("block_style") or {}), "align": align}

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
            # Content outside the cells goes first, in front of the caption,
            # and takes the ids carried from before the table (#704).
            _emit_fostered(elem)
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
        is_block = (
            elem.tag in block_tags
            or _local_tag(elem.tag) == "math"
            or id(elem) in leaf_cells
        )
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
                bstyle = _with_bidi(bstyle, elem, style_resolver)
                bstyle = _start_end_align(bstyle, css if style_resolver else None)
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
                        # Its level, for the heading navigation (#461).
                        **(
                            {"heading_level": int(_local_tag(elem.tag)[1])}
                            if _local_tag(elem.tag) in _HEADING_TAGS
                            else {}
                        ),
                        # A table row written as a paragraph (#219).
                        "row": _local_tag(elem.tag) == "tr",
                        # Only a picture set to display as a block (PVA H10).
                        **(
                            {"picture_block": True}
                            if _lone_block_picture(elem, style_resolver)
                            else {}
                        ),
                        # A picture in it set to display as a block keeps
                        # pictures beside it apart (#762, QA's Q766 Z18,
                        # Z31, Z38, Z40).
                        **(
                            {"pictures_apart": True}
                            if style_resolver is not None
                            and any(
                                isinstance(d.tag, str)
                                and _local_tag(d.tag) == "img"
                                and _displays_as_block(style_resolver(d))
                                for d in elem.iterdescendants()
                            )
                            else {}
                        ),
                        # In a list item: Previewer floats no picture there
                        # (#699).
                        **(
                            {"in_list": True}
                            if _local_tag(elem.tag) == "li"
                            or any(
                                isinstance(a.tag, str) and _local_tag(a.tag) == "li"
                                for a in elem.iterancestors()
                            )
                            else {}
                        ),
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
                # Text written straight in a bordered or filled <div>, <p> or
                # <pre> is a box around that one paragraph (#310, #643); the
                # box takes its margins and padding. Unboxed, its padding
                # spaces it, as Previewer pads its text (#643).
                if id(elem) in leaf_cells:
                    # The cell itself, as below (#655).
                    pass
                elif not _make_box(elem, len(blocks) - 1, leaf=True):
                    _note_margins(
                        elem, len(blocks) - 1, _note_padding(elem, len(blocks) - 1)
                    )
            else:
                pending_ids.extend(ids)  # empty anchor block: carry ids forward
                if _is_spacer(elem):
                    css = style_resolver(elem) if style_resolver is not None else None
                    if css is None:
                        _emit_space(line=_LINE_SPACE_EM)
                    else:
                        top, height, bottom = _spacer_em(css)
                        _emit_space(margin=top, height=height, margin_after=bottom)
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
        if id(elem) in leaf_cells:
            # The cell itself: its padding is the cell's, written on it, and
            # a cell has no margins (#655).
            return
        if not _make_box(elem, start):
            _carry_container_margins(elem, start)
            _note_margins(elem, start, _note_padding(elem, start))

    def _emit_space(margin=0.0, line=0.0, height=0.0, margin_after=0.0):
        """Space for the next paragraph to take: a spacer's blank line, or an
        <hr> that draws nothing, whose margins collapse with the paragraphs'
        around it. A cell keeps only blocks with text, so in a cell it is
        dropped, as before."""
        blocks.append(
            {
                **_EMPTY_BLOCK,
                "type": "space",
                "margin": margin,
                "line": line,
                # A rule with a height: the margin past it (QA on #649).
                "height": height,
                "margin_after": margin_after,
            }
        )

    def _emit_rule(elem):
        """An <hr>: a rule, as Kindle Previewer writes it ($596), with its
        margins as space above and below, or just space when its CSS draws
        no border or filled bar (#230). Its width, alignment, border and
        fill as `_rule_look` reads them (#722). Its ids name the paragraph
        after it."""
        rule_ids.update(_own_anchor_ids(elem))
        pending_ids.extend(_own_anchor_ids(elem))
        css = style_resolver(elem) if style_resolver is not None else None
        look = _rule_look(css)
        if css is not None and _css_border(css) is None and "fill" not in look:
            top, height, bottom = _hidden_rule_em(css)
            _emit_space(margin=top, height=height, margin_after=bottom)
            return

        def space(side):
            if not (css or {}).get(f"margin-{side}"):
                return _RULE_SPACE_EM
            return _margin_em(css, side)

        blocks.append(
            {
                **_EMPTY_BLOCK,
                "type": "rule",
                "rule": {
                    "space_above": space("top"),
                    "space_below": space("bottom"),
                    # Its width, alignment, border and fill (#722).
                    **look,
                },
            }
        )

    def _note_padding(elem, start):
        """Record an unboxed container's padding-top on the first paragraph
        it produced and its padding-bottom on the last, added to what is
        there: Kindle Previewer 4.0.3 draws it as a padded container, and
        padding doesn't collapse (#640)."""
        if style_resolver is None:
            return (False, False)
        css = style_resolver(elem) or {}
        sides = css.get("padding-sides") or ()
        if len(sides) != 4:
            return (False, False)
        ratio = _font_ratio(css)

        def em(value):
            v = _padding_em(value, ratio)
            return v[1] * (page_pt() / 1200) if isinstance(v, tuple) else (v or 0.0)

        top, bottom = em(sides[0]), em(sides[2])
        if not (top or bottom):
            return (False, False)
        mine = [b for b in blocks[start:] if b.get("type") not in ("space", "rule")]
        if not mine:
            return (False, False)
        # A horizontal block first or last, in vertical text: the padding is
        # space before or after it, on its right and left, added to what it
        # holds; padding doesn't collapse (QA's Q764 W05, W06).
        for i, key, value in ((0, "before", top), (-1, "after", bottom)):
            box = (mine[i].get("box") or {}) if value else {}
            if box.get("horizontal"):
                # The margins it holds are inside this padding now: fixed,
                # with it; margins outside meet only each other (QA's W21,
                # W30), as `paddings` keeps them for a paragraph (#642).
                fixed, inner = f"{key}_fixed", f"{key}_inner"
                if fixed in box:
                    box[fixed] += (box.get(key) or 0.0) + value
                else:
                    # The margins inside the first padding still meet the
                    # block's own margin (QA's Q764C W41).
                    box[inner] = box.get(key) or 0.0
                    box[fixed] = value
                box[key] = 0.0
        top = 0.0 if (mine[0].get("box") or {}).get("horizontal") else top
        bottom = 0.0 if (mine[-1].get("box") or {}).get("horizontal") else bottom
        if not (top or bottom):
            return (False, False)
        # An inner wrapper's margin waiting outside its padding is now inside
        # this one's: padding keeps it from collapsing with the margins
        # beyond (QA on #642).
        first = paddings.setdefault(mine[0], [0.0] * 4)
        if top:
            first[0] += first[2] + top
            first[2] = 0.0
        last = paddings.setdefault(mine[-1], [0.0] * 4)
        if bottom:
            last[1] += last[3] + bottom
            last[3] = 0.0
        return (bool(top), bool(bottom))

    def _box_share(elem, side):
        """The share of the page a % margin on `side` of `elem` is measured
        against, as Kindle Previewer 4.0.3 measures it (#652; QA on #684):
        the width of the first block round it the margin stays inside, and
        of each block round that. Walking out, a block the margin collapses
        out of on that side (`elem` first or last in it, nothing there to
        stop it) is skipped. From the containing block out: one with a width
        of its own is that wide (CSS width is the content's, so nothing comes
        off), a % of the block round it; one without loses its side borders
        and padding. Side margins don't narrow it: a blockquote's 10% leaves
        its paragraphs' 10% as the page's. calibre writes its own page
        margins on the <body> in place of the book's, so a body's side
        margins, which narrow it in Previewer, can't be read."""
        chain = []
        for a in elem.iterancestors():
            if not isinstance(a.tag, str):
                continue
            if _local_tag(a.tag) in ("body", "html"):
                break
            chain.append(a)
        child = elem
        held = None
        for i, a in enumerate(chain):
            css = style_resolver(a) or {}
            pads = dict(zip(_BORDER_SIDES, css.get("padding-sides") or ()))
            stops = (_css_border(css) or {}).get(side) or _padding_em(
                str(pads.get(side) or "").strip()
            )
            # Nothing with content, and no text, on that side of `child` in
            # `a`: an empty element (an <a id> marking a place), or one not
            # shown, is no block, and the margin collapses past it (QA on
            # #684).
            kids = list(a)
            at = kids.index(child) if child in kids else -1
            near = kids[:at] if side == "top" else kids[at + 1 :]
            text = (a.text or "") if side == "top" else (child.tail or "")
            at_edge = at >= 0 and not (
                text.strip()
                or any(
                    (
                        _has_content(k)
                        and str((style_resolver(k) or {}).get("display") or "").strip()
                        != "none"
                    )
                    or (k.tail or "").strip()
                    for k in near
                )
            )
            if at_edge and not stops:
                child = a
                continue
            held = i
            break
        if held is None:
            return 1.0
        share = 1.0
        for a in reversed(chain[held:]):
            css = style_resolver(a) or {}
            width = parse_css_length(str(css.get("width") or ""))
            if width and width[1] == "$314" and 0 < float(width[0]) < 100:
                share *= float(width[0]) / 100
                continue
            if width and width[1] in _CSS_PX:
                share = float(width[0]) * _CSS_PX[width[1]] * 0.45 / page_pt()
                continue
            border = _css_border(css) or {}
            share -= sum(
                border.get(s, (None, 0.0, None))[1] / page_pt()
                for s in ("left", "right")
            )
            pads = dict(zip(_BORDER_SIDES, css.get("padding-sides") or ()))
            outer = share
            for s in ("left", "right"):
                em = _padding_em(str(pads.get(s) or "").strip())
                share -= (
                    em[1] / 100 * outer
                    if isinstance(em, tuple)
                    else (em or 0.0) / (page_pt() / 12)
                )
        return max(share, 0.05)

    def _note_margins(elem, start, padded=(False, False)):
        """Record `elem`'s margin-top on the first paragraph it produced and
        its margin-bottom on the last, keeping a larger one already there:
        margins that touch collapse to the larger, as in a browser (#230).
        A side with padding keeps its margin outside, apart from the
        paragraph's: margins don't collapse through padding (QA on #642)."""
        if style_resolver is None:
            return
        css = style_resolver(elem)
        top = _margin_em(css, "top", _box_share(elem, "top"))
        bottom = _margin_em(css, "bottom", _box_share(elem, "bottom"))
        mine = [b for b in blocks[start:] if b.get("type") not in ("space", "rule")]
        unit = str((css or {}).get("margin-bottom") or "").strip().lower()
        if mine and mine[-1].get("type") is None and "own_bottom_em" not in mine[-1]:
            # The paragraph's own margin-bottom, noted first: the margin
            # waiting above a spacer is read in its size unless this one is
            # set and not in em or rem (#669, QA on #673 and #677).
            mine[-1]["own_bottom_em"] = not bottom or unit.endswith("em")
        if not (top or bottom):
            return
        if not mine:
            return
        # A horizontal block first or last: this element is vertical text (a
        # horizontal box is the first horizontal element of its branch), so
        # its margin-top and -bottom are its space before and after it, its
        # right and left (`_vertical_box`). The block takes them on those
        # sides, not on its top and bottom (QA's P749 Q04, probe PQ4).
        for i, key, value in ((0, "before", top), (-1, "after", bottom)):
            box = (mine[i].get("box") or {}) if value else {}
            if box.get("horizontal"):
                # It meets the margins outside any padding the block took
                # (QA's W11); the padding and what is inside it stay apart
                # ("before_fixed", "before_inner" and the same after).
                box[key] = _collapse(box.get(key) or 0.0, value)
        top = 0.0 if (mine[0].get("box") or {}).get("horizontal") else top
        bottom = 0.0 if (mine[-1].get("box") or {}).get("horizontal") else bottom
        # A padded wrapper inside, here or deeper, keeps the paragraph's
        # margin apart: this one meets that wrapper's margin (#643).
        if padded[0] or (paddings.get(mine[0]) or (0.0,))[0]:
            outer = paddings[mine[0]]
            outer[2] = _collapse(outer[2], top)
        else:
            first = margins.setdefault(mine[0], [0.0, 0.0])
            first[0] = _collapse(first[0], top)
        if padded[1] or (paddings.get(mine[-1]) or (0.0,) * 2)[1]:
            outer = paddings[mine[-1]]
            if bottom and bottom >= outer[3]:
                # Outside the padding, the margin is this element's own: a
                # spacer after it reads it in this element's size if it is in
                # em or rem (#669, QA on #688).
                mine[-1]["outer_em_ratio"] = (
                    _font_ratio(css) if unit.endswith("em") else None
                )
            outer[3] = _collapse(outer[3], bottom)
        else:
            last = margins.setdefault(mine[-1], [0.0, 0.0])
            if bottom and bottom >= last[1]:
                # Whether the margin that wins is in em or rem: a spacer
                # after the block reads it in the block's own size (#669).
                mine[-1]["bottom_in_em"] = unit.endswith("em")
            last[1] = _collapse(last[1], bottom)

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
        # A horizontal-tb block in vertical-rl text: Previewer writes it in a
        # horizontal container of its own, whatever the tag (P247h).
        horizontal = elem.get(_HORIZONTAL) is not None
        # And a vertical-rl block inside one: a vertical container (PSB).
        vertical = elem.get(_VERTICAL) is not None
        turned = horizontal or vertical
        if style_resolver is None or (tag not in _SIZED_BOX_TAGS and not turned):
            return False
        css = style_resolver(elem) or {}
        border, fill = _css_border(css), _box_background(css)
        if tag not in _BOX_TAGS:
            border = fill = None
        kids = blocks[start:]
        # A wrapper around a picture is drawn at its width (#382); one of
        # only a floated picture keeps #243's floated picture instead, with
        # no box even when bordered or filled: a floated box stops the
        # Voyage wrapping text beside it (#382), and one at the page's width
        # drew its border round the page (QA on #644).
        width = _box_width(css)
        side = _float_side(css)
        if width is not None and not any(
            _IMG_TOKEN_RE.search(b.get("text") or "") for b in kids
        ):
            width = None
        if width is not None and side and len(kids) == 1:
            m = _IMG_TOKEN_RE.fullmatch(kids[0].get("text") or "")
            if m and m.group(4):
                return False
        if border is None and fill is None and width is None and not turned:
            return False
        if not kids or not any(
            b.get("type") in ("table", "box") or (b.get("text") or "").strip()
            for b in kids
        ):
            return False
        if leaf:
            style = dict(kids[0].get("block_style") or {})
            style["margin_left"] = style["margin_right"] = None
            if style.get("bidi"):
                # The box's own `dir` and `lang`, set on the box: Previewer
                # writes them on the box only (QA's P735b on #735).
                style["bidi"] = {
                    k: v for k, v in style["bidi"].items() if k != "set_here"
                }
            kids[0]["block_style"] = style
        past_start = {aid for b in kids[1:] for aid in b.get("anchor_ids") or ()}
        if toc_targets and past_start & set(toc_targets):
            return False
        left = parse_css_length(css.get("margin-left") or "")
        right = parse_css_length(css.get("margin-right") or "")
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
                "font_px": css_font_size(css),
                "blocks": kids,
                "anchor_ids": ids,
                "anchor_offsets": dict.fromkeys(ids, 0),
                "box": {
                    "border": border,
                    "background": fill,
                    "padding": _box_padding(css),
                    # Its own size: the generator writes its margins and
                    # padding, in its ems, against the book's main size
                    # (#647).
                    "font_px": css_font_size(css),
                    # A vertical block's left and right are its space after
                    # and before, in ems ("start", "end" below).
                    "margin_left": None if vertical else _page_share(left) or None,
                    "margin_right": None if vertical else _page_share(right) or None,
                    "width": width,
                    "width_px": _box_width_px(css) if width is not None else None,
                    "float": side if width is not None else None,
                    "centred": width is not None
                    and str(css.get("margin-left")).strip() == "auto"
                    and str(css.get("margin-right")).strip() == "auto",
                    # The box carries its direction and language; the
                    # paragraphs in it repeat only theirs that differ (QA's
                    # Q15, #246).
                    "bidi": _bidi(elem, style_resolver),
                    **({"horizontal": True} if horizontal else {}),
                    **(
                        {
                            "vertical": True,
                            "start": _margin_em(css, "right"),
                            "end": _margin_em(css, "left"),
                        }
                        if vertical
                        else {}
                    ),
                },
            }
        ]
        _note_box_margins(blocks[start], css)
        return True

    def _note_box_margins(box, css):
        """Record a box's margins, spaced as a paragraph's (#553): Kindle
        Previewer 4.0.3 writes them on the box, and its padding inside it
        (#643). On a side with no border or padding, the margin of the block
        inside meets the box's and collapses into it, as in a browser: a
        filled <div> takes its paragraph's margins (my probe, #643)."""
        top, bottom = _margin_em(css, "top"), _margin_em(css, "bottom")
        # In em or rem, a spacer after it reads it in the box's own size, as
        # a paragraph's (QA on #673, #676).
        in_em = bool(bottom) and str(
            css.get("margin-bottom") or ""
        ).strip().lower().endswith("em")
        box["bottom_in_em"] = in_em
        box["own_bottom_em"] = not bottom or in_em
        border = box["box"]["border"] or {}
        padding = box["box"]["padding"] or {}
        mine = [b for b in box["blocks"] if b.get("type") not in ("space", "rule")]
        # A spacer or rule after the last block meets its margin first: the
        # margin stays with it (QA on #682).
        spaced_end = bool(box["blocks"]) and box["blocks"][-1].get("type") in (
            "space",
            "rule",
        )
        turned = box["box"].get("vertical")
        for side, i, end in (("top", 0, 0), ("bottom", -1, 1)):
            inner = margins.get(mine[i]) if mine else None
            if turned:
                # A vertical block in a horizontal one: its blocks' first
                # and last margins run along its own axis, before and after
                # it, its right and left sides; they meet its own there, not
                # the horizontal block's (QA's Q763 T01, T12, T14).
                edge = "right" if end == 0 else "left"
                if inner is None or border.get(edge) or padding.get(edge):
                    continue
                key = "start" if end == 0 else "end"
                box["box"][key] = _collapse(box["box"][key], inner[end])
                inner[end] = 0.0
                continue
            if inner is None or border.get(side) or padding.get(side):
                continue
            if end and spaced_end:
                continue
            if end == 0:
                top = _collapse(top, inner[0])
            else:
                bottom = _collapse(bottom, inner[1])
            inner[end] = 0.0
        margins[box] = [top, bottom]

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
            if b.get("type") == "box" and b["box"].get("horizontal"):
                # This element is vertical text, as around every horizontal
                # block: its left and right are its top and bottom
                # (`_vertical_box`), and the block takes them there, in ems
                # (PQ4 L05).
                box = b["box"]
                for key, side in (("over", "left"), ("under", "right")):
                    em = _margin_em(css, side)
                    if em:
                        box[key] = (box.get(key) or 0.0) + em
                continue
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

    def _inline_picture_parts(img, parent):
        """An <img> child of `parent` as `_walk_inline` writes a picture in
        the words: its ids, then its token (#757). A hidden one gives only
        its ids (#428)."""
        if _is_hidden(img, style_resolver):
            return [make_anchor_mark(a) for a in _subtree_anchor_ids(img)]
        parts = [make_anchor_mark(a) for a in _own_anchor_ids(img)]
        size = _img_size_hint(img, style_resolver)
        side, size = _img_float(img, parent, size, style_resolver)
        token = _make_img_token(
            _resolve_img_src(base_href, img.get("src", "") or ""),
            img.get("alt", "") or "",
            size,
            side,
            _img_inline_extras(img, style_resolver),
        )
        return [*parts, (token, frozenset())]

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
            # The container's text-align: loose text in a centred <div> is
            # centred, as Kindle Previewer 4.0.3 writes it (P757 R04, R07,
            # R11, R12; QA's Q27 on #755). Only its alignment: the margins
            # and indent are the box's.
            css = style_resolver(elem) if style_resolver is not None else None
            align = compute_block_style(css)["align"] if css is not None else None
            block_style = _with_bidi(
                {"align": align} if align else None, elem, style_resolver, own=False
            )
            if align:
                block_style = _start_end_align(block_style, css)
            blocks.append(
                {
                    "text": text,
                    "spans": spans,
                    # The container's language and direction: a line number
                    # between two paragraphs, or a list item's own line before
                    # its nested list, is in its page's language, as Kindle
                    # Previewer 4.0.3 writes it (#718).
                    "block_style": block_style,
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
        among_words = (
            _pictures_among_words(
                elem,
                _holds_blocks,
                lambda img: (
                    style_resolver is not None
                    and _displays_as_block(style_resolver(img))
                ),
            )
            if is_block is None
            else set()
        )
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
            elif child in among_words:
                # A picture among the container's loose words runs with them,
                # one paragraph, as Kindle Previewer 4.0.3 writes it (P757,
                # #757); alone between blocks it stays a block of its own.
                inline_parts.extend(_inline_picture_parts(child, elem))
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
    blocks[:] = _space_between(
        blocks, margins, measured=style_resolver is not None, paddings=paddings
    )

    # Trailing anchors (e.g. <a id="eof"/> after the last leaf block) never
    # reach a subsequent block to flush to; snap them onto the last emitted
    # block instead of dropping them silently. Not onto a rule, which holds
    # no anchor (#230).
    if trailing_ids is not None and blocks:
        # Not a rule's own: it names the rule, in this file. A link to the
        # <hr> under a title alone opened the next chapter, where Kindle
        # Previewer 4.0.3 lands on the rule (QA on #720).
        trailing_ids.extend(i for i in pending_ids if i not in rule_ids)
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
            # One entry each, as Kindle Previewer writes them (#454).
            metadata["authors"] = [str(c) for c in oeb_book.metadata.creator]

        description = getattr(oeb_book.metadata, "description", None)
        if description:
            metadata["description"] = str(description[0])

        if oeb_book.metadata.language:
            metadata["language"] = _kfx_language(str(oeb_book.metadata.language[0]))

        if hasattr(oeb_book.metadata, "publisher") and oeb_book.metadata.publisher:
            metadata["publisher"] = str(oeb_book.metadata.publisher[0])

        if hasattr(oeb_book.metadata, "date") and oeb_book.metadata.date:
            raw_date = str(oeb_book.metadata.date[0])
            metadata["issue_date"] = raw_date[:10]  # YYYY-MM-DD from ISO format
            # calibre fills a year alone out to a day: the book's own OPF
            # says which it gave. Only a full date is one, as Previewer
            # takes it (#454).
            own = _opf_raw_dates(oeb_book)
            if own is not None:
                first = (own[0] if own else "").strip()
                metadata["issue_date"] = (
                    first[:10] if _FULL_DATE_RE.match(first) else None
                )

    except Exception as e:
        log.error(f"Error extracting metadata: {e}")

    return metadata


_FULL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _opf_raw_dates(oeb_book):
    """The book's own dc:date texts, as its OPF writes them, or None when
    the OPF can't be read (#454)."""
    parse = _container_parser(oeb_book)
    name = getattr(getattr(oeb_book, "container", None), "opfname", None)
    opf = parse(name) if parse and name else None
    if opf is None:
        return None
    return [
        "".join(d.itertext())
        for d in opf.xpath("//*[local-name()='metadata']/*[local-name()='date']")
    ]


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
    # Only the first entry titled "Contents" is rebuilt; a later one is kept
    # as the book has it, so nothing merged into it is lost (#714).
    book_contents = next(
        (k for k, c in enumerate(coords) if _normalize_title(c[2]) in _CONTENTS_TITLES),
        None,
    )

    def rewritten(k):
        if _normalize_title(coords[k][2]) in _CONTENTS_TITLES:
            return k == book_contents
        return _rewritten_page(coords[k][2])

    heads = []
    for k, (fi, si, title, _key, _level) in enumerate(coords):
        if (
            k == 0
            or coords[k - 1][1] != si
            or not spine_items[si].get("breaks_known")
            or rewritten(k)
            or rewritten(k - 1)
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
    for si, b in enumerate(spine_blocks):
        for block in b:
            # Its file, so a Contents page can tell its own (#551 review).
            block["_spine_file"] = si
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
    # Coords pointing before the entry listed above them: places, in the
    # book's TOC order, landing on their own block (#484).
    out_of_order = set()
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
            # Kept where the book lists it, as Kindle Previewer keeps it,
            # landing on its own block; the sections still follow the
            # document (#484).
            log.info(
                f"  TOC entry {entry['title']!r} out of document order; kept as a "
                "place, no section of its own"
            )
            out_of_order.add(len(coords))
        key = _entry_key(spine_items_ordered[si], frag)
        if key is None and len(coords) in out_of_order:
            # A whole-file entry out of order: the file's key, on its first
            # block, so it lands there and not at the section's start.
            key = _key_doc(spine_items_ordered[si]["href"], "")
        if (
            key
            and frag not in amap
            and key not in flat[fi].setdefault("anchor_keys", [])
        ):
            flat[fi]["anchor_keys"].append(key)
            flat[fi].setdefault("anchor_offsets", {})[key] = 0
        coords.append((fi, si, entry["title"], key, entry.get("level", 0)))
        if len(coords) - 1 not in out_of_order:
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
                if ch["title"] != LEADING_TITLE_FALLBACK and _IMG_TOKEN_RE.search(
                    head[0].get("text") or ""
                ):
                    # Titled with the words beside a picture: that paragraph
                    # prints them, so a heading of them would print them
                    # twice. Kindle Previewer 4.0.3 prints them once (#525).
                    ch["_omit_title_heading"] = True
                if ch["title"] == LEADING_TITLE_FALLBACK:
                    # Invented label, not the book's own words — suppress it
                    # as a heading so it cannot print onto the page. Set only
                    # on this synthetic path: a book whose own TOC names a
                    # chapter "Front Matter" comes through `coords` and keeps
                    # its heading. (#133)
                    ch["_omit_title_heading"] = True
                # Not in the source TOC: this chapter exists because these
                # blocks precede the first TOC anchor. Kindle Previewer 4.0.3
                # lists none, whatever its first line (#558); #143 kept an
                # entry for one that read as a heading. The content still
                # ships.
                ch["_omit_from_toc"] = True
                chapters.append(ch)

    heads = _section_heads(coords, flat, spine_items_ordered, nav_flat, file_offset)
    for k in (*same_spot, *out_of_order):
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
                # On the section's own block: it opens the section (None). One
                # out of order lands on its own block (#484).
                key = (
                    coords[j][3]
                    if twin in out_of_order
                    else (None if twin == k else coords[twin][3])
                )
                subs.append({"title": coords[j][2], "key": key, "level": coords[j][4]})
            if subs:
                ch["sub_entries"] = subs
            chapters.append(ch)

    # The furthest file reached: an entry out of order can point back (#484).
    last_si = max(c[1] for c in coords)
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
    fixed_layout=False,
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
    spine_items = _spine_in_order(oeb_book, toc_entries)
    _respell_toc_hrefs(toc_entries, spine_items)
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

    for i, item in enumerate(spine_items):
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
            if fixed_layout:
                # A fixed-layout book (a comic) stays horizontal, each file
                # too, as the #711 manga passed on the Paperwhite; a page
                # set vertical-rl isn't checked on a Kindle (#752).
                _drop_vertical_marks(item.data)
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
            # Each block carries its file's writing mode, so a chapter
            # spanning files knows its own (#247). A file that sets none is
            # horizontal: in a vertical book Previewer sets its section so
            # (QA's P746 m2, m4, m5).
            if resolver is not None:
                writing_mode = (
                    "vertical-rl"
                    if not fixed_layout
                    and _file_writing_mode(item.data, resolver) == "vertical-rl"
                    else "horizontal-tb"
                )
                for b in blocks:
                    b["writing_mode"] = writing_mode
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
                    oeb_book, log, metadata, cover_href, native_tables, fixed_layout
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
        if not _page_shows_something(item["text"], cover_href):
            # The book's own cover page: the cover page shows it, as on the
            # TOC's path (#32, #524).
            log.info(f"  Skipping cover-only spine item {i + 1}")
            continue
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
        return _with_listings_kept(
            oeb_book, log, metadata, cover_href, native_tables, fixed_layout
        )
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


def _with_listings_kept(
    oeb_book, log, metadata, cover_href, native_tables, fixed_layout=False
):
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
        fixed_layout=fixed_layout,
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


def _respell_toc_hrefs(entries, spine):
    """Each TOC href that names a spine file only up to letter case or
    %-encoding, in the spine's spelling, when one file fits (#549). An NCX
    writing `01_Cover.xhtml` for the OPF's `01_cover.xhtml` lost every
    entry: calibre adds that spelling as a file of its own, which #476 drops
    as the same file. Files are matched by name, as chapters are."""
    names = {_normalize_href(i.href) for i in spine}
    loose = {}
    for i in spine:
        loose.setdefault(_normalize_href(unquote(i.href)).lower(), set()).add(i.href)
    for entry in entries:
        path, sep, frag = entry["href"].partition("#")
        if _normalize_href(path) in names:
            continue
        fits = loose.get(_normalize_href(unquote(path)).lower(), set())
        if len(fits) == 1:
            entry["href"] = next(iter(fits)) + sep + frag


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


def _split_files_after_contents(ch):
    """The pages from files after the contents page's own, as a chapter of
    their own, or None (#551 review).

    A contents entry's section runs on to the next TOC entry, so files the
    TOC doesn't list after it are part of it, and the rebuild replaced them:
    a foreword and an introduction, 64 blocks, in one library book. Kindle
    Previewer 4.0.3 prints them. They follow the rebuilt page, out of the
    navigation and with no heading, as #393's front matter does."""
    blocks = ch.get("blocks") or []
    own = blocks[0].get("_spine_file") if blocks else None
    cut = next(
        (
            n
            for n, b in enumerate(blocks)
            if b.get("_spine_file") is not None and b["_spine_file"] != own
        ),
        None,
    )
    if own is None or cut is None or not _count_text_blocks(blocks[cut:]):
        return None
    kept, ch["blocks"] = blocks[cut:], blocks[:cut]
    ch["text"] = "\n\n".join(b["text"] for b in ch["blocks"] if b.get("text"))
    return {
        "title": LEADING_TITLE_FALLBACK,
        "text": "\n\n".join(b["text"] for b in kept if b.get("text")),
        "blocks": kept,
        "toc_level": ch.get("toc_level", 0),
        "_omit_from_toc": True,
        "_omit_title_heading": True,
    }


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
    # The rebuilt page's lines print in the direction and language of the
    # page it replaces (#714), read before any of its text is split off.
    rebuilt_bidi = {}
    if nav_contents_ch is not None:
        rebuilt_bidi[id(nav_contents_ch)] = page_bidi(nav_contents_ch.get("blocks"))
        lead = _split_text_above_listing(nav_contents_ch)
        if lead is not None:
            chapters.insert(chapters.index(nav_contents_ch), lead)
            log.info(f"  Kept the text above the contents listing: {lead['title']}")
    # Only the book's first page titled "Contents" is its contents page and
    # rebuilt. Later ones are the contents of a part of it, as each play's in
    # pg100, and are printed as the book has them, as Kindle Previewer 4.0.3
    # prints them (#714): rebuilding each replaced a play's listing of its
    # scenes with the whole book's.
    book_contents = nav_contents_ch or next(
        (
            c
            for c in chapters
            if not c.get("_from_spine")
            and _normalize_title(c["title"]) in _CONTENTS_TITLES
        ),
        None,
    )
    if book_contents is not None and id(book_contents) not in rebuilt_bidi:
        rebuilt_bidi[id(book_contents)] = page_bidi(book_contents.get("blocks"))
    # Front matter after a listing about to be replaced is split off before
    # any rebuild, whose links name chapters by their place in the list
    # (#393). Only a chapter that dropped a listing has a place for it.
    for ch in list(chapters):
        if ch is book_contents:
            later = _split_files_after_contents(ch)
            if later is not None:
                chapters.insert(chapters.index(ch) + 1, later)
                log.info("  Kept the pages after the contents page")
            after = _split_front_matter_below_listing(ch)
            if after is not None:
                chapters.insert(chapters.index(ch) + 1, after)
                log.info("  Kept the front matter after the contents listing")
    for ch in chapters:
        if ch is nav_contents_ch:
            ch["page_bidi"] = rebuilt_bidi[id(ch)]
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
        elif ch is book_contents and not listings_kept:
            # Rebuild contents page from actual chapter titles
            ch["page_bidi"] = rebuilt_bidi[id(ch)]
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
        skip_own = ch_lower in CONTENTS_SKIP_TITLES or not ch["title"].strip()
        # A part's Contents page, kept as the book has it, isn't listed, but
        # the places after it in its section are, as before it shared one
        # with them (#714: pg5197's "Part I", "Part II", "ILLUSTRATIONS").
        if skip_own and ch_lower not in _CONTENTS_TITLES:
            continue
        if ch.get("_omit_from_toc"):
            # Left out of the navigation pane (#143), so left off this page
            # too: its title is a label kfxgen made up, such as a file name.
            # (#284 review: 835 of pg22210's 992 entries.)
            continue
        if not skip_own:
            toc_links.append(
                {
                    "text": ch["title"],
                    "target_chapter_idx": i,
                    "level": ch.get("toc_level", 0),
                }
            )
        # Places in the section the contents name, after it (#363). One with
        # no words (#459) gets no line: an empty line had no offset of its
        # own, and the position map listed the next one's id without one
        # (#562).
        toc_links.extend(
            {
                "text": sub["title"],
                "target_chapter_idx": i,
                "target_key": sub["key"],
                "level": sub.get("level", 0),
            }
            for sub in ch.get("sub_entries") or ()
            if (sub["title"] or "").strip()
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
    # The title prints as kfxgen's own label in place of the page's heading,
    # which keeps its level for the heading navigation (#580), when the page
    # opens with it (#585).
    blocks = contents_ch.get("blocks") or ()
    first = next((b for b in blocks if (b.get("text") or "").strip()), None)
    if first is not None and first.get("heading_level"):
        contents_ch["heading_level"] = first["heading_level"]
    # The page's other headings print too, as Kindle Previewer 4.0.3 prints
    # the page (#585), each before the entry of the next link on the page. A
    # heading holding a link, or naming an entry the page lists anyway (a
    # part in the TOC), is that entry's line and isn't printed twice (QA on
    # #594), unless another line of the page links that entry too (#622).
    listed = [_normalize_title(link["text"]) for link in toc_links]
    events = []  # ("link", words) or ("heading", line), in page order

    def is_link(b):
        return any(_is_link_flag(f) for _s, _n, fl in b.get("spans") or () for f in fl)

    def entry(words, after):
        """The first entry with `words` past entry `after`, else the first."""
        hits = [i for i, w in enumerate(listed) if w == words]
        return next((i for i in hits if i > after), hits[0])

    # How many of the page's links each entry is, decided per entry, not by
    # words: two "Introduction" entries can have one linked (#622).
    links_to = collections.Counter()
    last = -1
    for b in blocks:
        words = _normalize_title(b.get("text") or "")
        if b is not first and is_link(b) and words in listed:
            last = entry(words, last)
            links_to[last] += 1

    last = -1  # the entry of the page's latest link
    for b in blocks:
        if b is first:
            continue
        words = _normalize_title(b.get("text") or "")
        merged = False
        at = entry(words, last) if b.get("heading_level") and words in listed else None
        if at is not None and links_to[at] <= is_link(b):
            # A heading the TOC lists, as a part's, that no other line of the
            # page links: its line prints as the heading, its size, weight and
            # level, as Kindle Previewer 4.0.3 prints the page (#595), and
            # keeps its link. The entry is the one after the page's previous
            # link (#614).
            link = toc_links[at]
            link["heading_level"] = b["heading_level"]
            link["block_style"] = b.get("block_style")
            merged = True
        elif at is not None and is_link(b):
            # A heading that is a link the page repeats: printed where it is,
            # linked, and the entry keeps its own line, as Previewer prints
            # both (#622).
            events.append(
                (
                    "heading",
                    {
                        **toc_links[at],
                        "heading_level": b["heading_level"],
                        "block_style": b.get("block_style"),
                    },
                )
            )
            continue
        if is_link(b):
            if words in listed:
                last = entry(words, last)
            # The entry this link is, so a heading before it goes there (#618).
            events.append(("link", last if words in listed else None))
        elif b.get("heading_level") and words and not merged:
            # A heading the TOC doesn't list, or one the page links from
            # another line too: printed where it is (#585, #614).
            events.append(
                (
                    "heading",
                    {
                        "text": b["text"].strip(),
                        "heading_level": b["heading_level"],
                        "block_style": b.get("block_style"),
                    },
                )
            )
    inserts = []
    for n, (kind, line) in enumerate(events):
        if kind != "heading":
            continue
        # The next link with an entry: one the TOC doesn't list ("Map") has no
        # line on this page (#622).
        after = next(
            (w for k, w in events[n + 1 :] if k == "link" and w is not None), None
        )
        at = after if after is not None else len(listed)
        inserts.append((at, n, line))
    # Last first, so earlier places don't move; two at one place keep the
    # page's order (#618).
    for at, _n, line in sorted(inserts, key=lambda x: x[:2], reverse=True):
        toc_links.insert(at, line)
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


#: Landmark types kfxgen writes, as Kindle Previewer 4.0.3 maps them (#579).
_OPS_NS = "http://www.idpf.org/2007/ops"
_PAGE_LABEL_RE = re.compile(r"\d+|[ivxlcdm]+", re.IGNORECASE)
#: A label's number inside one pair of brackets and after "Page", "Pg" or
#: "p.", as Gutenberg writes them: "[Pg 12]", "{vii}" (#612 review).
_PAGE_LABEL_WRAP_RE = re.compile(
    r"^(?:\[\s*(.*?)\s*\]|\{\s*(.*?)\s*\}|\(\s*(.*?)\s*\)|(.*))$", re.DOTALL
)
_PAGE_LABEL_PREFIX_RE = re.compile(r"^(?:page|pg|p)\.?\s*", re.IGNORECASE)


def _page_label(text):
    """`text` as a page number: trimmed, out of its brackets, without a
    "Page"/"Pg"/"p." before it."""
    inner = next(
        g for g in _PAGE_LABEL_WRAP_RE.match(text.strip()).groups() if g is not None
    )
    return _PAGE_LABEL_PREFIX_RE.sub("", inner.strip()).strip()


_NAV_LANDMARKS = {"toc": "toc", "bodymatter": "start"}
_GUIDE_LANDMARKS = {"toc": "toc", "text": "start"}


def _book_files(oeb_book):
    """The book's own OPF, nav document and NCX, as calibre unpacked them:
    (opf, nav, the nav's path from the OPF's folder, ncx, the NCX's path),
    a missing one None, or None when there is no unpacked book."""
    parse = _container_parser(oeb_book)
    opfname = getattr(getattr(oeb_book, "container", None), "opfname", None)
    opf = parse(opfname) if parse and opfname else None
    if opf is None:
        return None
    opf_dir = posixpath.dirname(opfname)

    def item(test):
        hrefs = opf.xpath(
            f"//*[local-name()='manifest']/*[local-name()='item'][{test}]/@href"
        )
        return (
            posixpath.normpath(posixpath.join(opf_dir, unquote(hrefs[0])))
            if hrefs
            else None
        )

    nav_path = item("contains(concat(' ', normalize-space(@properties), ' '), ' nav ')")
    nav = parse(nav_path) if nav_path else None
    ncx_path = item("@media-type='application/x-dtbncx+xml'")
    ncx = parse(ncx_path) if ncx_path else None

    def rel(path):
        return posixpath.relpath(path, opf_dir or ".") if path else ""

    return opf, nav, rel(nav_path), ncx, rel(ncx_path)


def _respelled(found, oeb_book):
    """`found`'s (…, link key) items, each key naming a spine file only up to
    letter case put in the spine's spelling when one file fits (#610), as
    #549 does for TOC hrefs."""
    # The spine without calibre's copies of a file under another spelling,
    # which are left out of the book (#476), so each file has one name.
    spine = _spine_in_order(oeb_book, []) if getattr(oeb_book, "spine", None) else []
    names = {unquote(i.href) for i in spine}
    loose = {}
    for name in names:
        loose.setdefault(name.lower(), set()).add(name)
    out = []
    for *rest, key in found:
        path, sep, frag = key.partition("#")
        fits = loose.get(path.lower(), set())
        if path not in names and len(fits) == 1:
            key = next(iter(fits)) + sep + frag
        out.append((*rest, key))
    return out


def _nav_links(nav, kind):
    """The links of `nav`'s `<nav epub:type=kind>`."""
    if nav is None:
        return []
    return nav.xpath(
        "//*[local-name()='nav'][contains(concat(' ', normalize-space("
        f"@*[local-name()='type']), ' '), ' {kind} ')]//*[local-name()='a']"
    )


def _book_landmarks(oeb_book):
    """The book's contents and start landmarks, as (kind, label, link key),
    kind "toc" or "start", in the book's order. Kindle Previewer 4.0.3 takes
    them from an EPUB 3 landmarks nav (`toc`, `bodymatter`), else from the
    OPF guide (`toc`, `text`) (#579). calibre passes on neither the nav's
    nor a guide's the nav overrides, so both are read from the book's files."""
    files = _book_files(oeb_book)
    if files is None:
        return []
    opf, nav, nav_base, _ncx, _ncx_base = files
    links = _nav_links(nav, "landmarks")
    if links:
        # Its hrefs are written from its own folder; keys from the OPF's.
        found = [
            (
                next(
                    (
                        _NAV_LANDMARKS[t]
                        for t in (a.get(f"{{{_OPS_NS}}}type") or "").split()
                        if t in _NAV_LANDMARKS
                    ),
                    None,
                ),
                "".join(a.itertext()).strip(),
                _key_doc(nav_base, a.get("href") or ""),
            )
            for a in links
        ]
    else:
        found = [
            (
                _GUIDE_LANDMARKS.get((ref.get("type") or "").strip().lower()),
                (ref.get("title") or "").strip(),
                _key_doc("_", ref.get("href") or ""),
            )
            for ref in opf.xpath(
                "//*[local-name()='guide']/*[local-name()='reference']"
            )
        ]
    found = [(kind, label, key) for kind, label, key in found if kind and key]
    # The last contents landmark, as Previewer keeps it, where its own entry
    # is (#609). Every start stays: the generator keeps the first that lands.
    last_toc = max((n for n, m in enumerate(found) if m[0] == "toc"), default=None)
    found = [m for n, m in enumerate(found) if m[0] != "toc" or n == last_toc]
    return _respelled(found, oeb_book)


def _book_landmarks_for(oeb_book, opts):
    """`_book_landmarks`, without the start landmark when the user asked for
    new books to open at the cover (`kfxgen_open_at_cover`)."""
    marks = _book_landmarks(oeb_book)
    if getattr(opts, "kfxgen_open_at_cover", False) is True:
        marks = [m for m in marks if m[0] != "start"]
    return marks


def _book_page_list(oeb_book):
    """The book's print page breaks, as (label, link key) in the book's
    order, as Kindle Previewer 4.0.3 takes them: an EPUB 3 nav `page-list`,
    else the NCX's `pageList` (#578). Read from the book's own files."""
    files = _book_files(oeb_book)
    if files is None:
        return []
    _opf, nav, nav_base, ncx, ncx_base = files
    found = [
        ("".join(a.itertext()).strip(), _key_doc(nav_base, a.get("href") or ""))
        for a in _nav_links(nav, "page-list")
    ]
    if not found and ncx is not None:
        found = [
            (
                "".join(t.xpath("./*[local-name()='navLabel']//text()")).strip(),
                _key_doc(
                    ncx_base, (t.xpath("./*[local-name()='content']/@src") or [""])[0]
                ),
            )
            for t in ncx.xpath(
                "//*[local-name()='pageList']/*[local-name()='pageTarget']"
            )
        ]
    found = [(_page_label(label), key) for label, key in found if label and key]
    # Kindle Previewer 4.0.3 writes no page list when one label isn't all
    # digits or a roman numeral's letters (QA on #612). kfxgen cleans the
    # labels first, then does the same.
    if any(not _PAGE_LABEL_RE.fullmatch(label) for label, _key in found):
        return []
    return _respelled(found, oeb_book)


def _with_ncx_empty_labels(oeb_book, entries):
    """`entries` with the NCX's entries whose label is empty or only spaces
    put back where the NCX has them, their label as written (#459). calibre's
    TOC leaves them all out. Kindle Previewer 4.0.3 keeps them, but drops an
    entry with no label at all at the top level (6 probes). Unchanged unless
    calibre's entries are the NCX's other ones, in order: when calibre read
    another TOC, such as an EPUB 3 nav document, nothing is put in."""
    parse = _container_parser(oeb_book)
    opfname = getattr(getattr(oeb_book, "container", None), "opfname", None)
    opf = parse(opfname) if parse and opfname else None
    if opf is None:
        return entries
    ncx_hrefs = opf.xpath(
        "//*[local-name()='manifest']/*[local-name()='item']"
        "[@media-type='application/x-dtbncx+xml']/@href"
    )
    if not ncx_hrefs:
        return entries
    opf_dir = posixpath.dirname(opfname)
    ncx_path = posixpath.normpath(posixpath.join(opf_dir, unquote(ncx_hrefs[0])))
    ncx = parse(ncx_path)
    if ncx is None:
        return entries
    points = []

    def walk(node, depth):
        for point in node.xpath("./*[local-name()='navPoint']"):
            # The label's own text: not the line breaks around its <text>
            # in a pretty-printed NCX (QA on #459).
            label = "".join(
                point.xpath(
                    "./*[local-name()='navLabel']/*[local-name()='text']//text()"
                )
            )
            src = (point.xpath("./*[local-name()='content']/@src") or [""])[0]
            points.append((label, src, depth))
            walk(point, depth + 1)

    for nav_map in ncx.xpath("//*[local-name()='navMap']"):
        walk(nav_map, 0)
    if all(label.strip() for label, _src, _depth in points):
        return entries
    # The NCX's hrefs are written from its own folder; calibre's from the OPF's.
    ncx_dir = posixpath.relpath(posixpath.dirname(ncx_path) or ".", opf_dir or ".")
    out, i = [], 0
    for label, src, depth in points:
        if label.strip():
            if i >= len(entries) or entries[i]["title"].split() != label.split():
                return entries
            out.append(entries[i])
            i += 1
        elif src and (label or depth):
            path, sep, frag = src.partition("#")
            href = posixpath.normpath(posixpath.join(ncx_dir, path)) + sep + frag
            out.append({"title": label, "href": href, "level": depth})
    return out if i == len(entries) else entries


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

        kept = _with_ncx_empty_labels(oeb_book, toc_entries)
        if len(kept) > len(toc_entries):
            log.info(
                f"  Kept {len(kept) - len(toc_entries)} TOC entries with an empty "
                "label, from the book's NCX"
            )
            toc_entries[:] = kept
        if toc_entries:
            log.info(f"Extracted {len(toc_entries)} TOC entries with hrefs")
        else:
            log.info("TOC found but no entries extracted")

    except Exception as e:
        log.error(f"Error extracting TOC: {e}")

    return toc_entries


def extract_images_from_oeb(oeb_book, log, display_dims=None, own_size=()):
    """
    Walk OEB manifest for image/* items and return their raw bytes.

    Used by Phase 4 to emit $164 + $417 resource pairs. The cover's file is
    among them: where the text shows it too, the generator gives it the
    cover's resource rather than a second copy (#475).

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger
        display_dims: Optional dict, filled with {href: (width, height)} for
            each SVG drawn at twice its size: the size to show it at (#440)
        own_size: hrefs of SVGs to draw at their own size: those shown in a
            table cell, where a picture has no style and its pixels are its
            size (#441 device check)

    Returns:
        dict: { href: bytes } for every image manifest item
    """
    images = {}
    skipped_unsupported = 0
    converted_gifs = 0
    converted_svgs = 0
    try:
        for item in oeb_book.manifest:
            media = (getattr(item, "media_type", "") or "").lower()
            if "image" not in media:
                continue
            data = getattr(item, "data", None)
            href = getattr(item, "href", "") or ""
            if media == "image/svg+xml":
                if not href:
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
            if not isinstance(data, (bytes, bytearray)) or not data:
                continue
            if not href:
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
    try:
        inline = _inline_svg_pictures(oeb_book)
    except Exception as e:  # noqa: BLE001 - never fail a conversion over a picture
        log.warn(f"Error reading inline SVG pictures: {e}")
        inline = {}
    for href, svg in inline.items():
        # An <svg> in a page drawn only with shapes: a formula set as glyph
        # outlines (#709). Previewer 4.0.3 refuses Enhanced Typesetting for
        # it, as for an <img> of an SVG file, so it is drawn the same way,
        # its size multiplied in its markup rather than by `svg_to_png`.
        raw, scale = _svg_picture(svg, 1 if href in own_size else _INLINE_SVG_SCALE)
        drawn = svg_to_png(raw, log, 1)
        if drawn is None:
            skipped_unsupported += 1
            log.warn(f"  Skipping an inline SVG ({href!r}): not rasterised")
            continue
        converted_svgs += 1
        images[href], own = drawn
        if own and display_dims is not None:
            display_dims[href] = (own[0] / scale, own[1] / scale)
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


def _check_and_optimize(cover, cover_href, images, opts, log):
    """Pictures calibre can't decode left out (#471), and over-size ones
    optimized unless the user opted to embed originals (#11). The optimizer
    checks as it goes, so each picture is decoded once (#514)."""
    if getattr(opts, "kfxgen_embed_original_images", False):
        log.info("  Image optimization disabled (embed original images)")
        return _drop_undecodable(cover, cover_href, images, log)
    return optimize_images(cover, images, log, cover_href)


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
        display_dims=display_dims,
        own_size=_pictures_in_table_cells(oeb_book),
    )

    cover_image, images = cmyk_to_rgb(cover_image, images, log)
    cover_image, images = _check_and_optimize(
        cover_image, cover_href, images, opts, log
    )

    # Extract structured chapters
    log.info("Extracting chapters...")
    native_tables = not getattr(opts, "kfxgen_disable_native_tables", False)
    if not native_tables:
        log.info("  Native tables disabled (kfxgen_disable_native_tables=True)")
    fixed_layout = _fixed_layout(oeb_book)
    rtl = (
        getattr(getattr(oeb_book, "spine", None), "page_progression_direction", None)
        == "rtl"
    )
    # Vertical when the spine is rtl and the book Japanese, unless it is
    # fixed-layout (#247).
    writing_mode = (
        "horizontal-tb"
        if fixed_layout
        else _book_writing_mode(metadata["language"], rtl)
    )
    # Percentages are measured against Previewer's page: 480pt in a vertical
    # book, 384pt otherwise (P247j).
    pt = VERTICAL_PT if writing_mode == "vertical-rl" else HORIZONTAL_PT
    with page_width(pt):
        chapters = extract_chapters_from_oeb(
            oeb_book,
            log,
            metadata=metadata,
            cover_href=cover_href,
            native_tables=native_tables,
            fixed_layout=fixed_layout,
        )
    _warn_media(oeb_book, log)
    # A file set otherwise than the chapter it was joined to gets its own
    # section (#247).
    chapters = _split_by_writing_mode(chapters)
    total_chars = sum(len(ch["text"]) for ch in chapters)
    log.info(f"  Chapters: {len(chapters)}")
    log.info(f"  Total content: {total_chars:,} characters")

    # Build embedded-font table (#15), unless the user disabled embedding.
    font_table = _font_table_for(oeb_book, opts, log)

    # Generate KFX
    log.info("Generating KFX file...")
    gen = NativeKFXGenerator()
    with page_width(pt):
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
            # The spine's page-progression-direction, as calibre read it: only
            # "rtl" turns the pages right to left; the language doesn't (#246).
            rtl=rtl,
            writing_mode=writing_mode,
            publisher=metadata["publisher"],
            issue_date=metadata.get("issue_date"),
            authors=metadata.get("authors"),
            description=metadata.get("description"),
            font_table=font_table,
            landmarks=_book_landmarks_for(oeb_book, opts),
            page_list=_book_page_list(oeb_book),
            # Approximate pages for a book with no page list, unless the user
            # turned them off (#586).
            approximate_pages=getattr(opts, "kfxgen_disable_approximate_pages", False)
            is not True,
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
