# Table cells holding several paragraphs (#261)

## Problem

Since #251 a table whose cell holds more than one block (`<p>`, a heading, a
list, a `<div>` wrapping a `<p>`) is not written as a Kindle table. It falls
back to one paragraph per row, and that row path joins every block in the
cell into one paragraph. In pg21053, the table cells hold whole poems, with a
heading, one `<p>` per verse line, and line numbers between them. In 5.8.10
each poem reads as one run-on paragraph:
"1 Mein. Du bist mein, ich bin dein: Des sollst du gewiss sein. Du bist
beschlossen In meinem Herzen. 5 Verlore…"

## Reach

Gutenberg 90 on 5.8.10 (743bea2), through the shim:
- **131 of the 157 tables still written as rows** have a cell with several
  blocks and no other fallback reason. 129 are in pg21053, one in pg2465 and
  one in pg22120.
- pg21053's 129 are 125 one-column tables (a box around a poem or a
  dialogue) and 4 two-column tables (a text beside its notes).
- **16 of their cells are over 2,000 characters** (up to 8,291), but they are
  made of short paragraphs.

The library sample has not been counted for this cause.

## What Amazon writes

Kindle Previewer 4.0.1 on pg21053 (decoded with KFX Input):
- **All 131 tables are native**, including the one-column poem boxes and the
  8,291-character cells.
- **A cell holding blocks is a `$269` container:** `$155`, `$157` (cell
  style: padding, `$633` vertical-align), `$159 $269`, and `$146` holding one
  `$269` text entry per block, each with its own style. A heading in a cell
  is its own child with a heading style. Loose text between blocks, such as
  a line number in a `<span>`, is its own child.
- **A cell of plain text stays a bare `$269` text entry,** as kfxgen writes
  it now. Previewer writes a cell holding one `<p>` as a container with one
  child, but kfxgen keeps such cells as they are, so existing native tables
  are byte-identical.
- **Previewer gives the viewer properties (`$629`/`$630`) to 1 of the 131
  tables;** the one-column poem tables have none. kfxgen writes them on
  every table (#254). Whether they affect a cell that runs over several pages
  is a device question (see Device gate).

## Design

### Converter

- **`_table_is_native`:** a cell may hold several blocks. The cell-length
  limit applies per paragraph instead of per cell: the generator's
  2,000-character chunk size cuts a text entry, so any single block (or run
  of loose text) over 2,000 characters still sends the table to rows. Every
  other fallback is unchanged: images and other objects (#262), nested
  tables (#263), hidden parts, stray text, over 24 columns, and so on.
- **`_table_block(table, style_resolver, base_href, walk_cell=None)`:** for a
  cell with two or more blocks, it calls `walk_cell(cell)`, which returns the
  cell's blocks. A cell with fewer blocks is built as now (`_table_cell`).
  The cell becomes:

  ```python
  {"text": " ".join(paragraph texts),   # row text, title cut, text consumers
   "spans": [], "anchor_ids": [], "anchor_offsets": {},
   "block_style": <the cell's computed style>, "header": bool,
   "colspan": int, "rowspan": int,
   "paragraphs": [<blocks, as the body walker builds them>]}
  ```

- **`walk_cell`** is a closure inside `extract_blocks_from_html`. It runs
  `_walk_element(cell)` with `pending_ids` and `pending_markers` saved and
  cleared, takes the blocks it appended, and restores the walker's state.
  Headings, lists and their markers, line breaks, loose inline text, note
  links, anchors and block styles inside a cell are therefore handled
  exactly as in the body. The cell's own ids land on its first paragraph, as
  the walker gives a container's ids to its first leaf. Ids left pending at
  the end of the cell go on its last paragraph.
- **Anchors:** the table block's `anchor_ids` (used to find chapter starts)
  also include the paragraphs' ids. `_table_start_ids` counts the first
  paragraph of a first cell. `_attach_anchor_keys` keys the paragraphs as it
  keys cells.

### Generator

- **`_emit_table_chunks`:** for a cell with `paragraphs`, it emits
  `{"type": "open", "node": "cell", "cell": {...}, "block_style": ...}`, then
  one text chunk per paragraph (text, spans, block style, anchor keys and
  offsets), then `close`. A cell without paragraphs is emitted as now.
- **Styles:** the `cell` container takes the cell style that a text cell
  takes now (alignment, header bold, colspan, rowspan, font face), built by
  one shared helper. A paragraph inside a cell takes the ordinary body style
  from its block style. A paragraph in a header cell is bold, as a header
  text cell is.
- **`build_fragment_259`:** an `open` chunk with node `cell` becomes a `$269`
  entry with `$155`, `$157`, `$159` and `$146`, holding one position, as a
  row does.
- **Chapter-title cut:** `_row_text`, `_cut_row_text` and
  `_cut_title_from_table` read a paragraph cell as its paragraphs joined by
  spaces. A cut drops the paragraphs it covers whole and trims the one it
  reaches into.

### Not in scope

- The row path (5.8.8's one paragraph per row) is unchanged. It is still
  used for tables that fall back for other reasons, and with
  `--kfxgen-disable-native-tables`.
- Images in cells (#262), nested tables (#263), CSS borders and widths
  (#264).

## Testing

- **Unit (converter):** eligibility of multi-block cells; a cell's
  paragraphs for `<p><p>`, a heading plus paragraphs, loose text between
  blocks, a list in a cell, a `<div>` wrapping `<p>`s; ids in a cell landing
  on their paragraph; a single over-long paragraph falling back.
- **Unit (generator):** a paragraph cell is a `$269` container with one child
  per paragraph and the cell style; positions; a link into a paragraph lands
  there; a header cell's paragraphs are bold; the title cut across a
  paragraph cell; tables without paragraph cells unchanged.
- **Golden:** a new `table_paragraph_cells` fixture, also decoded through
  KFX Input (tier 2). The other goldens stay byte-identical.
- **Corpus:** Gutenberg 90 against 5.8.10, through the shim and real calibre.
  Words identical; books without such tables byte-identical; the 131 tables
  become native.

## Device gate

Add chapters to `research/make_table_sideload.py`:
- a one-column table holding a poem that runs over at least two pages, with
  a heading, verse lines and line numbers;
- a two-column table, a text beside its notes, with a note link inside a
  cell;
- a list inside a cell.

On the Voyage, the Oasis and the Paperwhite, check:
- every line and heading is its own paragraph;
- the long cell pages forward and back without losing or repeating lines;
- the links in cells land;
- the TOC still works.

If a long cell does not page, test the same chapter without the viewer
properties before going further.
