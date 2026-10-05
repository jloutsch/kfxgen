# Native table layout (#219)

**Status:** Draft design, for review before implementation
**Issue:** #219 · **Related:** #221 (rows as paragraphs, shipped in 5.8.8), #222 (real-calibre row styles), #223 (notes tables), #224 (cell alignment), #225 (notes split into chapters without an NCX), #226 (2,000-character cuts), #227 (progress follows location entries)
**Branch:** `feat/219-native-tables`

## Problem

kfxgen writes every table as one paragraph per row (5.8.8). Values that belong together stay together, but columns don't line up. The device pass for #221 found where that hurts: a two-column comparison reads as one merged sentence, list tables with a number, a title and a page number run the numbers together, and some tables looked better as 5.8.7's single centred block. Real table layout is the fix.

Amazon writes a table as containers nested inside the storyline. kfxgen's storyline has been strictly flat since 5.3.2, because the one attempt at nesting (5.3.0, "Phase 3") was followed by the Kindle losing its TOC button. That attempt was never isolated, though:

- It wrapped *each whole chapter* in one container, and pointed TOC entries at that container, which had no text of its own.
- It shipped together with several unrelated changes: body images, per-chapter content fragments, and position maps that broke their own subset rule.
- No firmware was recorded.

So a table nested inside an otherwise flat storyline, with consistent position maps, has never been tested.

## Goal

A simple HTML table becomes a real KFX table, rows and columns laid out by the Kindle, with the rest of the book unchanged. Anything the first version can't express correctly keeps 5.8.8's rows as paragraphs. A plugin setting turns the feature off.

**In scope (v1):**
- rows;
- `thead` / `tbody` / `tfoot`;
- header cells (`th`);
- `colspan`;
- `rowspan`;
- per-cell text alignment;
- links into rows and cells;
- the calibre MOBI→EPUB notes layout (#223);
- captions, as a paragraph before the table.

**Out of scope (v1, each becomes an issue if still wanted):**
- borders and cell padding from CSS;
- column widths;
- the Kindle table viewer (`yj_table_viewer`, `$629`/`$630`). *Added by #254: every native table now carries `$629: [$581, $326]` and `$630: $632`, and a book with one declares `yj_table_viewer` version 1;*
- images inside cells;
- nested tables;
- cells holding more than one block. *Added by #261: such a cell is a `$269` container of `$269` paragraphs;*
- native captions (`$615 $453`, which needs `yj_table` 7).

## Design

### What Amazon writes (decoded from Kindle Previewer 3.106)

A plain 2×2 table with no CSS:

```
$278 {$155 eid, $157 tableStyle, $150 false, $456 {0.9 $318}, $457 {0.9 $318}, $146: [
  $454 {$155 eid, $146: [                          // tbody: always emitted
    $279 {$155 eid, $146: [                        // row: only $155/$159/$146
      $269 {$155 eid, $157 cellStyle, $145 {content_1, n}}    // borderless cell = text entry
      $269 {...}]}
    $279 {...}]}]}
```

- **Table style:** `$16 {1 $505}`, `$42 {1 $310}`, `$65 {100 $314}`, `$83 4286611584`, `$10 'en'`.
- **Cell style:** `$633 $320` (vertical-align middle); padding `$52`/`$54` `{0.03125 $310}` (top/bottom) and `$53`/`$55` `{0.117 $314}` (left/right).
- **Colspan and rowspan** go in the cell's style: `$148 n`, `$149 n`.
- **`th`** is shown only by the cell style: bold, centred.
- **`thead` / `tfoot`** are `$151` / `$455` row groups.
- **Positions:** every container (table, row group, row) takes exactly one position, and every cell text takes its length. All of them appear in `$264` and `$265`.
- **Links:** an `id` on a row targets the `$279`, and one on a cell targets the cell.
- **Content feature `yj_table`:**
  - version 1: plain tables, colspan, thead, tfoot, borders;
  - version 3: rowspan (firmware 5.8.7+);
  - version 7: caption (5.9.4+).

  kfxgen already declares version 1. Every test device is on 5.13.6 or later.
- **KFX Input** (the repo's tier-2 decoder) rejects:
  - a row child that isn't a cell-like node;
  - any key left over on a node;
  - `$630` other than `$632`.

### Converter: a structured table block

`extract_blocks_from_html` gains a `native_tables` flag. When it is set and a `<table>` passes the eligibility check, the table becomes **one block**:

```python
{
    "type": "table",
    "text": "<rows joined by '\n', cells by ' '>",   # for text-only consumers
    "spans": [], "block_style": None,
    "anchor_ids": [every id in the table, in order],     # chapter assembly finds them
    "anchor_offsets": {id: 0 for each},
    "table": {
        "anchor_ids": [ids on the <table> itself],
        "rows": [
            {"group": "head" | "body" | "foot",
             "anchor_ids": [ids on the <tr> + between-row anchors that belong to it],
             "cells": [
                 {"text": str, "spans": [...], "anchor_ids": [...], "anchor_offsets": {...},
                  "block_style": dict | None, "header": bool, "colspan": int, "rowspan": int},
             ]},
        ],
    },
}
```

**Eligibility** (`_table_is_native`). Any of these keeps the table on rows as paragraphs:
- a nested `<table>`;
- an image (`img`, `svg`, `image`) or other embedded object anywhere in it;
- a cell with more than one block child. *Changed by #261: such a cell is written as paragraphs; only a paragraph over 2,000 characters still falls back;*
- a cell with more than 2,000 characters (the generator's `CHUNK_SIZE`, #226);
- a table wider than 8 columns (`_MAX_NATIVE_COLUMNS`): the widest row, counting each cell's `colspan` and the cells a `rowspan` carries down from earlier rows of the same row group. The device gate (#251) found 8 columns fit on all three devices, while 24 were unreadable on the Voyage 5.13.6 and the Oasis 5.18.2 (the Paperwhite 5.19.2 fitted them). This affects 5 of the Gutenberg 90's 601 eligible tables and 72 of the library sample's 4,766. The table viewer that would keep wider tables native is #254. *Changed by #254: with the viewer properties the limit is 24 columns. The Voyage 5.13.6 and the Oasis 5.18.2.1.1 read every table from 8 to 24 columns with them, and squeezed every one without them;*
- a cell outside a row, or a table with no rows or no cells (it needs at least one cell);
- a hidden or contents-listing row, row group or cell (`_is_non_rendered`, `_is_nav_listing`): the row path drops it, and native would show it;
- a `<tr>` whose parent is not `table`, `thead`, `tbody` or `tfoot`;
- text loose in the table, a row group or a row, before or between its children;
- an element directly in the table or a row group other than `tr`, `thead`, `tbody`, `tfoot`, `caption`, `col`, `colgroup` or an empty anchor, such as a `<p>`;
- an element directly in a row other than a cell or an empty anchor, such as a `<span>`;
- a hidden caption (native would show it), or more than one caption (native keeps only the first);
- a TOC entry that targets the table anywhere but its start. The start is the table's own id, an anchor just before it, its first row's ids and its first cell's ids. `extract_chapters_from_oeb` passes each file's TOC fragment ids to `extract_blocks_from_html` as `toc_targets`.

The hidden-content, `<tr>`-parent, loose-text, element-child and caption rules keep text the native walk would otherwise lose, or hidden content it would show. The TOC rule keeps 5.8.8's chapters: a chapter is a range of blocks, and a native table is one block.

A caption is emitted just before the table, walked by the ordinary block walker as the rows build walks it: each block inside it is its own paragraph, with its style and ids, and anchors carried from before the table, and the table's own id, land on its first paragraph, so a TOC entry or link to the table opens at its caption as in 5.8.8. An empty caption's ids, and then the table's own id, go to the table's start. A caption is always written before the table, wherever it sits in the source: HTML makes the caption the table's first child, and browsers draw it above the table by default.

**Anchors between rows** follow 5.8.8's rule (`_anchors_follow_rows`):
- In the calibre notes layout, an empty anchor after a row belongs to that row.
- Otherwise it belongs to the next row.
- Trailing anchors with no row to take them carry forward past the table, as they do today. At the end of a file they go on the table's last row.

**The #221 warning** counts only tables that fell back to rows. Its wording names that: "…written as one paragraph per row…".

### Generator: container markers in the flat chunk list

`_build_chapter_content` stays a flat list of chunks. A table block adds **marker chunks** around its cells:

```
{"type": "open", "node": "table"|"head"|"body"|"foot"|"row", "anchor_keys": [...], "table": {...}?}
{"type": "text", ..., "cell": {"header": bool, "colspan": n, "rowspan": n}}
{"type": "close"}
```

- **Positions:** an `open` chunk gets one position, and a `close` chunk none (`None` in `chunk_positions`). The eid sequence is pre-order, which is Amazon's.
- **`$265`:** gives each `open` length 1, like an image. `close` is skipped.
- **`$264` / `$550`:** list every non-`None` position.
- **`$145` packing** already takes only `type == "text"` chunks, so no change there.
- **`build_fragment_259`** assembles the tree with a stack:
  - `open` pushes a container `{$155, $159 <node type>, $146: []}`;
  - `close` pops it;
  - text and image entries go into the current container.
  - The table node also gets `$157`, `$150 false` and `$456`/`$457`.
  - Row groups and rows get only `$155`/`$159`/`$146`.
  - `$790: 1` goes on the chapter's first *leaf* entry, not on a container.
- **TOC targets** (`chapter_start_positions`) are the chapter's first text or image chunk, never a container. That is the 5.3.0 lesson.
- **Styles:**
  - `build_table_style_157` and `build_cell_style_157` mirror Amazon's styles above.
  - The cell style adds `$34` alignment, `$13` bold for header cells, `$148`/`$149` for spans, and `$11` font family when the book embeds fonts.
  - Allocation is shared through `_allocate_style` (kinds `_tbl`, `_td`).
- **Feature version:** `yj_table` becomes 3 when any table uses `rowspan > 1`, otherwise it stays 1.

### Anchors

A cell's and a row's anchor keys ride on their chunks: the row's on its `open` chunk, a cell's on its text chunk. A link into a cell lands on the cell's text entry at the cell-relative offset. A link to a row lands on the row container, as Amazon does.

The table's own `open` chunk (`$278`) carries no keys. Amazon uses rows and cells as link targets, never a `$278`, and 5.3.0 showed a container target can do nothing on tap. These table-level keys go on the first row's `open` chunk (`$279`), at offset 0:
- the table's own id;
- anchors carried in from just before the table;
- the file's bare key, when the table is the file's first block;
- title anchors carried onto the chapter's first chunk, when that chunk is a table.

Keys of a cell-less row move to the next row, or to the last row when none follows. Any key on the table block that no table part declares also goes on the last row.

### The off switch

This follows the font-embedding precedent (#15, 2026-07-05 spec):
- an opt-out output option, `kfxgen_disable_native_tables` (default False);
- a plugin preference, `disable_native_tables`;
- a Customize-dialog checkbox, "Write tables as one paragraph per row".

With it set, output is 5.8.8's, byte for byte.

### Device gate (before any of this merges)

The whole branch stays unmerged until a sideloaded A/B pair passes on all three test devices: Voyage 5.13.6, Oasis 5.18.2.1.1 and Paperwhite 5.19.2. If the TOC button disappears or navigation breaks on any of them, native tables don't ship. #219 then records the result, and 5.8.8's rows stay.

## Ripple / affected code

- **converter.py:**
  - new: `_table_is_native`, `_table_block`, `_table_rows`;
  - changed: `extract_blocks_from_html` (`native_tables` parameter), `_attach_anchor_keys` (rows and cells), `extract_chapters_from_oeb` and `convert_oeb_to_kfx` (thread the flag).
- **native_generator.py:**
  - changed: `_build_chapter_content` (marker chunks, positions, chapter start), `_build_position_data`, `build_fragment_259` (tree), `build_fragment_585` (`yj_table` version);
  - new: `build_table_style_157`, `build_cell_style_157`.
- **plugin/__init__.py, prefs.py, config.py:** the option, the preference and the checkbox.
- **Tests:**
  - flat-only walkers in `tests/unit/test_native_generator.py`, `test_position_map.py`, `tests/integration/test_golden_corpus.py` and `test_calibre_table_rows.py` read only top-level `$146`; they move to a new recursive `iter_entries` in `tests/_kfx_introspect.py`;
  - the golden `table_cells` fixture changes shape.
- **research/make_table_sideload.py:** new, the device gate's A/B pair.

## Edge cases

- **A chapter that starts with a table and has no heading.** Its TOC target must still be a text leaf.
- **A TOC entry pointing into a table.** At the table's start, the table stays native and the chapter starts at it. Past its start, the table keeps rows, so the chapter starts at the targeted row as in 5.8.8.
- **Several TOC entries into one table** (the #225 shape). If any is past the table's start, the table keeps rows and each entry keeps its chapter. Entries at the start collapse onto the table's block: no empty chapters, no crash.
- **An empty cell.** It becomes a one-space text entry, so the cell still exists and the columns stay aligned.
- **A chapter whose first table row holds its title.** The title dedupe cuts the title from that row's cells, as 5.8.8 cut it from the row's paragraph. A cell the cut covers becomes empty but stays, a cell it reaches into is trimmed (spans and anchor offsets rebased), and a row left with no text is dropped, its anchors moving to the first row written. A title split over rows, or over a paragraph and a row, counts one block per row, as 5.8.8 saw it. "First row" means first in source order, the order 5.8.8 wrote rows in, even when the rows are reordered head, body, foot. A table with no text left is dropped and its anchors go on the chapter's first chunk.
- **`colspan` or `rowspan` values** that are malformed, zero or over 1,000. Clamp them to 1–1,000.
- **A `rowspan` past its row group's last row.** Clamp it to the rows left in the group, counting from the cell's row, as HTML does. Only rows with cells count, since no other row is written. Rows directly in the table count as a group of their own.
- **A `colspan` wider than the table.** Left as is, up to HTML's own maximum of 1,000: the Kindle gets the value the source wrote. Neither corpus has one more than 7 columns past its table's width.
- **A `<tfoot>` before `<tbody>`** (HTML4 allowed it). Row groups are written head, then body, then foot, whatever the source order, as a browser draws them; rows of one kind keep their order. Anchors are placed in source order first and move with their rows. Neither corpus has this shape. Two effects follow: for this shape the native row order differs from the rows build's, and a TOC entry pointing at the footer's first row is past the table's start, so that table falls back to rows. The chapter-title cut still follows source order, so a title row in a leading `<tfoot>` is cut as 5.8.8 cut it.
- **A row with fewer cells than its neighbours.** Emit what's there; the Kindle lays it out.
- **A table inside a list item** that carries a list marker. The marker waits for the next text block (`_take_marker` only touches text blocks).
- **Very wide tables.** Over 8 columns, a table keeps rows (see Eligibility). Without the table viewer (#254), the Voyage and the Oasis squeeze 24 columns until they can't be read. *Changed by #254: the viewer properties are written and the limit is 24 columns.*

## Testing

- **Unit:**
  - eligibility;
  - block shape;
  - anchors between rows, in both layouts;
  - marker chunks and positions;
  - the tree shape against Amazon's minimal structure;
  - styles;
  - the feature version;
  - the off switch giving byte-identical output.
- **Invariants:** the existing `$155`-unique and `$265`-resolves tests, walked recursively.
- **Tier 2:** KFX Input decodes a table fixture with no warnings, and the round-trip EPUB has a `<table>` with the right rows and cells.
- **Golden:** `table_cells` regenerated, plus a new `table_layout` fixture (thead, colspan, rowspan, a link into a cell).
- **Calibre-gated:** `test_calibre_table_rows.py` extended to nested entries. Notes links still land on their own note.
- **Corpus:**
  - Gutenberg 90 through the shim: no text lost, chapters unchanged.
  - Real-calibre A/B against 5.8.8: every native table's cell texts equal the 5.8.8 rows' cells, and nothing outside tables changes.
- **Device gate:** above.

## Success criteria

- Every eligible table in the Gutenberg 90 is written as a `$278` that KFX Input decodes back to an HTML table with the same cells.
- No text is lost in any book, and non-table paragraphs are byte-identical to 5.8.8.
- On all three test devices:
  - the TOC button is present, and TOC jumps work;
  - page turns and progress through a long table work;
  - a table shows rows and columns;
  - a link into a cell lands on its row;
  - the notes book's links land on their own note.
- With the switch on, output is byte-identical to 5.8.8.
