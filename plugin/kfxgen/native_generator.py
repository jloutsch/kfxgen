import collections
import errno
import hashlib
import logging
import os
import re
import stat
import unicodedata
from urllib.parse import unquote

from ._img_tokens import IMG_TOKEN_RE
from .inline_style import (
    ALIGN_MAP,
    EXTERNAL_LINK_PREFIX,
    NOTEREF_FLAG,
    TYPOGRAPHY_KEYS,
    VALIGN_MAP,
    css_text_colour,
    math_of,
    ruby_of,
    without_math,
    without_ruby,
)
from .kfxlib_minimal.ion import IS, IonAnnotation, IonBLOB, IonDecimal, IonStruct
from .kfxlib_minimal.kfx_container import KfxContainer
from .kfxlib_minimal.standard_symbols import StandardSymbolTable
from .kfxlib_minimal.yj_container import (
    CONTAINER_FORMAT_KFX_MAIN,
    YJFragment,
    YJFragmentList,
)

# Windows drive-relative '..' patterns like 'C:..\foo' or 'C:..' have no
# separator before the '..', so the segment-split traversal check would
# pass them through. Match the literal prefix and reject up front.
_DRIVE_RELATIVE_TRAVERSAL_RE = re.compile(r"^[A-Za-z]:\.\.")

_security_log = logging.getLogger(__name__ + ".security")
_log = logging.getLogger(__name__)

#: A token size hint: axis, number, unit (bare = px).
_SIZE_HINT_RE = re.compile(r"^([wh])=(\d+(?:\.\d+)?)(%|px|em|pt)?$")
_CSS_PX_PER_UNIT = {"px": 1.0, "em": 16.0, "pt": 4.0 / 3.0}
#: Text column width Amazon sizes against, in CSS pixels: 512 in Kindle
#: Previewer 4.0.3 (#467, probed 1 to 400 px). It was 496, recovered from
#: Previewer 3.106's output across seven books (#145).
_IMAGE_COLUMN_PX = 512.0


def _size_in_box(hint, box):
    """A picture's size hint inside a sized box (#382): none fills the box,
    a % is already a share of it, and a width in a length becomes one: of a
    box in a length, picture ÷ box; of a box in %, the picture's share of
    the page ÷ the box's (#384 review). `_parse_size_hint` caps it at the
    box, as it caps any size at the column."""
    if not hint:
        return "w=100%"
    m = _SIZE_HINT_RE.match(hint)
    if not m or m.group(1) != "w" or m.group(3) == "%":
        return hint
    px = float(m.group(2)) * _CSS_PX_PER_UNIT[m.group(3) or "px"]
    if box.get("width_px"):
        pct = px / box["width_px"] * 100.0
    else:
        pct = px / _IMAGE_COLUMN_PX * 100.0 / box["width"] * 100.0
    return f"w={pct:.3f}%"


#: An inline picture's vertical-align as Kindle Previewer 4.0.3 writes it,
#: `$44` (#565, probed with all eight).
_INLINE_VALIGN_SYMBOLS = {
    "middle": "$320",
    "top": "$58",
    "bottom": "$60",
    "text-top": "$447",
    "text-bottom": "$449",
    "baseline": "$350",
    "super": "$370",
    "sub": "$371",
}


def _inline_length_em(m, font_size):
    """A size hint's length (`_SIZE_HINT_RE` match, not %) in the
    paragraph's em: em as written, px at 0.45pt and pt with 1em = 12pt."""
    number, unit = float(m.group(2)), m.group(3) or "px"
    if unit == "em":
        return number
    return number * (0.45 if unit == "px" else 1.0) / 12 / (font_size or 1)


def _cut_after_picture_lines(block):
    """`block` cut after each line holding only a picture when more follows,
    as Kindle Previewer 4.0.3 ends an entry there (#565): the picture stays
    in the words before it, and the next line starts an entry. After an
    empty line it is cut before any line holding a picture too, which then
    starts an entry (QA on #590, #593). [block] when
    there is no such line, the paragraph has no words, it is preformatted,
    or a picture in it floats, which is cut as before. The pieces after the
    first continue the paragraph: no heading level, space above or clearing
    of their own."""
    text = block["text"]
    tokens = list(IMG_TOKEN_RE.finditer(text))
    # Preformatted text stays one entry, picture lines and all, as Previewer
    # keeps it (QA on #590).
    if any(m.group(4) for m in tokens) or block.get("preformatted"):
        return [block]
    if not IMG_TOKEN_RE.sub("", text).strip():
        return [block]
    # (end of a piece, start of the next): a cut after a picture line drops
    # its line break; one before it, after an empty line, drops nothing.
    cuts, start = [], 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        end = start + len(line)
        has_picture = bool(IMG_TOKEN_RE.search(line))
        # After an empty line, a line starts an entry, as Previewer writes it:
        # a picture alone is then an entry of its own (QA on #590), one among
        # words a mixed entry (#593), words a text entry (#602).
        if line.strip() and i and not lines[i - 1].strip() and text[:start].strip():
            # The piece ends at its last words: it keeps one line break and
            # the first blank line's no-break spaces, as plain spaces, as
            # Previewer writes a Gutenberg spacer (#626).
            j = i - 1
            while j and not lines[j - 1].strip():
                j -= 1
            words_end = sum(len(x) + 1 for x in lines[:j]) - 1
            # A spacer line's own characters stay, its no-break spaces as
            # plain ones, as Previewer writes them (QA on #628).
            spacer = lines[j].lstrip(_ASCII_SPACE).replace("\xa0", " ")
            if cuts and cuts[-1][0] == words_end:
                # After a line of only a picture, cut there already (#590):
                # one cut, the blank lines going to the next piece, as
                # Previewer lands an id in them (QA on #628).
                cuts[-1] = (words_end, start, None)
            else:
                cuts.append((words_end, start, "\n" + spacer))
        if has_picture and not IMG_TOKEN_RE.sub("", line).strip():
            if i < len(lines) - 1 and text[end + 1 :].strip():
                cuts.append((end, end + 1, None))
        start = end + 1
    if not cuts:
        return [block]
    pieces, a, empty_line_cut, lo = [], 0, False, 0

    def place(o, a, b, tail):
        """Offset `o` on the piece [a, b): in the blank lines after it, at
        the same place in its kept tail (#626, QA on #628)."""
        if o <= b:
            return max(o - a, 0)
        return (b - a) + min(o - b - 1, len(tail) - 1)

    for b, after, tail in [*cuts, (len(text), None, None)]:
        # A piece holds its end (#487), its cut (QA on #590), and the blank
        # lines after it when it keeps a tail, as Previewer lands an id there
        # (#626). One at the next piece's start (#592), or in the blank lines
        # after a picture line, is the next piece's.
        hi = None if after is None else (after if tail is not None else b + 1)
        offsets = {
            k: place(o or 0, a, b, tail or "")
            for k, o in (block.get("anchor_offsets") or {}).items()
            if lo <= (o or 0) and (hi is None or (o or 0) < hi)
        }
        piece = {
            **block,
            "text": text[a:b],
            "spans": [
                (max(s, a) - a, min(s + n, b) - max(s, a), f)
                for s, n, f in block.get("spans") or ()
                if min(s + n, b) > max(s, a)
            ],
            "anchor_keys": [k for k in block.get("anchor_keys") or () if k in offsets],
            "anchor_offsets": offsets,
        }
        if tail is not None:
            # Before an empty line's cut: one line break kept, as Previewer
            # keeps it (#602), with a spacer line's spaces (#626).
            piece["trailing"] = tail
        if a:
            # The paragraph goes on: no space above, clearing or first-line
            # indent of its own. A heading's parts after an empty line stay
            # headings, as Previewer lists them (#602).
            for key in ("space_above", "margin_above", "tight", "clear"):
                piece.pop(key, None)
            if not empty_line_cut:
                piece.pop("heading_level", None)
            if (piece.get("block_style") or {}).get("indent"):
                piece["block_style"] = {**piece["block_style"], "indent": None}
        if hi is not None:
            piece.pop("margin_below", None)  # the paragraph goes on (#553)
        pieces.append(piece)
        empty_line_cut = tail is not None
        lo, a = hi, after
    # A key with no offset, or one past the text, stays on the first piece.
    placed = {k for p in pieces for k in p["anchor_keys"]}
    pieces[0]["anchor_keys"] = pieces[0]["anchor_keys"] + [
        k for k in block.get("anchor_keys") or () if k not in placed
    ]
    return pieces


def _parse_size_hint(hint, column_px):
    """('w' | 'h', percent) for a token size hint, or None to size by pixels.

    Amazon's arithmetic, recovered from Kindle Previewer 3.106 output: a
    width length is divided by the text column (em at 16px, pt at 4/3px), a
    percentage is passed through, and either is capped at the column. A
    height counts only as a percentage — a height in a length has no
    reference output to copy, so it falls back to the pixel rule.
    """
    m = _SIZE_HINT_RE.match(hint or "")
    if not m:
        return None
    axis, number, unit = m.group(1), float(m.group(2)), m.group(3) or "px"
    if unit == "%":
        pct = number
    elif axis == "h":
        return None
    else:
        pct = number * _CSS_PX_PER_UNIT[unit] / column_px * 100.0
    if pct <= 0:
        return None
    return axis, round(min(100.0, pct), 3)


# O_NOFOLLOW is POSIX-only; on Windows it isn't defined. Degrade to 0 (no-op
# flag bit) so the plugin still imports under Calibre on Windows. Symlink
# defense on Windows then relies on the islink() check above the open, which
# is TOCTOU-vulnerable but adequate for the single-user threat model.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

# Superscript/subscript character metrics (#52, #123). A raised run is a
# reduced `$16` font size plus `$44` (`-kfx-baseline-style`), the semantic
# enum: `$370` for super, `$371` for sub.
#
# kfxgen used to emit `$31` instead — a numeric baseline shift of +35% / -20%.
# Both render correctly; #67 verified the numeric form and #123 verified this
# one on a Paperwhite running 5.19.2. The enum is preferred because it states
# the intent and lets the reader compute the offset from its own font metrics,
# rather than hardcoding a percentage that was only ever measured on two
# devices. It is also what Amazon emits — exclusively, across four books
# checked against Kindle Previewer 3.106.
#
# The percentages are gone with the shift. Under `$44` the device chooses the
# offset, so `SUPERSCRIPT_SHIFT_PCT` / `SUBSCRIPT_SHIFT_PCT` and their
# `KFXGEN_*_SHIFT_PCT` overrides no longer have anything to act on; keeping
# them would mean a documented knob that silently does nothing.
SUPERSCRIPT_FONT_SIZE = 0.75
#: Subscripts use the same reduced size as superscripts, checked together on
#: hardware in #67. A literal rather than an alias, so editing one default on a
#: device report does not silently move the other.
SUBSCRIPT_FONT_SIZE = 0.75

#: Emitted as `$44`. `$370` raises the run, `$371` lowers it.
BASELINE_STYLE_SUPER = "$370"
BASELINE_STYLE_SUB = "$371"

#: Storyline node types for native table containers (#219).
_TABLE_NODE_TYPES = {
    "table": "$278",
    # A table that gets the zoom button: the same node, plus the table-viewer
    # properties (#272).
    "zoom-table": "$278",
    "head": "$151",
    "body": "$454",
    "foot": "$455",
    "row": "$279",
    # A cell holding several blocks: a $269 container of $269 text entries,
    # as Kindle Previewer writes it (#261).
    "cell": "$269",
    # A bordered or filled container around paragraphs, as Kindle Previewer
    # writes a sidebar or callout (#238).
    "box": "$270",
    # A wrapper with a width around a picture, with no border, fill or float:
    # the plain container Kindle Previewer writes for it (#382).
    "frame": "$269",
    # A container around a linked picture: its $142 link covers the picture
    # (#355), as Kindle Previewer writes `<a href><img/></a>`.
    "link": "$269",
}


def _is_frame(box):
    """A box drawn as Previewer's plain container: a wrapper's width around
    a picture, with no border, fill or float (#382)."""
    return bool(box.get("width")) and not (
        box.get("border") or box.get("background") is not None or box.get("float")
    )


#: Overrides retired by #123. Warned about rather than ignored: a variable that
#: used to change output and now cannot is exactly the silent no-op this
#: codebase keeps paying for elsewhere.
_RETIRED_SHIFT_ENV = ("KFXGEN_SUPERSCRIPT_SHIFT_PCT", "KFXGEN_SUBSCRIPT_SHIFT_PCT")


def _warn_retired_shift_env():
    """Warn once per conversion, from `generate_full_book`.

    Deliberately not called from the metrics functions. Those run once per
    emphasis run, so a footnote-heavy book emitted one identical warning per
    superscript — thousands of lines for a single stale variable. Worse in the
    other direction: a book with no raised text never called them at all, so
    the variable was silently ignored, which is the exact outcome retiring it
    loudly was meant to avoid. Warning at the book level fires exactly once and
    fires regardless of content.
    """
    for name in _RETIRED_SHIFT_ENV:
        if name in os.environ:
            _log.warning(
                "%s is set but no longer affects output: raised text now uses "
                "$44 baseline-style, where the device computes the offset (#123). "
                "Use KFXGEN_SUPERSCRIPT_FONT_SIZE / KFXGEN_SUBSCRIPT_FONT_SIZE "
                "to change the size.",
                name,
            )


def _env_number(name, default):
    """Read a numeric env override, falling back to `default` when unset or
    unparseable. Resolved per call, not at import, so a conversion can be run
    with a different value without reloading the plugin. (#68)"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw) if "." in raw else int(raw)
    except (TypeError, ValueError):
        _security_log.warning("ignoring invalid %s=%r; using %r", name, raw, default)
        return default


def superscript_metrics():
    """(font_size, baseline_style) for a superscript run.

    The size stays env-overridable — a device that renders raised text too
    small can still be corrected without a rebuild, which is why #68 exposed
    these. The offset is no longer ours to set: `$44` hands that to the
    reader (#123).
    """
    size = _env_number("KFXGEN_SUPERSCRIPT_FONT_SIZE", SUPERSCRIPT_FONT_SIZE)
    return size, BASELINE_STYLE_SUPER


def subscript_metrics():
    """(font_size, baseline_style) for a subscript run.

    `KFXGEN_SUBSCRIPT_FONT_SIZE` falls back to `KFXGEN_SUPERSCRIPT_FONT_SIZE`
    before the built-in default, so setting only the superscript variable still
    moves both — the behaviour that shipped in 5.x (#115).
    """
    if "KFXGEN_SUBSCRIPT_FONT_SIZE" in os.environ:
        size = _env_number("KFXGEN_SUBSCRIPT_FONT_SIZE", SUBSCRIPT_FONT_SIZE)
    else:
        size = _env_number("KFXGEN_SUPERSCRIPT_FONT_SIZE", SUBSCRIPT_FONT_SIZE)
    return size, BASELINE_STYLE_SUB


#: How many leading blocks may be consumed as a split chapter opener. Two
#: covers the common numeral+title shape; three allows a subtitle line. Beyond
#: that the risk of eating real body text outweighs the tidier heading.
_MAX_SPLIT_TITLE_BLOCKS = 3


def _normalize_title(text):
    """Case-fold and drop punctuation/whitespace for title comparison.

    A split opener writes "3" and "Chapter Title Here" where the TOC says
    "3. Chapter Title Here" — the separator lives only in the TOC form, so the
    comparison has to ignore it.
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _consume_split_title(blocks, title):
    """Number of leading blocks that together reconstruct `title` exactly.

    Returns 0 when they do not, so ordinary body text is never eaten. The match
    must be complete: a prefix is not enough, or a chapter whose first paragraph
    happens to open with the title's words would lose it. (#64)
    """
    target = _normalize_title(title)
    if not target:
        return 0
    acc = ""
    for i, blk in enumerate(blocks[:_MAX_SPLIT_TITLE_BLOCKS]):
        acc += _normalize_title(blk.get("text", ""))
        if acc == target:
            return i + 1
        if not target.startswith(acc):
            return 0
    return 0


def _row_text(row):
    """A native table row's text as the rows build wrote it: its non-empty
    cells joined by a space."""
    return " ".join(c["text"] for c in row["cells"] if c["text"])


def _with_rows(block, rows):
    """Copy of native table `block` holding `rows` instead of its own."""
    return {
        **block,
        "text": "\n".join(t for t in map(_row_text, rows) if t),
        "table": {**block["table"], "rows": rows},
    }


def _source_order(rows):
    """Row indices in source order. Rows are written head, body, foot; the
    converter records where each sat in the source, which is the order the
    rows build wrote them in and so the order the title cut follows."""
    return sorted(range(len(rows)), key=lambda j: rows[j].get("source_order", j))


def _drop_table_rows(block, dropped):
    """Copy of native table `block` without the rows at indices `dropped`.

    Their own and their cells' anchor keys join the table's own keys, which
    go on the first row written, where the dropped rows' text began. Returns
    None when no row with text is left: the rows build would then have
    nothing of this table to show. (#219)
    """
    rows = block["table"]["rows"]
    moved = [
        k
        for j in sorted(dropped)
        for k in (rows[j].get("anchor_keys") or [])
        + [
            k
            for c in rows[j]["cells"]
            for part in [c, *(c.get("paragraphs") or ())]
            for k in part.get("anchor_keys") or []
        ]
    ]
    kept = [r for j, r in enumerate(rows) if j not in dropped]
    if not any(_row_text(r) for r in kept):
        return None
    out = _with_rows(block, kept)
    out["table"]["anchor_keys"] = _dedupe_keys(
        list(block["table"].get("anchor_keys") or []) + moved
    )
    return out


def _trim_text(part, cut):
    """Copy of a cell or paragraph with its first `cut` characters removed,
    its spans and anchor offsets rebased as the paragraph path rebases a cut
    paragraph's spans. (#219)"""
    rest = part["text"][cut:]
    spans = []
    for s, length, flags in part.get("spans") or []:
        start = max(s - cut, 0)
        end = min(s + length - cut, len(rest))
        if end > start:
            spans.append((start, end - start, flags))
    offsets = {
        k: min(max(v - cut, 0), len(rest))
        for k, v in (part.get("anchor_offsets") or {}).items()
    }
    return {**part, "text": rest, "spans": spans, "anchor_offsets": offsets}


_SMALL_CAPS_SYMBOLS = {"small-caps": "$369", "normal": "$349"}
_TRANSFORM_SYMBOLS = {
    "uppercase": "$372",
    "lowercase": "$373",
    "capitalize": "$374",
    "none": "$349",
}


def _write_typography(value, small_caps, text_transform, letter_spacing, colour):
    """Small caps ($583), text-transform ($41), letter-spacing ($32) and
    colour ($19) onto a $157 value, as Kindle Previewer writes them (#239)."""
    if small_caps in _SMALL_CAPS_SYMBOLS:
        value[IS("$583")] = IS(_SMALL_CAPS_SYMBOLS[small_caps])
    if text_transform in _TRANSFORM_SYMBOLS:
        value[IS("$41")] = IS(_TRANSFORM_SYMBOLS[text_transform])
    if letter_spacing:
        mag, unit = letter_spacing
        value[IS("$32")] = IonStruct(IS("$307"), IonDecimal(mag), IS("$306"), IS(unit))
    if colour is not None:
        value[IS("$19")] = colour


#: One line of space, in ems: what an unindented paragraph has above it
#: (build_fragment_157's $47 1lh), and lh = em / 1.2 in the box style.
_LINE_EM = 1.2
_SIDES = ("top", "right", "bottom", "left")


def _has_space_above(block):
    """Whether `block` brings its own space above: an unindented text
    paragraph does (1 line, $47) and so does a box; an indented paragraph,
    a table or a picture does not, as build_fragment_157 writes them (#309,
    #310)."""
    if block.get("type") == "table":
        return False
    if block.get("type") == "box":
        return True
    if not IMG_TOKEN_RE.sub("", block.get("text") or "").strip():
        return False
    return (block.get("block_style") or {}).get("indent") is None


#: What may be trimmed from a paragraph's edges: ASCII whitespace, never a
#: non-breaking space the book set for indentation (#244).
_ASCII_SPACE = " \t\n\r\f"


#: CSS `clear` as Kindle Previewer writes it, `$628`: the side as written
#: (#381).
_CLEAR_SYMBOLS = {"both": "$421", "left": "$59", "right": "$61"}


def _write_clear(value, clear):
    if clear:
        value[IS("$628")] = IS(_CLEAR_SYMBOLS[clear])


def _on_page(argb):
    """A text colour as it is drawn on the white page: unchanged when it has
    4.5:1 contrast there, else darkened as #239 darkens it (#383)."""
    return css_text_colour(f"#{argb & 0xFFFFFF:06x}")


def _space_to_write(block):
    """The space a scene break puts above a paragraph, in ems, or None to
    keep kfxgen's own when that is as much: a line (1.2em) above an
    unindented paragraph, none above an indented one (#230)."""
    em = block.get("space_above")
    if not em:
        return None
    own = 0.0 if (block.get("block_style") or {}).get("indent") else _LINE_EM
    return em if em > own + 0.01 else None


def _drop_edge_rules(blocks):
    """A chapter's blocks without the rules at its start or end: the page
    break already separates chapters, and Gutenberg puts an <hr> before
    each chapter heading (#230). Run before the chapter title is cut, so a
    rule under the book's own heading stays under kfxgen's, where a browser
    and Kindle Previewer draw it."""
    blocks = list(blocks)
    while blocks and blocks[0].get("type") == "rule":
        blocks.pop(0)
    while blocks and blocks[-1].get("type") == "rule":
        blocks.pop()
    return blocks


def _block_keys(block):
    """Every anchor key a block holds, inside a box or a table included."""
    keys = list(block.get("anchor_keys") or ())
    if block.get("type") == "box":
        for kid in block["blocks"]:
            keys += _block_keys(kid)
    tbl = block.get("table")
    if tbl:
        keys += tbl.get("anchor_keys") or []
        for row in tbl["rows"]:
            keys += row.get("anchor_keys") or []
            for cell in row["cells"]:
                for part in [cell, *(cell.get("paragraphs") or ())]:
                    keys += part.get("anchor_keys") or []
    return keys


def _is_text_block(block):
    """A paragraph of text: not a table, a box or only a picture."""
    return block.get("type") not in ("table", "box") and bool(
        IMG_TOKEN_RE.sub("", block.get("text") or "").strip()
    )


def _leading_blocks(blocks):
    """A chapter's blocks in reading order, with boxes opened."""
    for block in blocks:
        if block.get("type") == "box":
            yield from _leading_blocks(block.get("blocks") or [])
        else:
            yield block


def _same_words(text, title):
    """`text` and `title` as the same words, ignoring case and spacing."""
    return " ".join(text.split()).lower() == " ".join(title.split()).lower()


def _title_after_pictures(blocks, title):
    """Whether the chapter opens with one or more pictures and then its
    title, as in a book that puts a chapter's art above its heading (#346).
    Rules are passed over; any other block before the title says no."""
    seen_picture = False
    for block in _leading_blocks(blocks):
        if block.get("type") == "rule":
            continue
        if _is_text_block(block):
            # The whole paragraph is the title: a sentence that only starts
            # with the title's words is prose, and kfxgen's heading prints
            # (#347 QA).
            return seen_picture and _same_words(block["text"], title)
        if block.get("type") == "table" or not IMG_TOKEN_RE.search(
            block.get("text") or ""
        ):
            return False
        seen_picture = True
    return False


def _title_runs_in(blocks, title):
    """Whether the chapter's first block is a paragraph that runs its title
    into its text ("Chapter One was a long day"). The book prints the title
    there, so kfxgen prints no heading of its own and cuts nothing, as
    Kindle Previewer has it (#468). A heading, and a paragraph that is the
    title alone, are cut as before."""
    first = next(iter(_drop_edge_rules(blocks or ())), None)
    if first is None or first.get("type") not in (None, "text") or first.get("heading"):
        return False
    text = (first.get("text") or "").lstrip()
    return _opens_with_title(text, title) and any(
        c.isalnum() for c in text[len(title) :]
    )


def _title_in_table(blocks, title):
    """Whether the chapter opens with a table, or a box opening with one,
    whose first rows print its title: in one row, or spelled over rows, as
    the title cut reads a table. The table is printed as written and kfxgen
    prints no heading, as Kindle Previewer has it; the cut left an empty
    cell, or ate the rows (#470)."""
    blocks = _drop_edge_rules(blocks or ())
    first = next(iter(blocks), None)
    while first is not None and first.get("type") == "box":
        first = next(iter(first.get("blocks") or ()), None)
    if first is None or first.get("type") != "table":
        return False
    if _table_opens_with_title(first, title):
        return True
    return _eat_split_title(blocks, title)[0] != blocks


def _heading_near_start(blocks):
    """Whether the book prints its own heading among the section's first
    blocks, past the first: after an epigraph or a picture, or in a table
    cell. kfxgen then prints no label of its own, as Kindle Previewer has
    it; a section with no heading near its start keeps its label (#492).
    A first block that is the heading prints in the label's place (#481)."""
    for block in _drop_edge_rules(blocks or ())[:_MAX_SPLIT_TITLE_BLOCKS]:
        if block.get("heading"):
            return True
        for row in (block.get("table") or {}).get("rows") or ():
            for cell in row["cells"]:
                if cell.get("heading") or any(
                    p.get("heading") for p in cell.get("paragraphs") or ()
                ):
                    return True
    return False


def _ruby_heading_title(blocks, title):
    """(The base text of a chapter's first block, its runs) when that block,
    each reading run in after its base, is `title`; else (`title`, None)
    (#236). calibre takes a TOC label from the book's navigation, which runs
    the readings in, as Kindle Previewer's labels do. Matched against its
    base, the book's heading printed under kfxgen's. A reading may be in
    parentheses, as <rp> gives it, and spaces are ignored, as they were when
    the heading's text held its readings (#399 QA)."""
    blocks = _drop_edge_rules(blocks or ())
    first = blocks[0] if blocks else None
    if first is None or first.get("type") not in (None, "text"):
        return title, None
    rubies = {}
    for s, n, flags in first.get("spans") or ():
        ruby = ruby_of(flags)
        if ruby:
            rubies[ruby[0]] = (s + n, ruby[1])
    if not rubies:
        return title, None

    def run_in(fmt):
        text = first["text"]
        for end, reading in sorted(rubies.values(), reverse=True):
            text = text[:end] + fmt.format(reading) + text[end:]
        return "".join(text.split())

    if "".join(title.split()) not in {run_in(f) for f in ("{}", "({})", "（{}）")}:
        return title, None
    base = first["text"].strip()
    return base, _title_spans(first, len(base))


def _label_blocks(blocks, title):
    """The chapter's first text blocks, up to three, that `title` spells out,
    or []. `title` matches when it is their text without case, spaces or
    punctuation, as a duplicate heading has always been found (#64), or,
    spaces ignored, when it writes an equation as a producer does: its
    source text run together ("The ab=1 rule" for "The a/b = 1 rule",
    #402), its `alttext`, or its text (#404)."""
    want = "".join(title.split())
    target = _normalize_title(title)
    matched = []
    for block in _drop_edge_rules(blocks or ())[:_MAX_SPLIT_TITLE_BLOCKS]:
        if block.get("type") not in (None, "text"):
            return []
        matched.append(block)
        forms = max(
            (len(b["label_texts"]) for b in matched if b.get("label_texts")), default=1
        )
        readings = zip(*(b.get("label_texts") or (b["text"],) * forms for b in matched))
        if (
            target and _normalize_title("".join(b["text"] for b in matched)) == target
        ) or (want in {"".join("".join(r).split()) for r in readings}):
            return matched
    return []


def _book_heading_title(blocks, title):
    """(The text of a chapter's first block, its runs) when `title` is that
    block (see `_label_blocks`), else (`title`, None). The book's heading
    then prints in place of the label, once and as the book writes it, as
    on Kindle Previewer's page: its line breaks, runs and spacing (#409);
    or when it is the first words of a heading holding one (#408). A label
    spelling out several blocks leaves them to print as they are (#552)."""
    matched = _label_blocks(blocks, title)
    if len(matched) == 1:
        base = matched[0]["text"].strip()
        return base, _title_spans(matched[0], len(base))
    if matched:
        return title, None
    # A section opening with the book's own heading prints that heading
    # alone, whatever its label, as on Kindle Previewer's page; the label
    # stays in the TOC (#408, #481). Prose opening with it doesn't, nor a
    # heading holding a picture, which kfxgen's heading can't print.
    first = next(iter(_drop_edge_rules(blocks or ())), None)
    if (
        first is not None
        and first.get("heading")
        and not IMG_TOKEN_RE.search(first["text"])
    ):
        base = first["text"].strip()
        return base, _title_spans(first, len(base))
    return title, None


def _heading_offsets(block, title):
    """`block`'s anchor offsets in kfxgen's heading, which prints its text
    from the first character after any leading space (#491). One at the end
    lands on the last character, as Kindle Previewer 4.0.3 places it."""
    lead = len(block["text"]) - len(block["text"].lstrip())
    return {
        k: min(max(off - lead, 0), max(len(title) - 1, 0))
        for k, off in (block.get("anchor_offsets") or {}).items()
    }


def _title_spans(block, length):
    """`block`'s runs, moved onto a heading printing its first `length`
    characters after any leading space: the block is the book's own heading,
    cut because the heading prints its title (#366)."""
    lead = len(block["text"]) - len(block["text"].lstrip())
    out = []
    for start, n, flags in block.get("spans") or ():
        s, e = max(start - lead, 0), min(start + n - lead, length)
        if e > s:
            out.append((s, e - s, flags))
    return out


def _opens_with_title(text, title):
    """Whether `text`, already stripped on the left, opens with the chapter
    title as whole words, so cutting it leaves no part of a word: "A" opens
    "A storm" but not "After", "I" not "It" or "I'm", "1" not "12" or
    "1st" (#318)."""
    if text[: len(title)].lower() != title.lower():
        return False
    rest = text[len(title) :]
    if not title or not rest:
        return True
    last, nxt = title[-1], rest[0]
    if {unicodedata.east_asian_width(c) for c in (last, nxt)} & {"W", "F"}:
        # Chinese and Japanese write no spaces between words: "第一章" opens
        # "第一章天下大势" and is cut, as before (#321 QA).
        return True
    if nxt.isalpha() and last.isalnum():
        return False
    if nxt.isdigit() and last.isdigit():
        return False
    # A contraction or possessive: "I" + "I'm here", "John" + "John's".
    return not (last.isalpha() and nxt in "'\u2019" and rest[1:2].isalpha())


def _cut_title_from_box(box, title):
    """(box, carried anchor keys) with `title` cut from the box's first
    paragraph, as the chapter title cut does for a paragraph (#238), or None
    when the box doesn't open with it. A box opening with a box is followed
    into it (#310); one opening with a table printing the title is kept
    whole and never reaches here (#470). The box is None when nothing is
    left in it; its own keys are then carried too."""
    kids = box["blocks"]
    first = kids[0]
    if first.get("type") == "box":
        cut = _cut_title_from_box(first, title)
        if cut is None:
            return None
        inner, carried = cut
        kids = ([inner] if inner else []) + kids[1:]
    elif not _is_text_block(first):
        return None
    else:
        stripped = first["text"].lstrip()
        if not _opens_with_title(stripped, title):
            return None
        remainder = stripped[len(title) :].lstrip()
        if remainder:
            kids = [_trim_text(first, len(first["text"]) - len(remainder))] + kids[1:]
            carried = []
        else:
            kids = kids[1:]
            carried = list(first.get("anchor_keys") or [])
    if not kids:
        return None, carried + list(box.get("anchor_keys") or [])
    text = "\n\n".join(k["text"] for k in kids)
    return {**box, "blocks": kids, "text": text}, carried


def _box_title_lines(box, path=()):
    """([(path, text)], whole) for the paragraphs a box opens with, through
    the boxes it opens with, as the split-title cut reads them: they read as
    lines before they were boxed (#238, #310). A native table's rows with
    text are lines too, each `path + (j, ("row", r))`, as the cut reads a
    table on its own (#219, #344). `whole` is False when a picture stops the
    run."""
    out = []
    for j, kid in enumerate(box["blocks"]):
        if kid.get("type") == "box":
            inner, whole = _box_title_lines(kid, path + (j,))
            out += inner
            if not whole:
                return out, False
        elif kid.get("type") == "table":
            rows = kid["table"]["rows"]
            out += [
                (path + (j, ("row", r)), _row_text(rows[r]))
                for r in _source_order(rows)
                if _row_text(rows[r])
            ]
        elif _is_text_block(kid):
            out.append((path + (j,), kid.get("text", "")))
        else:
            return out, False
    return out, True


def _drop_box_lines(box, path):
    """(box or None, carried keys): `box` with every block up to and
    including the paragraph at `path` taken out (#310), or a table's rows up
    to and including the row at `path`, as for a table on its own; a table
    left with none goes (#344)."""
    kids = box["blocks"]
    j = path[0]
    carried = [k for kid in kids[:j] for k in _block_keys(kid)]
    if len(path) == 1:
        carried += list(kids[j].get("anchor_keys") or [])
        rest = kids[j + 1 :]
    elif isinstance(path[1], tuple):
        table = kids[j]
        order = _source_order(table["table"]["rows"])
        kept = _drop_table_rows(table, set(order[: order.index(path[1][1]) + 1]))
        if kept is None:
            carried += list(table.get("anchor_keys") or [])
        rest = ([kept] if kept else []) + kids[j + 1 :]
    else:
        inner, more = _drop_box_lines(kids[j], path[1:])
        carried += more
        rest = ([inner] if inner else []) + kids[j + 1 :]
    if not rest:
        return None, carried + list(box.get("anchor_keys") or [])
    text = "\n\n".join(k["text"] for k in rest)
    return {**box, "blocks": rest, "text": text}, carried


def _table_opens_with_title(block, title):
    """Whether a native table's first row with text opens with `title`, as
    the rows build read it as the chapter's first paragraph (#219)."""
    rows = block["table"]["rows"]
    j = next((j for j in _source_order(rows) if _row_text(rows[j])), None)
    return j is not None and _opens_with_title(_row_text(rows[j]).lstrip(), title)


def _eat_split_title(blocks, title):
    """Drop the leading blocks that together make up `title` (#64).

    A native table counts as one block per row with text, as the rows build
    saw it, so a title split over rows, or over a paragraph and a row, is
    eaten as it was in 5.8.8; a table only partly eaten keeps its other rows.
    Returns (blocks, the dropped blocks' anchor keys). (#219)
    """
    entries = []
    for i, blk in enumerate(blocks):
        if len(entries) >= _MAX_SPLIT_TITLE_BLOCKS:
            break
        if blk.get("type") == "table":
            rows = blk["table"]["rows"]
            entries.extend(
                (i, j, _row_text(rows[j]))
                for j in _source_order(rows)
                if _row_text(rows[j])
            )
        elif blk.get("type") == "box":
            # A box counts one block per paragraph, as it read before it was
            # boxed: a title split over its first lines is eaten (#238),
            # through a box it opens with (#310), never into a table.
            lines, whole = _box_title_lines(blk)
            entries.extend((i, ("box", path), text) for path, text in lines)
            if not whole:
                break
        else:
            entries.append((i, None, blk.get("text", "")))
    eaten = _consume_split_title([{"text": t} for _, _, t in entries], title)
    if not eaten:
        return blocks, []
    last, last_row, _ = entries[eaten - 1]
    carried = [k for blk in blocks[:last] for k in blk.get("anchor_keys") or []]
    if isinstance(last_row, tuple):
        kept, more = _drop_box_lines(blocks[last], last_row[1])
        carried.extend(more)
        return ([kept] if kept else []) + blocks[last + 1 :], carried
    if last_row is not None:
        order = _source_order(blocks[last]["table"]["rows"])
        kept = _drop_table_rows(blocks[last], set(order[: order.index(last_row) + 1]))
        if kept is not None:
            return [kept] + blocks[last + 1 :], carried
    carried.extend(blocks[last].get("anchor_keys") or [])
    return blocks[last + 1 :], carried


def _dedupe_keys(keys):
    """Order-preserving dedupe for anchor-key lists."""
    seen = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


#: Per-`$145` content-fragment byte ceiling enforced by the format. Upstream
#: kfxlib (`yj_structure.py`) errors at `>=`, not `>`, so a fragment measuring
#: exactly this is already a violation.
MAX_CONTENT_FRAGMENT_SIZE = 8192

#: Positions between reading locations, as kfxlib places them for a Kindle
#: Previewer book (`KFX_POSITIONS_PER_LOCATION`). The Paperwhite's progress %
#: is the share of locations passed, so evenly spaced locations make it follow
#: the text (#227).
POSITIONS_PER_LOCATION = 110


def place_locations(sections):
    """[(eid, offset)] for `$550`: a location every POSITIONS_PER_LOCATION
    positions, counted again from each section's start, with the offset into
    the entry it falls in. `sections` is one list per section of (eid,
    length) in reading order, the section's own eid first. This is kfxlib's
    `generate_approximate_locations`."""
    locations = []
    for entries in sections:
        pid = next_location = 0
        for eid, length, *start in entries:
            start = start[0] if start else 0
            if length <= 0:
                # A link container shares its picture's position (#355): a
                # location falling there is the container's, as in kfxlib.
                if next_location == pid:
                    locations.append((eid, 0))
                    next_location += POSITIONS_PER_LOCATION
                continue
            while next_location < pid + length:
                locations.append((eid, start + next_location - pid))
                next_location += POSITIONS_PER_LOCATION
            pid += length
    return locations


#: Positions between approximate pages and the most a page break moves back
#: to its word's start, as kfxlib has them (`TYPICAL_POSITIONS_PER_PAGE`,
#: `MAX_WHITE_SPACE_ADJUST`). A Paperwhite showed kfxlib's pages on a kfxgen
#: book (#586).
POSITIONS_PER_PAGE = 1850
MAX_PAGE_BREAK_ADJUST = 50


def place_pages(sections, texts):
    """[(eid, offset)] for an approximate page list: a page at each section's
    start, then one every POSITIONS_PER_PAGE positions, moved back to the
    start of its word. `sections` is as for `place_locations`, `texts` each
    text entry's words by eid. This is kfxlib's `determine_approximate_pages`
    with its defaults (#586)."""
    pages = []
    for entries in sections:
        pid = 0
        next_page = None
        # Not the section's own element, which kfxlib counts for locations
        # but not for pages.
        for eid, length, *start in entries[1:]:
            start = start[0] if start else 0
            if next_page is None:
                next_page = pid
            text = texts.get(eid, "")[start : start + length]
            lowest = 0
            while (offset := max(next_page - pid, 0)) < length:
                if text and not text[offset].isspace():
                    first = offset
                    while offset and not text[offset - 1].isspace():
                        if offset <= lowest:
                            offset = first
                            break
                        offset -= 1
                pages.append((eid, start + offset))
                next_page += POSITIONS_PER_PAGE
                lowest = offset + max(POSITIONS_PER_PAGE - MAX_PAGE_BREAK_ADJUST, 1)
            pid += max(length, 0)
    return pages


def pack_content_fragments(text_strings, start_index, limit=MAX_CONTENT_FRAGMENT_SIZE):
    """Group a chapter's chunk strings into `$145` fragments under the cap (#37).

    One `$145` per chapter overflowed the format's per-fragment maximum on
    ordinary trade books — a 121 KB fragment against a 8192-byte ceiling. The
    reference KDP layout splits a chapter's content across several ~8 KB
    fragments instead, which is what this reproduces.

    The cap is measured the way upstream kfxlib measures it, which is not
    obvious and not what #37's original design assumed:

        sum(len(s.encode("utf8")) for s in strings[:-1]) >= 8192  -> error

    The **last** string of a fragment is excluded from the total, and the
    comparison is `>=`. Two consequences shape this function: a group is
    charged only for the strings that are not currently last, and a group
    holding a single string is always conformant no matter how long that
    string is — so there is no unsplittable input and the loop always makes
    progress.

    Budgeting is on encoded bytes, not characters: CJK runs 3 bytes/char, and
    a character budget would emit fragments upstream rejects.

    Args:
        text_strings: The chapter's chunk strings, in reading order.
        start_index: 1-based number for this chapter's first fragment. Names
            are allocated sequentially from here (`content_<n>`), so a chapter
            fitting in one fragment keeps the plain `content_N` name it had
            before this change and short books stay byte-identical.
        limit: Byte ceiling, exposed for tests.

    Returns:
        (groups, refs) where `groups` is [(fragment_name, [strings])] and
        `refs` is one (fragment_name, local_index) per input string, in input
        order. `refs` is what `$259` entries address content through.

        Always at least one group, even for a chapter with no text at all —
        an image-only chapter (a synthetic cover) has owned an empty `$145`
        since #2, and dropping it here would move bytes in device-verified
        output for a reason unrelated to the size cap.
    """
    if not text_strings:
        return [(f"content_{start_index}", [])], []

    groups = []
    refs = []
    current = []
    # Bytes of every string in `current` except the last — the total upstream
    # will charge this fragment if nothing more is appended.
    committed = 0
    name = f"content_{start_index}"

    for s in text_strings:
        if current:
            # Appending makes the present last string a non-final one, so it
            # starts counting against the budget.
            projected = committed + len(current[-1].encode("utf-8"))
            if projected >= limit:
                groups.append((name, current))
                start_index += 1
                name = f"content_{start_index}"
                current = []
                committed = 0
            else:
                committed = projected
        refs.append((name, len(current)))
        current.append(s)

    groups.append((name, current))
    return groups, refs


def _safe_write_bytes(path, data):
    """Write `data` to `path` with symlink and traversal defenses (#45).

    - Rejects paths containing '..' segments at the input boundary.
    - Refuses to overwrite if the destination is already a symlink (loud
      failure rather than silently writing through to the symlink target).
    - Writes to `path + ".tmp"` with O_NOFOLLOW where supported (so a
      symlink planted at the tmp slot fails with ELOOP on POSIX) then
      atomically replaces the destination via os.replace. On Windows
      O_NOFOLLOW is unavailable; defense relies on the islink() check.

    A TOCTOU window exists between the islink check and the replace — out
    of scope for the single-user / single-machine threat model in
    SECURITY.md.

    `path` may be str or any os.PathLike (e.g. pathlib.Path). Normalize
    once via os.fspath so the safety checks, the tmp slot, and the
    rename all operate on the same string representation (#102).
    """
    path = os.fspath(path)
    candidate = path

    # Defense in depth — two independent layers, either of which rejects.
    # (A third, normpath, could only find a '..' that layer 2 had already
    # rejected: removed in #457.)
    #
    # Layer 1 (regex): catches Windows drive-relative '..' with no separator
    #   before the dots, e.g. 'C:..\\foo' or bare 'C:..'. These have no '/'
    #   or '\\' between the drive letter and the '..', so the segment-split
    #   layers below see a single token like 'C:..' and miss the traversal.
    #   This regex is the only layer that catches that shape.
    if _DRIVE_RELATIVE_TRAVERSAL_RE.match(candidate):
        _security_log.warning("rejected drive-relative output path: %r", path)
        raise ValueError("output path is drive-relative with '..': %r" % path)

    # Layer 2 (raw segment split): substitute '\\' → '/' so Windows-style
    #   separators participate, then split and reject any literal '..'
    #   segment. Catches inputs where '..' appears verbatim as a path
    #   component, e.g. 'foo/../bar', 'foo\\..\\bar', or 'C:.\\..\\foo'
    #   (which splits into ['C:.', '..', 'foo']).
    raw_parts = candidate.replace("\\", "/").split("/")
    if any(p == ".." for p in raw_parts):
        _security_log.warning("rejected output path with traversal segment: %r", path)
        raise ValueError("output path contains '..' segment: %r" % path)

    if os.path.islink(path):
        _security_log.warning("refused to overwrite symlink at output path: %r", path)
        raise OSError(errno.ELOOP, "refusing to overwrite symlink", path)

    tmp = path + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NOFOLLOW
    try:
        # A file being replaced keeps its mode: a book made private stays
        # private when re-converted (#458).
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        mode = None
    fd = os.open(tmp, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if mode is not None and hasattr(os, "fchmod"):
                try:
                    os.fchmod(f.fileno(), mode)
                except OSError:
                    pass  # best-effort: vfat refuses chmod (#493 QA)
            f.flush()
            # On disk before the rename, so a crash can't leave an empty book.
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Also when the replace fails, e.g. a directory at `path` (#458).
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _picture_link(at, spans):
    """The link flags of the span covering a picture's token at `at`, or None
    (#355). Only the link: emphasis has nothing to draw on a picture."""
    from .inline_style import link_target

    for start, length, flags in spans:
        if start <= at < start + length:
            link = frozenset(f for f in flags if link_target({f}))
            if link:
                return link
    return None


#: The heading navigation's unit for each heading level (#461).
_HEADING_LEVEL_UNITS = {
    1: "$799",
    2: "$800",
    3: "$801",
    4: "$802",
    5: "$803",
    6: "$804",
}


def _book_headings(ch_data):
    """[(position, level)] of the book's <h1>-<h6> in reading order (#461).
    Only those: Kindle Previewer 4.0.3 lists no label kfxgen prints (#575)."""
    out = []
    for idx, chunk in enumerate(ch_data["all_chunks"]):
        level = chunk.get("heading_level")
        position = ch_data["chunk_positions"][idx]
        if level and position is not None:
            out.append((position, level))
    return out


def _heading_levels(headings):
    """$798's units: one per level present, at its first heading, holding a
    child per heading, as Kindle Previewer 4.0.3 writes them (#461), in the
    order the levels first appear, not level order (#584)."""
    units = []
    for level in dict.fromkeys(lv for _p, lv in headings):
        positions = [p for p, lv in headings if lv == level]

        def target(position):
            return IonStruct(IS("$155"), position, IS("$143"), 0)

        units.append(
            IonAnnotation(
                [IS("$393")],
                IonStruct(
                    IS("$238"),
                    IS(_HEADING_LEVEL_UNITS[level]),
                    IS("$241"),
                    IonStruct(IS("$244"), "heading-nav-unit"),
                    IS("$246"),
                    target(positions[0]),
                    IS("$247"),
                    [
                        IonAnnotation(
                            [IS("$393")],
                            IonStruct(
                                IS("$241"),
                                IonStruct(IS("$244"), "heading-nav-unit"),
                                IS("$246"),
                                target(p),
                            ),
                        )
                        for p in positions
                    ],
                ),
            )
        )
    return units


def _with_anchors(block, source):
    """`block` also holding `source`'s anchors, at its start."""
    keys = list(source.get("anchor_keys") or [])
    return {
        **block,
        "anchor_keys": [*keys, *(block.get("anchor_keys") or [])],
        "anchor_ids": [
            *(source.get("anchor_ids") or []),
            *(block.get("anchor_ids") or []),
        ],
        "anchor_offsets": {
            **dict.fromkeys(keys, 0),
            **(block.get("anchor_offsets") or {}),
        },
    }


#: A picture inside a paragraph's words, one character of its text (#529).
INLINE_PICTURE = "\ufffc"


def _token_resource(href, image_resources, resolved_image_refs):
    """The (resource, location) a picture token's href names, or None: its
    exact manifest href, then its decoded form; its basename only for hrefs
    that were never resolved (`_may_fall_back_by_basename`). A query string
    names no different file."""
    bare = href.split("#", 1)[0].split("?", 1)[0] if href else ""
    resource = image_resources.get(bare)
    if resource is None:
        resource = image_resources.get(unquote(bare))
    if resource is None and _may_fall_back_by_basename(bare, resolved_image_refs):
        resource = image_resources.get(bare.rsplit("/", 1)[-1])
    return resource


def _may_fall_back_by_basename(href, resolved_image_refs):
    """Whether an image href that matched no key may try its bare filename.

    Callers that pass raw document-relative sources rely on the basename to
    bridge the two namespaces. When converter has resolved the href, a miss
    means the file is not there, and a basename match is some other image
    that shares the name: the reader would see the wrong picture (#195). Only
    an href resolution could not handle (it escaped the book root, or is
    absolute) still falls back; in a 200-book library sample the one such
    case found the right image.
    """
    if not resolved_image_refs:
        return True
    return href.startswith("/") or ".." in href.replace("\\", "/").split("/")


def _table_feature_version(chapters):
    """`yj_table` version the book needs: 3 when a native table uses rowspan
    (firmware 5.8.7+, Kindle Previewer 3.106), otherwise 1 (#219)."""
    for chapter in chapters:
        for block in chapter.get("blocks") or ():
            tbl = block.get("table") if isinstance(block, dict) else None
            if tbl and any(
                c.get("rowspan", 1) > 1 for r in tbl["rows"] for c in r["cells"]
            ):
                return 3
    return 1


#: CSS border style -> KFX symbol, as Kindle Previewer writes it (#264).
_BORDER_STYLE_SYMBOLS = {
    "solid": "$328",
    "double": "$329",
    "dashed": "$330",
    "dotted": "$331",
    "groove": "$334",
    "ridge": "$335",
    "inset": "$336",
    "outset": "$337",
}
#: Per side: (width, style, colour) properties.
_BORDER_SIDE_KEYS = {
    "top": ("$94", "$89", "$84"),
    "right": ("$97", "$92", "$87"),
    "bottom": ("$96", "$91", "$86"),
    "left": ("$95", "$90", "$85"),
}


def _border_key(border):
    """A border dict as a hashable style-cache key, or None."""
    return tuple(sorted(border.items())) if border else None


def _apply_border(value, border, is_table):
    """Write a table's or cell's border into its $157 `value` as Kindle
    Previewer 4.0.1 does (#264): equal sides as $93/$88 (+$83 unless black),
    otherwise each drawn side's own width, style and colour. A table's
    default grey $83 goes when its border is black, or it would draw grey.
    `border` is the converter's {side: (style, width_pt, argb or None)}."""
    sides = {
        side: spec
        for side, spec in dict(border or ()).items()
        if spec[0] in _BORDER_STYLE_SYMBOLS and spec[1] > 0
    }
    if not sides:
        return

    def pt(width):
        return IonStruct(IS("$307"), IonDecimal(f"{width:g}"), IS("$306"), IS("$318"))

    specs = set(sides.values())
    if len(sides) == 4 and len(specs) == 1:
        kind, width, colour = specs.pop()
        value[IS("$93")] = pt(width)
        value[IS("$88")] = IS(_BORDER_STYLE_SYMBOLS[kind])
        if colour:
            value[IS("$83")] = colour
        elif is_table:
            value.pop(IS("$83"), None)
        return
    for side, (kind, width, colour) in sides.items():
        w, st, c = _BORDER_SIDE_KEYS[side]
        value[IS(w)] = pt(width)
        value[IS(st)] = IS(_BORDER_STYLE_SYMBOLS[kind])
        if colour:
            value[IS(c)] = colour
    if is_table:
        value.pop(IS("$83"), None)


def _column_widths_152(column_widths):
    """A table's $152 column list as Kindle Previewer 4.0.1 writes it (#264),
    or None. From <col>: one entry per column, {$56: n%, $546: $377} or {}.
    From cells: {$56: n%}, adjacent equal widths merged by a $118 count.
    Trailing empty entries are left out."""
    if not column_widths:
        return None
    source, widths = column_widths
    widths = list(widths)
    while widths and widths[-1] is None:
        widths.pop()
    if not widths:
        return None

    def pct(w):
        return IonStruct(IS("$307"), IonDecimal(f"{w:g}"), IS("$306"), IS("$314"))

    out = []
    if source == "col":
        for w in widths:
            out.append(
                IonStruct()
                if w is None
                else IonStruct(IS("$56"), pct(w), IS("$546"), IS("$377"))
            )
        return out
    i = 0
    while i < len(widths):
        w = widths[i]
        n = 1
        while i + n < len(widths) and widths[i + n] == w and w is not None:
            n += 1
        if w is None:
            out.append(IonStruct())
        else:
            entry = IonStruct(IS("$56"), pct(w))
            if n > 1:
                entry[IS("$118")] = n
            out.append(entry)
        i += n
    return out


def _padding_values(padding, table_width):
    """A cell's padding as Kindle Previewer 4.0.1 writes it (#264):
    {"$52": IonStruct, ...} for each side that isn't zero. Top and bottom
    em / 1.2 lh; left and right em x 3.125 %, over the table's width
    fraction; a percentage p is p x 32 / 100 em vertically and p %
    horizontally."""
    from decimal import ROUND_HALF_UP, Decimal

    frac = (table_width / 100.0) if table_width else 1.0
    keys = {"top": "$52", "right": "$55", "bottom": "$54", "left": "$53"}
    out = {}
    for side, value in padding.items():
        if isinstance(value, (tuple, list)):
            em, pct = value[1] * 32 / 100.0, value[1] / frac
        else:
            em, pct = value, value * 3.125 / frac
        if em == 0:
            continue
        if side in ("top", "bottom"):
            out[keys[side]] = IonStruct(
                IS("$307"), IonDecimal(f"{em / 1.2:.6g}"), IS("$306"), IS("$310")
            )
        else:
            q = Decimal(repr(pct)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            out[keys[side]] = IonStruct(
                IS("$307"), IonDecimal(str(q.normalize())), IS("$306"), IS("$314")
            )
    return out


def _table_has_zoom(tbl):
    """Whether a native table gets the table viewer's zoom button: every table
    of 2 or more columns. A one-column table gets `max-width: 100%` instead
    and reads as plain paragraphs (#272).

    Kindle Previewer 4.0.1 also leaves the button off plain 2-3 column tables
    with no header row and no border, but the Voyage (5.13.6) then drops
    their columns and stacks every cell (#272 device check), as #254 found it
    squeezing them. So only one-column tables go without it."""
    return (
        max(
            (sum(c.get("colspan", 1) for c in r["cells"]) for r in tbl["rows"]),
            default=0,
        )
        >= 2
    )


def _nest_nav_entries(entries):
    """Contents entries in reading order, each with a "level", as a tree:
    each entry's "children" are the entries after it at a deeper level, up to
    the next at its own level or shallower (#313). An entry whose parent was
    left out goes under the nearest entry above it at a shallower level; one
    with none is a top-level entry."""
    roots, open_ = [], []  # open_: (level, entry) from the top down
    for e in entries:
        node = {**e, "children": []}
        level = e.get("level", 0)
        while open_ and open_[-1][0] >= level:
            open_.pop()
        (open_[-1][1]["children"] if open_ else roots).append(node)
        open_.append((level, node))
    return roots


class NativeKFXGenerator:
    """
    Generates KFX files from scratch using standard symbols and deterministic
    fragment structures, without requiring templates or Kindle Previewer.
    """

    def __init__(self):
        self.symtab = StandardSymbolTable()
        self.fragments = []
        self._wrote_zoom_table = False
        self.entity_ids = {}  # Map of logical ID to entity ID
        # Start at 349 to avoid collision with fid=348 used by metadata fragments
        # ($585, $490, $538, $410, $419 all use fid=348)
        self.next_entity_id = 349
        self.field_403_counter = 10  # Global counter for Fragment 259 Field $403
        # Empty by default (#15): storyline builders may run without a full
        # generate_full_book call. generate_full_book overrides this per book.
        from .font_table import FontTable  # noqa: PLC0415

        self.font_table = FontTable([])
        # Pop-up notes (#445), empty for the same reason.
        self._footnote_keys = set()
        self._popup_anchors = set()  # link anchors of pop-up notes
        self._popup_positions = set()  # the note entries they land on

    def build_fragment_585(self, table_version=1):
        """
        Builds Fragment $585 (Content Features)
        Standard structure for reflowable books.

        Includes capabilities matching known-good KFX files:
        - yj_table: table support
        - yj_table_viewer: added by `_declare_table_viewer` once a native
          table has been written (#254)
        - reflow-style: basic reflowable styling
        - CanonicalFormat: standard format marker
        reflow-section-size, yj_hdv and yj_jpg_rst_marker_present are added
        by `_declare_content_features` once the content is built, only when
        the book has them (#455).
        """
        self.symtab.create_local_symbol("com.amazon.yjconversion")
        self.symtab.create_local_symbol("reflow-style")
        self.symtab.create_local_symbol("yj_table")
        self.symtab.create_local_symbol("version")
        self.symtab.create_local_symbol("SDK.Marker")
        self.symtab.create_local_symbol("CanonicalFormat")

        def make_version(major, minor=0):
            return IonStruct(
                IS("version"), IonStruct(IS("$587"), major, IS("$588"), minor)
            )

        features_list = [
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "yj_table",
                IS("$589"),
                make_version(table_version),
            ),
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "reflow-style",
                IS("$589"),
                make_version(1),
            ),
            IonStruct(
                IS("$586"),
                "SDK.Marker",
                IS("$492"),
                "CanonicalFormat",
                IS("$589"),
                make_version(1),
            ),
        ]

        value = IonStruct(IS("$590"), features_list)
        return YJFragment(fid=IS("$348"), ftype=IS("$585"), value=value)

    def _declare_content_features(self, max_section_pids):
        """Add the content features the built book has to $585, by kfxlib's
        rules (#455): reflow-section-size from its largest section's
        positions, left at 1 (undeclared) up to 65,536; yj_hdv for a picture
        over 1920 px on a side; yj_jpg_rst_marker_present for a JPEG holding
        a restart marker. Symbols are created here, last, as
        `_declare_table_viewer`'s is."""
        features = next(f for f in self.fragments if f.ftype == IS("$585")).value[
            IS("$590")
        ]

        def feature(name, major):
            self.symtab.create_local_symbol(name)
            return IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                name,
                IS("$589"),
                IonStruct(IS("version"), IonStruct(IS("$587"), major, IS("$588"), 0)),
            )

        n = max_section_pids
        size = min((n - 65536) // 16384 + 2, 256) if n > 65536 else 1
        if size > 1:
            # After yj_table and its viewer, where it stood before (#455).
            at = sum(
                1
                for f in features
                if str(f.get(IS("$492"))) in ("yj_table", "yj_table_viewer")
            )
            features.insert(at, feature("reflow-section-size", size))
        resources = [f.value for f in self.fragments if f.ftype == IS("$164")]
        if any(
            max(int(r.get(IS("$422"), 0) or 0), int(r.get(IS("$423"), 0) or 0)) > 1920
            for r in resources
        ):
            features.append(feature("yj_hdv", 1))
        jpeg_locations = {
            str(r.get(IS("$165"))) for r in resources if r.get(IS("$161")) == IS("$285")
        }
        if any(
            re.search(rb"\xff[\xd0-\xd7]", bytes(f.value))
            for f in self.fragments
            if f.ftype == IS("$417") and str(f.fid) in jpeg_locations
        ):
            features.append(feature("yj_jpg_rst_marker_present", 1))

    def _declare_ruby(self):
        """Add `yj_ruby` 1.0 to the built $585 once a ruby is written, as Kindle
        Previewer declares it (#236). The symbol is created last, as
        `_declare_table_viewer`'s is."""
        self.symtab.create_local_symbol("yj_ruby")
        frag = next(f for f in self.fragments if f.ftype == IS("$585"))
        frag.value[IS("$590")].append(
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "yj_ruby",
                IS("$589"),
                IonStruct(IS("version"), IonStruct(IS("$587"), 1, IS("$588"), 0)),
            )
        )

    def build_ruby_style_157(self, entity_name):
        """A reading's style: half the base's size at a normal line height,
        Kindle Previewer's for every reading (#236)."""
        self.symtab.create_local_symbol(entity_name)
        value = IonStruct(
            IS("$16"),
            IonStruct(IS("$307"), IonDecimal("0.5"), IS("$306"), IS("$308")),
            IS("$42"),
            IS("$383"),
            IS("$173"),
            IS(entity_name),
        )
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_fragment_756(self, name, readings):
        """A chapter's ruby fragment (#236): one text entry per reading, whose
        `$758` is the number a base's event names. `readings` is
        [(eid, reading)] in reading order."""
        self.symtab.create_local_symbol(name)
        entries = [
            IonStruct(
                IS("$155"),
                eid,
                IS("$157"),
                IS(self.RUBY_STYLE_NAME),
                IS("$159"),
                IS("$269"),
                IS("$758"),
                number,
                IS("$145"),
                reading,
            )
            for number, (eid, reading) in enumerate(readings, 1)
        ]
        value = IonStruct(IS("$757"), IS(name), IS("$146"), entries)
        return YJFragment(fid=IS(name), ftype=IS("$756"), value=value)

    def _declare_feature(self, name):
        """Append `name` 1.0 to the built $585 (#235)."""
        self.symtab.create_local_symbol(name)
        frag = next(f for f in self.fragments if f.ftype == IS("$585"))
        frag.value[IS("$590")].append(
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                name,
                IS("$589"),
                IonStruct(IS("version"), IonStruct(IS("$587"), 1, IS("$588"), 0)),
            )
        )

    def _math_note(self, text):
        """A content reference to an equation's alt text or source (#235).
        A fragment takes strings until it holds MAX_CONTENT_FRAGMENT_SIZE
        bytes, kfxlib's limit, which leaves out its last string."""
        groups = self._math_strings
        if not groups or sum(len(t.encode("utf-8")) for t in groups[-1][1]) >= (
            MAX_CONTENT_FRAGMENT_SIZE
        ):
            name = f"content_math{len(groups) + 1}"
            self.symtab.create_local_symbol(name)
            groups.append((name, []))
        name, strings = groups[-1]
        strings.append(text)
        return IonStruct(IS("$4"), IS(name), IS("$403"), len(strings) - 1)

    def _inline_picture_entry(self, item, eids):
        """A picture inside a paragraph's words, as Kindle Previewer 4.0.3
        writes it (#529): inline, its pixels / 16 in em on both axes, in the
        paragraph's own em (48 px is 3em; in text at 1.5em, 2em). A width the
        markup gives is kept: a % of Previewer 4's 512 px column, em as
        written, px at 0.45pt and pt with 1em = 12pt (as #541 measured
        margins); its height follows the picture's shape. A height given
        alone is kept the same way, the width following it; a height given
        with a width is kept too, its shape aside, as is a vertical-align
        (#565). A picture the markup sizes is marked `$546: $377`, as
        Previewer marks it."""
        _kind, resource, alt, hint, font_size, extra = item
        extras = dict(e.split("=", 1) for e in (extra or "").split(";") if "=" in e)
        (eid,) = eids
        dims = {}
        for key, (res, _loc) in (self.image_resources or {}).items():
            if key in (self._image_dims or {}):
                dims.setdefault(res, self._image_dims[key])
        width, height = dims.get(resource) or (None, None)
        # Pixels unknown (a picture that won't decode): one em.
        width, height = width or 16, height or 16
        scale = 16 * (font_size or 1)
        w_em, h_em, fill = width / scale, height / scale, False
        m = _SIZE_HINT_RE.match(hint or "")
        number, unit = (float(m.group(2)), m.group(3) or "px") if m else (0, "")
        if m and m.group(1) == "w":
            if unit == "em":
                w_em, fill = number, True
            elif unit == "%":
                w_em = number / 100 * 512 / scale
            else:
                pt = number * (0.45 if unit == "px" else 1.0)
                w_em = pt / 12 / (font_size or 1)
            h_em = w_em * height / width
            given = _SIZE_HINT_RE.match(f"h={extras.get('h', '')}")
            if given and (given.group(3) or "px") != "%":
                h_em = _inline_length_em(given, font_size)
        elif m and unit != "%":
            # A height alone, the same rule on the other axis (QA on #564:
            # 2em stays 2em, height="30" is 1.125em).
            h_em = _inline_length_em(m, font_size)
            w_em = h_em * width / height
        valign = _INLINE_VALIGN_SYMBOLS.get(extras.get("va"))
        size = (f"{round(w_em, 5):g}", f"{round(h_em, 5):g}", fill, bool(m), valign)
        if size not in self._inline_picture_styles:
            name = f"s_ipic{len(self._inline_picture_styles)}"
            self.symtab.create_local_symbol(name)

            def em(v):
                return IonStruct(IS("$307"), IonDecimal(v), IS("$306"), IS("$308"))

            value = IonStruct(IS("$56"), em(size[0]), IS("$57"), em(size[1]))
            if fill:
                # Never past the column, as Previewer writes an em width.
                value[IS("$65")] = IonStruct(
                    IS("$307"), IonDecimal("100"), IS("$306"), IS("$314")
                )
            if m:
                value[IS("$546")] = IS("$377")
            if valign:
                value[IS("$44")] = IS(valign)
            value[IS("$173")] = IS(name)
            self._inline_picture_styles[size] = (name, value)
        return IonStruct(
            IS("$155"),
            eid,
            IS("$584"),
            alt,
            IS("$601"),
            IS("$283"),
            IS("$157"),
            IS(self._inline_picture_styles[size][0]),
            IS("$159"),
            IS("$271"),
            IS("$175"),
            IS(resource),
        )

    def _math_entry(self, item, eids):
        """A drawn equation as Kindle Previewer writes it (#235): a container
        holding a container holding one shape of glyph paths."""
        _serial, drawing, alt, source = item
        e1, e2, e3 = eids

        def em(v):
            return IonStruct(
                IS("$307"), IonDecimal(f"{v / 1000:.5f}"), IS("$306"), IS("$308")
            )

        pct100 = IonStruct(IS("$307"), IonDecimal("100"), IS("$306"), IS("$314"))
        n = len(self._math_styles) // 2
        box_style, shape_style = f"mbox{n}", f"mshape{n}"
        for name in (box_style, shape_style):
            self.symtab.create_local_symbol(name)
        self._math_styles.append(
            (
                box_style,
                IonStruct(
                    IS("$56"),
                    em(drawing.width),
                    IS("$65"),
                    pct100,
                    IS("$173"),
                    IS(box_style),
                ),
            )
        )
        self._math_styles.append(
            (
                shape_style,
                IonStruct(
                    IS("$546"),
                    IS("$377"),
                    IS("$173"),
                    IS(shape_style),
                    IS("$31"),
                    em(-drawing.depth),
                ),
            )
        )

        def number(v):
            # In its fewest digits: the Oasis reads a decimal's digits as a
            # 32-bit number, so 4474.5 as 4474.500000 wraps round to 179.5.
            return v if isinstance(v, int) else IonDecimal(str(v))

        paths = []
        for glyph, path, matrix in drawing.paths:
            # A glyph's path is stored once in the book, as Previewer does.
            index = self._math_glyphs.get(glyph) if glyph else None
            if index is None:
                index = len(self._math_paths)
                self._math_paths.append(list(path))
                if glyph:
                    self._math_glyphs[glyph] = index
            paths.append(
                IonStruct(
                    IS("$249"),
                    IonStruct(IS("$403"), index, IS("$4"), IS("mpaths")),
                    IS("$98"),
                    [number(v) for v in matrix],
                    IS("$76"),
                    0,
                    IS("$159"),
                    IS("$273"),
                )
            )
        shape = IonStruct(
            IS("$155"),
            e3,
            IS("$65"),
            pct100,
            IS("$66"),
            drawing.width,
            IS("$67"),
            drawing.height,
            IS("$56"),
            em(drawing.width),
            IS("$57"),
            em(drawing.height),
            IS("$250"),
            paths,
            IS("$157"),
            IS(shape_style),
            IS("$686"),
            IS("$269"),
            IS("$159"),
            IS("$272"),
        )
        inner = IonStruct(
            IS("$155"),
            e2,
            IS("$156"),
            IS("$323"),
            IS("$159"),
            IS("$270"),
            IS("$615"),
            IS("$689"),
            IS("$146"),
            [shape],
        )
        notes = [
            IonStruct(IS("$145"), self._math_note(alt or " "), IS("$687"), IS("$584")),
            IonStruct(IS("$145"), self._math_note(source), IS("$687"), IS("$690")),
        ]
        return IonStruct(
            IS("$155"),
            e1,
            IS("$615"),
            IS("$688"),
            IS("$601"),
            IS("$283"),
            IS("$683"),
            notes,
            IS("$156"),
            IS("$323"),
            IS("$684"),
            IS("$441"),
            IS("$157"),
            IS(box_style),
            IS("$159"),
            IS("$270"),
            IS("$146"),
            [inner],
        )

    def _declare_table_viewer(self):
        """Add `yj_table_viewer` 1 after `yj_table` in the built $585 (#254).

        Called once the storylines are built, and only when one wrote a $278
        with the zoom button: Kindle Previewer declares it only then (#272).
        A table can be cut whole as a chapter title, so the chapters' blocks
        don't say whether the book has one. The symbol is created here, last,
        so every other symbol keeps its number.
        """
        self.symtab.create_local_symbol("yj_table_viewer")
        frag = next(f for f in self.fragments if f.ftype == IS("$585"))
        frag.value[IS("$590")].insert(
            1,
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "yj_table_viewer",
                IS("$589"),
                IonStruct(IS("version"), IonStruct(IS("$587"), 1, IS("$588"), 0)),
            ),
        )

    def build_fragment_164(
        self,
        resource_name,
        location_name,
        image_format="jpeg",
        width=None,
        height=None,
        include_mime=False,
    ):
        """
        Builds Fragment $164 (Resource Metadata) for an image resource.

        The $164 and $417 fragments MUST have different fids, linked by $165:
        - $164.fid = resource_name (e.g., "cover_img")
        - $164.$165 = location_name (e.g., "cover_img_raw") → matches $417 fid
        - $164.$175 = resource_name (self-reference, required by Kindle)
        - $164.$162 = MIME type — required for cover thumbnail extraction on
          Kindle home screen (#39, verified on Paperwhite). Optional for
          body images; reference Calibre KFX includes it on some, omits
          on others.

        Args:
            resource_name: Local symbol name for this resource (e.g., "cover_img")
            location_name: Local symbol name for the $417 raw data (e.g., "cover_img_raw")
            image_format: "jpeg" or "png"
            width: Optional image width in pixels
            height: Optional image height in pixels
            include_mime: If True, emit $162 with the MIME type. Pass True
                for the cover image to enable Kindle home-screen thumbnail
                extraction (#39).

        Returns:
            YJFragment with type $164
        """
        self.symtab.create_local_symbol(resource_name)
        self.symtab.create_local_symbol(location_name)
        # $285 = JPEG, $284 = PNG, $286 = GIF
        format_sym = "$285" if image_format == "jpeg" else "$284"

        value = IonStruct(
            IS("$175"),
            IS(resource_name),  # Self-reference (required by Kindle)
            IS("$161"),
            IS(format_sym),  # Format
            IS("$165"),
            location_name,  # Location ref — plain STRING (not symbol!)
        )
        if width and height:
            value[IS("$422")] = width
            value[IS("$423")] = height
        if include_mime:
            mime = (
                "image/jpg"
                if image_format == "jpeg"
                else ("image/png" if image_format == "png" else "image/jpeg")
            )
            value[IS("$162")] = mime

        return YJFragment(fid=IS(resource_name), ftype=IS("$164"), value=value)

    def build_fragment_417(self, location_name, image_data):
        """
        Builds Fragment $417 (Raw Binary Data) for cover image.

        $417 is a RAW_FRAGMENT_TYPE — serialized as raw bytes, not Ion.
        The fid MUST match the $165 value in the corresponding $164 fragment.

        Args:
            location_name: Local symbol name matching $164.$165
            image_data: Raw image bytes

        Returns:
            YJFragment with type $417
        """
        self.symtab.create_local_symbol(location_name)
        return YJFragment(
            fid=IS(location_name), ftype=IS("$417"), value=IonBLOB(image_data)
        )

    def build_fragment_418(self, location_name, font_data):
        """Raw font BLOB ($418) — analog of $417 for images.

        The fid MUST match the $165 value of the paired $262 fragment.
        """
        self.symtab.create_local_symbol(location_name)
        return YJFragment(
            fid=IS(location_name), ftype=IS("$418"), value=IonBLOB(font_data)
        )

    def build_fragment_262(self, emitted_family, location_name, weight, italic):
        """@font-face declaration ($262) — analog of $164 for images.

        $11 (family) is the join key a $157 style sets to apply this face.
        $12/$13 (style/weight) reflect the actual face; omitted when default
        ($350), matching observed KDP output. $15 (stretch) omitted in v1.

        In a final (non-KPF-prepub) KFX, $262 is a ROOT fragment and MUST be
        keyed by its own type — fid="$262" — not the family name (kfxlib
        kpf_book.py re-keys prepub family-named $262 fragments to "$262" on the
        way to final KFX). The family name lives only in $11; multiple faces
        are separate $262 fragments all sharing fid="$262".
        """
        from .font_table import BOLD_WEIGHT_THRESHOLD  # noqa: PLC0415

        self.symtab.create_local_symbol(emitted_family)
        self.symtab.create_local_symbol(location_name)
        value = IonStruct(
            IS("$11"),
            emitted_family,  # font-family (plain string)
            IS("$165"),
            location_name,  # location -> $418 fid (plain string)
        )
        if weight >= BOLD_WEIGHT_THRESHOLD:
            value[IS("$13")] = IS("$361")  # font-weight: bold
        if italic:
            value[IS("$12")] = IS("$382")  # font-style: italic
        return YJFragment(fid=IS("$262"), ftype=IS("$262"), value=value)

    def build_fragment_490(
        self,
        title,
        author,
        asin,
        container_id,
        cover_image=None,
        language="en",
        issue_date=None,
        publisher="kfxgen",
        authors=None,
        description=None,
    ):
        """
        Builds Fragment $490 (Book Metadata). One author entry per author,
        the book's own description, and an issue date only when the book
        gives one, as Kindle Previewer 4.0.3 writes them (#454).
        """
        # Ensure symbols exist
        for sym in [
            "title",
            "author",
            "ASIN",
            "asset_id",
            "book_id",
            "content_id",
            "cover_image",
            "description",
            "is_sample",
            "issue_date",
            "language",
            "override_kindle_font",
            "publisher",
            "kindle_audit_metadata",
            "kindle_ebook_metadata",
            "kindle_title_metadata",
            "kindle_capability_metadata",
            "file_creator",
            "creator_version",
            "selection",
            "nested_span",
            "KPR",
        ]:
            self.symtab.create_local_symbol(sym)

        # 1. Audit Metadata
        audit = IonStruct(
            IS("$495"),
            "kindle_audit_metadata",
            IS("$258"),
            [
                IonStruct(IS("$492"), "file_creator", IS("$307"), "KPR"),
                IonStruct(IS("$492"), "creator_version", IS("$307"), "3.98.0"),
            ],
        )

        # 2. Ebook Metadata
        ebook = IonStruct(
            IS("$495"),
            "kindle_ebook_metadata",
            IS("$258"),
            [
                IonStruct(IS("$492"), "selection", IS("$307"), "enabled"),
                IonStruct(IS("$492"), "nested_span", IS("$307"), "enabled"),
            ],
        )

        # 3. Title Metadata
        # We need to populate this with actual data
        title_meta_list = [
            IonStruct(IS("$492"), "ASIN", IS("$307"), asin),
            IonStruct(IS("$492"), "asset_id", IS("$307"), container_id),
            *(
                IonStruct(IS("$492"), "author", IS("$307"), name)
                for name in (authors or [author])
            ),
            # Use ASIN as book_id/content_id for simplicity
            IonStruct(IS("$492"), "book_id", IS("$307"), asin),
            IonStruct(IS("$492"), "cde_content_type", IS("$307"), "PDOC"),
            IonStruct(IS("$492"), "content_id", IS("$307"), asin),
            *(
                [IonStruct(IS("$492"), "description", IS("$307"), description)]
                if description
                else []
            ),
            IonStruct(IS("$492"), "is_sample", IS("$307"), False),
            # None when the book gives none: no date of the conversion's
            # (#89), nor the 1970-01-01 placeholder (#454).
            *(
                [IonStruct(IS("$492"), "issue_date", IS("$307"), issue_date)]
                if issue_date
                else []
            ),
            IonStruct(IS("$492"), "language", IS("$307"), language),
            # override_kindle_font: when the book embeds fonts (#15), the
            # embedded faces must win over the device's selected font, so set
            # True. With no embedded fonts, keep False so the device font is
            # respected (byte-identical to pre-5.4.0 output).
            IonStruct(
                IS("$492"),
                "override_kindle_font",
                IS("$307"),
                bool(self.font_table.faces),
            ),
            IonStruct(IS("$492"), "publisher", IS("$307"), publisher),
            IonStruct(IS("$492"), "title", IS("$307"), title),
        ]

        if cover_image:
            title_meta_list.append(
                IonStruct(IS("$492"), "cover_image", IS("$307"), cover_image)
            )

        title_meta = IonStruct(
            IS("$495"), "kindle_title_metadata", IS("$258"), title_meta_list
        )

        # 4. Capability Metadata (Empty)
        capability = IonStruct(IS("$495"), "kindle_capability_metadata", IS("$258"), [])

        value = IonStruct(IS("$491"), [audit, ebook, title_meta, capability])

        return YJFragment(fid=IS("$348"), ftype=IS("$490"), value=value)

    def build_fragment_258(self, section_names):
        """
        Builds Fragment $258 (Reading Order Metadata)

        Required by the Kindle to discover the navigation structure.
        Has the same $169 structure as $538, linking $351 reading order to sections.

        Args:
            section_names: List of $260 section fids (e.g., ["c0"])
        """

        for name in section_names:
            self.symtab.create_local_symbol(name)

        value = IonStruct(
            IS("$169"),
            [
                IonStruct(
                    IS("$178"),
                    IS("$351"),
                    IS("$170"),
                    [IS(name) for name in section_names],
                )
            ],
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$258"), value=value)

    def build_fragment_538(self, section_names):
        """
        Builds Fragment $538 (Document Data / Reading Order)

        $538.$169[0].$178 = $351 (standard reading order identifier, shared with $389)
        $538.$169[0].$170 = list of $260 section fids

        Args:
            section_names: List of $260 section fids (e.g., ["c0"])
        """

        for name in section_names:
            self.symtab.create_local_symbol(name)

        value = IonStruct(
            IS("$560"),
            IS("$557"),
            IS("$192"),
            IS("$376"),
            IS("$16"),
            IonStruct(IS("$307"), IonDecimal(1), IS("$306"), IS("$308")),
            IS("$112"),
            IS("$383"),
            IS("$436"),
            IS("$441"),
            IS("max_id"),
            15000,
            IS("$42"),
            IonStruct(IS("$307"), IonDecimal("1.2"), IS("$306"), IS("$308")),
            IS("$477"),
            IS("$56"),
            IS("$169"),
            [
                IonStruct(
                    IS("$178"),
                    IS("$351"),
                    IS("$170"),
                    [IS(name) for name in section_names],
                )
            ],
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$538"), value=value)

    def build_fragment_389_toc(
        self,
        toc_entries,
        cover_position=None,
        headings=None,
        landmarks=(),
        pages=(),
        page_list_name="nP",
    ):
        """
        Builds Fragment $389 (Navigation) with TOC and landmarks.

        Matches the structure used in real Kindle books:
        - Value is a LIST containing one struct
        - Top-level struct has $178: $351 (reading order ref) and $392 (nav containers)
        - Three nav containers (matching known-good):
          1. TOC ($235: $212) — table of contents entries
          2. Landmarks ($235: $236) — structural landmarks (e.g., bodymatter start)

        Args:
            toc_entries: List of dicts with 'title' and 'position' keys, an
                'offset' into that position for an entry that names a place
                inside a paragraph (#363), and a 'level', its depth in the
                book's contents (#313), in reading order
            cover_position: Position of the synthetic cover section, when the
                book has one. The $233 landmark then marks the cover (#160).

        Returns:
            YJFragment with type $389
        """
        heading_name = "nH"
        toc_name = "nT"
        landmarks_name = "nL"
        if headings:
            self.symtab.create_local_symbol(heading_name)
        self.symtab.create_local_symbol(toc_name)
        if cover_position is not None or landmarks:
            self.symtab.create_local_symbol(landmarks_name)

        # The heading navigation ($798): from the book's <h1>-<h6>, a unit
        # per level at its first heading holding one child per heading, as
        # Kindle Previewer 4.0.3 builds it (#461). A book with none has no
        # $798, as in Previewer's file (#575).
        heading_container = None
        if headings:
            heading_container = IonAnnotation(
                [IS("$391")],
                IonStruct(
                    IS("$235"),
                    IS("$798"),
                    IS("$239"),
                    IS(heading_name),
                    IS("$247"),
                    _heading_levels(headings),
                ),
            )

        # Build TOC nav_units, each annotated with $393. An entry's children
        # go in its own $247, as Kindle Previewer 4 nests them (#313).
        def nav_unit(entry):
            fields = [
                IS("$241"),
                IonStruct(IS("$244"), entry["title"]),
                IS("$246"),
                IonStruct(
                    IS("$155"), entry["position"], IS("$143"), entry.get("offset", 0)
                ),
            ]
            if entry["children"]:
                fields += [IS("$247"), [nav_unit(c) for c in entry["children"]]]
            return IonAnnotation([IS("$393")], IonStruct(*fields))

        nav_units = [nav_unit(e) for e in _nest_nav_entries(toc_entries)]

        # TOC container annotated with $391, type $212
        toc_container = IonAnnotation(
            [IS("$391")],
            IonStruct(
                IS("$235"), IS("$212"), IS("$239"), IS(toc_name), IS("$247"), nav_units
            ),
        )

        # Landmarks container annotated with $391, type $236.
        #
        # $233 is the cover landmark. Kindle Previewer 3.106 points it at the
        # cover section, labelled 'cover-nav-unit', and the device draws the
        # page it names as a full-screen cover: scaled to the screen, outside
        # the reader's margin setting. kfxgen pointed it at the first listed
        # chapter instead, so the real cover was an ordinary page and sat
        # inside the margins. A Paperwhite 11th gen (5.19.2) A/B at the widest
        # margins shows the landmark alone decides it: neither the cover
        # section's layout nor the image's own style changed anything (#160).
        #
        # A book with no cover has none, as Kindle Previewer 4.0.3 writes it:
        # a cover landmark at the first chapter could have it drawn as a
        # full-screen cover (#460).
        containers = [c for c in (heading_container, toc_container) if c]
        # The page list ($237): an entry per print page break, as Kindle
        # Previewer 4.0.3 writes it (#578).
        if pages:
            self.symtab.create_local_symbol(page_list_name)
            containers.append(
                IonAnnotation(
                    [IS("$391")],
                    IonStruct(
                        IS("$235"),
                        IS("$237"),
                        IS("$239"),
                        IS(page_list_name),
                        IS("$247"),
                        [
                            IonAnnotation(
                                [IS("$393")],
                                IonStruct(
                                    IS("$241"),
                                    IonStruct(IS("$244"), label),
                                    IS("$246"),
                                    IonStruct(IS("$155"), pos, IS("$143"), offset),
                                ),
                            )
                            for label, pos, offset in pages
                        ],
                    ),
                )
            )
        # After it, the book's contents ($212) and start ($396) landmarks,
        # as Kindle Previewer 4.0.3 writes them (#579).
        units = []
        if cover_position is not None:
            units.append(("$233", "cover-nav-unit", cover_position, 0))
        units += [
            ({"toc": "$212", "start": "$396"}[kind], label, pos, offset)
            for kind, label, pos, offset in landmarks or ()
        ]
        if units:
            landmarks_units = [
                IonAnnotation(
                    [IS("$393")],
                    IonStruct(
                        IS("$238"),
                        IS(symbol),
                        IS("$241"),
                        IonStruct(IS("$244"), label),
                        IS("$246"),
                        IonStruct(IS("$155"), pos, IS("$143"), offset),
                    ),
                )
                for symbol, label, pos, offset in units
            ]
            landmarks_container = IonAnnotation(
                [IS("$391")],
                IonStruct(
                    IS("$235"),
                    IS("$236"),
                    IS("$239"),
                    IS(landmarks_name),
                    IS("$247"),
                    landmarks_units,
                ),
            )

            containers.append(landmarks_container)

        # Top-level struct with reading order ref and its nav containers
        top_struct = IonStruct(
            IS("$178"),
            IS("$351"),
            IS("$392"),
            containers,
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$389"), value=[top_struct])

    def build_fragment_264(self, section_positions):
        """
        Builds Fragment $264 (Position Index)

        Maps section names to lists of position IDs that appear in each section.
        This is critical for enabling navigation through the book.

        Args:
            section_positions: Dict mapping section names to lists of position IDs
                e.g., {"c0": [1000, 1002, 1004, ...], "c1": [1200, 1202, ...]}

        Returns:
            YJFragment with type $264
        """
        entries = []
        for section_name, positions in section_positions.items():
            self.symtab.create_local_symbol(section_name)
            entry = IonStruct(IS("$181"), positions, IS("$174"), IS(section_name))
            entries.append(entry)

        return YJFragment(fid=IS("$348"), ftype=IS("$264"), value=entries)

    def build_fragment_265(self, position_ids):
        """
        Builds Fragment $265 (Position Index Table)

        Maps character offsets to position IDs. The Kindle uses this to
        translate reading locations to content positions.

        Known-good files map $184 = character offset, $185 = position ID.
        Dense position data (one entry per ~200 characters) is required
        for the Kindle to enable the navigation pane.

        Args:
            position_ids: List of (char_offset, position_id) tuples in document order
                e.g., [(0, 1000), (200, 1002), (400, 1004), ...]
                OR list of plain position IDs (legacy, auto-indexed)

        Returns:
            YJFragment with type $265
        """
        entries = []
        for char_offset, position_id, *rest in position_ids:
            eid_offset = rest[0] if rest else None
            entry = IonStruct(IS("$184"), char_offset, IS("$185"), position_id)
            if eid_offset:
                entry[IS("$143")] = eid_offset
            entries.append(entry)

        return YJFragment(fid=IS("$348"), ftype=IS("$265"), value=entries)

    def _build_position_data(self, chapters, section_names, ch_data):
        """
        Build position data fragments ($264, $265, $550) using Z3 pattern.

        Creates interleaved position entries in $265 with both section positions
        (from $260, range 10000+) and content positions (from $259, range 1000+).
        This ensures all position IDs referenced in $265 are properly defined.

        Args:
            chapters: List of {'title': str, 'text': str} dicts
            section_names: List of section names (e.g., ["c0", "c1", ...])
            ch_data: Dict from _build_chapter_content() with chunk data

        Returns:
            dict with keys:
                'position_entries_265': List of (char_offset, position_id) for $265
                'section_positions_264': Dict of section_name -> [position_ids] for $264
                'locations': [(position_id, offset)] for $550, one every
                    POSITIONS_PER_LOCATION positions (#227)
        """
        section_positions = ch_data["section_positions"]
        chunk_positions = ch_data["chunk_positions"]
        math_by_position = ch_data.get("math_by_position") or {}
        math_positions = set()
        chapter_chunk_ranges = ch_data["chapter_chunk_ranges"]
        all_chunks = ch_data["all_chunks"]
        # outer_positions stays in ch_data shape for future nested-$259
        # re-enabling but is unused here in the flat-revert v5.3.2 path.

        # Build $265 entries — section element + content positions (from
        # $259), including image entries.
        #
        # The reference Calibre KFX file maps every $259 leaf entry into
        # $265, including image entries. Excluding image positions from
        # $265 (as v5.3.4 did) breaks navigation for image-heavy chapters
        # like a diagram-only chapter — the TOC target's surrounding storyline has
        # unmapped positions and Kindle bails out to the start of the
        # book. Give image chunks a small synthetic char-offset (+1) so
        # they get their own $265 entry without colliding with adjacent
        # text chunks.
        #
        # Section positions ($260) ARE included, as the pid-0 (length-1)
        # element at the very start of each section (#20). jhowell's KFX
        # Input resolves every eid referenced by $264 and $550 against the
        # eids defined in $265; with the section eids present only in
        # $264/$550, the reader reports "position_map has extra eids" /
        # "location_map failed to locate eid" and fails the desktop
        # KFX->EPUB round-trip. The reader's own walk of the real $259
        # content places the section eid at pid 0, length 1, immediately
        # before the heading — and the jhowell KFX Output reference emits
        # exactly that ($265 begins each section with {$184: pid, $185:
        # <section eid>}). An earlier attempt that inserted section
        # positions at the WRONG offset produced phantom boundary markers
        # and Kindle TOC nav landed one page past the target; placing the
        # eid at the section's real start offset (matching the reference)
        # is the conformant shape.
        entries_265_raw = []
        char_offset = 0
        location_sections = []
        page_texts = {}  # each text entry's words, for page breaks (#586)
        link_positions = set()  # link containers: zero length (#355)

        for ch_idx in range(len(chapters)):
            start, end = chapter_chunk_ranges[ch_idx]

            # Section element: length 1, at the section's starting pid.
            entries_265_raw.append((char_offset, section_positions[ch_idx]))
            char_offset += 1
            located = [(section_positions[ch_idx], 1)]
            location_sections.append(located)

            for chunk_idx in range(start, end):
                chunk = all_chunks[chunk_idx]
                kind = chunk.get("type") if isinstance(chunk, dict) else "text"
                if kind == "close":
                    continue  # ends a container; takes no position (#219)
                if kind == "open" and chunk.get("node") == "link":
                    # Its inline picture starts at the same position, as
                    # kfxlib counts a container whose first child is inline
                    # (#355).
                    chunk_text_len = 0
                    link_positions.add(chunk_positions[chunk_idx])
                elif kind in ("image", "open", "rule"):
                    # synthetic; an image, a rule or a container takes one
                    # offset slot
                    chunk_text_len = 1
                else:
                    chunk_text_len = len(chunk["text"])
                    page_texts[chunk_positions[chunk_idx]] = chunk["text"]
                entries_265_raw.append((char_offset, chunk_positions[chunk_idx]))
                maths = math_by_position.get(chunk_positions[chunk_idx])
                if maths:
                    # A drawn equation (#235): its container, inner container
                    # and shape take a position each, the paragraph one offset.
                    # A picture in the words (#529): its one id, one position.
                    text, items = maths
                    pos = chunk_positions[chunk_idx]
                    done = extra = 0
                    for k, _item, eids in items:
                        base = char_offset + k + extra
                        located.append((pos, k - done, done))
                        if k > done:
                            # The paragraph reaches the item here; right
                            # after another, where that one's resumes it.
                            entries_265_raw.append((base, pos, k))
                            math_positions.add((base, pos))
                        for n, e in enumerate(eids):
                            entries_265_raw.append((base + n, e))
                            math_positions.add(e)
                            located.append((e, 1))
                        done = k + 1
                        if done < len(text):
                            entries_265_raw.append((base + len(eids), pos, done))
                            math_positions.add((base + len(eids), pos))
                        extra += len(eids) - 1
                    if done < len(text):
                        located.append((pos, len(text) - done, done))
                    char_offset += len(text) + extra
                    continue
                char_offset += chunk_text_len
                if chunk_positions[chunk_idx] is not None:
                    located.append((chunk_positions[chunk_idx], chunk_text_len))

        entries_265_raw.sort(key=lambda x: x[0])
        # Deduplicate entries with same $184 char-offset (rare with text-
        # only entries — only fires for adjacent zero-length chunks). A link
        # container and its picture share an offset, and both are listed, as
        # Kindle Previewer lists them (#355).
        seen_offsets = set()
        deduped = []
        for entry in entries_265_raw:
            shares_link = (
                bool(deduped)
                and deduped[-1][0] == entry[0]
                and deduped[-1][1] in link_positions
            )
            in_math = entry[1] in math_positions or tuple(entry[:2]) in math_positions
            if entry[0] not in seen_offsets or shares_link or in_math:
                seen_offsets.add(entry[0])
                deduped.append(entry)
        entries_265_raw = deduped
        entries_265_raw.append(
            (char_offset, 0)
        )  # sentinel (required by Kindle nav pane)

        # Build $264 — map sections to all position IDs (section + every
        # chunk in the section, text and image alike). Image positions
        # are now in $265 too, so this is no longer the source of the
        # progress regression that v5.3.4 was guarding against.
        section_positions_264 = {}
        for ch_idx, sec_name in enumerate(section_names):
            start, end = chapter_chunk_ranges[ch_idx]
            pids = [section_positions[ch_idx]]
            for p in chunk_positions[start:end]:
                if p is not None:
                    pids.append(p)
                    for _k, _item, eids in (math_by_position.get(p) or ("", ()))[1]:
                        pids.extend(eids)
            section_positions_264[sec_name] = pids

        locations = place_locations(location_sections)

        return {
            "position_entries_265": entries_265_raw,
            "section_positions_264": section_positions_264,
            "locations": locations,
            "pages": place_pages(location_sections, page_texts),
            # kfxlib's pid count of each section, for reflow-section-size.
            "max_section_pids": max(
                (
                    sum(max(length, 0) for _eid, length, *_ in sec)
                    for sec in location_sections
                ),
                default=0,
            ),
        }

    def build_ion_symbol_table_fragment(self):
        """
        Builds Fragment $ion_symbol_table (Document Symbol Table)

        This fragment is REQUIRED by the KFX container format. It tells
        the Kindle parser how to interpret Ion symbol IDs in the fragments.

        The symbol table includes:
        - imports: List of shared symbol tables to import (e.g., YJ_Symbols)
        - symbols: List of local symbols defined in this document
        - max_id: Total number of symbols after imports

        Returns:
            YJFragment with type $ion_symbol_table
        """
        # Use the symbol table's create_import method to generate the data
        return YJFragment(self.symtab.create_import())

    def build_fragment_270(self, container_id):
        """
        Builds Fragment $270 (Container Info)

        This fragment is REQUIRED by the KFX container format. It contains:
        - Container ID (CR!...)
        - Compression and DRM settings
        - Application version info

        Args:
            container_id: String (e.g. "CR!FEHAKUKMU05DIVSIJBZWPW44H9VH")

        Returns:
            YJFragment with type $270
        """
        value = IonStruct(
            IS("$409"),
            container_id,  # Container ID
            IS("$412"),
            4096,  # Chunk size (default)
            IS("$410"),
            0,  # Compression type (0 = none)
            IS("$411"),
            0,  # DRM scheme (0 = none)
            IS("$587"),
            "",  # Application version
            IS("$588"),
            "",  # Package version
            IS("$161"),
            CONTAINER_FORMAT_KFX_MAIN,  # Container format
            IS("version"),
            2,  # Version
        )

        return YJFragment(ftype=IS("$270"), value=value)

    def build_fragment_419(self, container_id, entity_names):
        """
        Builds Fragment $419 (Entity Index / Container Map)

        Args:
            container_id: String (e.g. "CR!...")
            entity_names: List of local symbol names (e.g. ["content_1", "s0", "l0", "c0"])
                         These are looked up via the symbol table to get proper IDs.
        """
        # Use IS() with plain names so get_id does proper symbol table lookup
        entity_symbols = [IS(name) for name in entity_names]

        value = IonStruct(
            IS("$252"),
            [IonStruct(IS("$155"), container_id, IS("$181"), entity_symbols)],
            IS("$253"),
            [],
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$419"), value=value)

    def build_fragment_145(self, text_strings, content_name):
        """
        Builds Fragment $145 (Content Strings)

        Working KFX files use a unique local symbol as the fid (e.g., "content_1"),
        NOT the shared $348 entity ID. The $145 fragment's name field ($4) must
        match the fid, and $259 entries must reference this same name.

        Args:
            text_strings: List of strings (paragraphs/text chunks)
            content_name: Local symbol name for the fid and $4 field (e.g., "content_1")

        Returns:
            YJFragment with type $145
        """
        self.symtab.create_local_symbol(content_name)

        value = IonStruct(IS("$4"), IS(content_name), IS("$146"), text_strings)

        # Use content_name as fid for a unique entity ID (NOT shared $348)
        # This matches working KFX files where $145 has fid=content_1
        return YJFragment(fid=IS(content_name), ftype=IS("$145"), value=value)

    def build_fragment_157(
        self,
        entity_name,
        font_size=1.0,
        line_height=1.0,
        underline=False,
        strike=False,
        bold=False,
        margin_top=None,
        is_heading=False,
        italic=False,
        upright=False,
        align=None,
        text_indent=None,
        margin_left=None,
        margin_right=None,
        font_family=None,
        baseline_style=None,
        no_align=False,
        space_above=None,
        space_below=None,
        explicit_size=False,
        drop_cap=None,
        small_caps=None,
        text_transform=None,
        letter_spacing=None,
        colour=None,
        clear=None,
    ):
        """
        Builds Fragment $157 (Style Definition).

        Creates a text style with proper font-size, line-height, text-align,
        and display type matching the structure of known-good KFX files.

        Args:
            entity_name: Local symbol name for this fragment (e.g., "s0", "s1").
                        Auto-generated if None.
            font_size: Font size in rem units (default 1.0). When 1.0, font-size
                      is omitted to let Kindle use its default (matching reference KFX).
            line_height: Line height in lh units (default 1.0, matching reference KFX)
            underline: If True, add text-decoration: underline ($23: $328)
            strike: If True, add text-decoration: line-through ($27: $328),
                as Kindle Previewer 4 writes it (#237)
            bold: If True, set font-weight: bold ($13: $361)
            italic: If True, add font-style: italic ($12: $382)
            upright: If True and not italic, write font-style: normal
                ($12: $350), for a run set upright inside an italic
                paragraph; without it the run takes the paragraph's italic
                (#367 review)
            is_heading: If True, omit the body top margin ($47); headings space
                        via $46 (margin_top param) instead.
            align: Text alignment override. If a key in ALIGN_MAP ("left", "right", "center"),
                  use the mapped symbol; otherwise default to "justify" ($321).
            text_indent: If a tuple (magnitude_str, unit_symbol), set text-indent ($36)
                        and suppress the body top margin ($47). Default None uses
                        0% indent and the normal 1lh top margin.
            margin_left: If a tuple (magnitude_str, unit_symbol), override the default
                        margin-left ($48). Default None uses 0.5% ($314).
            margin_right: If a tuple (magnitude_str, unit_symbol), emit margin-right ($50).
                         Default None omits $50.
            space_above: Space above the paragraph in ems, from the book's
                        margins (#230). Written as $47 in place of the 1lh
                        default, indented or not; 0 writes none (#331).
            space_below: Space below it in ems, $49, when the paragraph after
                        has no margin-top of its own (#553).
            baseline_style: Symbol emitted as $44 — `$370` raises
                        (superscript), `$371` lowers (subscript). Reference
                        noteref styles pair it with a reduced $16 font-size.
                        Default None omits $44. Replaced the numeric $31 shift
                        in #123. (#52)

        Returns:
            YJFragment with type $157
        """
        self.symtab.create_local_symbol(entity_name)

        # $13 = font-weight: $350=normal, $361=bold
        font_weight = IS("$361") if bold else IS("$350")

        # $34 = text-align; default justify ($321), overridden per element.
        text_align = IS(ALIGN_MAP[align]) if align in ALIGN_MAP else IS("$321")

        # $36 = text-indent; default 0%, overridden per element.
        if text_indent is not None:
            indent_struct = IonStruct(
                IS("$307"),
                IonDecimal(text_indent[0]),
                IS("$306"),
                IS(text_indent[1]),
            )
        else:
            indent_struct = IonStruct(
                IS("$307"), IonDecimal("0"), IS("$306"), IS("$314")
            )

        # $48 = margin-left; default 0.5%, overridden per element.
        if margin_left is not None:
            margin_left_struct = IonStruct(
                IS("$307"),
                IonDecimal(margin_left[0]),
                IS("$306"),
                IS(margin_left[1]),
            )
        else:
            margin_left_struct = IonStruct(
                IS("$307"), IonDecimal("0.5"), IS("$306"), IS("$314")
            )

        value = IonStruct(
            IS("$48"),
            margin_left_struct,
            IS("$34"),
            text_align,  # text-align: justify ($320=center, $321=justify, $59=left, $61=right)
            IS("$36"),
            indent_struct,  # text-indent
            IS("$42"),
            IonStruct(
                IS("$307"), IonDecimal(str(line_height)), IS("$306"), IS("$310")
            ),  # line-height (lh)
            IS("$173"),
            IS(entity_name),  # self-reference
            IS("$13"),
            font_weight,  # font-weight
        )

        # $47 = margin-top (authoritative per jhowell's catalog; earlier comments
        # mislabeled it "padding-top"). Body text gets a 1lh top margin for
        # inter-paragraph spacing; headings get their spacing from $46 (margin_top
        # param). A non-zero first-line indent replaces inter-paragraph spacing
        # (print convention), so suppress this top margin when indented.
        if space_below:
            value[IS("$49")] = IonStruct(
                IS("$307"),
                IonDecimal(f"{space_below / _LINE_EM / line_height:.6g}"),
                IS("$306"),
                IS("$310"),
            )
        if space_above is not None:
            # 0: a line of verse set tight under the one before (#331).
            if space_above:
                value[IS("$47")] = IonStruct(
                    IS("$307"),
                    IonDecimal(f"{space_above / _LINE_EM / line_height:.6g}"),
                    IS("$306"),
                    IS("$310"),
                )
        elif not is_heading and (
            # A first-line indent replaces the space; a hanging one doesn't
            # (#465).
            text_indent is None or float(text_indent[0]) < 0
        ):
            # margin-top: a line of the main text, in this paragraph's own
            # line heights, as Previewer measures it (#463).
            value[IS("$47")] = IonStruct(
                IS("$307"), IonDecimal(f"{1 / line_height:.6g}"), IS("$306"), IS("$310")
            )

        # Only set font-size when non-default — lets Kindle use its default for normal text
        # Reference KFX files omit font-size on most styles and only set it for headings/small text
        # `explicit_size`: a run back at the main size inside a sized
        # paragraph says 1, or it takes the paragraph's size (#241).
        if font_size != 1.0 or explicit_size:
            value[IS("$16")] = IonStruct(
                IS("$307"), IonDecimal(str(font_size)), IS("$306"), IS("$505")
            )  # rem

        # A drop cap: the lines its initial drops through ($125) and the
        # initial's characters ($126), as Kindle Previewer writes them (#240).
        if drop_cap:
            value[IS("$125")], value[IS("$126")] = drop_cap

        _write_typography(value, small_caps, text_transform, letter_spacing, colour)
        # The paragraph starts below the floats on that side (#381).
        _write_clear(value, clear)

        # $23: $328 = text-decoration: underline
        if underline:
            value[IS("$23")] = IS("$328")
        # $27: $328 = text-decoration: line-through (#237)
        if strike:
            value[IS("$27")] = IS("$328")

        # $12 = font-style: $382 = italic (authoritative, jhowell kfxlib)
        if italic:
            value[IS("$12")] = IS("$382")
        elif upright:
            value[IS("$12")] = IS("$350")

        # $11 = font-family (#15). Set only when an embedded face was matched;
        # its absence preserves byte-identical output for non-font books.
        if font_family:
            value[IS("$11")] = font_family

        # $46 = margin-top (for visual separation before chapter headings)
        if margin_top is not None:
            value[IS("$46")] = IonStruct(
                IS("$307"), IonDecimal(str(margin_top)), IS("$306"), IS("$310")
            )

        # $50 = margin-right; emitted only when the source specifies it.
        if margin_right is not None:
            value[IS("$50")] = IonStruct(
                IS("$307"), IonDecimal(margin_right[0]), IS("$306"), IS(margin_right[1])
            )

        # $44 = baseline style, the semantic enum: $370 raises, $371 lowers.
        # Paired with a reduced $16, this is the shape Amazon emits for noteref
        # runs — exclusively, across four books checked against Previewer 3.106.
        #
        # Was $31, a numeric percentage shift. Both render correctly (#67 for
        # the numeric form, #123 for this one on a Paperwhite / 5.19.2); the
        # enum wins because the device computes the offset from its own font
        # metrics instead of trusting a percentage measured on two devices.
        if baseline_style is not None:
            value[IS("$44")] = IS(baseline_style)

        if no_align:
            # A run inside a table cell: a span can't carry text-align, and
            # KFX Input flags one that differs from the cell's (#281).
            del value[IS("$34")]

        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_table_style_157(self, entity_name, border=None, width=None, clear=None):
        """$157 for a native table node, as Kindle Previewer writes it (#219),
        with the table's own border (#264) and clear (#381)."""
        self.symtab.create_local_symbol(entity_name)
        value = IonStruct(
            IS("$16"), IonStruct(IS("$307"), IonDecimal("1"), IS("$306"), IS("$505")),
            IS("$65"), IonStruct(IS("$307"), IonDecimal("100"), IS("$306"), IS("$314")),
            IS("$42"), IonStruct(IS("$307"), IonDecimal("1"), IS("$306"), IS("$310")),
            IS("$173"), IS(entity_name),
            IS("$83"), 4286611584,
        )  # fmt: skip
        _apply_border(value, border, is_table=True)
        _write_clear(value, clear)
        if width is not None:
            # A percentage table width, as Previewer writes it (#264): a
            # min-width, and at 100% the width too.
            pct = IonStruct(
                IS("$307"), IonDecimal(f"{width:g}"), IS("$306"), IS("$314")
            )
            if width == 100:
                value[IS("$56")] = pct
            value[IS("$63")] = pct
            value[IS("$546")] = IS("$377")
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_box_style_157(
        self,
        entity_name,
        border=None,
        background=None,
        padding=None,
        margin_left=None,
        margin_right=None,
        space_above=None,
        space_below=None,
        width=None,
        float_side=None,
        centred=False,
        clear=None,
    ):
        """$157 for a box (#238), as Kindle Previewer 4.0.1 writes a bordered
        or filled container: border as for a table part, fill $70 (ARGB),
        padding as for a cell, side margins $48/$50 as a share of the page,
        and space above and below ($47/$49) in line heights, em / 1.2. A
        wrapper's width ($56, a share of the page), float ($140) and centring
        ($580) as Previewer writes a sized wrapper around a picture (#382)."""
        self.symtab.create_local_symbol(entity_name)
        value = IonStruct(IS("$173"), IS(entity_name))
        _apply_border(value, dict(border) if border else None, is_table=False)
        if background is not None:
            value[IS("$70")] = background
        if padding:
            for k, v in _padding_values(dict(padding), None).items():
                value[IS(k)] = v
        for key, pct in (("$48", margin_left), ("$50", margin_right)):
            if pct:
                value[IS(key)] = IonStruct(
                    IS("$307"), IonDecimal(f"{pct:.6g}"), IS("$306"), IS("$314")
                )
        for key, em in (("$47", space_above), ("$49", space_below)):
            if em:
                value[IS(key)] = IonStruct(
                    IS("$307"), IonDecimal(f"{em / 1.2:.6g}"), IS("$306"), IS("$310")
                )
        if width:
            value[IS("$56")] = IonStruct(
                IS("$307"), IonDecimal(f"{width:.6g}"), IS("$306"), IS("$314")
            )
        if float_side:
            value[IS("$140")] = IS("$59" if float_side == "left" else "$61")
        if centred:
            value[IS("$580")] = IS("$320")
        # A box or rule set to clear floats (#381).
        _write_clear(value, clear)
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_part_style_157(self, entity_name, border=None):
        """$157 for a table row or row group that draws its own border under
        border-collapse, as Kindle Previewer writes it (#264)."""
        self.symtab.create_local_symbol(entity_name)
        value = IonStruct(IS("$173"), IS(entity_name))
        _apply_border(value, border, is_table=False)
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_cell_style_157(
        self,
        entity_name,
        align=None,
        bold=False,
        italic=False,
        colspan=1,
        rowspan=1,
        font_family=None,
        font_size=1.0,
        valign="middle",
        border=None,
        border_box=False,
        padding=None,
        table_width=None,
        small_caps=None,
        text_transform=None,
        letter_spacing=None,
        colour=None,
        background=None,
    ):
        """$157 for a table cell: Previewer's padding, plus the cell's own
        alignment, header weight and spans (#219), its vertical alignment
        (#269; none for a value outside VALIGN_MAP, as Previewer writes it),
        and the chapter's font size, omitted at 1.0 as build_fragment_157
        does."""
        self.symtab.create_local_symbol(entity_name)
        lh = lambda v: IonStruct(IS("$307"), IonDecimal(v), IS("$306"), IS("$310"))  # noqa: E731
        pct = lambda v: IonStruct(IS("$307"), IonDecimal(v), IS("$306"), IS("$314"))  # noqa: E731
        value = IonStruct(
            IS("$52"), lh("0.03125"), IS("$53"), pct("0.117"),
            IS("$54"), lh("0.03125"), IS("$55"), pct("0.117"),
            IS("$173"), IS(entity_name),
        )  # fmt: skip
        if valign in VALIGN_MAP:
            # First, where it has always been written, so a middle cell's
            # bytes don't change.
            value[IS("$633")] = IS(VALIGN_MAP[valign])
            value.move_to_end(IS("$633"), last=False)
        if align in ALIGN_MAP:
            value[IS("$34")] = IS(ALIGN_MAP[align])
        if bold:
            value[IS("$13")] = IS("$361")
        if italic:
            value[IS("$12")] = IS("$382")
        _write_typography(value, small_caps, text_transform, letter_spacing, colour)
        if colspan > 1:
            value[IS("$148")] = colspan
        if rowspan > 1:
            value[IS("$149")] = rowspan
        if font_family:
            value[IS("$11")] = font_family
        if font_size != 1.0:
            value[IS("$16")] = IonStruct(
                IS("$307"), IonDecimal(str(font_size)), IS("$306"), IS("$505")
            )  # rem
        _apply_border(value, border, is_table=False)
        if padding:
            # The cell's own padding replaces Previewer's 1px default (#264).
            for key in ("$52", "$53", "$54", "$55"):
                value.pop(IS(key), None)
            for key, v in _padding_values(dict(padding), table_width).items():
                value[IS(key)] = v
        if border_box:
            # A cell that sets its own width, as Previewer writes it (#264).
            value[IS("$546")] = IS("$377")
        if background is not None:
            # Its fill, as Previewer writes it on the cell (#502).
            value[IS("$70")] = background
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_fragment_157_image(
        self,
        entity_name,
        width_pct=None,
        height_pct=None,
        float_side=None,
        clear=None,
    ):
        """Builds a $157 style for a picture entry: its own width against the
        text column, as Amazon sizes it (#145), at any size (#462);
        `height_pct` sizes it by height ($57) instead, Amazon's shape for
        `height="98%"` and `height: 100%`.
        """
        self.symtab.create_local_symbol(entity_name)
        # Width from the image's own pixel width against the text column,
        # capped at full width — what Amazon does. (#145)
        # A height-sized image swaps $56 for $57 and keeps the max-width
        # guard, so a tall page image still cannot overflow the column.
        axis, pct = (
            (IS("$57"), height_pct)
            if height_pct is not None
            else (IS("$56"), width_pct)
        )
        value = IonStruct(
            axis,
            IonStruct(IS("$307"), IonDecimal(str(pct)), IS("$306"), IS("$314")),
            IS("$65"),
            IonStruct(IS("$307"), IonDecimal("100"), IS("$306"), IS("$314")),
            IS("$785"),
            IonStruct(IS("$131"), 2, IS("$132"), 2),
            IS("$546"),
            IS("$377"),
            IS("$42"),
            IonStruct(IS("$307"), IonDecimal("1"), IS("$306"), IS("$310")),
            IS("$580"),
            IS("$320"),
            IS("$173"),
            IS(entity_name),
        )
        if float_side:
            # Floated: the text after it wraps beside it, and it isn't
            # centred, as Kindle Previewer writes it (#243).
            value.pop(IS("$580"), None)
            value[IS("$140")] = IS("$59" if float_side == "left" else "$61")
        # A picture set to clear floats (#381).
        _write_clear(value, clear)
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_fragment_266_uri(self, anchor_name, uri):
        """A $266 anchor for a link leaving the book: its name ($180) and the
        address ($186), as Kindle Previewer 4.0.3 writes http, https and
        mailto links (#474)."""
        self.symtab.create_local_symbol(anchor_name)
        return YJFragment(
            fid=IS(anchor_name),
            ftype=IS("$266"),
            value=IonStruct(IS("$180"), IS(anchor_name), IS("$186"), uri),
        )

    def build_fragment_266(self, anchor_name, position_id, offset=0):
        """
        Builds Fragment $266 (Anchor / Bookmark)

        Creates an internal anchor that maps a name to a position in the book.
        Used by $179 link references in $259 entries to create clickable links.

        Args:
            anchor_name: Local symbol name for this anchor (e.g., "toc_anchor_0")
            position_id: Target position ID (from $259 content positions)
            offset: Character offset within that position's paragraph. Emitted
                    as $143; omitted when 0.

        Returns:
            YJFragment with type $266

        $180 is the anchor's own name and is what a link span's $179 resolves
        against — the fragment id alone is not enough. Every $266 in a Calibre
        KFX Output build and in Amazon-produced KFX is shaped ($180, $183) or
        ($180, $186). Omitting $180 left every link dangling, so Kindle
        rendered linked runs as plain text. (#51)

        $143 inside $183 is the character offset within the target paragraph,
        the same meaning it carries in a $142 link span. A position alone names
        only the paragraph, so a note's return link landed on its first line
        while the marker sat at the end — measured at up to 1.2 pages away on a
        real book, and confirmed on device. Amazon carries $143 on roughly half
        its anchors (321/676, 970/1299, 488/1015 across three reference files),
        and every one falls within its target paragraph's length. Anchors
        pointing at a paragraph start omit it, which is also what keeps books
        with no mid-paragraph markers byte-identical. (#79)
        """
        self.symtab.create_local_symbol(anchor_name)
        target = IonStruct(IS("$155"), position_id)
        if offset:
            target[IS("$143")] = offset
        value = IonStruct(
            IS("$180"),
            IS(anchor_name),
            IS("$183"),
            target,
        )
        return YJFragment(fid=IS(anchor_name), ftype=IS("$266"), value=value)

    def build_fragment_259(
        self,
        story_names,
        content_refs,
        entity_name,
        positions=None,
        link_targets=None,
        link_styles=None,
        link_text_lengths=None,
        outer_position=None,
        outer_style=None,
        chunk_kinds=None,
        image_specs=None,
        emphasis_spans=None,
        container_nodes=None,
        container_extras=None,
        maths=None,
    ):
        """
        Builds Fragment $259 (Storyline / Flow Map)

        Emits a FLAT $146 list — one entry per chunk, directly under the
        storyline:

            $259.value.$146 = [
              { $155: pos[0], $157: story[0], $790: 1, ..., $145: ... },
              { $155: pos[1], $157: story[1],         ..., $145: ... },
              ...
            ]

        The one exception is a native table (#219): an `open` kind starts a
        container ($278 table → $151/$454/$455 row group → $279 row) whose
        $146 holds the entries up to its matching `close`, so cells nest
        under their row. `close` entries take no position. `$790` goes on the
        first leaf entry, never on a container.

        (A nested single-outer-wrapper shape was tried during the Phase-3 work
        but reverted; the flat shape is what ships and is device-verified. The
        `outer_position` / `outer_style` params are retained for compatibility
        but are not emitted in the flat shape.)

        Args:
            story_names: List of $157 fragment local symbol names per chunk.
            content_refs: One (content_fragment_name, index_into_$146) per
                        entry, or None for entries that hold no text (image
                        entries). A chapter's text may span several $145
                        fragments once it exceeds the per-fragment byte cap,
                        so the reference is per entry rather than one
                        fragment name plus a running index (#37).
            entity_name: Local symbol name for this fragment (e.g., "l0").
                        Auto-generated if None.
            positions: List of position IDs, one per story_name (children).
            link_targets: Optional list of $266 anchor names (or None) per child.
                         When set, the entry gets a $142 character-span marking
                         text [0:link_text_lengths[i]] as a hyperlink to the
                         anchor. Reference uses $142 spans (not entry-level
                         $179) — Kindle treats $142 as inline hyperlinks but
                         entry-level $179 as a non-tappable structural ref.
            link_styles: Optional list of $157 style names (or None) per child,
                        used inside the $142 span as the link's visual style
                        (typically underlined). Falls back to the entry's
                        story_name when None.
            link_text_lengths: Optional list of int (or None) per child giving
                              the character length to mark as link. Defaults
                              to a large value when None (covers the whole
                              paragraph).
            outer_position: Position ID for the outer wrapping entry. Required
                           when story_names is non-empty.
            outer_style: $157 style name for the outer entry. Defaults to the
                         first story_name when None.

        Returns:
            YJFragment with type $259
        """
        self.symtab.create_local_symbol(entity_name)
        for ref in content_refs or ():
            if ref:
                self.symtab.create_local_symbol(ref[0])

        root = []
        stack = [root]
        first_leaf = True
        for i, story_name in enumerate(story_names):
            kind = chunk_kinds[i] if chunk_kinds and i < len(chunk_kinds) else "text"
            if kind == "close":
                stack.pop()
                continue
            position = positions[i] if positions and i < len(positions) else 1000 + i
            if kind == "open":
                node = container_nodes[i]
                entry = IonStruct(
                    IS("$155"), position, IS("$159"), IS(_TABLE_NODE_TYPES[node])
                )
                if node in ("table", "zoom-table"):
                    self.symtab.create_local_symbol(story_name)
                    entry[IS("$157")] = IS(story_name)
                    entry[IS("$150")] = False
                    entry[IS("$456")] = IonStruct(
                        IS("$307"), IonDecimal("0.9"), IS("$306"), IS("$318")
                    )
                    entry[IS("$457")] = IonStruct(
                        IS("$307"), IonDecimal("0.9"), IS("$306"), IS("$318")
                    )
                    # Table viewer (the zoom button), on every table of 2+
                    # columns (#272). Without it the Voyage and the Oasis
                    # squeeze wide columns until words break a character
                    # per line (#254), and the Voyage stacks the cells of
                    # a plain 2-3 column table (#272).
                    extras = (
                        container_extras[i]
                        if container_extras and i < len(container_extras)
                        else None
                    )
                    if extras:
                        for k, v in extras.items():
                            entry[IS(k)] = v
                    if node == "zoom-table":
                        entry[IS("$629")] = [IS("$581"), IS("$326")]
                        entry[IS("$630")] = IS("$632")
                        self._wrote_zoom_table = True
                elif node == "cell":
                    self.symtab.create_local_symbol(story_name)
                    entry[IS("$157")] = IS(story_name)
                elif (
                    container_extras
                    and i < len(container_extras)
                    and container_extras[i]
                ):
                    for k, v in container_extras[i].items():
                        entry[IS(k)] = v
                entry[IS("$146")] = []
                stack[-1].append(entry)
                stack.append(entry[IS("$146")])
                continue

            if kind == "rule":
                # A horizontal rule (#230): no text, one position, its
                # spacing in its style, as Kindle Previewer writes <hr>.
                self.symtab.create_local_symbol(story_name)
                entry = IonStruct(
                    IS("$155"),
                    position,
                    IS("$157"),
                    IS(story_name),
                    IS("$159"),
                    IS("$596"),
                )
                if first_leaf:
                    entry[IS("$790")] = 1
                    first_leaf = False
                stack[-1].append(entry)
                continue

            if (
                kind == "image"
                and image_specs
                and i < len(image_specs)
                and image_specs[i]
            ):
                spec = image_specs[i]
                resource_name = spec.get("resource")
                alt = spec.get("alt", "")
                if resource_name:
                    self.symtab.create_local_symbol(resource_name)
                entry = IonStruct(
                    IS("$155"),
                    position,
                    IS("$157"),
                    IS(story_name),
                    IS("$159"),
                    IS("$271"),
                    IS("$584"),
                    alt,
                )
                if story_name is None:
                    # A picture in a table cell: no style, so it shows at its
                    # own size within the cell (#262).
                    del entry[IS("$157")]
                if resource_name:
                    entry[IS("$175")] = IS(resource_name)
                if spec.get("inline"):
                    # In a link's container: an inline picture, the one
                    # position its link covers (#355).
                    entry[IS("$601")] = IS("$283")
                # Image entries hold no text, so they carry no content
                # reference — their slot in content_refs is None.
                if first_leaf:
                    entry[IS("$790")] = 1
                    first_leaf = False
                stack[-1].append(entry)
                continue

            # Text entry
            entry = IonStruct(
                IS("$155"), position, IS("$157"), IS(story_name), IS("$159"), IS("$269")
            )
            if content_refs[i] is not None:
                ref_name, ref_index = content_refs[i]
                entry[IS("$145")] = IonStruct(
                    IS("$4"), IS(ref_name), IS("$403"), ref_index
                )
            if first_leaf:
                entry[IS("$790")] = 1
                first_leaf = False
            if position in self._popup_positions:
                entry[IS("$615")] = IS("$281")
            if maths and position in maths:
                # Text and drawn equations together, as Previewer writes a
                # paragraph holding an equation (#235).
                text, items = maths[position]
                mixed, last = [], 0
                for k, item, eids in items:
                    if text[last:k]:
                        mixed.append(text[last:k])
                    mixed.append(
                        self._inline_picture_entry(item, eids)
                        if item[0] == "picture"
                        else self._math_entry(item, eids)
                    )
                    last = k + 1
                if text[last:]:
                    mixed.append(text[last:])
                entry.pop(IS("$145"), None)
                entry[IS("$146")] = mixed

            if link_targets and i < len(link_targets) and link_targets[i]:
                anchor_name = link_targets[i]
                self.symtab.create_local_symbol(anchor_name)
                # Emit $142 character span (reference's mechanism for inline
                # hyperlinks). Entry-level $179 is non-tappable — span-level
                # $179 inside $142 makes the marked text region a tappable
                # hyperlink.
                span_length = (
                    link_text_lengths[i]
                    if link_text_lengths
                    and i < len(link_text_lengths)
                    and link_text_lengths[i] is not None
                    else 1000
                )
                span_style = (
                    link_styles[i]
                    if link_styles and i < len(link_styles) and link_styles[i]
                    else story_name
                )
                self.symtab.create_local_symbol(span_style)
                span = IonStruct(
                    IS("$143"),
                    0,
                    IS("$144"),
                    span_length,
                    IS("$179"),
                    IS(anchor_name),
                    IS("$157"),
                    IS(span_style),
                )
                entry[IS("$142")] = [span]

            if emphasis_spans and i < len(emphasis_spans) and emphasis_spans[i]:
                existing = entry.get(IS("$142"), [])
                for span_spec in emphasis_spans[i]:
                    # (start, length, style_name) for emphasis-only runs;
                    # (start, length, style_name|None, anchor|None) once a run
                    # can also be a link (#53). Either arity is accepted.
                    # A fifth member is a ruby: (its chapter's ruby fragment,
                    # its number there) (#236).
                    start, length, style_name = span_spec[:3]
                    anchor_name = span_spec[3] if len(span_spec) > 3 else None
                    ruby = span_spec[4] if len(span_spec) > 4 else None
                    if style_name is None and anchor_name is None and ruby is None:
                        continue
                    span = IonStruct(IS("$143"), start, IS("$144"), length)
                    if anchor_name:
                        self.symtab.create_local_symbol(anchor_name)
                        if anchor_name in self._popup_anchors:
                            span[IS("$616")] = IS("$617")
                        span[IS("$179")] = IS(anchor_name)
                    if style_name:
                        self.symtab.create_local_symbol(style_name)
                        span[IS("$157")] = IS(style_name)
                    if ruby:
                        self.symtab.create_local_symbol(ruby[0])
                        span[IS("$757")] = IS(ruby[0])
                        span[IS("$758")] = ruby[1]
                    existing.append(span)
                entry[IS("$142")] = existing

            stack[-1].append(entry)

        # Flat: one entry per chunk, except a native table (#219), which
        # nests its row groups, rows and cells under a $278 entry.
        value = IonStruct(
            IS("$176"),
            IS(entity_name),
            IS("$146"),
            root,
        )

        return YJFragment(fid=IS(entity_name), ftype=IS("$259"), value=value)

    def build_fragment_260(
        self,
        storyline_name,
        section_name,
        field_155_value,
    ):
        """
        Builds Fragment $260 (Section)

        CRITICAL: $260.fid MUST equal $260.$174.

        All sections use inline display ($269) and no page break of their
        own (see below). Block display ($270) with fixed dimensions causes
        text overlap and is only appropriate for cover image sections.

        Args:
            storyline_name: Local symbol name of the $259 fragment (e.g., "l0")
            section_name: Local symbol name for BOTH fid AND $174 (e.g., "c0").
            field_155_value: Position id for Field $155.

        Returns:
            (YJFragment, section_name): Fragment and its fid/name for use in $538
        """
        self.symtab.create_local_symbol(section_name)
        self.symtab.create_local_symbol(storyline_name)

        # All sections use inline display ($269) without explicit page-break.
        # The $790: 1 flag on heading $259 entries handles chapter breaks.
        # Explicit $156: $326 page-break causes TOC navigation to land one
        # page late (preview shows correct page but tap goes to next page).
        entry = IonStruct(
            IS("$155"),
            field_155_value,
            IS("$176"),
            IS(storyline_name),
            IS("$159"),
            IS("$269"),
        )

        value = IonStruct(IS("$174"), IS(section_name), IS("$141"), [entry])

        return YJFragment(
            fid=IS(section_name), ftype=IS("$260"), value=value
        ), section_name

    def generate_full_book(
        self,
        title,
        author,
        chapters,
        asin=None,
        output_path=None,
        cover_image=None,
        language="en",
        publisher="kfxgen",
        issue_date=None,
        authors=None,
        description=None,
        images=None,
        font_table=None,
        resolved_image_refs=False,
        display_dims=None,
        footnote_keys=None,
        landmarks=None,
        page_list=None,
        approximate_pages=False,
    ):
        """
        Generates a complete KFX book with metadata and content.

        Creates per-chapter sections matching known-good KFX structure:
        each chapter gets its own $259 storyline and $260 section.

        Args:
            title: Book title
            author: Book author
            chapters: Required list of chapter dicts with 'title' and 'text'
                keys (text may contain inline image tokens emitted by
                converter.extract_text_from_html). Optionally each chapter
                may carry 'font_size', 'toc_links', and 'preserved_images'
                (image tokens rescued from a replaced contents section,
                which are read from their own key because the 'toc_links'
                path ignores 'text' entirely — #117). Raises ValueError
                if empty or None.
            asin: Optional ASIN (auto-generated if None)
            output_path: Optional path to write KFX file
            cover_image: Optional bytes of cover image (JPEG or PNG)
            language: The book's language code, as Kindle Previewer writes
                it: "en", "en-GB", "haw" (default 'en'; #453)
            publisher: Publisher string for $490 metadata
            issue_date: Optional ISO date string
            images: Optional dict of {href: raw bytes} for body images.
                Each gets a $164 manifest + $417 blob pair; <img> tokens
                in chapter text resolve against this dict by full href,
                then by percent-decoded href, then by basename.
            resolved_image_refs: True when the caller resolved every
                <img src> against its document, as converter does. A
                resolved href that matches no image then shows nothing,
                instead of falling back to another image that happens to
                share its filename (#195). Hrefs that could not be resolved
                (a `..` segment, an absolute path) still fall back.
            display_dims: Optional {href: (width, height)}: the size to show
                an image at when it is not its pixel size, as for an SVG
                drawn at twice its size (#440). Only the page size uses it.
            footnote_keys: Optional set of anchor keys that name a footnote:
                a noteref link to one is written as Previewer writes a pop-up
                note (#445).

        Returns:
            bytes: Serialized KFX data
        """
        # Once per book, before anything content-dependent: a retired override
        # must be reported even for a book with no raised text (#123).
        _warn_retired_shift_env()

        # Container ID and ASIN are derived deterministically from book
        # identity rather than randomly generated (#89). Two consecutive
        # conversions of the same book now produce byte-identical output,
        # which:
        #   1. Lets `tier3_strict` golden-file tests verify bit-level
        #      stability of the generator.
        #   2. Causes re-conversions of the same EPUB to overwrite (rather
        #      than duplicate) on the Kindle library, which is the
        #      expected UX for "I tweaked the plugin and re-converted."
        # SHA-256 hex digest is in [0-9a-f]; uppercased it becomes
        # [0-9A-F], all alphanumeric, matching the format the random
        # versions produced. The seed includes (title, author, language,
        # publisher, issue_date, len(chapters)) — enough to distinguish
        # most books in practice. Known limitation: two distinct editions
        # of the same book with the same metadata and same chapter count
        # but different per-chapter content will collide. Callers that
        # need stronger uniqueness should pass an explicit `asin`.
        _id_seed = "\x00".join(
            [
                str(title),
                str(author),
                str(language),
                str(publisher),
                str(issue_date or ""),
                str(len(chapters)),
            ]
        ).encode("utf-8")
        _id_digest = hashlib.sha256(_id_seed).hexdigest().upper()

        # Container ID: "CR!" prefix + 28-char alphanumeric tail. Reference
        # Calibre KFX uses this exact shape.
        container_id = "CR!" + _id_digest[:28]

        if asin is None:
            # 32-char uppercase alphanumeric (no prefix). Reference Calibre
            # KFX uses this format (e.g. 'NRHEO70SKAG5UVR2SSWENJ42365REDE2').
            # The previous `ASIN_<10>` short prefix format inhibited Kindle
            # home-screen thumbnail extraction (#39, verified on Paperwhite).
            asin = _id_digest[:32]

        # Reset state for clean generation
        self._resolved_image_refs = resolved_image_refs
        self.fragments = []
        self.symtab = StandardSymbolTable()
        self.entity_ids = {}
        self.next_entity_id = 349
        self.field_403_counter = 10
        self._wrote_zoom_table = False

        from .font_table import FontTable  # noqa: PLC0415

        self.font_table = font_table if font_table is not None else FontTable([])

        # 1. Build metadata fragments
        self.fragments.append(
            self.build_fragment_585(table_version=_table_feature_version(chapters))
        )

        # Detect cover image format and build resource fragments
        # $164 (metadata) and $417 (raw data) MUST have different fids, linked by $165
        # $165 must be a plain STRING (not IonSymbol) — Kindle requires this
        cover_resource_name = None
        cover_location_name = None
        cover_dims = None
        if cover_image:
            cover_resource_name = "cover_img"
            cover_location_name = "resource/cover_img"
            if cover_image[:3] == b"\xff\xd8\xff":
                img_format = "jpeg"
            elif cover_image[:4] == b"\x89PNG":
                img_format = "png"
            else:
                # Defense in depth: extract_cover_image validates magic bytes
                # before this code is reached (#46). If we still arrive here
                # with garbage, refuse rather than mislabel as JPEG.
                _security_log.warning(
                    "rejected cover image with unrecognized magic bytes: %r",
                    cover_image[:8],
                )
                raise ValueError(
                    "cover_image bytes are neither JPEG nor PNG (magic %s)"
                    % cover_image[:4].hex()
                )

            # Detect image dimensions
            width, height = self._detect_image_dimensions(cover_image)
            cover_dims = (width, height)

            # Cover gets $162 MIME type — enables Kindle home-screen
            # thumbnail extraction (#39, verified on Paperwhite). Body
            # images stay without $162.
            self.fragments.append(
                self.build_fragment_164(
                    cover_resource_name,
                    cover_location_name,
                    img_format,
                    width=width,
                    height=height,
                    include_mime=True,
                )
            )
            self.fragments.append(
                self.build_fragment_417(cover_location_name, cover_image)
            )

        # Body images: emit one $164 + $417 pair per spine-referenced image.
        # image_resources maps href -> (resource_name, location_name) so the
        # img-rewriting step (Phase 4 step B/C) can resolve <img src="href"> to
        # the right $164 fid for $259 image entries.
        # image_resources is indexed by basename so <img src="../path/x.jpg">
        # in spine XHTML resolves against manifest href "OEBPS/path/x.jpg".
        def _img_basename(h):
            h = h.split("#", 1)[0]
            return h.rsplit("/", 1)[-1]

        image_resources = {}
        cover_hrefs = []  # body pictures that are the cover's (#475)
        self._image_dims = {}  # basename -> (width, height) for style picker
        self._footnote_keys = set(footnote_keys or ())
        self._popup_anchors, self._popup_positions = set(), set()
        if images:
            # Filter to recognized formats first, then enumerate so resource
            # names stay contiguous (img_0, img_1, ...) regardless of skips.
            # Unsupported formats are dropped silently here; the converter
            # is responsible for pre-filtering or logging.
            #
            # Sorted by href, so the numbering is a property of the book rather
            # than of however the caller's mapping happened to be ordered.
            # Calibre's `Manifest` is backed by a `set`, so `oeb_book.manifest`
            # yields a different order between processes; converting one book
            # twice through `ebook-convert` gave `img_1` to a different picture
            # each time and produced a different file. Nothing rendered wrongly
            # — every entry referenced whichever name held its image — but the
            # output was not reproducible on the path users actually use (#189).
            #
            # This is #96 one allocation over. That fixed the same drift in
            # `$157` style symbols, for the same reason, with the same remedy:
            # let the content decide the order, never the iteration.
            valid = []
            for href, data in sorted(images.items()):
                if not data:
                    continue
                if cover_resource_name and data == cover_image:
                    # The cover's picture used in the text: shown there from
                    # the cover's resource, not stored twice (#475).
                    cover_hrefs.append(href)
                    continue
                if data[:3] == b"\xff\xd8\xff":
                    fmt = "jpeg"
                elif data[:4] == b"\x89PNG":
                    fmt = "png"
                else:
                    continue
                valid.append((href, data, fmt))

            for idx, (href, data, fmt) in enumerate(valid):
                resource_name = f"img_{idx}"
                location_name = f"resource/img_{idx}"
                width, height = self._detect_image_dimensions(data)
                self.fragments.append(
                    self.build_fragment_164(
                        resource_name, location_name, fmt, width=width, height=height
                    )
                )
                # The resource states its real pixels; the page size comes
                # from the size to show it at, when the caller gives one: an
                # SVG drawn at twice its size (#440).
                width, height = (display_dims or {}).get(href) or (width, height)
                self.fragments.append(self.build_fragment_417(location_name, data))
                base = _img_basename(href)
                # Basename collision (e.g. images/foo.jpg + ext/foo.jpg): keep
                # the first entry so existing <img src> resolutions stay
                # stable. The collision is logged once below.
                if base not in image_resources:
                    image_resources[base] = (resource_name, location_name)
                    self._image_dims[base] = (width, height)
                else:
                    self._image_basename_collisions = getattr(
                        self, "_image_basename_collisions", []
                    ) + [base]
                # The full manifest href is the exact key: converter resolves
                # <img src> against its document, so a token's href matches it
                # directly, and two images sharing a filename stay two images.
                # Set after the basename entry so a root-level href that equals
                # another image's basename still finds its own resource.
                image_resources[href] = (resource_name, location_name)
                self._image_dims[href] = (width, height)
                # Calibre keeps some manifest hrefs percent-encoded (spaces)
                # and others literal (non-ASCII), while markup may spell the
                # same name either way. The decoded form is a second exact
                # key, so both spellings meet. (#195)
                image_resources.setdefault(
                    unquote(href), (resource_name, location_name)
                )

        # Embedded fonts (#15): one $418 (bytes) + one $262 (@font-face) per
        # face, mirroring the image $417/$164 pair. Application (setting $11 on
        # $157 styles) happens later via self.font_table.match().
        for face in self.font_table.faces:
            self.fragments.append(self.build_fragment_418(face.location, face.data))
            self.fragments.append(
                self.build_fragment_262(
                    face.emitted_family, face.location, face.weight, face.italic
                )
            )

        for href in cover_hrefs:
            dims = (display_dims or {}).get(href) or cover_dims
            for key in (_img_basename(href), href, unquote(href)):
                if key not in image_resources:
                    image_resources[key] = (cover_resource_name, cover_location_name)
                    self._image_dims[key] = dims

        # Cover-in-reading-flow (#32): make the cover image visible as the
        # first reading page by registering its resource under a synthetic
        # basename and prepending a cover chapter. The cover chapter has no
        # heading text, no TOC entry, and a single image entry referencing
        # the cover resource via the standard image-token mechanism.
        _cover_basename = "__kfxgen_cover__"
        if cover_resource_name and chapters:
            image_resources[_cover_basename] = (
                cover_resource_name,
                cover_location_name,
            )
            if cover_dims:
                self._image_dims[_cover_basename] = cover_dims

        # Expose to _build_chapter_content for img-token rewriting (Phase 4b).
        self.image_resources = image_resources

        self.fragments.append(
            self.build_fragment_490(
                title,
                author,
                asin,
                container_id,
                cover_image=cover_resource_name,
                language=language,
                issue_date=issue_date,
                publisher=publisher,
                authors=authors,
                description=description,
            )
        )

        # 2. Build content fragments ($145, $157, $259). The legacy
        # `content_text=`/`toc_entries=` API was removed in 5.3.1 — callers
        # must pass structured `chapters` (the production converter has
        # always done so via extract_chapters_from_oeb).
        if not chapters:
            raise ValueError("generate_full_book requires a non-empty chapters list")

        # Prepend synthetic cover chapter (#32). All toc_link target indices
        # in subsequent chapters shift by +1 to account for the new chapter
        # at index 0.
        if cover_resource_name:
            # The book's first page opening with the cover: that picture is
            # the cover page's, not printed again under it (#475).
            blocks = self._without_leading_cover(chapters[0], cover_resource_name)
            if blocks is not None:
                chapters = [{**chapters[0], "blocks": blocks}, *chapters[1:]]
            chapters = [
                {
                    **ch,
                    "toc_links": [
                        {**link, "target_chapter_idx": link["target_chapter_idx"] + 1}
                        if "target_chapter_idx" in link
                        else link
                        for link in (ch.get("toc_links") or [])
                    ],
                }
                if ch.get("toc_links")
                else ch
                for ch in chapters
            ]
            cover_chunk = f"\x00IMG\x01{_cover_basename}\x01\x00"
            cover_chapter = {
                "title": "",
                "text": cover_chunk,
                "_omit_from_toc": True,
                "_is_cover": True,
            }
            chapters = [cover_chapter] + chapters

        ch_data = self._build_chapter_content(chapters)

        # Drop image resources no chunk displays (#102). Resources are emitted
        # from the manifest before chunks exist, so what is actually referenced
        # is only knowable now: publishers ship unused assets, and content
        # kfxgen elides takes its <img> references with it. Left in, the $164
        # and its $417 bytes are dead weight that upstream grades ERROR, and
        # Amazon-produced files carry none.
        #
        # The cover is exempt and must be: it is referenced through $490's
        # cover_image value, not a $259 entry, so a rule that only consulted
        # $259 would delete it on any book whose cover is not also in the
        # reading flow. `image_resources` maps the cover's basename to the same
        # names, so the exemption is by name rather than by entry.
        _displayed = {
            c.get("resource")
            for c in ch_data["all_chunks"]
            if isinstance(c, dict) and c.get("type") == "image"
        } | {
            # A picture inside a paragraph's words (#529).
            resource
            for c in ch_data["all_chunks"]
            if isinstance(c, dict)
            for _k, (resource, *_rest) in c.get("pictures") or ()
        }
        _orphans = {
            (res, loc)
            for base, (res, loc) in image_resources.items()
            if res not in _displayed and res != cover_resource_name
        }
        if _orphans:
            _dead_names = {n for pair in _orphans for n in pair}
            self.fragments = [
                f for f in self.fragments if str(f.fid) not in _dead_names
            ]
            image_resources = {
                base: pair
                for base, pair in image_resources.items()
                if pair not in _orphans
            }
            self.image_resources = image_resources
            # Recorded rather than logged: this method has no logger, and the
            # neighbouring basename-collision diagnostic uses the same channel.
            self._dropped_image_resources = sorted(res for res, _loc in _orphans)

        story_names = ch_data["story_names"]
        storyline_names = ch_data["storyline_names"]
        content_names = ch_data["content_names"]
        section_positions = ch_data["section_positions"]
        toc_positions = ch_data["chapter_start_positions"]

        # 3. Build one $260 section per chapter/storyline
        section_names = []
        for i, sl_name in enumerate(storyline_names):
            sec_name = f"c{i}"
            frag_260, sec_name = self.build_fragment_260(
                sl_name,
                section_name=sec_name,
                field_155_value=section_positions[i],
            )
            section_names.append(sec_name)
            self.fragments.append(frag_260)

        # 4. Build position data (required for Kindle nav pane)
        pos_data = self._build_position_data(chapters, section_names, ch_data)

        # 5. Build Fragment $538 (Document Data / Reading Order)
        # Lists ALL sections in reading order
        self.fragments.append(self.build_fragment_538(section_names))

        # 6. Build Fragment $389 (Navigation / TOC). Synthetic chapters
        # marked with `_omit_from_toc` (e.g. the cover-in-reading-flow
        # chapter) are excluded so they don't appear in the nav-pane.
        nav_entries = []
        for ch, pos in zip(chapters, toc_positions):
            if not ch.get("_omit_from_toc"):
                nav_entries.append(
                    {
                        "title": ch["title"],
                        "position": pos,
                        "level": ch.get("toc_level", 0),
                    }
                )
            # Entries that name a place in the section, not a section of
            # their own (#363). One that can't be found opens the section.
            for sub in ch.get("sub_entries") or ():
                at, offset = ch_data["key_positions"].get(sub["key"], (pos, 0))
                nav_entries.append(
                    {
                        "title": sub["title"],
                        "position": at,
                        "offset": offset,
                        "level": sub.get("level", 0),
                    }
                )
        # The synthetic cover chapter is always first when present (#32).
        cover_position = section_positions[0] if chapters[0].get("_is_cover") else None

        # The book's contents and start landmarks, where their links land
        # (#579). One that lands nowhere is left out.
        def lands(key):
            idx = ch_data["key_to_chunk"].get(key)
            offset = ch_data["key_to_offset"].get(key, 0)
            if idx is None and "#" not in key:
                idx, offset = ch_data["file_to_chunk"].get(key), 0
            return None if idx is None else (ch_data["chunk_positions"][idx], offset)

        marks = [
            (kind, label, *at)
            for kind, label, key in landmarks or ()
            if (at := lands(key)) is not None
        ]
        # The first start landmark that lands, as Previewer keeps it (#609).
        starts = [n for n, m in enumerate(marks) if m[0] == "start"]
        marks = [m for n, m in enumerate(marks) if m[0] != "start" or n == starts[0]]
        # The book's print page breaks, the same way (#578).
        pages = [
            (label, *at)
            for label, key in page_list or ()
            if (at := lands(key)) is not None
        ]
        page_list_name = "nP"
        if not pages and approximate_pages:
            # None of its own: approximate pages, as kfxlib adds them (#586).
            pages = [(str(n), *at) for n, at in enumerate(pos_data["pages"], 1)]
            page_list_name = "APPROXIMATE_PAGE_LIST"
        self.fragments.append(
            self.build_fragment_389_toc(
                nav_entries,
                cover_position=cover_position,
                headings=_book_headings(ch_data),
                landmarks=marks,
                pages=pages,
                page_list_name=page_list_name,
            )
        )

        # 7. Build Fragment $258 (Reading Order Metadata)
        # Lists ALL sections in reading order (same as $538)
        self.fragments.append(self.build_fragment_258(section_names))

        # 8. Build Fragment $395 (empty nav units)
        self.fragments.append(
            YJFragment(
                fid=IS("$348"), ftype=IS("$395"), value=IonStruct(IS("$247"), [])
            )
        )

        # 9. Build Fragment $264 (Position Index)
        self.fragments.append(
            self.build_fragment_264(pos_data["section_positions_264"])
        )

        # 10. Build Fragment $265 (Position Index Table)
        self.fragments.append(self.build_fragment_265(pos_data["position_entries_265"]))

        # 11. Build Fragment $593 (Format Capabilities)
        # CRITICAL: Container-level fragments must NOT have fid — only ftype.
        # Using both fid+ftype creates double annotation ($348::$593::) that
        # breaks Kindle parsing of the format_capabilities blob.
        # textBlock goes with the $145 fragments: a book of pictures only has
        # none, so it declares nothing, as kfxlib's own conversion does (#357).
        capabilities = []
        if any(len(e) > 2 and e[2] for e in pos_data["position_entries_265"]):
            # Position entries part-way into a paragraph (#235). An equation
            # alone in its paragraph makes none, and kfxlib flags the
            # capability declared without one.
            self.symtab.create_local_symbol("kfxgen.pidMapWithOffset")
            self.symtab.create_local_symbol("version")
            capabilities.append(
                IonStruct(IS("$492"), "kfxgen.pidMapWithOffset", IS("version"), 1)
            )
        if content_names:
            self.symtab.create_local_symbol("kfxgen.textBlock")
            self.symtab.create_local_symbol("version")
            capabilities.append(
                IonStruct(IS("$492"), "kfxgen.textBlock", IS("version"), 1)
            )
        self.fragments.append(YJFragment(ftype=IS("$593"), value=capabilities))

        # 12. Build Fragment $597 (Section Annotation) - one per section
        self.symtab.create_local_symbol("IS_TARGET_SECTION")
        section_ad_names = []
        for sec_name in section_names:
            ad_name = sec_name + "-ad"
            self.symtab.create_local_symbol(ad_name)
            section_ad_names.append(ad_name)
            self.fragments.append(
                YJFragment(
                    fid=IS(ad_name),
                    ftype=IS("$597"),
                    value=IonStruct(
                        IS("$598"),
                        IS(ad_name),
                        IS("$258"),
                        [IonStruct(IS("$492"), "IS_TARGET_SECTION", IS("$307"), True)],
                    ),
                )
            )

        # 13. Build Fragment $550 (Page Break Positions)
        page_positions = [
            IonStruct(IS("$155"), p, IS("$143"), off)
            for p, off in pos_data["locations"]
        ]
        self.fragments.append(
            YJFragment(
                fid=IS("$348"),
                ftype=IS("$550"),
                value=[IonStruct(IS("$182"), page_positions)],
            )
        )

        # 14. Build Fragment $419 (Entity Index)
        # Collect extra entity names from chapter content (headings, links, anchors)
        anchor_names = ch_data.get("anchor_names", [])
        extra_style_names = ch_data.get("extra_style_names", [])
        ruby_names = ch_data.get("ruby_names", [])

        all_entity_names = (
            list(content_names)
            + storyline_names
            + section_names
            + story_names
            + extra_style_names
            + anchor_names
            + section_ad_names
            + ruby_names
            + ch_data.get("math_names", [])
        )
        if cover_resource_name:
            all_entity_names.append(cover_resource_name)
            all_entity_names.append(cover_location_name)
        for resource_name, location_name in image_resources.values():
            all_entity_names.append(resource_name)
            all_entity_names.append(location_name)
        # Embedded fonts (#15): register the $418 raw-font locations so the
        # reader can resolve them (otherwise "missing from entity map" and the
        # device font wins). $262 is a self-keyed root fragment (fid="$262"),
        # so it is not listed here by name — only its $418 location is.
        for face in self.font_table.faces:
            all_entity_names.append(face.location)
        # Each once, as Kindle Previewer writes it: the cover and a shared
        # style were listed again where the text used them (#520).
        self.fragments.append(
            self.build_fragment_419(container_id, list(dict.fromkeys(all_entity_names)))
        )

        # 15. Build $270 container info fragment (REQUIRED)
        self.fragments.append(self.build_fragment_270(container_id))

        if self._wrote_zoom_table:
            self._declare_table_viewer()
        if ruby_names:
            self._declare_ruby()
        if ch_data.get("math_names"):
            self._declare_feature("yj_mathml")
        self._declare_content_features(pos_data["max_section_pids"])

        # 16. Build $ion_symbol_table fragment (REQUIRED for Kindle)
        # Must be added last so all symbols used by other fragments are registered
        self.fragments.append(self.build_ion_symbol_table_fragment())

        # 17. Sort fragments to match PREFERED_FRAGMENT_TYPE_ORDER
        # Known-good KFX files have fragments sorted by type then by fid.
        # The Kindle may depend on this ordering.
        sorted_frags = YJFragmentList()
        for f in sorted(self.fragments, key=lambda f: f.annotations.sort_key()):
            sorted_frags.append(f)
        self.fragments = sorted_frags

        # 18. Create and serialize container
        container = KfxContainer(self.symtab)
        container.fragments = self.fragments

        data = container.serialize()

        if output_path:
            _safe_write_bytes(output_path, data)

        return data

    # Position data constants (Z3 pattern).
    #
    # Content positions ($259 outer + chunk children) start at
    # CONTENT_POS_BASE and grow by CONTENT_POS_STEP. Section positions
    # ($260) start at SECTION_POS_BASE and grow by SECTION_POS_STEP.
    #
    # Empirical position-id facts learned the hard way (v5.3.0–v5.3.2,
    # see CHANGELOG):
    #
    #   - The two ranges may arithmetically overlap. v5.2.0 produced ~71
    #     position values shared between content and section ranges on
    #     the test corpus and rendered correctly on Kindle. EIDs in $259
    #     entries and $260 sections are resolved by context (which
    #     fragment they live in), not by global uniqueness, so position
    #     duplicates don't break nav.
    #   - Despite (a), the values themselves matter. Pushing
    #     SECTION_POS_BASE up to 100000 broke Kindle's progress display
    #     ("at start of book" showed 100% complete). Keep section eids in
    #     the low 5-digit range Kindle has been observed to handle.
    #   - The section base is dynamic (#30): SECTION_POS_BASE is the floor,
    #     but for books whose content overflows it the sections relocate to
    #     just above content_max (via _section_base) so content and section
    #     eids stay disjoint. Normal books are unaffected (stay at 10000).
    #     A huge book (~1200+ chapters) pushes relocated section eids into the
    #     ~20k range. Device-verified (#30): a 1170-chapter book (Complete
    #     Works of Shakespeare, section eids to ~20k) navigates correctly on a
    #     physical Kindle — late-chapter TOC taps land right and the progress
    #     readout is sane (88% at a late play). Still well under the 100000
    #     that broke progress; keep section eids in the low 5-digit range.
    #   - There is no need to assert a content-position ceiling. An
    #     earlier attempt (#19, removed in v5.3.2) added a ValueError
    #     when content_pos_id climbed into the section range. That
    #     guard fired on a perfectly-rendering v5.2.0-style book, so it
    #     was wrong. The actual operative limit is "stay in the 5-digit
    #     range Kindle has been observed to handle"; future code that
    #     needs more positions should investigate Kindle's actual limit
    #     on a real device, not assume one based on the constants.
    CONTENT_POS_BASE = 1000  # Position ID base for $259 entries
    #: Every reading's style (#236).
    RUBY_STYLE_NAME = "s0_rt"
    CONTENT_POS_STEP = 2  # Position ID step for $259 entries
    SECTION_POS_BASE = 10000  # Position ID base for $260 sections (v5.2.0 known-good)
    SECTION_POS_STEP = 2  # Position ID step for $260 sections

    @classmethod
    def _section_base(cls, content_max):
        """Base position id for $260 sections.

        Sections start strictly above the content range so content and
        section eids never collide (#30). For normal books (content stays
        under SECTION_POS_BASE) this is the floor SECTION_POS_BASE, keeping
        output byte-identical; only books whose content would overflow the
        floor relocate their sections just above content_max. Content eids
        are always even, so content_max + SECTION_POS_STEP stays even-aligned.
        """
        return max(cls.SECTION_POS_BASE, content_max + cls.SECTION_POS_STEP)

    @staticmethod
    def _detect_image_dimensions(image_data):
        """Detect width and height from JPEG or PNG image data."""
        if image_data[:3] == b"\xff\xd8\xff":  # JPEG
            # Scan for SOF markers (0xFFC0-0xFFC3) which contain dimensions.
            # Must handle: 0xFF padding bytes, parameterless markers (RST, SOI, EOI),
            # and APP/COM segments with length fields that may contain embedded images.
            i = 2
            while i < len(image_data) - 1:
                # Skip any 0xFF padding bytes
                while (
                    i < len(image_data) - 1
                    and image_data[i] == 0xFF
                    and image_data[i + 1] == 0xFF
                ):
                    i += 1
                if i >= len(image_data) - 1 or image_data[i] != 0xFF:
                    break
                marker = image_data[i + 1]
                if marker == 0x00:
                    # Stuffed byte (the padding loop above took any 0xFF)
                    i += 2
                    continue
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    # SOF marker — dimensions at offset +5/+7
                    if i + 9 <= len(image_data):
                        height = (image_data[i + 5] << 8) | image_data[i + 6]
                        width = (image_data[i + 7] << 8) | image_data[i + 8]
                        return width, height
                    break
                if marker == 0xD9:
                    break  # EOI — end of image
                if 0xD0 <= marker <= 0xD8:
                    # Parameterless markers (RST0-RST7, SOI) — no length field
                    i += 2
                    continue
                # All other markers have a 2-byte length field
                if i + 4 > len(image_data):
                    break
                length = (image_data[i + 2] << 8) | image_data[i + 3]
                if length < 2:
                    break
                i += 2 + length
        elif image_data[:4] == b"\x89PNG":  # PNG
            if len(image_data) > 24:
                width = int.from_bytes(image_data[16:20], "big")
                height = int.from_bytes(image_data[20:24], "big")
                return width, height
        return None, None

    # Heading style: font size multiplier relative to chapter body font size
    # 1.2 keeps headings distinct but readable on small Kindle screens
    HEADING_FONT_SIZE = 1.2

    def _without_leading_cover(self, chapter, cover_resource_name):
        """`chapter`'s blocks without its first one when that block is a
        picture of the cover alone, or None. Its anchors move to the block
        after it, at its start, so links to the picture still land (#475). A
        cover sharing its paragraph with text stays, as Kindle Previewer
        4.0.3 prints it twice there (QA's c1 probe on #516). A styled box
        (#238) is looked into: its own blocks print it."""
        found = self._drop_leading_cover(
            chapter.get("blocks") or [], cover_resource_name
        )
        if found is None:
            return None
        blocks, pending = found
        if not any((b.get("text") or "").strip() for b in blocks):
            # Nothing else on the page prints: left out, it was a blank page
            # but for spaces. It stays, as Kindle Previewer 4.0.3 prints it
            # (#524).
            return None
        if pending:
            # Nothing after it: an empty block holds its anchors.
            blocks = [_with_anchors({"text": "", "spans": []}, pending), *blocks]
        return blocks

    def _drop_leading_cover(self, blocks, cover_resource_name):
        """(blocks less the leading cover picture, anchors left to place), or
        None when the first block with text isn't that picture."""
        blocks = list(blocks)
        for n, block in enumerate(blocks):
            text = (block.get("text") or "").strip()
            if not text:
                continue
            is_box = block.get("type") == "box"
            m = None if is_box else IMG_TOKEN_RE.fullmatch(text)
            resource = m and _token_resource(
                m.group(1), self.image_resources, self._resolved_image_refs
            )
            if not is_box and (not resource or resource[0] != cover_resource_name):
                return None
            pending = block
            if is_box:
                found = self._drop_leading_cover(
                    block.get("blocks") or [], cover_resource_name
                )
                if found is None:
                    return None
                kids, inner = found
                if kids:
                    if inner:
                        kids = [_with_anchors(kids[0], inner), *kids[1:]]
                    blocks[n] = {
                        **block,
                        "text": "\n\n".join(k.get("text") or "" for k in kids),
                        "blocks": kids,
                    }
                    return blocks, None
                pending = _with_anchors(block, inner) if inner else block
            del blocks[n]
            if n < len(blocks):
                blocks[n] = _with_anchors(blocks[n], pending)
                return blocks, None
            return blocks, pending
        return None

    def _build_chapter_content(self, chapters):
        """
        Build content fragments for multi-chapter books.

        Uses the Z3 pattern for working TOC navigation:
        - One chunk per paragraph (or per piece between pictures), stored
          in $145.$146; a long paragraph is not cut (#226)
        - Multi-entry $259 storylines (one entry per chunk, not one per chapter)
        - Separate position ID ranges: content (1000+) for $259, section (10000+) for $260
        - All position IDs properly defined in $265

        Each chapter's first chunk is its title, styled with a bold/larger heading.
        The title is stripped from the body text start to avoid duplication.

        Chapters with 'toc_links' get special handling:
        - Each link entry becomes its own chunk
        - Link entries use underlined style and $179 references to $266 anchors

        Args:
            chapters: List of {'title': str, 'text': str} dicts.
                     Chapters may optionally have 'toc_links': list of
                     {'text': str, 'target_chapter_idx': int} for in-book links.

        Returns:
            dict with keys:
                story_names: List of $157 body style names
                storyline_names: List of $259 names
                content_name: Name of shared $145 fragment
                section_positions: List of $260 position IDs (one per chapter)
                chapter_start_positions: List of content position IDs at chapter starts (for TOC)
                chunk_positions: List of content position IDs (one per chunk)
                chapter_chunk_ranges: List of (start_idx, end_idx) per chapter
                all_chunks: List of chunk text strings
                anchor_names: List of $266 anchor entity names (for the entity index)
                extra_style_names: List of additional $157 style names (headings, links)
        """
        # Per-chapter $145 fragments (#2): each chapter owns its own content
        # fragment instead of all chapters sharing a singleton content_1.
        # Reference Calibre KFX emits 149 $145 fragments on the test corpus;
        # the singleton walked a 1.3MB array on every nav lookup. The names
        # are allocated further down, once chunk text exists to measure — a
        # chapter over the per-fragment byte cap takes more than one (#37).

        # Phase 4b: chunks are typed dicts so a chapter's reading flow can
        # interleave text paragraphs and inline image entries.
        # Each chunk is either:
        #   {'type': 'text',  'text': '<paragraph>'}
        #   {'type': 'image', 'resource': 'img_<i>', 'alt': '<alt>'}
        # Token shape (IMG_TOKEN_RE) is defined once in _img_tokens and shared
        # with converter so both layers agree on what an image token is.
        image_resources = getattr(self, "image_resources", {}) or {}

        all_chunks = []
        chapter_chunk_ranges = []
        heading_chunk_indices = set()
        toc_link_chunks = {}
        toc_link_levels = {}  # chunk index -> depth in the contents (#313)

        def _emit_text_chunks(para_text, preformatted=False, trailing=""):
            """Yield typed chunks from a paragraph that may contain image tokens.

            A preformatted paragraph keeps its leading spaces: they are its
            indentation, and `strip()` also removes the non-breaking spaces
            that carry it. (#202)"""

            def _trim(seg):
                # ASCII only: a non-breaking space is the book's (#244).
                return (
                    seg.rstrip(_ASCII_SPACE)
                    if preformatted
                    else seg.strip(_ASCII_SPACE)
                )

            chunks = []
            last = 0
            for m in IMG_TOKEN_RE.finditer(para_text):
                if m.start() > last:
                    seg = _trim(para_text[last : m.start()])
                    # Only spaces, a non-breaking one beside a picture: no
                    # entry (#244).
                    if seg.strip():
                        chunks.append({"type": "text", "text": seg})
                href = m.group(1)
                alt = m.group(2).replace("\x02", " ")
                # Exact manifest href, then its decoded form; basename only
                # for hrefs that were never resolved — see _img_basename
                # helper above. A query string names no different file.
                resource = _token_resource(
                    href, image_resources, self._resolved_image_refs
                )
                if resource is not None:
                    chunk = {
                        "type": "image",
                        "resource": resource[0],
                        "alt": alt,
                        "at": m.start(),
                    }
                    if m.group(3):
                        chunk["size"] = m.group(3)  # the markup's own size
                    if m.group(4):
                        chunk["float"] = m.group(4)  # left or right (#243)
                    chunks.append(chunk)
                # Unknown href: drop the token silently
                last = m.end()
            if last < len(para_text):
                seg = _trim(para_text[last:])
                if seg.strip():
                    # Before an empty line's cut, one line break stays (#602).
                    chunks.append({"type": "text", "text": seg + trailing})
            return chunks

        def _append_text_with_spans(
            chunk_text,
            para_text,
            para_spans,
            block_style,
            anchor_keys=None,
            anchor_offsets=None,
        ):
            """Append chunk_text as one chunk, with the part of para_spans
            covering it, offsets rebased to it. The chunk_text is a
            (stripped) substring of para_text; find its offset once, then
            translate spans.

            A long paragraph stays one chunk, as Kindle Previewer writes it:
            cutting it every 2,000 characters drew a paragraph break there,
            often inside a word (#226).
            """
            # find() returns the FIRST occurrence. In the rare case of two
            # identical text segments around an inline image in one paragraph,
            # the second segment's spans could rebase to the first's offset.
            # Low impact (needs emphasis + duplicate segment text + inline img);
            # tracked in #21. Defensive base=0 means emphasis just won't apply
            # to a chunk whose text can't be located.
            base = para_text.find(chunk_text)
            if base < 0:
                base = 0
            offsets = anchor_offsets or {}
            end = base + len(chunk_text)
            pspans = []
            for s, length, flags in para_spans:
                a = max(s, base)
                b = min(s + length, end)
                if b > a:
                    pspans.append((a - base, b - a, flags))
            chunk = {
                "type": "text",
                "text": chunk_text,
                "spans": pspans,
                "block_style": block_style,
            }
            # An anchor belongs to the chunk that contains it, with its offset
            # rebased into that chunk. Anchors with no offset of their own (ids
            # inherited from a container, or a whole-file key) sit at 0.
            # (#53, #79)
            mine = [k for k in anchor_keys or () if base <= offsets.get(k, 0) <= end]
            if mine:
                chunk["anchor_keys"] = mine
                # One after the last word lands on the last character, as
                # Kindle Previewer writes it, not one past the text (#487).
                last = max(len(chunk_text) - 1, 0)
                chunk["anchor_offsets"] = {
                    k: min(offsets.get(k, 0) - base, last) for k in mine
                }
            all_chunks.append(chunk)
            return set(mine)

        def _inline_pictures(para, para_spans, offsets):
            """(text, spans, offsets, pictures) for a paragraph whose words
            hold pictures, or None (#529). Each picture becomes one
            character, U+FFFC, and `pictures` lists (offset, (resource,
            alt)): the paragraph stays one entry, its text and pictures
            together, as Kindle Previewer 4.0.3 writes it. A paragraph of
            pictures alone, or one with a floated picture, is cut at them as
            before."""
            tokens = list(IMG_TOKEN_RE.finditer(para))
            if not tokens or any(m.group(4) for m in tokens):
                return None
            # Pictures alone: entries. A line of only a picture after words
            # stays in them; `_cut_after_picture_lines` has cut the
            # paragraph after it (#565).
            if not IMG_TOKEN_RE.sub("", para).strip():
                return None
            text, cuts, pictures, last = [], [], [], 0
            for m in tokens:
                text.append(para[last : m.start()])
                resource = _token_resource(
                    m.group(1), image_resources, self._resolved_image_refs
                )
                at = sum(len(t) for t in text)
                if resource is not None:
                    pictures.append(
                        (
                            at,
                            (
                                resource[0],
                                m.group(2).replace("\x02", " "),
                                m.group(3),
                                m.group(5),
                            ),
                        )
                    )
                    text.append(INLINE_PICTURE)
                cuts.append((m.start(), m.end(), 1 if resource is not None else 0))
                last = m.end()
            text.append(para[last:])

            def moved(i):
                # Where `i` lands once each token is one character, or none
                # for a picture the book doesn't hold.
                shift = 0
                for start, end, kept in cuts:
                    if i >= end:
                        shift += end - start - kept
                    elif i > start:
                        return start - shift
                    else:
                        break
                return i - shift

            spans = [
                (moved(s), moved(s + length) - moved(s), flags)
                for s, length, flags in para_spans
                if moved(s + length) > moved(s)
            ]
            return (
                "".join(text),
                spans,
                {k: moved(v) for k, v in offsets.items()},
                pictures,
            )

        def _emit_paragraph(block, inline_pictures=True):
            """A paragraph's chunks: its text, cut at each picture into text
            and image chunks, with spans and anchors placed on the piece
            holding them; anchors no piece holds go on the first chunk.
            Used for the body's paragraphs and for a cell paragraph holding
            a picture (#262).

            Returns the anchor keys it could not place because it emitted
            nothing (only a picture the book doesn't hold), so the caller
            can put them elsewhere."""
            if inline_pictures:
                pieces = _cut_after_picture_lines(block)
                if len(pieces) > 1:
                    return [k for p in pieces for k in _emit_paragraph(p)]
            preformatted = bool(block.get("preformatted"))
            para = (
                block["text"].rstrip(_ASCII_SPACE)
                if preformatted
                else block["text"].strip(_ASCII_SPACE)
            )
            if not para.strip():
                return []
            para_spans = block.get("spans", [])
            block_style = block.get("block_style")
            block_anchor_keys = block.get("anchor_keys") or []
            block_offsets = block.get("anchor_offsets") or {}
            block_first_chunk = len(all_chunks)
            # Not in a table cell, whose pictures are entries of their own
            # (#262).
            inline = inline_pictures and _inline_pictures(
                para, para_spans, block_offsets
            )
            if inline:
                para, para_spans, block_offsets, pictures = inline
            space = 0.0 if block.get("tight") else _space_to_write(block)
            if "margin_above" in block and not block.get("tight"):
                # The book's margins, as Previewer writes them (#553).
                space = block["margin_above"]
            for chunk in _emit_text_chunks(
                para, preformatted, block.get("trailing") or ""
            ):
                if chunk["type"] == "image":
                    # Figure ids live on image blocks; without this
                    # a link to a figure resolved to nothing. (#62)
                    at_start = [
                        k for k in block_anchor_keys if not block_offsets.get(k)
                    ]
                    if at_start:
                        chunk["anchor_keys"] = at_start
                        block_anchor_keys = [
                            k for k in block_anchor_keys if k not in set(at_start)
                        ]
                    link = _picture_link(chunk.pop("at"), para_spans)
                    if link:
                        # A linked picture: a container whose link covers
                        # the picture, as Kindle Previewer writes it (#355).
                        all_chunks.append(
                            {"type": "open", "node": "link", "spans": [(0, 1, link)]}
                        )
                        chunk["inline"] = True
                        # The Voyage drops a floated picture's link, in this
                        # shape and in Previewer's, so it stays unfloated
                        # (#243).
                        chunk.pop("float", None)
                    all_chunks.append(chunk)
                    if link:
                        all_chunks.append({"type": "close"})
                else:
                    placed = _append_text_with_spans(
                        chunk["text"],
                        para,
                        para_spans,
                        block_style,
                        block_anchor_keys,
                        block_offsets,
                    )
                    block_anchor_keys = [
                        k for k in block_anchor_keys if k not in placed
                    ]
                    if inline:
                        all_chunks[-1]["pictures"] = pictures
            # Anything whose offset landed in no piece — a clamped
            # value, or text the chunker could not locate — belongs
            # to the block's first chunk rather than nowhere.
            if block_anchor_keys and len(all_chunks) > block_first_chunk:
                first = all_chunks[block_first_chunk]
                first["anchor_keys"] = _dedupe_keys(
                    (first.get("anchor_keys") or []) + block_anchor_keys
                )
                first.setdefault("anchor_offsets", {}).update(
                    dict.fromkeys(block_anchor_keys, 0)
                )
            level = block.get("heading_level")
            if level:
                # A book heading, for the heading navigation (#461).
                first_text = next(
                    (c for c in all_chunks[block_first_chunk:] if c["type"] == "text"),
                    None,
                )
                if first_text is not None:
                    first_text["heading_level"] = level
            if space is not None and len(all_chunks) > block_first_chunk:
                # On the first piece only: the pieces after a picture
                # continue the paragraph.
                first = all_chunks[block_first_chunk]
                if first["type"] == "text":
                    first["space_above"] = space
            if block.get("margin_below") and len(all_chunks) > block_first_chunk:
                last = next(
                    (
                        c
                        for c in reversed(all_chunks[block_first_chunk:])
                        if c["type"] == "text"
                    ),
                    None,
                )
                if last is not None:
                    last["space_below"] = block["margin_below"]
            if block.get("clear"):
                # Its first entry starts below the floats (#381): the pieces
                # after a picture continue the paragraph. A linked picture's
                # is its link's container, as Previewer writes it (#386
                # review).
                first = next(
                    (
                        c
                        for c in all_chunks[block_first_chunk:]
                        if c["type"] in ("text", "image")
                        or (c["type"] == "open" and c.get("node") == "link")
                    ),
                    None,
                )
                if first is not None:
                    first["clear"] = block["clear"]
            return block_anchor_keys if len(all_chunks) == block_first_chunk else []

        def _attach_keys(index, keys):
            """Put anchor `keys` at offset 0 on the chunk at `index`, or on a
            table's first row when it opens a table: rows, not the `$278`,
            are link targets (#219)."""
            chunk = all_chunks[index]
            if chunk.get("node") == "table":
                depth = 0
                for c in all_chunks[index:]:
                    depth += {"open": 1, "close": -1}.get(c["type"], 0)
                    if depth == 0:
                        break
                    if c.get("node") == "row":
                        chunk = c
                        break
            elif chunk.get("node") == "box":
                # Its first paragraph, not the container (#238).
                chunk = next(
                    c for c in all_chunks[index:] if c["type"] not in ("open", "close")
                )
            chunk["anchor_keys"] = _dedupe_keys((chunk.get("anchor_keys") or []) + keys)
            chunk.setdefault("anchor_offsets", {}).update(dict.fromkeys(keys, 0))

        def _emitted_content(first):
            """Whether a chunk from `first` on can take anchor keys: a box
            whose paragraphs emitted nothing is only its `open` and `close`
            (#434)."""
            return any(c["type"] not in ("open", "close") for c in all_chunks[first:])

        def _emit_box_chunks(block, following):
            """A box (#238): an `open` chunk, its paragraphs, a `close`. The
            box's own keys (a file's key, ids carried to it) go on its first
            paragraph: a paragraph, not the container, is the link target,
            as a table's first row is (#219). Returns the keys it could not
            place.

            Spacing follows kfxgen's paragraphs, which carry 1 line of space
            above when unindented and none below (#309 review): the box gets
            space below only when `following` brings none of its own, and
            when its first paragraph has space above inside the border, its
            bottom padding grows by the same, so the text sits evenly."""
            box = dict(block["box"])
            if block.get("clear"):
                box["clear"] = block["clear"]  # (#381)
            if not _is_frame(box) and _has_space_above(block["blocks"][0]):
                pad = dict(box.get("padding") or dict.fromkeys(_SIDES, 0.0))
                if not isinstance(pad["bottom"], tuple):
                    pad["bottom"] = pad["bottom"] + _LINE_EM
                    box["padding"] = pad
            below = box.get("space_below") or 0.0
            if following is not None and not _has_space_above(following):
                box["space_below"] = max(below, _LINE_EM)
            else:
                # The next paragraph's own line counts toward a declared
                # margin: only what is beyond it is added (#309 review).
                box["space_below"] = max(below - _LINE_EM, 0.0)
            inner = {k for kid in block["blocks"] for k in _block_keys(kid)}
            own = [k for k in block.get("anchor_keys") or [] if k not in inner]
            all_chunks.append({"type": "open", "node": "box", "box": box})
            pending = own
            kids = block["blocks"]
            for k_idx, kid in enumerate(kids):
                first = len(all_chunks)
                # A table or a box inside is nested in it, as Kindle
                # Previewer nests them (#310).
                if kid.get("type") == "table":
                    _emit_table_chunks(kid)
                    unplaced = []
                elif kid.get("type") == "box":
                    nxt = kids[k_idx + 1] if k_idx + 1 < len(kids) else None
                    unplaced = _emit_box_chunks(kid, nxt)
                else:
                    unplaced = _emit_paragraph(kid)
                    if box.get("width"):
                        # A picture's size is a share of a sized box, as
                        # Previewer writes it (#382). Set here, not in the
                        # token: a longer token would shift the paragraph's
                        # runs.
                        for ch in all_chunks[first:]:
                            if ch.get("type") == "image":
                                ch["size"] = _size_in_box(ch.get("size"), box)
                if pending and _emitted_content(first):
                    _attach_keys(first, pending)
                    pending = []
                pending += unplaced
            all_chunks.append({"type": "close"})
            return pending

        def _emit_table_chunks(block):
            """Marker chunks around a native table's cells (#219). `open`
            becomes a container entry with one position; `close` ends it and
            takes none. Cells are ordinary text chunks with a `cell` key.
            A row with no cells is not emitted; its anchor keys move to the
            next emitted row, or to the last one when none follows.

            The table's `open` carries no anchor keys. Its own keys (its id,
            anchors carried in from before it, the bare-filename key) go on
            its first row, a `$279`: Amazon uses rows and cells as link
            targets and was never seen to use a `$278`, and a container as a
            target was a no-op on tap in 5.3.0. Only a table with no emitted
            row keeps them, and the converter never sends one."""
            tbl = block["table"]
            inner = {k for r in tbl["rows"] for k in (r.get("anchor_keys") or [])} | {
                k
                for r in tbl["rows"]
                for c in r["cells"]
                for part in [c, *(c.get("paragraphs") or ())]
                for k in (part.get("anchor_keys") or [])
            }
            own = [k for k in (tbl.get("anchor_keys") or []) if k not in inner]
            table_open = {
                "type": "open",
                "node": "table",
                "anchor_keys": [],
                "zoom": _table_has_zoom(tbl),
                "border": tbl.get("border"),
                "column_widths": tbl.get("column_widths"),
                "width": tbl.get("width"),
                "clear": block.get("clear"),  # (#381)
            }
            all_chunks.append(table_open)
            group = None
            last_row = None
            carried = []  # anchor keys of skipped cell-less rows
            for row in tbl["rows"]:
                if not row["cells"]:
                    carried.extend(row.get("anchor_keys") or [])
                    continue
                if row["group"] != group:
                    if group is not None:
                        all_chunks.append({"type": "close"})
                    group = row["group"]
                    all_chunks.append(
                        {
                            "type": "open",
                            "node": group,
                            "border": row.get("group_border"),
                        }
                    )
                leading = own if last_row is None else []
                keys = _dedupe_keys(leading + carried + (row.get("anchor_keys") or []))
                carried = []
                last_row = {
                    "type": "open",
                    "node": "row",
                    "border": row.get("border"),
                    "anchor_keys": keys,
                    "anchor_offsets": dict.fromkeys(keys, 0),
                }
                all_chunks.append(last_row)
                for cell in row["cells"]:
                    cell_info = {
                        "header": bool(cell.get("header")),
                        "not_bold": bool(cell.get("not_bold")),
                        "colspan": cell.get("colspan", 1),
                        "rowspan": cell.get("rowspan", 1),
                        "valign": cell.get("valign"),
                        "border": cell.get("border"),
                        "width_set": cell.get("width_set"),
                        "padding": cell.get("padding"),
                        "background": cell.get("background"),
                        "table_width": tbl.get("width"),
                    }
                    if not cell.get("paragraphs") and cell.get("text"):
                        # A cell's text cut at an empty line: a container of
                        # its parts, as Kindle Previewer 4.0.3 writes it
                        # (#624), and as a cell of several blocks is (#261).
                        parts = _cut_after_picture_lines(
                            {
                                "text": cell["text"],
                                "spans": cell.get("spans") or [],
                                "anchor_keys": cell.get("anchor_keys") or [],
                                "anchor_offsets": cell.get("anchor_offsets") or {},
                                "block_style": cell.get("block_style"),
                            }
                        )
                        if len(parts) > 1:
                            # The cell's own top: no paragraph space above.
                            parts[0]["space_above"] = 0.0
                            cell = {
                                **cell,
                                "paragraphs": [
                                    {**p, "text": p["text"] + p.pop("trailing", "")}
                                    for p in parts
                                ],
                            }
                    if cell.get("paragraphs"):
                        # A cell holding several blocks (#261): a container,
                        # styled as a cell, around one text entry per block.
                        all_chunks.append(
                            {
                                "type": "open",
                                "node": "cell",
                                "anchor_keys": [],
                                "block_style": cell.get("block_style"),
                                "cell": cell_info,
                            }
                        )
                        inside = len(all_chunks)
                        unplaced = []
                        for para in cell["paragraphs"]:
                            if IMG_TOKEN_RE.search(para["text"]):
                                # A picture alone, or alone on its line, is
                                # an image entry in the cell, with no style
                                # of its own (#262); one inside the words
                                # stays in them (#565), as Kindle Previewer
                                # writes both.
                                first = len(all_chunks)
                                unplaced += _emit_paragraph(para)
                                for ch in all_chunks[first:]:
                                    if (
                                        ch["type"] == "image"
                                        or ch.get("node") == "link"
                                    ):
                                        ch["in_cell"] = True
                                    elif ch["type"] == "text":
                                        ch["in_header_cell"] = (
                                            cell_info["header"]
                                            and not cell_info["not_bold"]
                                        )
                                continue
                            all_chunks.append(
                                {
                                    "type": "text",
                                    "text": para["text"],
                                    "spans": para.get("spans") or [],
                                    "block_style": para.get("block_style"),
                                    "anchor_keys": para.get("anchor_keys") or [],
                                    "anchor_offsets": para.get("anchor_offsets") or {},
                                    "space_above": para.get("space_above"),
                                    "in_header_cell": cell_info["header"]
                                    and not cell_info["not_bold"],
                                }
                            )
                        if len(all_chunks) == inside:
                            # Only a picture the book doesn't hold: keep the
                            # cell, as an empty text cell is kept.
                            all_chunks.append({"type": "text", "text": " "})
                        if unplaced:
                            # Ids of such a picture go on the cell's first
                            # entry, so a link to one still lands (#290
                            # review); on main it landed on the row.
                            first = all_chunks[inside]
                            first["anchor_keys"] = _dedupe_keys(
                                (first.get("anchor_keys") or []) + unplaced
                            )
                            first.setdefault("anchor_offsets", {}).update(
                                dict.fromkeys(unplaced, 0)
                            )
                        all_chunks.append({"type": "close"})
                        continue
                    if not (cell["text"] or "").strip(" \t\r\n\xa0") and not cell.get(
                        "anchor_keys"
                    ):
                        # An empty cell: a cell with nothing in it, as Kindle
                        # Previewer writes it, not a text entry of a space
                        # (#480), an NBSP-only one too, as Previewer does. One
                        # holding an id keeps its entry, for a link to land on.
                        all_chunks.append(
                            {
                                "type": "open",
                                "node": "cell",
                                "anchor_keys": [],
                                "block_style": cell.get("block_style"),
                                "cell": cell_info,
                            }
                        )
                        all_chunks.append({"type": "close"})
                        continue
                    all_chunks.append(
                        {
                            "type": "text",
                            "text": cell["text"] or " ",
                            "spans": (cell.get("spans") or []) if cell["text"] else [],
                            "block_style": cell.get("block_style"),
                            "anchor_keys": cell.get("anchor_keys") or [],
                            "anchor_offsets": cell.get("anchor_offsets") or {},
                            "cell": {
                                "header": bool(cell.get("header")),
                                "not_bold": bool(cell.get("not_bold")),
                                "colspan": cell.get("colspan", 1),
                                "rowspan": cell.get("rowspan", 1),
                                "valign": cell.get("valign"),
                                "border": cell.get("border"),
                                "width_set": cell.get("width_set"),
                                "padding": cell.get("padding"),
                                "background": cell.get("background"),
                                "table_width": tbl.get("width"),
                            },
                        }
                    )
                all_chunks.append({"type": "close"})
            if group is not None:
                all_chunks.append({"type": "close"})
            # Backstop: a key on the block that no part of the table declares
            # would otherwise be on no chunk, and a link to it is dropped.
            declared = inner | set(tbl.get("anchor_keys") or [])
            stray = [k for k in block.get("anchor_keys") or () if k not in declared]
            leftover = _dedupe_keys(carried + stray)
            if last_row is None:
                keys = _dedupe_keys(own + leftover)
                table_open["anchor_keys"] = keys
                table_open["anchor_offsets"] = dict.fromkeys(keys, 0)
            elif leftover:
                last_row["anchor_keys"] = _dedupe_keys(
                    last_row["anchor_keys"] + leftover
                )
                last_row["anchor_offsets"].update(dict.fromkeys(leftover, 0))
            all_chunks.append({"type": "close"})

        for ch_idx, chapter in enumerate(chapters):
            start_idx = len(all_chunks)
            # Anchor ids belonging to blocks this chapter drops (a heading that
            # duplicates the chapter title). Re-attached to the chapter's first
            # chunk below so links to those ids still resolve. (#62) A page the
            # converter rebuilt names the keys of the blocks it replaced (#436).
            carried_anchor_keys = list(chapter.get("anchor_keys") or ())
            # Offsets in kfxgen's heading for ids of the block it prints (#491).
            heading_offsets = {}
            title = chapter["title"]
            toc_links = chapter.get("toc_links")
            title, title_spans = _ruby_heading_title(chapter.get("blocks"), title)
            if title_spans is None:
                title, title_spans = _book_heading_title(chapter.get("blocks"), title)

            # Decide whether to emit the title as a heading text chunk.
            # Skip it when:
            # - `_is_cover`: synthetic cover chapter (#32), no title text.
            # - `_omit_title_heading`: caller-set flag (e.g. a Title Page
            #   label, which is structural, not the book's text) (#33).
            # - The body (after title-prefix stripping) contains image
            #   tokens but no real text — for image-only chapters
            #   (map pages, diagram pages), the title heading appears
            #   as awkward standalone text on top of the image (#33).
            body_text = chapter.get("text", "") or ""
            stripped = body_text.lstrip()
            if _opens_with_title(stripped, title):
                body_after_title = stripped[len(title) :].lstrip()
            else:
                body_after_title = stripped
            text_after_imgs = IMG_TOKEN_RE.sub("", body_after_title).strip()
            body_is_image_only = (
                bool(body_after_title.strip()) and not text_after_imgs and not toc_links
            )
            # A picture and then the book's own heading: that heading prints
            # and kfxgen's is left out, so the page reads picture, then
            # title, as the book and Kindle Previewer have it (#346).
            title_after_pictures = not toc_links and _title_after_pictures(
                chapter.get("blocks") or [], title
            )
            title_runs_in = not toc_links and _title_runs_in(
                chapter.get("blocks"), title
            )
            title_in_table = not toc_links and _title_in_table(
                chapter.get("blocks"), title
            )
            # Only when no book heading took the label's place (#481).
            heading_near_start = (
                not toc_links
                and title_spans is None
                and _heading_near_start(chapter.get("blocks"))
            )
            # A label spelling out the book's first blocks, a heading split
            # over them: they print as they are, each its own entry, and
            # kfxgen prints no heading, as Kindle Previewer 4.0.3 has it (#552).
            label_over_blocks = (
                not toc_links and len(_label_blocks(chapter.get("blocks"), title)) > 1
            )
            omit_heading = (
                chapter.get("_is_cover")
                # An empty title: its heading sat at the next paragraph's
                # position, an entry of no length (#473).
                or not title.strip()
                or chapter.get("_omit_title_heading")
                or body_is_image_only
                or title_after_pictures
                or title_runs_in
                or title_in_table
                or heading_near_start
                or label_over_blocks
            )
            heading_chunk = None
            if not omit_heading:
                # First chunk: chapter title (heading)
                heading_chunk_indices.add(len(all_chunks))
                heading_chunk = {"type": "text", "text": title}
                if chapter.get("heading_level"):
                    # A rebuilt contents page's own heading (#580).
                    heading_chunk["heading_level"] = chapter["heading_level"]
                if title_spans:
                    # Its readings, even when the chapter is the heading
                    # alone and no block is left to cut it from.
                    heading_chunk["spans"] = title_spans
                all_chunks.append(heading_chunk)

            if toc_links:
                for link in toc_links:
                    chunk_idx = len(all_chunks)
                    if "target_chapter_idx" not in link:
                        # One of the page's own headings, printed as it is
                        # (#585): no link, its level and style.
                        all_chunks.append(
                            {
                                "type": "text",
                                "text": link["text"],
                                "block_style": link.get("block_style"),
                                "heading_level": link.get("heading_level"),
                                "own_size": True,
                            }
                        )
                        continue
                    line = {"type": "text", "text": link["text"]}
                    if link.get("heading_level"):
                        # A heading the TOC lists prints as the heading
                        # (#595), linked.
                        line.update(
                            block_style=link.get("block_style"),
                            heading_level=link["heading_level"],
                            own_size=True,
                        )
                    all_chunks.append(line)
                    toc_link_chunks[chunk_idx] = (
                        link["target_chapter_idx"],
                        link.get("target_key"),
                    )
                    toc_link_levels[chunk_idx] = link.get("level", 0)
            else:
                # The title is cut from the body only because the heading
                # prints it. With the heading suppressed by the caller, the
                # matching words are the page's own and stay (#284 review:
                # back/notes.xhtml lost its "Notes"), and so on a page of a
                # heading and a picture, which prints no heading of
                # kfxgen's (#33; #409 QA: a part page lost its heading).
                cut_title = (
                    not chapter.get("_omit_title_heading")
                    and not body_is_image_only
                    and not title_runs_in
                    and not title_in_table
                    and not heading_near_start
                    and not label_over_blocks
                )
                text = chapter["text"]
                stripped = text.lstrip()
                if cut_title and _opens_with_title(stripped, title):
                    text = stripped[len(title) :].lstrip()
                if text:
                    blocks = chapter.get("blocks")
                    if blocks is not None:
                        iter_blocks = _drop_edge_rules(blocks)
                        # An opening table printing the title is kept whole
                        # (title_in_table); one that doesn't has none to cut.
                        if (
                            iter_blocks
                            and cut_title
                            and iter_blocks[0].get("type") != "table"
                        ):
                            first = iter_blocks[0]
                            first_stripped = first["text"].lstrip()
                            if first.get("type") == "box" and (
                                box_cut := _cut_title_from_box(first, title)
                            ):
                                kept_box, keys = box_cut
                                carried_anchor_keys.extend(keys)
                                iter_blocks = (
                                    [kept_box] if kept_box else []
                                ) + iter_blocks[1:]
                            elif first.get("type") == "box":
                                # Not the paragraph cut: a box is written from
                                # its blocks, so trimming its joined text
                                # removed nothing, and the title printed twice
                                # (#344). A title split over its lines or a
                                # table's rows is eaten from them.
                                iter_blocks, eaten_keys = _eat_split_title(
                                    iter_blocks, title
                                )
                                carried_anchor_keys.extend(eaten_keys)
                            elif _opens_with_title(first_stripped, title):
                                remainder = first_stripped[len(title) :].lstrip()
                                removed = len(first["text"]) - len(remainder)
                                if remainder:
                                    # Spans and anchor offsets both shift, or
                                    # a return link landed title-length
                                    # characters late (#277).
                                    iter_blocks[0] = _trim_text(first, removed)
                                else:
                                    # The block is gone, but its ids are what
                                    # the TOC links to — a back-matter file
                                    # opening with a heading equal to its
                                    # chapter title is the common shape. Carry
                                    # them onto the chapter's first chunk so
                                    # the link still lands here. (#62)
                                    carried_anchor_keys.extend(
                                        first.get("anchor_keys") or []
                                    )
                                    iter_blocks = iter_blocks[1:]
                                    # Its italic or bold words go on the
                                    # heading that prints it (#366).
                                    if heading_chunk is not None:
                                        heading_offsets = _heading_offsets(first, title)
                                        heading_chunk["spans"] = _title_spans(
                                            first, len(title)
                                        )
                                        # And its small caps, transform,
                                        # spacing and colour (#239).
                                        bs = first.get("block_style") or {}
                                        heading_chunk["typography"] = {
                                            k: bs[k]
                                            for k in TYPOGRAPHY_KEYS
                                            if bs.get(k) is not None
                                        }
                                        # And the rest of its style (#490).
                                        heading_chunk["book_style"] = bs
                                        # And its level (#461).
                                        if first.get("heading_level"):
                                            heading_chunk["heading_level"] = first[
                                                "heading_level"
                                            ]
                            else:
                                # Publishers routinely split the opener across
                                # elements — the numeral in one, the title in
                                # the next. The single-block test above misses
                                # that, so the synthesized heading landed on top
                                # of the book's own opener and the chapter name
                                # rendered twice. (#64)
                                # A native table among them counts one block
                                # per row, as the rows build saw it. (#219)
                                first_style = (
                                    (iter_blocks[0].get("block_style") or {})
                                    if iter_blocks
                                    else {}
                                )
                                before = len(iter_blocks)
                                iter_blocks, eaten_keys = _eat_split_title(
                                    iter_blocks, title
                                )
                                if (
                                    heading_chunk is not None
                                    and len(iter_blocks) < before
                                ):
                                    # The book's heading prints: its style (#490).
                                    heading_chunk["book_style"] = first_style
                                carried_anchor_keys.extend(eaten_keys)
                        para_iter = iter_blocks
                    else:
                        para_iter = [
                            {"text": p, "spans": []} for p in text.split("\n\n")
                        ]

                    # Ids of a paragraph that emitted nothing (only a picture
                    # the book doesn't hold) wait for the next block's first
                    # chunk, so a link to one still lands (#291).
                    pending = []
                    para_iter = list(para_iter)
                    for b_idx, block in enumerate(para_iter):
                        first = len(all_chunks)
                        if block.get("type") == "table":
                            _emit_table_chunks(block)
                            unplaced = []
                        elif block.get("type") == "rule":
                            all_chunks.append(
                                {
                                    "type": "rule",
                                    "rule": {
                                        **block["rule"],
                                        "clear": block.get("clear"),
                                    },
                                }
                            )
                            unplaced = list(block.get("anchor_keys") or ())
                        elif block.get("type") == "box":
                            following = (
                                para_iter[b_idx + 1]
                                if b_idx + 1 < len(para_iter)
                                else None
                            )
                            unplaced = _emit_box_chunks(block, following)
                        else:
                            if b_idx == 0 and block.get("space_above"):
                                # A chapter's first paragraph opens a page or
                                # sits under its heading (#230).
                                block = {
                                    k: v for k, v in block.items() if k != "space_above"
                                }
                            unplaced = _emit_paragraph(block)
                        if pending and _emitted_content(first):
                            _attach_keys(first, pending)
                            pending = []
                        pending += unplaced
                    if pending:
                        # Nothing followed in the chapter: the last entry
                        # before, else the chapter's start (below).
                        last = next(
                            (
                                i
                                for i in range(len(all_chunks) - 1, start_idx - 1, -1)
                                if all_chunks[i].get("type") in ("text", "image")
                            ),
                            None,
                        )
                        if last is not None:
                            _attach_keys(last, pending)
                        else:
                            carried_anchor_keys.extend(pending)
                elif chapter.get("blocks"):
                    # Only its title: the heading prints it, and the cut
                    # leaves no block. The blocks' keys still name this
                    # chapter, or every link to them went dead: 15 of
                    # pg2160's letter links once their ids moved onto such
                    # a heading (#338).
                    for b in chapter["blocks"]:
                        carried_anchor_keys.extend(_block_keys(b))
                    first = next(iter(_drop_edge_rules(chapter["blocks"])), None)
                    if (
                        heading_chunk is not None
                        and first is not None
                        and (first.get("text") or "").strip() == title
                    ):
                        heading_offsets = _heading_offsets(first, title)

            # Illustrations the source printed in a contents section that was
            # replaced. They arrive as their own key rather than inside
            # `chapter["text"]` because the `toc_links` branch above ignores
            # that text entirely and emits one chunk per link.
            #
            # Outside the if/else, not inside the `toc_links` branch, because
            # the converter sets this key whether or not any links survive.
            # A book whose every other chapter is in `CONTENTS_SKIP_TITLES`
            # rebuilds to an empty listing, and handling it only in the
            # `toc_links` branch dropped the image again while the converter
            # logged that it had been kept.
            #
            # Cannot double-emit: the rebuilt `text` never contains these
            # tokens — they are read off the blocks just before those blocks
            # are discarded.
            #
            # Emitted after the body, since the listing is the page's purpose
            # and a plate that sat mid-section has no position to return to
            # once the surrounding text is gone. (#117)
            for token in chapter.get("preserved_images") or []:
                all_chunks.extend(_emit_text_chunks(token))

            # Guarantee every chapter contributes at least one chunk so it
            # owns a navigable content position and the per-chapter arrays
            # stay aligned. A chapter emits zero chunks when its only body
            # is an <img> whose href doesn't resolve to a known body
            # resource — e.g. a recovered cover.xhtml orphan whose image is
            # the separately-handled cover (#32). Without a placeholder, a
            # trailing empty chapter made chapter_start_positions index
            # chunk_positions out of range (IndexError), and a middle empty
            # chapter silently pointed its TOC entry at the next chapter.
            # Container markers (#219) don't count: a table with no cells
            # leaves only markers, and the chapter would start on a container.
            if not any(
                c.get("type") in ("text", "image") for c in all_chunks[start_idx:]
            ):
                all_chunks.append({"type": "text", "text": " "})

            if carried_anchor_keys:
                first_chunk = all_chunks[start_idx]
                if first_chunk.get("node") == "table":
                    # Not the `$278`: its first row, as for the table's own
                    # keys (#219). The search stops at the table's close.
                    depth = 0
                    for c in all_chunks[start_idx:]:
                        depth += {"open": 1, "close": -1}.get(c["type"], 0)
                        if depth == 0:
                            break
                        if c.get("node") == "row":
                            first_chunk = c
                            break
                elif first_chunk.get("node") == "box":
                    # Its first paragraph (#238).
                    first_chunk = next(
                        c
                        for c in all_chunks[start_idx:]
                        if c["type"] not in ("open", "close")
                    )
                first_chunk["anchor_keys"] = _dedupe_keys(
                    (first_chunk.get("anchor_keys") or []) + carried_anchor_keys
                )
                # The block these came from is gone, so its offsets went with
                # it — they land at the chapter's start (#62), unless kfxgen's
                # heading prints that block's words (#491).
                offsets = heading_offsets if first_chunk is heading_chunk else {}
                first_chunk.setdefault("anchor_offsets", {}).update(
                    {k: offsets.get(k, 0) for k in carried_anchor_keys}
                )

            chapter_chunk_ranges.append((start_idx, len(all_chunks)))

        # Position IDs for the nested $259 structure: each chapter gets one
        # outer position followed by N child positions (one per chunk). All
        # in the content range, all unique, no overlap with the section range.
        chunk_positions = [None] * len(all_chunks)
        outer_positions = []
        content_pos_id = self.CONTENT_POS_BASE
        for ch_idx, (start, end) in enumerate(chapter_chunk_ranges):
            outer_positions.append(content_pos_id)
            content_pos_id += self.CONTENT_POS_STEP
            for chunk_idx in range(start, end):
                if all_chunks[chunk_idx].get("type") == "close":
                    continue  # ends a container; takes no position (#219)
                chunk_positions[chunk_idx] = content_pos_id
                content_pos_id += self.CONTENT_POS_STEP

        # Each ruby's reading is an entry with an id of its own, which takes
        # no position: Kindle Previewer leaves readings out of the position
        # maps (#236). Their ids follow the content's, below the sections'.
        ruby_eids = {}
        for chunk_idx, chunk in enumerate(all_chunks):
            for _s, _length, flags in chunk.get("spans") or ():
                ruby = ruby_of(flags)
                if ruby and (chunk_idx, ruby[0]) not in ruby_eids:
                    ruby_eids[chunk_idx, ruby[0]] = content_pos_id
                    content_pos_id += self.CONTENT_POS_STEP
        # A drawn equation (#235): its container, the container in it and
        # its shape, an id each, all three taking positions.
        math_eids = {}
        for chunk_idx, chunk in enumerate(all_chunks):
            for _s, _length, flags in chunk.get("spans") or ():
                m = math_of(flags)
                if m and (chunk_idx, m[0]) not in math_eids:
                    math_eids[chunk_idx, m[0]] = tuple(
                        content_pos_id + n * self.CONTENT_POS_STEP for n in range(3)
                    )
                    content_pos_id += 3 * self.CONTENT_POS_STEP
        # A picture inside a paragraph's words (#529): an id of its own.
        picture_eids = {}
        for chunk_idx, chunk in enumerate(all_chunks):
            for n, _picture in enumerate(chunk.get("pictures") or ()):
                picture_eids[chunk_idx, n] = (content_pos_id,)
                content_pos_id += self.CONTENT_POS_STEP
        math_by_position = {}
        self._math_paths, self._math_strings, self._math_styles = [], [], []
        self._inline_picture_styles = {}
        self._math_glyphs = {}
        #: {chapter: [(eid, reading)]}, numbered from 1 in each chapter: Kindle
        #: Previewer writes one ruby fragment per storyline.
        ruby_readings = {}

        # Section position IDs (for $260). The base is dynamic (#30): sections
        # start strictly above the content range so content and section eids
        # are disjoint at any chapter count. Normal books keep SECTION_POS_BASE
        # (byte-identical); only overflow books relocate.
        content_max = content_pos_id - self.CONTENT_POS_STEP
        section_base = self._section_base(content_max)
        section_positions = [
            section_base + i * self.SECTION_POS_STEP for i in range(len(chapters))
        ]

        # Chapter start positions for TOC entries. These MUST point to a
        # leaf $259 child with a $145 content reference and (ideally) a
        # $790:1 section marker — i.e. the heading entry, which is the
        # first child of the outer wrapper. Kindle treats outer wrapper
        # positions as non-navigable (no $145 ref, no $790:1), so TOC
        # entries that target wrappers behave as no-ops on tap.
        def _first_leaf_position(start, end):
            # A TOC target must be a leaf with content, never a container:
            # 5.3.0 pointed the TOC at a wrapper and taps did nothing. (#219)
            for i in range(start, end):
                if all_chunks[i].get("type") in ("text", "image"):
                    return chunk_positions[i]
            return chunk_positions[start]

        chapter_start_positions = [
            _first_leaf_position(*chapter_chunk_ranges[i]) for i in range(len(chapters))
        ]

        # Where each declared id lives, and where each file first appears.
        # A link may name a whole file ("chapter_002.xhtml") with no
        # fragment, which resolves to the earliest position known for it.
        key_to_chunk = {}
        key_to_offset = {}
        file_to_chunk = {}
        for chunk_idx, chunk in enumerate(all_chunks):
            offsets = chunk.get("anchor_offsets") or {}
            for key in chunk.get("anchor_keys") or []:
                if key not in key_to_chunk:
                    key_to_chunk[key] = chunk_idx
                    key_to_offset[key] = offsets.get(key, 0)
                file_to_chunk.setdefault(key.split("#", 1)[0], chunk_idx)

        # Contents entries that name a place in a section (#363): the
        # position and offset each lands on. A leaf, as for a chapter's
        # start; past a container, the leaf's start.
        key_positions = {}
        for ch in chapters:
            for sub in ch.get("sub_entries") or ():
                idx = key_to_chunk.get(sub["key"])
                if idx is None:
                    continue
                offset = key_to_offset.get(sub["key"], 0)
                leaf = next(
                    (
                        i
                        for i in range(idx, len(all_chunks))
                        if all_chunks[i].get("type") in ("text", "image")
                    ),
                    idx,
                )
                key_positions[sub["key"]] = (
                    chunk_positions[leaf],
                    offset if leaf == idx else 0,
                )

        # Build $266 anchor fragments for TOC link entries
        anchor_names = []
        chunk_anchor_map = {}
        for chunk_idx, (target_ch_idx, key) in toc_link_chunks.items():
            anchor_name = f"toc_anchor_{len(anchor_names)}"
            if target_ch_idx < len(chapter_start_positions):
                target_pos = chapter_start_positions[target_ch_idx]
            else:
                target_pos = chapter_start_positions[0]
            target_pos, offset = key_positions.get(key, (target_pos, 0))
            self.fragments.append(
                self.build_fragment_266(anchor_name, target_pos, offset)
            )
            chunk_anchor_map[chunk_idx] = anchor_name
            anchor_names.append(anchor_name)

        # Build $266 anchors for in-body link targets (#53). Only ids that
        # something actually links to get an anchor — a book's markup carries
        # thousands of ids, and emitting one fragment each would bloat the
        # container for no reading benefit. First collect what is referenced,
        # then walk the chunks that declare those ids.
        from .inline_style import link_target as _link_target

        referenced_targets = set()
        noteref_targets = set()
        for chunk in all_chunks:
            for _s, _length, flags in chunk.get("spans") or []:
                target = _link_target(flags)
                if target:
                    referenced_targets.add(target)
                    if NOTEREF_FLAG in flags:
                        noteref_targets.add(target)
        # A noteref to a footnote: Previewer marks its link and the note it
        # lands on (#445). shortcut: marked per anchor, so a plain link to
        # the same note is marked too; split the anchor if a book does that.
        popup_targets = noteref_targets & self._footnote_keys

        body_anchor_map = {}  # anchor key -> anchor name
        if referenced_targets:
            # A link written in other capitals, "C2.XHTML#t" for c2.xhtml#t,
            # still lands; an exact match comes first (#476).
            folded_keys, folded_files = {}, {}
            for k in sorted(key_to_chunk):
                folded_keys.setdefault(k.lower(), k)
            for f in sorted(file_to_chunk):
                folded_files.setdefault(f.lower(), f)
            for target in sorted(referenced_targets):
                if target.startswith(EXTERNAL_LINK_PREFIX):
                    # Leaving the book: an anchor holding the address, one
                    # per address, as Kindle Previewer writes it (#474).
                    anchor_name = f"ext_anchor_{len(body_anchor_map)}"
                    self.fragments.append(
                        self.build_fragment_266_uri(
                            anchor_name, target[len(EXTERNAL_LINK_PREFIX) :]
                        )
                    )
                    body_anchor_map[target] = anchor_name
                    anchor_names.append(anchor_name)
                    continue
                key = (
                    target
                    if target in key_to_chunk
                    else folded_keys.get(target.lower())
                )
                chunk_idx = key_to_chunk.get(key)
                offset = key_to_offset.get(key, 0)
                if chunk_idx is None and "#" not in target:
                    # A whole-file target names the file's start, not a marker.
                    chunk_idx = file_to_chunk.get(
                        target
                        if target in file_to_chunk
                        else folded_files.get(target.lower())
                    )
                    offset = 0
                if chunk_idx is None:
                    continue  # nothing declares it — leave the run unlinked
                anchor_name = f"body_anchor_{len(body_anchor_map)}"
                self.fragments.append(
                    self.build_fragment_266(
                        anchor_name, chunk_positions[chunk_idx], offset
                    )
                )
                body_anchor_map[target] = anchor_name
                anchor_names.append(anchor_name)
                in_cell = {"cell", "in_header_cell"} & set(all_chunks[chunk_idx])
                if target in popup_targets and not in_cell:
                    # Not in a table cell: Previewer marks none there (#446
                    # review).
                    self._popup_anchors.add(anchor_name)
                    self._popup_positions.add(chunk_positions[chunk_idx])

        # Build the $145 content fragments for each chapter's chunk-range
        # slice. Image chunks are filtered out — $145 holds only text
        # paragraphs; images are referenced from $259 via $175 against $164
        # resources.
        #
        # A chapter yields one fragment when its text fits the format's
        # per-fragment cap and several when it does not (#37). Names are
        # allocated sequentially across the whole book, so a book whose
        # chapters each fit produces exactly content_1..content_N as before
        # and its bytes do not move.
        content_names = []
        content_refs_per_chapter = []
        next_content_index = 1
        for ch_idx, (start, end) in enumerate(chapter_chunk_ranges):
            # A paragraph holding an equation or a picture in its words is
            # written with its text in its own content list, so none goes in
            # the store, where nothing would point at it (#567).
            text_positions = [
                i
                for i in range(start, end)
                if all_chunks[i].get("type") == "text"
                and not all_chunks[i].get("pictures")
                and not any(
                    math_of(flags) for _s, _n, flags in all_chunks[i].get("spans") or ()
                )
            ]
            if text_positions:
                groups, refs = pack_content_fragments(
                    [all_chunks[i]["text"] for i in text_positions],
                    start_index=next_content_index,
                )
            else:
                # A chapter with no text at all — a cover is the usual case —
                # must emit no $145. Packing an empty list still returns one
                # group, which became an empty content fragment that nothing
                # referenced: upstream grades that ERROR and Amazon-produced
                # files carry none. (#102)
                groups, refs = [], []
            for name, strings in groups:
                content_names.append(name)
                self.fragments.append(
                    self.build_fragment_145(strings, content_name=name)
                )
            next_content_index += len(groups)

            # Align refs with every chunk in the chapter, text and image
            # alike: $259 walks the full range and image entries carry no
            # content reference.
            ref_by_chunk = dict(zip(text_positions, refs))
            content_refs_per_chapter.append(
                [ref_by_chunk.get(i) for i in range(start, end)]
            )

        # Build $157 styles. Identical attribute fingerprints share one fragment
        # across chapters; previously each chapter cloned its own body/heading
        # styles, producing 2N fragments for N chapters.
        story_names = []  # body style per chapter
        extra_style_names = []  # heading and link styles (for entity registration)
        heading_style_names = {}  # ch_idx -> heading style name
        heading_attrs = {}  # ch_idx -> its build_fragment_157 arguments
        toc_link_style_names = {}  # ch_idx -> underlined link style name

        style_cache = {}  # (kind, sorted-attrs-tuple) -> entity_name
        kind_counts = {}  # kind -> next index for that kind

        #: The fills of the boxes open around the chunk being written: the
        #: converter measures a text colour against the box around it, which
        #: kfxgen may not draw (a box in a flattened <aside>, #383).
        open_fills = []

        def _allocate_style(kind, builder=None, **attrs):
            if (
                attrs.get("colour") is not None
                and not any(open_fills)
                and attrs.get("background") is None
            ):
                # Text written on the page is measured against it: a colour
                # already readable there is unchanged (#383).
                attrs["colour"] = _on_page(attrs["colour"])
            key = (kind, tuple(sorted(attrs.items())))
            if key in style_cache:
                return style_cache[key]
            idx = kind_counts.get(kind, 0)
            kind_counts[kind] = idx + 1
            name = f"s{idx}{kind}"
            style_cache[key] = name
            self.fragments.append(
                (builder or self.build_fragment_157)(entity_name=name, **attrs)
            )
            if kind == "_em" and name not in extra_style_names:
                extra_style_names.append(name)
            return name

        for i, chapter in enumerate(chapters):
            fs = chapter.get("font_size", 1.0)

            body_name = _allocate_style("", font_size=fs)
            story_names.append(body_name)

            # Only chapters that actually emitted a heading chunk need a
            # heading style. A cover emits none, and allocating one anyway left
            # a $157 nothing applied — an unreferenced fragment upstream grades
            # ERROR (#102).
            #
            # Derived from what the chunk loop emitted rather than re-deriving
            # the omit_heading condition here, which would be two copies of one
            # rule free to drift apart.
            ch_start, ch_end = chapter_chunk_ranges[i]
            if any(ch_start <= h < ch_end for h in heading_chunk_indices):
                heading_fs = round(max(fs * self.HEADING_FONT_SIZE, 1.0), 4)
                mt = 2.0 if i > 0 else None
                heading_attrs[i] = dict(
                    font_size=heading_fs,
                    bold=True,
                    margin_top=mt,
                    is_heading=True,
                )
                heading_name = _allocate_style("_h", **heading_attrs[i])
                heading_style_names[i] = heading_name
                if heading_name not in extra_style_names:
                    extra_style_names.append(heading_name)

            if chapter.get("toc_links"):
                link_name = _allocate_style("_link", font_size=fs, underline=True)
                toc_link_style_names[i] = link_name
                if link_name not in extra_style_names:
                    extra_style_names.append(link_name)

        # Picture entries need their own $157 styles (build_fragment_157_image).
        IMAGE_COLUMN_PX = _IMAGE_COLUMN_PX

        def _image_width_pct(w):
            """CSS width for an image `w` pixels wide, as Amazon computes it.

            `min(100, px / 512 * 100)`, as Kindle Previewer 4.0.3 writes it
            (#467; 496 in Previewer 3.106, #145). An image at least as
            wide as the column fills it; a narrower one keeps its natural
            proportion instead of being stretched.
            """
            return round(min(100.0, w / IMAGE_COLUMN_PX * 100.0), 3)

        any_image_chunk = any(
            isinstance(c, dict) and c.get("type") == "image" for c in all_chunks
        )
        width_style_names = {}
        height_style_names = {}
        float_style_names = {}  # ((axis, pct), side) -> name (#243)
        resource_to_dims = {}

        def _image_size_key(c):
            """('w', pct) or ('h', pct) for an image chunk.

            A size the markup asked for wins over the pixel-width rule: it is
            what Amazon honours, and it is how a page-image book says "fill
            the page" (`height="98%"`, `width: 100%`).
            """
            hint = _parse_size_hint(c.get("size"), IMAGE_COLUMN_PX)
            if hint:
                return hint
            w, _h = resource_to_dims.get(c.get("resource"), (None, None))
            return ("w", _image_width_pct(w) if w else 100.0)

        if any_image_chunk:
            for base, dims in (self._image_dims or {}).items():
                resname, _ = (self.image_resources or {}).get(base, (None, None))
                if resname:
                    resource_to_dims[resname] = dims

            styled = [
                c
                for c in all_chunks
                if isinstance(c, dict)
                and c.get("type") == "image"
                and not c.get("in_cell")
            ]
            # A floated image's style is its own, allocated after the
            # others, so a book's unfloated images keep their names (#243).
            # A cleared one too (#381).
            keys_used = {
                _image_size_key(c)
                for c in styled
                if not (c.get("float") or c.get("clear"))
            }
            floated = sorted(
                {
                    (_image_size_key(c), c.get("float"), c.get("clear"))
                    for c in styled
                    if c.get("float") or c.get("clear")
                },
                key=lambda k: (k[0][0], k[0][1] or 0, k[1] or "", k[2] or ""),
            )
            widths_used = {pct for axis, pct in keys_used if axis == "w"}
            heights_used = {pct for axis, pct in keys_used if axis == "h"}

            # Sorted, so the allocation order is a property of the widths
            # rather than of set iteration — the #96 reproducibility rule
            # applies here too, and there are now many of these rather than
            # three.
            for idx, pct in enumerate(sorted(widths_used)):
                name = f"s_img_w{idx}"
                self.fragments.append(
                    self.build_fragment_157_image(entity_name=name, width_pct=pct)
                )
                extra_style_names.append(name)
                width_style_names[pct] = name

            for idx, pct in enumerate(sorted(heights_used)):
                name = f"s_img_h{idx}"
                self.fragments.append(
                    self.build_fragment_157_image(entity_name=name, height_pct=pct)
                )
                extra_style_names.append(name)
                height_style_names[pct] = name

            for idx, ((axis, pct), side, clear) in enumerate(floated):
                name = f"s_img_f{idx}"
                self.fragments.append(
                    self.build_fragment_157_image(
                        entity_name=name,
                        width_pct=pct if axis == "w" else None,
                        height_pct=pct if axis == "h" else None,
                        float_side=side,
                        clear=clear,
                    )
                )
                extra_style_names.append(name)
                float_style_names[((axis, pct), side, clear)] = name

        def _image_style_for(chunk):
            if chunk.get("float") or chunk.get("clear"):
                return float_style_names.get(
                    (_image_size_key(chunk), chunk.get("float"), chunk.get("clear"))
                )
            axis, pct = _image_size_key(chunk)
            if axis == "h":
                return height_style_names.get(pct)
            return width_style_names.get(pct)

        from .inline_style import (
            FLAG_BOLD,
            FLAG_ITALIC,
            FLAG_NOT_BOLD,
            FLAG_NOT_ITALIC,
            FLAG_STRIKE,
            FLAG_SUB,
            FLAG_SUPER,
            FLAG_TEXT_BOTTOM,
            FLAG_TEXT_TOP,
            FLAG_UNDERLINE,
            TYPOGRAPHY_FLAGS,
            flag_value,
            is_monospace,
            mono_families,
            run_font_size,
        )

        #: A block style's small caps, transform, spacing and colour as
        #: build_fragment_157's arguments, by their run flags (#239).
        typography_args = dict(zip(TYPOGRAPHY_FLAGS, TYPOGRAPHY_KEYS))

        def _typography(bs):
            return {k: bs[k] for k in TYPOGRAPHY_KEYS if bs.get(k) is not None}

        def _chars_by_px(chunk):
            # Each character at its run's size, or else its block's (#375
            # review: a book can size its text on a span in every paragraph).
            px = (chunk.get("block_style") or {}).get("font_size")
            counts = collections.Counter()
            if not px or chunk.get("type") in ("open", "close", "rule", "image"):
                return counts
            in_runs = 0
            for _s, length, flags in chunk.get("spans") or []:
                run_px = run_font_size(flags)
                if run_px:
                    counts[run_px] += length
                    in_runs += length
            counts[px] += len(chunk.get("text") or "") - in_runs
            return counts

        def _most(counts):
            return max(sorted(counts), key=counts.get) if counts else None

        # The book's main text size: the CSS size most of its text has. Sizes
        # are written against it, so the main text is the reader's own size,
        # as Kindle Previewer writes them (#241).
        text_px = collections.Counter()
        for chunk in all_chunks:
            # A heading kept on a rebuilt contents page (#585) isn't the
            # book's text: in a short book it outweighed it (QA on #594).
            if not chunk.get("own_size"):
                text_px.update(_chars_by_px(chunk))
        main_px = _most(text_px)

        # The book's main line height, as a factor of each paragraph's own
        # font size: the one most of its text has. A paragraph's $42 is
        # written against it, so the main text is 1lh, as Kindle Previewer
        # writes it (#463).
        line_counts = collections.Counter()
        for chunk in all_chunks:
            factor = (chunk.get("block_style") or {}).get("line_height")
            if chunk.get("type") == "text" and factor and not chunk.get("own_size"):
                line_counts[factor] += len(chunk.get("text") or "")
        main_line = _most(line_counts)

        def _line_ratio(chunk):
            factor = (chunk.get("block_style") or {}).get("line_height")
            if not factor or not main_line:
                return 1.0
            # Previewer writes none below 0.6 (probed to 0.5).
            return float(f"{max(factor / main_line, 0.6):.6g}")

        def _rem(px):
            # A CSS px size as `$16` says it: against the main text size.
            if not px or not main_px:
                return 1.0
            return round(px / main_px, 4)

        def _block_font_size(ch_idx, chunk):
            # A page kfxgen sizes keeps its size (a copyright page); any other
            # block takes its CSS size, or the size of a span holding all its
            # text, as in Previewer's output (#375 review).
            fs = chapters[ch_idx].get("font_size", 1.0)
            # A heading kept on a rebuilt contents page keeps its own size
            # (#585), not the page's.
            if fs != 1.0 and not chunk.get("own_size"):
                return fs
            counts = +_chars_by_px(chunk)
            if len(counts) == 1:
                return _rem(next(iter(counts)))
            return _rem((chunk.get("block_style") or {}).get("font_size"))

        def _drop_cap(chunk):
            # (lines, characters) for a paragraph opening on a drop cap, or
            # None (#240). Kindle Previewer's lines follow the initial's size
            # against the main text: 1.5 and 1.8 drop 2, 2 to 3.5 drop 3, 3.8
            # to 5 drop 4, 6 5, 8 6, 10 7. Below 1.5 it makes none.
            n = (chunk.get("block_style") or {}).get("drop_cap")
            text = chunk.get("text") or ""
            if not n or not text[n:].strip():
                return None
            px = None
            for s, length, flags in chunk.get("spans") or []:
                if s <= n - 1 < s + length:
                    px = run_font_size(flags)
            size = _rem(px) if px else 0
            if size < 1.5:
                return None
            return int(2 + 0.55 * size), n

        def _family(family_list, bold, italic):
            # An embedded face wins; with none, monospace text is written
            # "monospace", as Kindle Previewer writes it (#242).
            fam = self.font_table.match(family_list, bold, italic)
            if not fam and is_monospace(family_list):
                return "monospace"
            return fam

        # Block-level emphasis (CSS `font-weight`/`font-style` on the paragraph)
        # is written whether or not the book embeds fonts, as Kindle Previewer
        # 4 writes it (#367); #34 had kept no-font books unchanged.

        def _emphasis_style(
            flags,
            family_list,
            blk_bold=False,
            blk_italic=False,
            in_cell=False,
            sized=True,
            blk_fs=1.0,
        ):
            # Effective emphasis = inline run flags OR the block's CSS emphasis.
            # The applied $157 always carries the run's own weight/style ($13/$12)
            # so it matches the $262 face descriptor the Kindle resolves against
            # (#50): a real face is used when present, synthesized otherwise. With
            # no embedded family, font_family is omitted and bold/italic still
            # drive synthetic rendering -> byte-identical to today's output.
            # A run its CSS set back to normal stays normal (#367 review).
            bold = (FLAG_BOLD in flags) or (blk_bold and FLAG_NOT_BOLD not in flags)
            italic = (FLAG_ITALIC in flags) or (
                blk_italic and FLAG_NOT_ITALIC not in flags
            )
            mono = mono_families(flags)
            fam = _family(list(mono) if mono else family_list, bold, italic)
            attrs = {"italic": italic, "bold": bold}
            if blk_italic and not italic:
                # Set back to upright inside an italic paragraph: say so, or
                # the run takes the paragraph's italic (#367 review).
                attrs["upright"] = True
            if fam:
                attrs["font_family"] = fam
            size = run_font_size(flags) if sized else None
            if size is not None and _rem(size) != blk_fs:
                # Its own size (#241); 1 is written too, when the paragraph's
                # isn't (#375 review).
                attrs["font_size"] = _rem(size)
                attrs["explicit_size"] = attrs["font_size"] == 1.0
            # Superscript/subscript: reduced font-size plus a $44 baseline
            # style, matching the reference noteref character styles. <sup>
            # wins if a run is somehow marked both. (#52, #123)
            if FLAG_SUPER in flags:
                attrs["font_size"], attrs["baseline_style"] = superscript_metrics()
            elif FLAG_SUB in flags:
                attrs["font_size"], attrs["baseline_style"] = subscript_metrics()
            elif FLAG_TEXT_TOP in flags:
                attrs["baseline_style"] = "$447"  # no size change (#485)
            elif FLAG_TEXT_BOTTOM in flags:
                attrs["baseline_style"] = "$449"
            # Set only when drawn, so every other style keeps its key (#237).
            if FLAG_UNDERLINE in flags:
                attrs["underline"] = True
            if FLAG_STRIKE in flags:
                attrs["strike"] = True
            for kind, arg in typography_args.items():
                value = flag_value(flags, kind)
                if value is not None:
                    attrs[arg] = value
            if in_cell:
                attrs["no_align"] = True
            return _allocate_style("_em", **attrs)

        def _cell_style(cell, bs, font_size):
            """A table cell's $157: a text cell's, or a cell container's (#261).
            It must declare the weight and style of the face it names, or the
            Kindle falls back from the embedded face (#50). A header is bold
            on its own."""
            blk_bold = bool(bs.get("bold"))
            blk_italic = bool(bs.get("italic"))
            cell_bold = (cell["header"] and not cell.get("not_bold")) or blk_bold
            cell_fam = _family(bs.get("font_family", []), cell_bold, blk_italic)
            cattrs = {
                "align": bs.get("align") or ("center" if cell["header"] else None),
                "bold": cell_bold,
                "italic": blk_italic,
                "colspan": cell["colspan"],
                "rowspan": cell["rowspan"],
                "font_size": font_size,
                # Unset is middle, as calibre's UA sheet makes it, so the two
                # share one style.
                "valign": cell.get("valign") or "middle",
                "border": _border_key(cell.get("border")),
                "border_box": bool(cell.get("width_set")),
                "padding": _border_key(cell.get("padding")),
                "table_width": cell.get("table_width") if cell.get("padding") else None,
                "background": cell.get("background"),
                **_typography(bs),
            }
            if cell_fam:
                cattrs["font_family"] = cell_fam
            return _allocate_style("_td", builder=self.build_cell_style_157, **cattrs)

        # Build multi-entry $259 storylines (one entry per chunk per chapter)
        storyline_names = []
        for ch_idx in range(len(chapters)):
            sl_name = f"l{ch_idx}"
            start, end = chapter_chunk_ranges[ch_idx]

            # Build per-entry style names, link targets, kinds, image specs
            entry_styles = []
            entry_link_targets = []
            entry_link_styles = []
            entry_link_text_lengths = []
            entry_kinds = []
            entry_image_specs = []
            entry_emphasis_spans = []
            entry_nodes = []
            entry_extras = []
            open_fills.clear()
            for chunk_idx in range(start, end):
                chunk = all_chunks[chunk_idx]
                if chunk.get("type") == "open":
                    open_fills.append(
                        (chunk.get("box") or chunk.get("cell") or {}).get("background")
                    )
                elif chunk.get("type") == "close" and open_fills:
                    open_fills.pop()
                if chunk.get("type") in ("open", "close"):
                    node = chunk.get("node")
                    if node == "table":
                        entry_styles.append(
                            _allocate_style(
                                "_tbl",
                                builder=self.build_table_style_157,
                                border=_border_key(chunk.get("border")),
                                width=chunk.get("width"),
                                **(
                                    {"clear": chunk["clear"]}
                                    if chunk.get("clear")
                                    else {}
                                ),
                            )
                        )
                    elif node == "cell":
                        entry_styles.append(
                            _cell_style(
                                chunk["cell"],
                                chunk.get("block_style") or {},
                                _block_font_size(ch_idx, chunk),
                            )
                        )
                    else:
                        entry_styles.append(story_names[ch_idx])
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                    entry_kinds.append(chunk["type"])
                    entry_image_specs.append(None)
                    entry_emphasis_spans.append(None)
                    if node == "table" and chunk.get("zoom"):
                        entry_nodes.append("zoom-table")
                    elif node == "box" and _is_frame(chunk["box"]):
                        entry_nodes.append("frame")
                    else:
                        entry_nodes.append(node)
                    widths = (
                        _column_widths_152(chunk.get("column_widths"))
                        if node == "table"
                        else None
                    )
                    extras = {"$152": widths} if widths else {}
                    if node == "link":
                        # A linked picture (#355): the link covers the one
                        # position the inline picture takes. The container
                        # centres the picture, as `$580` does a block one; in
                        # a cell it has no style, as the picture has none.
                        ((_s, _length, flags),) = chunk["spans"]
                        anchor = body_anchor_map.get(_link_target(flags))
                        if anchor:
                            extras["$142"] = [
                                IonStruct(
                                    IS("$143"), 0, IS("$144"), 1, IS("$179"), IS(anchor)
                                )
                            ]
                        if not chunk.get("in_cell"):
                            extras["$157"] = IS(
                                _allocate_style(
                                    "",
                                    font_size=chapters[ch_idx].get("font_size", 1.0),
                                    align="center",
                                    **(
                                        {"clear": chunk["clear"]}
                                        if chunk.get("clear")
                                        else {}
                                    ),
                                )
                            )
                    if node == "box":
                        box = chunk["box"]
                        extras["$157"] = IS(
                            _allocate_style(
                                "_box",
                                builder=self.build_box_style_157,
                                border=_border_key(box.get("border")),
                                background=box.get("background"),
                                padding=_border_key(box.get("padding")),
                                margin_left=box.get("margin_left"),
                                margin_right=box.get("margin_right"),
                                space_above=box.get("space_above"),
                                space_below=box.get("space_below"),
                                width=box.get("width"),
                                float_side=box.get("float"),
                                centred=bool(box.get("centred")),
                                **({"clear": box["clear"]} if box.get("clear") else {}),
                            )
                        )
                    if node in ("row", "head", "body", "foot") and chunk.get("border"):
                        # A row's or group's own border under collapse (#264).
                        extras["$157"] = IS(
                            _allocate_style(
                                "_tp",
                                builder=self.build_part_style_157,
                                border=_border_key(chunk["border"]),
                            )
                        )
                    entry_extras.append(extras or None)
                    continue
                entry_nodes.append(None)
                entry_extras.append(None)
                if chunk.get("type") == "rule":
                    rule = chunk["rule"]
                    entry_styles.append(
                        _allocate_style(
                            "_r",
                            builder=self.build_box_style_157,
                            space_above=rule["space_above"],
                            space_below=rule["space_below"],
                            **({"clear": rule["clear"]} if rule.get("clear") else {}),
                        )
                    )
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                    entry_kinds.append("rule")
                    entry_image_specs.append(None)
                    entry_emphasis_spans.append(None)
                    continue
                if chunk.get("type") == "image":
                    entry_styles.append(
                        None
                        if chunk.get("in_cell")
                        else _image_style_for(chunk) or story_names[ch_idx]
                    )
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                    entry_kinds.append("image")
                    entry_image_specs.append(
                        {
                            "resource": chunk["resource"],
                            "alt": chunk.get("alt", ""),
                            "inline": bool(chunk.get("inline")),
                        }
                    )
                    entry_emphasis_spans.append(None)
                    continue
                entry_kinds.append("text")
                entry_image_specs.append(None)
                drop = None
                if chunk_idx in heading_chunk_indices:
                    # The book's heading's small caps, transform, spacing
                    # and colour go on kfxgen's (#239).
                    typo = chunk.get("typography")
                    book = chunk.get("book_style")
                    if book:
                        # The book's own heading prints: its weight, size,
                        # alignment and side margins, as Kindle Previewer
                        # prints it, over kfxgen's spacing (#490).
                        attrs = {
                            **heading_attrs[ch_idx],
                            "bold": bool(book.get("bold")),
                            "font_size": _rem(book.get("font_size")),
                        }
                        for key in ("align", "margin_left", "margin_right"):
                            if book.get(key):
                                attrs[key] = book[key]
                        if not book.get("align"):
                            # None set: none written, as Previewer writes it,
                            # not kfxgen's justified body default.
                            attrs["no_align"] = True
                        if book.get("italic"):
                            attrs["italic"] = True
                        entry_styles.append(
                            _allocate_style("_h", **attrs, **(typo or {}))
                        )
                    else:
                        entry_styles.append(
                            _allocate_style("_h", **heading_attrs[ch_idx], **typo)
                            if typo
                            else heading_style_names[ch_idx]
                        )
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                elif chunk_idx in chunk_anchor_map and not chunk.get("heading_level"):
                    # Link entry: entry uses body style; link styling lives
                    # inside the $142 span via entry_link_styles. Reference
                    # has entry $157 = body style, span $157 = link style.
                    level = toc_link_levels.get(chunk_idx, 0)
                    if level:
                        # Indented by depth, 6.25% a level: Kindle
                        # Previewer's width for a 2em margin (#238, #313).
                        entry_styles.append(
                            _allocate_style(
                                "",
                                font_size=chapters[ch_idx].get("font_size", 1.0),
                                margin_left=(f"{6.25 * level:g}", "$314"),
                            )
                        )
                    else:
                        entry_styles.append(story_names[ch_idx])
                    entry_link_targets.append(chunk_anchor_map[chunk_idx])
                    entry_link_styles.append(toc_link_style_names[ch_idx])
                    entry_link_text_lengths.append(len(chunk["text"]))
                else:
                    bs = chunk.get("block_style") or {}
                    attrs = {"font_size": _block_font_size(ch_idx, chunk)}
                    if _line_ratio(chunk) != 1.0:
                        attrs["line_height"] = _line_ratio(chunk)
                    if bs.get("align"):
                        attrs["align"] = bs["align"]
                    if bs.get("indent"):
                        attrs["text_indent"] = bs["indent"]
                    if bs.get("margin_left"):
                        attrs["margin_left"] = bs["margin_left"]
                    if bs.get("margin_right"):
                        attrs["margin_right"] = bs["margin_right"]
                    if chunk.get("space_above") is not None:
                        attrs["space_above"] = chunk["space_above"]
                    if chunk.get("space_below"):
                        attrs["space_below"] = chunk["space_below"]
                    if chunk.get("clear"):
                        attrs["clear"] = chunk["clear"]  # (#381)
                    # Body run: match the block's font-family, honouring any
                    # block-level CSS bold/italic. The entry $157 carries the
                    # block's own weight/style ($13/$12) so it matches the $262
                    # face descriptor the Kindle resolves against (#50) — the
                    # real face when present, synthesized otherwise, with or
                    # without an embedded family (#367).
                    blk_bold = bool(bs.get("bold"))
                    blk_italic = bool(bs.get("italic"))
                    if chunk.get("in_header_cell"):
                        # A paragraph in a header cell is bold, as a header
                        # cell's own text is (#261).
                        blk_bold = True
                        attrs["bold"] = True
                    fam = _family(bs.get("font_family", []), blk_bold, blk_italic)
                    if fam:
                        attrs["font_family"] = fam
                    if blk_bold:
                        attrs["bold"] = True
                    if blk_italic:
                        attrs["italic"] = True
                    attrs.update(_typography(bs))
                    cell = chunk.get("cell")
                    if cell is not None:
                        entry_styles.append(_cell_style(cell, bs, attrs["font_size"]))
                    else:
                        drop = _drop_cap(chunk)
                        if drop:
                            attrs["drop_cap"] = drop
                        entry_styles.append(_allocate_style("", **attrs))
                    if chunk_idx in chunk_anchor_map:
                        # A contents line printed as a heading (#595): its
                        # link in the heading's own style, not underlined.
                        entry_link_targets.append(chunk_anchor_map[chunk_idx])
                        entry_link_styles.append(entry_styles[-1])
                        entry_link_text_lengths.append(len(chunk["text"]))
                    else:
                        entry_link_targets.append(None)
                        entry_link_styles.append(None)
                        entry_link_text_lengths.append(None)
                chunk_spans = chunk.get("spans", [])
                _cbs = chunk.get("block_style") or {}
                chunk_fam = _cbs.get("font_family", [])
                # A header cell is bold on its own, with or without embedded
                # fonts, exactly as its cell style is; a span style names the
                # run's whole face, so it must say bold too. (#219)
                _blk_b = (
                    bool(_cbs.get("bold"))
                    or bool(
                        (chunk.get("cell") or {}).get("header")
                        and not chunk["cell"].get("not_bold")
                    )
                    or bool(chunk.get("in_header_cell"))
                    # kfxgen's chapter heading is bold (#366).
                    or chunk_idx in heading_chunk_indices
                )
                _blk_i = bool(_cbs.get("italic"))
                # A run may be emphasis, a link, or both. Its visual style is
                # whatever the flags say; its $179 is the resolved anchor, or
                # None when nothing in the book declares the target — an
                # unresolvable href is dropped rather than emitted dangling.
                # A run that is ONLY a link gets no $157: reference KFX leaves
                # link spans unstyled and lets the reader render them. (#53)
                spans_out = []
                blk_fs = _block_font_size(ch_idx, chunk)
                # A ruby's event covers its whole base, which may be several
                # runs; its reading is an entry in the book's ruby fragment
                # (#236). {serial: [start, end, reading]}, in reading order.
                rubies = {}
                for s, length, flags in chunk_spans:
                    ruby = ruby_of(flags)
                    if ruby:
                        rubies.setdefault(ruby[0], [s, s, ruby[1]])[1] = s + length
                maths = [
                    (s, math_of(flags), math_eids[chunk_idx, math_of(flags)[0]])
                    for s, length, flags in chunk_spans
                    if math_of(flags)
                ]
                maths += [
                    (
                        k,
                        ("picture", resource, alt, size, blk_fs, extra),
                        picture_eids[chunk_idx, n],
                    )
                    for n, (k, (resource, alt, size, extra)) in enumerate(
                        chunk.get("pictures") or ()
                    )
                ]
                maths.sort(key=lambda item: item[0])
                if maths:
                    math_by_position[chunk_positions[chunk_idx]] = (
                        chunk["text"],
                        maths,
                    )
                for s, length, flags in chunk_spans:
                    flags = without_math(without_ruby(flags))
                    if not flags:
                        continue
                    if drop and s + length <= drop[1]:
                        # The Kindle sizes a drop cap to its lines: Previewer
                        # gives the initial no size of its own (#240).
                        flags = frozenset(
                            f for f in flags if run_font_size((f,)) is None
                        )
                        if not flags:
                            continue
                    target = _link_target(flags)
                    anchor = body_anchor_map.get(target) if target else None
                    has_emphasis = any(
                        f in flags
                        for f in (
                            FLAG_BOLD,
                            FLAG_ITALIC,
                            FLAG_NOT_BOLD,
                            FLAG_NOT_ITALIC,
                            FLAG_SUPER,
                            FLAG_SUB,
                            FLAG_TEXT_TOP,
                            FLAG_TEXT_BOTTOM,
                            FLAG_UNDERLINE,
                            FLAG_STRIKE,
                        )
                    ) or any(flag_value(flags, k) is not None for k in TYPOGRAPHY_FLAGS)
                    has_emphasis = has_emphasis or bool(mono_families(flags))
                    # kfxgen's chapter heading has its own size: a sized run
                    # in the book's heading doesn't change it (#241).
                    sized = chunk_idx not in heading_chunk_indices
                    run_px = run_font_size(flags)
                    if sized and run_px is not None and _rem(run_px) != blk_fs:
                        has_emphasis = True
                    elif (
                        not anchor
                        and flags
                        and all(run_font_size((f,)) is not None for f in flags)
                    ):
                        # Only a size, and the paragraph's: no run, as a
                        # paragraph wholly in a sized span has none (#375).
                        continue
                    style = (
                        _emphasis_style(
                            flags,
                            chunk_fam,
                            _blk_b,
                            _blk_i,
                            in_cell="cell" in chunk or "in_header_cell" in chunk,
                            sized=sized,
                            blk_fs=blk_fs,
                        )
                        if has_emphasis or not anchor
                        else None
                    )
                    spans_out.append((s, length, style, anchor))
                for serial, (r_start, r_end, reading) in rubies.items():
                    readings = ruby_readings.setdefault(ch_idx, [])
                    readings.append((ruby_eids[chunk_idx, serial], reading))
                    ruby = (f"ruby{ch_idx}", len(readings))
                    at = next(
                        (i for i, sp in enumerate(spans_out) if sp[0] >= r_start),
                        len(spans_out),
                    )
                    span = spans_out[at] if at < len(spans_out) else None
                    base = (r_start, r_end - r_start)
                    if span and span[:2] == base:
                        # Emphasis or a link over the whole base goes on the
                        # ruby's event, as Previewer writes them.
                        spans_out[at] = (*span, ruby)
                    else:
                        spans_out.insert(at, (*base, None, None, ruby))
                entry_emphasis_spans.append(spans_out)

            frag_259 = self.build_fragment_259(
                entry_styles,
                content_refs=content_refs_per_chapter[ch_idx],
                entity_name=sl_name,
                positions=chunk_positions[start:end],
                link_targets=entry_link_targets,
                link_styles=entry_link_styles,
                link_text_lengths=entry_link_text_lengths,
                outer_position=outer_positions[ch_idx],
                outer_style=story_names[ch_idx],
                chunk_kinds=entry_kinds,
                image_specs=entry_image_specs,
                emphasis_spans=entry_emphasis_spans,
                container_nodes=entry_nodes,
                container_extras=entry_extras,
                maths=math_by_position,
            )
            storyline_names.append(sl_name)
            self.fragments.append(frag_259)

        # Register every allocated $157 style. _allocate_style only auto-tracks
        # the chapter body (story_names) and _em/heading/link styles; per-chunk
        # body styles (distinct block CSS or font-family) also produce $157
        # fragments and would otherwise be orphaned ("missing from entity map").
        registered = set(story_names) | set(extra_style_names)
        for name in style_cache.values():
            if name not in registered:
                extra_style_names.append(name)
                registered.add(name)

        # Drop styles no storyline uses. A chapter's default paragraph style
        # is allocated up front, and a chapter of nothing but tables (or one
        # whose plain paragraphs were all removed) never applies it; KFX Input
        # grades an unreferenced $157 an error (#281). Pruned at the end, so
        # every other style keeps its name and symbol number. Every $157
        # reference is written by build_fragment_259.
        referenced = set()

        def _refs(nodes):
            for e in nodes or []:
                if not isinstance(e, IonStruct):
                    continue
                if IS("$157") in e:
                    referenced.add(str(e[IS("$157")]))
                for sp in e.get(IS("$142")) or []:
                    if IS("$157") in sp:
                        referenced.add(str(sp[IS("$157")]))
                _refs(e.get(IS("$146")))

        for frag in self.fragments:
            if frag.ftype == IS("$259"):
                _refs(frag.value.get(IS("$146")))
        unused = registered - referenced
        if unused:
            self.fragments = [
                f
                for f in self.fragments
                if not (f.ftype == IS("$157") and str(f.fid) in unused)
            ]
            story_names = [n for n in story_names if n not in unused]
            extra_style_names[:] = [n for n in extra_style_names if n not in unused]

        if ruby_readings:
            # Built after the pruning, which looks only in the storylines.
            self.fragments.append(self.build_ruby_style_157(self.RUBY_STYLE_NAME))
            extra_style_names.append(self.RUBY_STYLE_NAME)
        for ch_idx, readings in ruby_readings.items():
            self.fragments.append(self.build_fragment_756(f"ruby{ch_idx}", readings))
        for name, value in self._inline_picture_styles.values():
            # Built after the pruning, as the equations' are (#529).
            self.fragments.append(
                YJFragment(fid=IS(name), ftype=IS("$157"), value=value)
            )
            extra_style_names.append(name)
        math_names = []
        if self._math_paths:
            self.symtab.create_local_symbol("mpaths")
            self.fragments.append(
                YJFragment(
                    fid=IS("mpaths"),
                    ftype=IS("$692"),
                    value=IonStruct(
                        IS("$4"), IS("mpaths"), IS("$693"), self._math_paths
                    ),
                )
            )
            math_names.append("mpaths")
            for name, strings in self._math_strings:
                self.fragments.append(
                    self.build_fragment_145(strings, content_name=name)
                )
                content_names.append(name)
            for name, value in self._math_styles:
                self.fragments.append(
                    YJFragment(fid=IS(name), ftype=IS("$157"), value=value)
                )
                extra_style_names.append(name)

        return {
            "math_names": math_names,
            "math_by_position": math_by_position,
            "ruby_names": [f"ruby{ch_idx}" for ch_idx in ruby_readings],
            "story_names": story_names,
            "storyline_names": storyline_names,
            "content_names": content_names,
            "section_positions": section_positions,
            "chapter_start_positions": chapter_start_positions,
            "key_positions": key_positions,
            "key_to_chunk": key_to_chunk,
            "key_to_offset": key_to_offset,
            "file_to_chunk": file_to_chunk,
            "chunk_positions": chunk_positions,
            "outer_positions": outer_positions,
            "chapter_chunk_ranges": chapter_chunk_ranges,
            "all_chunks": all_chunks,
            "heading_chunk_indices": heading_chunk_indices,
            "anchor_names": anchor_names,
            "extra_style_names": extra_style_names,
        }
