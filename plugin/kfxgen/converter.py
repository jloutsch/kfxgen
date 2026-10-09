"""
KFX Converter - Extract content from OEB and generate KFX

Uses NativeKFXGenerator to produce KFX with working TOC navigation.
"""

import logging
import os
import posixpath
import re
import string
from urllib.parse import unquote

from ._img_tokens import (
    IMG_TOKEN_DELIM as _IMG_TOKEN_DELIM,
    IMG_TOKEN_FIELD as _IMG_TOKEN_FIELD,
    IMG_TOKEN_RE as _IMG_TOKEN_RE,
    IMG_TOKEN_SPACE as _IMG_TOKEN_SPACE,
)
from .image_optimize import GIF_SIGNATURES, gif_to_png, optimize_images
from .inline_style import (
    FLAG_BOLD,
    FLAG_ITALIC,
    FLAG_PRE,
    FLAG_PRE_LINE,
    FLAG_SUB,
    FLAG_SUPER,
    HARD_BREAK,
    compute_block_style,
    make_anchor_mark,
    make_link_flag,
    normalize_runs_with_anchors,
    parse_css_length,
    parse_vertical_align,
)
from .native_generator import NativeKFXGenerator

_ITALIC_TAGS = {"em", "i"}
_BOLD_TAGS = {"strong", "b"}
_SUPER_TAGS = {"sup"}
_SUB_TAGS = {"sub"}

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
#: `NativeKFXGenerator.CHUNK_SIZE`. Longer text is cut into two storyline
#: entries, which inside a row would be two cells (#226).
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

        def resolve(elem):
            try:
                st = stylizer.style(elem)
                return {
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
                    "font-weight": _computed_value(st, "font-weight"),
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
                    "background-repeat": st.get("background-repeat"),
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


def _make_img_token(href, alt, size=None):
    """Encode an <img> reference as a placeholder token string.

    Spaces in alt text are escaped to a control char so str.split() doesn't
    fragment the token during whitespace normalization. `size` is the hint
    from `_img_size_hint`, carried as an optional third field.
    """
    safe_href = _strip_token_controls(href or "")
    escaped_alt = _strip_token_controls(alt or "").replace(" ", _IMG_TOKEN_SPACE)
    size_field = f"{_IMG_TOKEN_FIELD}{size}" if size else ""
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


#: In `_walk_inline(split_tables=True)`, stands in for a table nested in the
#: row being walked, as `(_TABLE_SPLIT, <table>)`; the caller splits there.
_TABLE_SPLIT = object()


def _walk_inline(
    elem,
    flags=frozenset(),
    style_resolver=None,
    is_root=True,
    base_href=None,
    split_tables=False,
):
    """Yield (segment, flags) pairs for inline content, accumulating italic/
    bold from ancestor <em>/<i>/<strong>/<b> and superscript/subscript from
    <sup>/<sub> or a CSS `vertical-align`. <img> becomes an IMG token
    segment with empty flags so the generator still splits on it.

    `style_resolver` (elem -> css dict|None) is consulted only for inline
    descendants, never for the block element itself: a paragraph carrying
    `vertical-align` would otherwise turn its whole text into one raised
    run. (#52)"""
    if not isinstance(elem.tag, str):
        # A comment or a processing instruction: lxml gives it a `.text`, but
        # it is markup, not content ("H2 anchor", a <?dp n="12"?> page
        # marker). Its `.tail` is real text, and the caller keeps it. (#252)
        # An unresolved entity node lands here too; calibre resolves entities
        # before the plugin sees the tree, so a conversion never meets one.
        return []
    local = _local_tag(elem.tag)
    if local == "br":
        # A forced line break, which the normalizer turns into a newline. It
        # used to vanish, fusing "line one<br/>line two" into one word. (#202)
        return [make_anchor_mark(aid) for aid in _own_anchor_ids(elem)] + [
            (HARD_BREAK, frozenset(flags) - _WS_FLAGS)
        ]
    cur = (set(flags) - _WS_FLAGS) | _white_space_flags(elem, style_resolver, flags)
    if local in _ITALIC_TAGS:
        cur.add(FLAG_ITALIC)
    if local in _BOLD_TAGS:
        cur.add(FLAG_BOLD)
    if local in _SUPER_TAGS:
        cur.add(FLAG_SUPER)
    if local in _SUB_TAGS:
        cur.add(FLAG_SUB)
    if local == "a":
        target = _resolve_link_target(elem.get("href"), base_href)
        if target:
            cur.add(make_link_flag(target))
    if style_resolver is not None and not is_root:
        css = style_resolver(elem)
        if css:
            shift = parse_vertical_align(css.get("vertical-align"))
            if shift == "super":
                cur.add(FLAG_SUPER)
            elif shift == "sub":
                cur.add(FLAG_SUB)
    cur = frozenset(cur)
    parts = []
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
        elif clocal == "img":
            for aid in _own_anchor_ids(child):
                parts.append(make_anchor_mark(aid))
            href = _resolve_img_src(base_href, child.get("src", "") or "")
            alt = child.get("alt", "") or ""
            size = _img_size_hint(child, style_resolver)
            parts.append((_make_img_token(href, alt, size), frozenset()))
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
        elif clocal in _CELL_BLOCK_TAGS:
            # A block walked inline (a cell on the rows path, a native cell
            # mixing text and a block): its edges are a word boundary even
            # when the HTML puts no whitespace there.
            # <td>cellone<p>celltwo</p></td> read "cellonecelltwo" before.
            # Text inside one block is unchanged. Outside tables this also
            # reaches a <dl>, which the block walker doesn't treat as a block:
            # its outer edges get a space, but its dt/dd still join (#229).
            # (#279)
            parts.append((" ", frozenset()))
            parts.extend(
                _walk_inline(
                    child,
                    cur,
                    style_resolver,
                    is_root=False,
                    base_href=base_href,
                    split_tables=split_tables,
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
    return parts


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
    links back to R. Then T is a note and R its marker.

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


def _table_is_native(table):
    """True when `table` can be written as a real KFX table (#219).

    Anything else keeps rows as paragraphs: a nested table, an object other
    than an <img> (#262), a cell or a paragraph in a cell longer than the generator's chunk
    size, a cell outside a row, or no rows or cells at all. A cell holding
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
    """
    rows = 0
    cells = 0
    captions = 0
    for e in table.iter():
        tag = _local_tag(e.tag)
        if tag is None:
            continue
        if tag in _ROW_GROUP_TAGS or tag == "tr" or tag in _CELL_TAGS:
            if e is not table and (_is_non_rendered(e) or _is_nav_listing(e)):
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
            if _is_non_rendered(e):
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
            if _longest_cell_run(e) > _MAX_NATIVE_CELL_CHARS:
                return False
    return rows > 0 and cells > 0 and _table_width(table) <= _MAX_NATIVE_COLUMNS


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
    is counted as one run, which can only overstate. The generator cuts an
    entry at 2,000 characters, which inside a table would split a cell or a
    paragraph in two (#226, #261)."""
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
#: The basic CSS colour names. Any other name writes no colour, so the device
#: draws its default (black), as for `currentColor`.
_CSS_COLOURS = {
    "black": 0x000000, "silver": 0xC0C0C0, "gray": 0x808080, "grey": 0x808080,
    "white": 0xFFFFFF, "maroon": 0x800000, "red": 0xFF0000, "purple": 0x800080,
    "fuchsia": 0xFF00FF, "green": 0x008000, "lime": 0x00FF00, "olive": 0x808000,
    "yellow": 0xFFFF00, "navy": 0x000080, "blue": 0x0000FF, "teal": 0x008080,
    "aqua": 0x00FFFF,
}  # fmt: skip


def _border_colour(value):
    """A CSS colour as the ARGB integer Previewer writes, or None for black and
    for anything it can't read, which the device draws as its default black
    (#264)."""
    v = str(value or "").strip().lower()
    rgb = None
    if v in _CSS_COLOURS:
        rgb = _CSS_COLOURS[v]
    elif re.fullmatch(r"#[0-9a-f]{3}", v):
        rgb = int("".join(c * 2 for c in v[1:]), 16)
    elif re.fullmatch(r"#[0-9a-f]{6}", v):
        rgb = int(v[1:], 16)
    else:
        m = re.fullmatch(
            r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.]+\s*)?\)", v
        )
        if m:
            r, g, b = (min(int(x), 255) for x in m.groups())
            rgb = (r << 16) | (g << 8) | b
    if not rgb:
        return None
    return 0xFF000000 | rgb


def _is_transparent(value):
    """True for a colour that draws nothing: `transparent`, or rgba() with an
    alpha of 0. Read as unreadable it would become the default black (#292
    review)."""
    v = str(value or "").strip().lower()
    if v == "transparent":
        return True
    m = re.fullmatch(r"rgba\(.*,\s*([\d.]+)\s*\)", v)
    return bool(m) and float(m.group(1)) == 0


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


def _box_background(css):
    """A box's fill as the ARGB integer Previewer writes ($70), or None for
    none, transparent, white (the page) or anything unreadable (#238)."""
    value = (css or {}).get("background-color")
    if not value or _is_transparent(value):
        return None
    argb = _border_colour(value)
    return None if argb in (None, 0xFFFFFFFF) else argb


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
        has_picture = any(_local_tag(d.tag) == "img" for d in cell.iter())
        if walk_cell is not None and (_cell_paragraph_count(cell) >= 2 or has_picture):
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


def _is_note_element(elem):
    types = (elem.get(_EPUB_TYPE_ATTR) or elem.get("epub:type") or "").lower().split()
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
    if root is None:
        return ids
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
_NON_RENDERED_EPUB_TYPES = {"page-list", "landmarks", "lot", "loi", "lov"}
_EPUB_TYPE_ATTR = "{http://www.idpf.org/2007/ops}type"


def _is_non_rendered(elem):
    """True when `elem` heads a subtree that is markup, not reading content.

    Two signals: the HTML5 `hidden` attribute (the general rule — the producer
    said not to display this), and an `epub:type` naming a navigation kind that
    is structural by definition, which catches producers that omit `hidden`.
    """
    if elem.get("hidden") is not None:
        return True
    raw = elem.get(_EPUB_TYPE_ATTR) or elem.get("epub:type") or ""
    return any(part in _NON_RENDERED_EPUB_TYPES for part in raw.lower().split())


#: Marks a contents listing the book printed itself: a `class` token, an
#: `epub:type`, or the ARIA role. Gutenberg overwhelmingly uses `class="toc"`,
#: on the listing container in some books and on each entry paragraph in
#: others; EPUB 3 producers use `epub:type="toc"` / `role="doc-toc"`.
_TOC_CLASS_TOKEN = "toc"
_TOC_DOC_ROLE = "doc-toc"


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
    if _TOC_CLASS_TOKEN in (elem.get("class") or "").split():
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
    for block in blocks:
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
            # Its paragraphs are link targets; the box only carries their
            # ids for the chapter split (#238).
            for kid in block["blocks"]:
                by_kid = kid.get("anchor_offsets") or {}
                kid_ids = kid.get("anchor_ids", ())
                kid["anchor_keys"] = (
                    [f"{normalized}#{a}" for a in kid_ids] if normalized else []
                )
                kid["anchor_offsets"] = (
                    {f"{normalized}#{a}": by_kid.get(a, 0) for a in kid_ids}
                    if normalized
                    else {}
                )
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


def _list_ordinals(list_elem):
    """{li: ordinal} for the items of one list, honouring `start`, `reversed`
    and `<li value>`. Hidden items take no number, as in a browser."""
    items = [
        child
        for child in list_elem
        if _local_tag(child.tag) == "li" and not _is_non_rendered(child)
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
        "dd",
        "details",
        "dl",
        "dt",
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
    ):
        block_tags.add(tag)
        block_tags.add(ns + tag)

    blocks = []
    notes_tables = set()  # ids of tables written as notes paragraphs (#268)
    native_made = set()  # ids of tables written as native tables
    pending_ids = []  # anchors awaiting the next leaf block (containers, standalone <a>)
    # List markers awaiting the next text block: an item's number belongs on
    # the first text it holds, which may sit in a nested <p> (#201). An entry
    # is (marker, ordinal or None).
    pending_markers = []
    list_ordinals = {}  # list element -> {li: ordinal}

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
        if href is None:
            href = _resolve_img_src(base_href, elem.get("src", "") or "")
            alt = elem.get("alt", "") or ""
            size = _img_size_hint(elem, style_resolver)
        blocks.append(
            {
                "text": _make_img_token(href, alt, size),
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
        pending_ids.clear()
        pending_markers.clear()
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
            if not text:
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
        if _local_tag(elem.tag) != "li" or _is_non_rendered(elem):
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
            list_ordinals[parent] = _list_ordinals(parent)
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
        if _is_non_rendered(elem):
            return
        if _is_nav_listing(elem):
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

        native = (
            native_tables and _local_tag(elem.tag) == "table" and _table_is_native(elem)
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
                # The rows walk below records any listing again.
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

        is_block = elem.tag in block_tags
        has_block_child = any(child.tag in block_tags for child in elem)

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
            if text:
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
                        # Ids inherited from an enclosing container point at
                        # this block's start; only ids declared inside it have
                        # a position of their own.
                        "anchor_offsets": {
                            aid: mark_offsets.get(aid, 0) for aid in block_ids
                        },
                    }
                )
            else:
                pending_ids.extend(ids)  # empty anchor block: carry ids forward
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

    def _make_box(elem, start):
        """Replace the blocks a bordered or filled container produced with
        one box block holding them, drawn as Kindle Previewer draws it: a
        `$270` with the border, fill, padding and side margins, around the
        paragraphs (#238). Only text paragraphs and headings are boxed.
        A table, a picture or another box inside stays flat, with the
        margins carried as stage 1 does: Previewer nests those, which
        kfxgen has never written. A TOC entry past the box's first
        paragraph keeps it flat too, as for a native table (#219).
        Returns whether it made a box."""
        if style_resolver is None or _local_tag(elem.tag) not in _BOX_TAGS:
            return False
        css = style_resolver(elem) or {}
        border, fill = _css_border(css), _box_background(css)
        if border is None and fill is None:
            return False
        kids = blocks[start:]
        if not kids or any(
            b.get("type") in ("table", "box")
            or not _has_real_text(b.get("text") or "")
            or _IMG_TOKEN_RE.search(b.get("text") or "")
            for b in kids
        ):
            return False
        past_start = {aid for b in kids[1:] for aid in b.get("anchor_ids") or ()}
        if toc_targets and past_start & set(toc_targets):
            return False
        left = parse_css_length(css.get("margin-left") or "")
        right = parse_css_length(css.get("margin-right") or "")
        ids = _dedupe_keep_order([a for b in kids for a in b.get("anchor_ids") or ()])
        blocks[start:] = [
            {
                "type": "box",
                "text": "\n\n".join(b["text"] for b in kids),
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
                    "space_above": _box_space(css.get("margin-top"), _BOX_SPACE_EM),
                    "space_below": _box_space(css.get("margin-bottom")),
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
            if not text:
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
                else child.tag in block_tags or _local_tag(child.tag) in ("img", "svg")
            ):
                _flush_inline()
                start = len(blocks)
                _walk(child)
                if follow and _local_tag(child.tag) == "tr":
                    row_block = blocks[start] if len(blocks) > start else None
            elif not _is_non_rendered(child):
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

    # Trailing anchors (e.g. <a id="eof"/> after the last leaf block) never
    # reach a subsequent block to flush to; snap them onto the last emitted
    # block instead of dropping them silently.
    if pending_ids and blocks:
        last_block = blocks[-1]
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
    return "\n\n".join(b["text"] for b in extract_blocks_from_html(element))


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
            lang = str(oeb_book.metadata.language[0])
            metadata["language"] = lang[:2].lower()

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


def _find_manifest_item(oeb_book, href):
    """Return a manifest item whose href matches (exactly or by basename)."""
    if not hasattr(oeb_book, "manifest") or oeb_book.manifest is None:
        return None
    norm = _normalize_href(href)
    if not norm:
        return None

    hrefs = getattr(oeb_book.manifest, "hrefs", None)
    if hrefs:
        item = hrefs.get(href)
        if item is not None:
            return item
        for h, manifest_item in hrefs.items():
            if _normalize_href(h) == norm:
                return manifest_item

    try:
        for manifest_item in oeb_book.manifest:
            m_href = getattr(manifest_item, "href", "") or ""
            if _normalize_href(m_href) == norm:
                return manifest_item
    except TypeError:
        pass
    return None


def _extract_text_from_manifest_item(oeb_book, href, log):
    """Extract chapter text from a manifest item, used when the href isn't in the spine.

    Returns None when nothing usable is found — caller decides whether to drop
    the TOC entry or warn.
    """
    try:
        item = _find_manifest_item(oeb_book, href)
    except Exception as e:
        log.warn(f"  Manifest lookup failed for {_normalize_href(href)}: {e}")
        return None
    if item is None or not hasattr(item, "data") or item.data is None:
        return None
    media = (getattr(item, "media_type", "") or "").lower()
    if "html" not in media and "xml" not in media:
        # Empty/absent media_type or any non-XHTML type (image, font, ...) —
        # don't try to parse the data as XHTML.
        return None
    try:
        text = extract_text_from_html(item.data)
    except Exception as e:
        log.warn(f"  Manifest fallback failed for {_normalize_href(href)}: {e}")
        return None
    if text and text.strip():
        return text
    return None


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

    coords = []  # (flat_index, spine_index, title)
    last_block_in_file = {}
    prev_flat = -1
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
        if fi <= prev_flat:
            log.warn(
                f"  TOC entry {entry['title']!r} out of document order; skipping split"
            )
            continue
        coords.append((fi, si, entry["title"]))
        prev_flat = fi
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
    for fi, si, _title in coords:
        starts[fi] = si
    toc_frag_at = {}
    for entry in toc_entries:
        frag = _href_fragment(entry["href"])
        if frag:
            toc_frag_at.setdefault(_normalize_href(entry["href"]), set()).update(
                (frag, unquote(frag))
            )
    nav_flat = set()
    for si, s_item in enumerate(spine_items_ordered):
        last = len(spine_blocks[si]) - 1
        if last < 0:
            continue
        frags_here = toc_frag_at.get(_normalize_href(s_item["href"]), set())
        for local, ids in s_item.get("nav_listing_at") or ():
            fi = file_offset[si] + min(local, last)
            if fi in starts and 0 < local <= last and not (ids & frags_here):
                fi -= 1
            nav_flat.add(fi)

    def _flag_nav(ch, start, end):
        """Mark a chapter that held a discarded listing. Half-open, matching
        the slice the chapter was built from."""
        if ch and any(start <= i < end for i in nav_flat):
            ch["_had_nav_listing"] = True
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

    for k, (fi, si, title) in enumerate(coords):
        if k + 1 < len(coords):
            end = coords[k + 1][0]
        else:
            end = file_offset[si] + len(spine_blocks[si])
        start = 0 if (k == 0 and head_has_toc_anchor) else fi
        ch = _flag_nav(_mk(title, flat[start:end]), start, end)
        if ch:
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
    oeb_book, log, metadata=None, cover_href=None, native_tables=False
):
    """
    Extract structured chapters from OEB book by mapping TOC to spine items.

    Produces a list of {'title': str, 'text': str} dicts suitable for
    NativeKFXGenerator.generate_full_book(chapters=...).

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger
        metadata: Optional dict with 'title' and 'author' for title page replacement
        cover_href: Manifest href of the cover image, when one was found
        native_tables: Write eligible tables as one table block each (#219)

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

    for i, item in enumerate(oeb_book.spine):
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
                # Assembly skips entries naming a footnote; so does this.
                toc_targets=toc_targets.get(
                    _normalize_href(getattr(item, "href", "") or ""), set()
                )
                - note_ids,
            )
            text = "\n\n".join(b["text"] for b in blocks)
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
            }
        )
        table_blocks.extend((href, b) for b in tables_seen)
        notes_blocks.extend((href, b) for b in notes_seen)
        log.info(f"  Spine item {i + 1}: {len(text)} chars ({norm_href})")

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
            _replace_title_page(chapters, metadata, log)
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
    _replace_title_page(chapters, metadata, log)
    _warn_flattened_tables(chapters, table_blocks, log, native_tables, notes_blocks)
    return chapters


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
        # rebuild would replace (#252/#276 review).
        texts = [
            b
            for b in ch.get("blocks") or ()
            if _IMG_TOKEN_RE.sub("", b.get("text") or "").strip()
        ]
        if len(texts) <= 1:
            return ch
    return None


def _keep_illustrations(ch):
    """Move the pictures off a chapter whose text is about to be replaced.

    Three front-matter pages have their body rewritten: the contents listing
    becomes kfxgen's own (#132), and the title and half-title pages become the
    book's title and author. In every case the reason is that the *text*
    duplicates something KFX carries itself or states worse than the metadata
    does. None of that is a reason to drop what the publisher printed there.

    #117 made this call for the contents page. The title pages had no
    equivalent, so a scanned title page — the whole page, in a page-scan book —
    went with the text that replaced it, and so did the vignette or series
    device an ordinary illustrated book prints above its title. Measured across
    a 226-book library: 181 books, 314 images (#178).

    Tokens only, never the blocks they came from: the surrounding text is
    exactly what the caller is replacing. `preserved_images` is read outside
    the generator's `toc_links` branch, so it works for a page with no links.
    """
    return [
        m.group(0)
        for b in (ch.get("blocks") or [])
        for m in _IMG_TOKEN_RE.finditer(b.get("text") or "")
    ]


def _replace_title_page(chapters, metadata, log):
    """Replace title page, reformat copyright/contents, and set font sizes for front/back matter."""
    if not metadata:
        return
    title = metadata.get("title", "")
    author = metadata.get("author", "")
    if not title:
        return
    # A listing recognised by markup rather than by title still has to become
    # kfxgen's contents page; discarding it alone leaves the book with none
    # (#132). Resolved before the loop so the "already has one" test sees
    # every chapter, not just those visited so far.
    nav_contents_ch = _nav_listing_contents_chapter(chapters)
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
        if ch_title in TITLE_PAGE_TITLES:
            kept = _keep_illustrations(ch)
            ch["text"] = f"{title}\n\nby\n\n{author}"
            ch.pop("blocks", None)
            if kept:
                ch["preserved_images"] = kept
                log.info(
                    f"  Kept {len(kept)} illustration(s) from the replaced title page"
                )
            # The replaced body already contains the book title — don't
            # also render the chapter's TOC name ("Title Page") as a
            # heading on top of it (#33).
            ch["_omit_title_heading"] = True
            log.info(f"  Replaced title page with: {title} by {author}")
        elif ch_title in HALF_TITLE_TITLES:
            # Half-title convention: book title only, no author. The TOC
            # label ("Half Title Page") is structural metadata, never
            # printed content — replace with the title and suppress the
            # label as a heading so it can't leak onto the page. (#107)
            kept = _keep_illustrations(ch)
            ch["text"] = title
            ch.pop("blocks", None)
            ch["_omit_title_heading"] = True
            if kept:
                ch["preserved_images"] = kept
                log.info(
                    f"  Kept {len(kept)} illustration(s) from the replaced half-title"
                )
            log.info(f"  Replaced half-title page with: {title}")
        elif ch_title in ("copyright", "copyright page"):
            ch["font_size"] = SMALL_FONT_SIZE
            log.info(f"  Copyright page (font_size={SMALL_FONT_SIZE})")
        elif ch_title in _CONTENTS_TITLES:
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
        toc_links.append({"text": ch["title"], "target_chapter_idx": i})

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

            try:
                for child in node:
                    process_toc_node(child, level + 1)
            except (TypeError, AttributeError):
                pass

        try:
            for node in oeb_book.toc:
                process_toc_node(node)
        except (TypeError, AttributeError):
            process_toc_node(oeb_book.toc)

        if toc_entries:
            log.info(f"Extracted {len(toc_entries)} TOC entries with hrefs")
        else:
            log.info("TOC found but no entries extracted")

    except Exception as e:
        log.error(f"Error extracting TOC: {e}")

    return toc_entries


def extract_images_from_oeb(oeb_book, log, exclude_hrefs=None):
    """
    Walk OEB manifest for image/* items and return their raw bytes.

    Used by Phase 4 to emit $164 + $417 resource pairs. The cover image is
    typically excluded (handled separately via extract_cover_image), so callers
    pass its href in `exclude_hrefs`.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger
        exclude_hrefs: Optional iterable of normalized hrefs to skip

    Returns:
        dict: { href: bytes } for every image manifest item that's not excluded
    """
    images = {}
    skipped_unsupported = 0
    converted_gifs = 0
    excluded = {_normalize_href(h) for h in (exclude_hrefs or [])}
    try:
        for item in oeb_book.manifest:
            media = (getattr(item, "media_type", "") or "").lower()
            if "image" not in media:
                continue
            data = getattr(item, "data", None)
            if not isinstance(data, (bytes, bytearray)) or len(data) <= 100:
                continue
            href = getattr(item, "href", "") or ""
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
            for ref in oeb_book.guide:
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
    images = extract_images_from_oeb(
        oeb_book, log, exclude_hrefs=[cover_href] if cover_href else []
    )

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
