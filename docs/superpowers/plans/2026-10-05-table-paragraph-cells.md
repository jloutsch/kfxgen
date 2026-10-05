# Table Cells Holding Several Paragraphs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A native table whose cells hold several blocks is written as a Kindle table, each block a paragraph inside a `$269` cell container, instead of falling back to one run-on paragraph per row (#261).

**Architecture:** The converter walks a multi-block cell with the body's own block walker (a closure in `extract_blocks_from_html`) and stores the result as `cell["paragraphs"]`. The generator emits such a cell as an `open`/`close` chunk pair around ordinary text chunks, and `build_fragment_259` turns the `open` into a `$269` container with the cell style. Cells with at most one block are untouched, so existing output is byte-identical.

**Tech Stack:** Python 3.13, lxml, kfxgen's Ion writer, pytest, KFX Input (tier 2), calibre 9.14.0 (calibre-gated tests and corpus).

**Spec:** `docs/superpowers/specs/2026-10-05-table-paragraph-cells-design.md`

## Global Constraints

- A cell with 0 or 1 block is written exactly as in 5.8.10. Every golden file except new ones stays byte-identical.
- A single paragraph (or run of loose text) over 2,000 characters (`_MAX_NATIVE_CELL_CHARS`) still sends the table to rows.
- The row path (`--kfxgen-disable-native-tables`, and fallback tables) is unchanged.
- Run `.venv/bin/python -m pytest`, `-m tier3_strict`, `tests/integration/test_kfxlib_diff.py`, `ruff check .` and `ruff format --check .` (ruff 0.15.1) before each commit.
- Never name an in-copyright book in the repo or on GitHub; Gutenberg books may be named.

## Review Focus

1. **An anchor (`id`) on the `<td>` itself, or on a `<p>` inside the cell, as a link target:** the link must land on the paragraph that holds it. Task 2 pins the cell's own id landing on the first paragraph; Task 3 pins a link resolving to a paragraph entry.
2. **A list inside a cell:** each item must keep its marker ("1.", "•"), and the marker must not leak onto the next cell. Task 2 pins both.
3. **A chapter whose first block is a table with a paragraph cell whose text starts with the chapter title:** the title must show once. Task 4 pins the cut.
4. **A cell mixing a block with loose text** (`<td>Lead <p>x</p> tail</td>`): no text lost, in order. Task 2 pins it.
5. **A header cell (`<th>`) holding paragraphs:** its text must be bold, as a plain header cell's is. Task 3 pins it.

---

### Task 1: Eligibility: multi-block cells stay native

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`_table_is_native`, the cell branch; the `_CELL_BLOCK_TAGS` comment)
- Test: `tests/unit/test_converter.py` (near `test_a_table_wider_than_24_columns_falls_back`)

**Interfaces:**
- Produces: `_table_is_native(table)` returns True for a table whose cells hold several blocks, False when any single leaf block or loose-text run in a cell is over `_MAX_NATIVE_CELL_CHARS`.
- Produces: `_cell_paragraph_count(cell) -> int`, the number of block elements in a cell (the existing count), used by Task 2 to choose the paragraph path (`>= 2`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
@pytest.mark.parametrize(
    "cell, native",
    [
        ("<td><p>a</p><p>b</p></td>", True),
        ("<td><h5>1</h5><p>a</p><span>5</span><p>b</p></td>", True),
        ("<td><div><p>a</p></div></td>", True),
        ("<td><ul><li>a</li><li>b</li></ul></td>", True),
        (f"<td><p>{'x' * 2001}</p><p>b</p></td>", False),
        (f"<td><p>a</p>{'x' * 2001}</td>", False),
        (f"<td><p>{'x' * 1500}</p><p>{'y' * 1500}</p></td>", True),
    ],
    ids=["two-p", "heading-loose", "wrapped-p", "list", "long-p", "long-loose", "long-cell-short-p"],
)
def test_a_cell_holding_several_blocks_stays_native(cell, native):
    # #261: 131 Gutenberg tables fell back for this alone; the 2,000-character
    # chunk limit now applies to each paragraph, not to the whole cell.
    html = f"<table><tr>{cell}<td>z</td></tr></table>"
    assert _conv._table_is_native(_first_table(html)) is native
```

- [ ] **Step 2: Run it and see it fail** — `.venv/bin/python -m pytest tests/unit/test_converter.py -k several_blocks -q`. Expected: the True cases fail (multi-block rejected) and `long-cell-short-p` fails (cell over 2,000).

- [ ] **Step 3: Implement.** In the cell branch of `_table_is_native`, replace the `len(blocks) > 1` rejection and the per-cell length check with a per-run check: for each leaf block in the cell (a block element with no block element inside it), `len("".join(leaf.itertext())) + <br count>` must be ≤ `_MAX_NATIVE_CELL_CHARS`; and the cell's loose text (its `.text`, and the tail of every block element directly or indirectly in it that is not inside another leaf) must be ≤ the same limit. A cell with no block elements keeps the existing whole-cell check. Update the docstring and the `_CELL_BLOCK_TAGS` comment ("Two or more make the cell a container of paragraphs (#261)").

- [ ] **Step 4: Run it and see it pass**, then run the whole converter test file: the old "cell holding more than one block" fallback tests must be updated to the new rule (delete or flip them, naming #261 in the comment).

- [ ] **Step 5: Commit** — `feat: a table cell may hold several blocks (#261)`. Note: until Task 2 lands, such tables are native with run-on cells; do not release between tasks.

### Task 2: Converter: a multi-block cell becomes paragraphs

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`_table_block`, `extract_blocks_from_html`'s native branch, `_table_start_ids`, `_attach_anchor_keys`)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `_cell_paragraph_count(cell)` from Task 1.
- Produces: `_table_block(table, style_resolver=None, base_href=None, walk_cell=None)`. `walk_cell(cell_elem) -> list[dict]` returns body-style blocks. When `walk_cell` is given and a cell has ≥ 2 blocks, the cell dict is `{"text": " ".join(p["text"] for p in paragraphs), "spans": [], "anchor_ids": [], "anchor_offsets": {}, "block_style": <cell css>, "header": bool, "colspan": int, "rowspan": int, "paragraphs": [...]}`.
- Produces: in `_attach_anchor_keys`, each paragraph in `cell["paragraphs"]` gets `anchor_keys` and keyed `anchor_offsets`, like a cell.

- [ ] **Step 1: Write the failing tests.** Use the existing helper `extract_blocks_from_html(element, native_tables=True)` on `<body>` documents (see how nearby tests build `_doc(...)`).

```python
def _native_table(body):
    blocks = extract_blocks_from_html(_doc(body), native_tables=True)
    (table,) = [b for b in blocks if b.get("type") == "table"]
    return table, blocks


def _cell(table, r, c):
    return table["table"]["rows"][r]["cells"][c]


@pytest.mark.unit
def test_a_multi_block_cell_keeps_each_block_as_a_paragraph():
    table, _ = _native_table(
        "<table><tr><td><h5>1</h5><h5>Mein.</h5><p>Du bist mein,</p>"
        "<span>5</span><p>ich bin dein.</p></td></tr></table>"
    )
    cell = _cell(table, 0, 0)
    assert [p["text"] for p in cell["paragraphs"]] == [
        "1", "Mein.", "Du bist mein,", "5", "ich bin dein.",
    ]
    assert cell["text"] == "1 Mein. Du bist mein, 5 ich bin dein."


@pytest.mark.unit
def test_a_one_block_cell_is_unchanged():
    table, _ = _native_table("<table><tr><td><p>only</p></td></tr></table>")
    assert "paragraphs" not in _cell(table, 0, 0)


@pytest.mark.unit
def test_loose_text_around_blocks_in_a_cell_is_kept_in_order():
    table, _ = _native_table("<table><tr><td>Lead <p>x</p> tail<p>y</p></td></tr></table>")
    assert [p["text"] for p in _cell(table, 0, 0)["paragraphs"]] == ["Lead", "x", "tail", "y"]


@pytest.mark.unit
def test_a_list_in_a_cell_keeps_its_markers_and_does_not_leak():
    table, blocks = _native_table(
        "<table><tr><td><ol><li>a</li><li>b</li></ol></td><td>c</td></tr></table><p>after</p>"
    )
    assert [p["text"] for p in _cell(table, 0, 0)["paragraphs"]] == ["1. a", "2. b"]
    assert _cell(table, 0, 1)["text"] == "c"
    assert blocks[-1]["text"] == "after"


@pytest.mark.unit
def test_ids_in_a_paragraph_cell_land_on_their_paragraph():
    table, _ = _native_table(
        '<table><tr><td id="cell"><p>a</p><p id="two">b</p></td></tr></table>'
    )
    paras = _cell(table, 0, 0)["paragraphs"]
    assert "cell" in paras[0]["anchor_ids"]
    assert "two" in paras[1]["anchor_ids"]
    assert {"cell", "two"} <= set(table["anchor_ids"])
```

  Also add a test that `_attach_anchor_keys` gives each paragraph `anchor_keys` (`"<file>#two"`), and one that `_table_start_ids` includes `"cell"` for a first cell that has paragraphs.

- [ ] **Step 2: Run them and see them fail** (`KeyError: 'paragraphs'`).

- [ ] **Step 3: Implement.**
  - In `extract_blocks_from_html`, define the closure:

```python
    def _walk_cell(cell):
        """A table cell's blocks, walked as the body is (#261). The walker's
        pending ids and list markers are set aside so nothing leaks in or out."""
        saved_ids, saved_markers = pending_ids[:], pending_markers[:]
        pending_ids.clear()
        pending_markers.clear()
        start = len(blocks)
        _walk_element(cell)
        out = blocks[start:]
        del blocks[start:]
        if pending_ids and out:
            out[-1]["anchor_ids"] = _dedupe_keep_order(out[-1]["anchor_ids"] + pending_ids)
            out[-1]["anchor_offsets"].update(
                {a: len(out[-1]["text"]) for a in pending_ids if a not in out[-1]["anchor_offsets"]}
            )
        pending_ids[:] = saved_ids
        pending_markers[:] = saved_markers
        return out
```

    and pass `walk_cell=_walk_cell` to `_table_block` in the native branch.
  - In `_table_block`'s row loop, build each cell with a new helper:

```python
def _build_cell(cell, style_resolver, base_href, walk_cell):
    if walk_cell is not None and _cell_paragraph_count(cell) >= 2:
        paragraphs = [p for p in walk_cell(cell) if p.get("text")]
        if paragraphs:
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
                "paragraphs": paragraphs,
            }
    return _table_cell(cell, style_resolver, base_href)
```

    The `"text"` of the table block and of each row then follows from `cell["text"]` as now.
  - `every` in `_table_block`: also collect `p["anchor_ids"]` for every paragraph of every cell.
  - `_table_start_ids`: for the first row's first cell, also add its first paragraph's `anchor_ids`.
  - `_attach_anchor_keys`: add `[p for r in tbl["rows"] for c in r["cells"] for p in c.get("paragraphs") or ()]` to the parts it keys.
  - Image blocks cannot appear (Task 1 still rejects `_NON_TEXT_TAGS`), so `walk_cell` returns text blocks only; drop any block without `text` defensively as above.

- [ ] **Step 4: Run them and see them pass**, then the full suite. `tier3_strict` must be unchanged (no golden has a multi-block cell).

- [ ] **Step 5: Commit** — `feat: keep each block of a table cell as its own paragraph (#261)`.

### Task 3: Generator: a paragraph cell is a `$269` container

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`_TABLE_NODE_TYPES`, `_emit_table_chunks`, the per-chunk style loop, `build_fragment_259`'s `open` branch)
- Test: `tests/unit/test_native_generator.py` (next to `test_table_nests_like_amazons_minimal_table`)

**Interfaces:**
- Consumes: `cell["paragraphs"]` (each with `text`, `spans`, `block_style`, `anchor_keys`, `anchor_offsets`) from Task 2.
- Produces: chunk `{"type": "open", "node": "cell", "cell": {"header", "colspan", "rowspan"}, "block_style": ..., "anchor_keys": []}`; text chunks inside carry `"in_header_cell": bool`.

- [ ] **Step 1: Write the failing tests.** Extend the `_table_block(rows)` test helper (line ~2889) or build the block by hand:

```python
def _paragraph_cell(texts, header=False):
    return {
        "text": " ".join(texts), "spans": [], "anchor_ids": [], "anchor_offsets": {},
        "anchor_keys": [], "block_style": None, "header": header,
        "colspan": 1, "rowspan": 1,
        "paragraphs": [
            {"text": t, "spans": [], "block_style": None, "anchor_keys": [], "anchor_offsets": {}}
            for t in texts
        ],
    }


@pytest.mark.unit
def test_a_paragraph_cell_is_a_container_of_text_entries(tmp_path):
    block = _table_block([["a1", "b1"]])
    block["table"]["rows"][0]["cells"][0] = _paragraph_cell(["line one", "line two"])
    top, styles = _storyline(tmp_path, [block])
    table = next(e for e in top if str(e["$159"]) == "$278")
    (body,) = table["$146"]
    (row,) = body["$146"]
    cell, plain = row["$146"]
    assert str(cell["$159"]) == "$269" and "$145" not in cell
    assert [str(k) for k in cell] == ["$155", "$159", "$157", "$146"]
    assert [str(c["$159"]) for c in cell["$146"]] == ["$269", "$269"]
    assert all("$145" in c for c in cell["$146"])
    assert str(plain["$159"]) == "$269" and "$145" in plain
    assert "$148" not in styles[str(cell["$157"])] or styles[str(cell["$157"])]["$148"] == 1
```

  Also: positions (the container's `$155` is unique, and each child takes its text length, using `iter_entries` and the `$264`/`$265` position maps the existing table tests read); a header paragraph cell's children have bold styles (`$13` = `$361` in their `$157`, as `test_..._header...` tests check for header text cells); a link to a paragraph's key lands on that child entry (copy the pattern of the existing link-into-cell test around line 3270).

- [ ] **Step 2: Run them and see them fail.**

- [ ] **Step 3: Implement.**
  - `_TABLE_NODE_TYPES["cell"] = "$269"`.
  - In `_emit_table_chunks`, for a cell with `paragraphs`:

```python
                    all_chunks.append({
                        "type": "open", "node": "cell", "anchor_keys": [],
                        "block_style": cell.get("block_style"),
                        "cell": {"header": bool(cell.get("header")),
                                 "colspan": cell.get("colspan", 1),
                                 "rowspan": cell.get("rowspan", 1)},
                    })
                    for p in cell["paragraphs"]:
                        all_chunks.append({
                            "type": "text", "text": p["text"],
                            "spans": p.get("spans") or [],
                            "block_style": p.get("block_style"),
                            "anchor_keys": p.get("anchor_keys") or [],
                            "anchor_offsets": p.get("anchor_offsets") or {},
                            "in_header_cell": bool(cell.get("header")),
                        })
                    all_chunks.append({"type": "close"})
```

    Include paragraph keys in the `inner` set at the top of `_emit_table_chunks`.
  - Move the cell-style computation in the style loop (the `if cell is not None:` block) into a local helper `_cell_style(cell, bs)` returning the allocated style name. Use it for text cells, as now, and in the `open`/`close` branch for `node == "cell"`, where `bs = chunk.get("block_style") or {}`.
  - For text chunks with `in_header_cell`, set `attrs["bold"] = True` and fold it into `_blk_b`, as `cell["header"]` does for text cells.
  - In `build_fragment_259`'s `open` branch: `if node == "cell": entry[IS("$157")] = IS(story_name)`, placed so the key order is `$155, $159, $157, $146`.
  - Check how chunk positions are assigned to `open` chunks (search `chunk_positions`); a `cell` open must take one position, as `row` does.

- [ ] **Step 4: Run them and see them pass**, then the full suite, `tier3_strict` (unchanged) and tier 2.

- [ ] **Step 5: Commit** — `feat: write a paragraph cell as a $269 container (#261)`.

### Task 4: Generator: the chapter-title cut reads paragraph cells

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`_cut_row_text`, `_drop_table_rows`)
- Test: `tests/unit/test_native_generator.py` (next to the existing title-cut tests; search `_cut_title_from_table`)

**Interfaces:**
- Consumes: `cell["paragraphs"]`, `cell["text"] == " ".join(paragraph texts)`.

- [ ] **Step 1: Write the failing tests:** `_cut_title_from_table` on a table whose first row's first cell has paragraphs `["Mein.", "Du bist mein,"]` and title `"Mein."` leaves paragraphs `["Du bist mein,"]` and cell text `"Du bist mein,"`. A cut that ends inside a paragraph trims it (title `"Mein. Du"` leaves `["bist mein,"]`). A cut covering the whole cell empties it, so the row drops when nothing else holds text, and the cell's and paragraphs' anchor keys move as `_drop_table_rows` moves cell keys.

- [ ] **Step 2: Run them and see them fail.**

- [ ] **Step 3: Implement.** In `_cut_row_text`, when a covered or reached cell has `paragraphs`, run the same cut over its paragraphs, using `" ".join` positions (+1 per separator). Drop the paragraphs it covers; trim the one it reaches into, rebasing spans and offsets exactly as the text-cell branch does. Recompute `cell["text"]`. A cell left with no paragraphs gets `text = ""` and `paragraphs = []`, and `_emit_table_chunks` writes it as an empty text cell (`" "`), as now. In `_drop_table_rows`, also move `p["anchor_keys"]` of every paragraph of the dropped rows.

- [ ] **Step 4: Run them and see them pass**, then the full suite.

- [ ] **Step 5: Commit** — `fix: the chapter-title cut reads table cells holding paragraphs (#261)`.

### Task 5: Golden fixture and tier 2

**Files:**
- Modify: `tests/fixtures/golden/inputs.py` (add `make_table_paragraph_cells` and register it)
- Create: `tests/fixtures/golden/expected/table_paragraph_cells.kfx` (via `python -m tests.fixtures.golden.regenerate`)

- [ ] **Step 1:** Add an input whose chapter holds a one-column table with a heading, three verse `<p>`s and a loose line number, and a two-column table with a text cell (two `<p>`, one with an `id`) beside a notes cell. Follow `make_table_layout`'s pattern in `inputs.py`.
- [ ] **Step 2:** Regenerate. `git diff --stat tests/fixtures/golden/expected/` shows only the new file.
- [ ] **Step 3:** Run `pytest -m tier3`, `tier3_strict` and `tests/integration/test_kfxlib_diff.py`. KFX Input must decode the new golden with no new error kinds, and its EPUB must show one `<p>` per paragraph inside the cell's `<td>`.
- [ ] **Step 4: Commit** — `test: golden for table cells holding paragraphs (#261)`.

### Task 6: Corpus check, gate book and docs

**Files:**
- Modify: `research/make_table_sideload.py` (three chapters, checklist items)
- Modify: `tests/device/checklist.py` (`native_tables` procedure: a long one-column poem table)
- Modify: `README.md` (Limitations: multi-paragraph cells are tables now)
- Modify: `docs/superpowers/specs/2026-09-30-native-table-layout-design.md` (note that #261 changed the "cells holding more than one block" scope line)

- [ ] **Step 1:** Gutenberg 90 A/B against `main` through the shim, using the scratch `corpus_ab_254.py` approach. Expected: words identical in 90 books; books without such tables byte-identical; tables 510/157 → 641/26 (131 move); every `paragraphs` cell written as a `$269` container.
- [ ] **Step 2:** The same through real calibre 9.14.0 with isolated configs.
- [ ] **Step 3:** Add gate chapters 15–17: a one-column poem table running over two pages (heading, about 60 verse lines, line numbers every 5); a two-column text-and-notes table with a note link inside a cell; a cell holding a numbered list. Add checklist items: every line its own paragraph; the long cell pages both ways with no line lost or repeated; links in cells land; list markers shown.
- [ ] **Step 4:** Update the checklist entry, the README and the spec note. Commit — `test: gate chapters for table cells holding paragraphs (#261)`.
- [ ] **Step 5:** Build the gate pair (`.venv/bin/python research/make_table_sideload.py`), open the PR with the corpus results, and run the device gate on the Voyage, Oasis and Paperwhite. If a long cell does not page, rebuild chapter 15 without the viewer properties and compare before changing anything.
