# Native Table Layout (#219) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write simple HTML tables as real KFX tables (`$278` → row groups → `$279` rows → cells), laid out by the Kindle. Tables the first version can't express keep 5.8.8's one paragraph per row. A plugin setting turns native tables off.

**Architecture:**
- **Converter:** it turns an eligible `<table>` into one structured block, `{"type": "table", ...}`.
- **Generator:** it keeps its flat list of chunks, and a table adds *marker chunks* (`open` / `close`) around the cell text chunks.
- **Positions:** an `open` chunk gets one position (length 1 in `$265`, listed in `$264`/`$550`), and a `close` chunk gets none.
- **Storyline:** `build_fragment_259` turns the markers into nested entries with a stack.
- **Merge gate:** nothing merges before a three-device gate (Task 10), because the one earlier nesting attempt (5.3.0) lost the TOC button.

**Tech Stack:** Python 3 (plugin runs inside calibre), lxml, the vendored `kfxlib_minimal` Ion writer, pytest, ruff 0.15.1, calibre 9.14.0 for the gated tests, Kindle Previewer 3.106 plus KFX Input's `decode_book()` as the reference.

**Spec:** `docs/superpowers/specs/2026-09-30-native-table-layout-design.md`

## Global Constraints

- **Tests:** run with `.venv/bin/python -m pytest` (system python3 lacks hypothesis). The default run excludes `slow`, `device` and `tier3_strict`, so run `-m tier3_strict` and `-m slow` explicitly where a task says so.
- **Lint:** ruff 0.15.1, both `ruff check .` and `ruff format --check .`, before every push.
- **Books:** no in-copyright title, author, ISBN or identifying file name in the repo, commits or GitHub. Gutenberg books may be named.
- **Test files stay out of git:** test books and KFX files live in `test_books/`. `.epub` and `.kfx` are git-ignored there, but other file types are not.
- **Branch:** `feat/219-native-tables`, from `main` at 77e852d (v5.8.8). **Do not merge before Task 10 passes.**
- **With the switch on,** or for a book with no tables, output must be byte-identical to 5.8.8. `tier3_strict` checks this for every golden file except the ones this plan regenerates on purpose.
- **Match Amazon:** every new KFX structure copies Amazon's (spec, "What Amazon writes"). Don't add keys Amazon doesn't emit; KFX Input rejects a node with leftover keys.

## Review Focus

These are the input classes most likely to bite a reader that no task's happy path covers. Each has its test in the task named.

1. **A TOC entry, or several, pointing into a table** (the #225 shape). The chapter must start at the table, with no empty chapters and no crash. Test: Task 4, `test_toc_entries_into_one_table_do_not_make_empty_chapters`.
2. **A chapter whose first content is a table, with no heading.** Its TOC target must be a text leaf, never a container (the 5.3.0 lesson). Test: Task 5, `test_chapter_start_skips_container_markers`.
3. **An empty cell.** It must still be a cell, so later columns don't shift left. Test: Task 5, `test_empty_cell_is_still_a_cell`.
4. **A cell long enough to be cut at 2,000 characters.** That cut would split one cell into two. The table must fall back to rows. Test: Task 2, `test_a_cell_over_the_chunk_size_falls_back`.
5. **The notes layout (#223), anchors after rows.** Links must still land on their own note once the table is native. Tests: Task 3, `test_notes_layout_anchor_after_row_stays_with_row`, and Task 9's calibre-gated `test_each_note_link_lands_on_its_own_note`, walked recursively.

---

### Task 1: A recursive storyline walker for tests

The test suite reads only the top level of `$259`'s `$146`. Once tables nest, cell text sits two to three levels down, and flat readers would lose it without failing. This task changes no plugin code.

**Files:**
- Modify: `tests/_kfx_introspect.py`
- Modify:
  - `tests/unit/test_position_map.py:318-325` (`_image_entry_positions`);
  - `tests/unit/test_native_generator.py` walkers at `:428`, `:546`, `:615`, `:769`, `:869`, `:982`;
  - `tests/integration/test_golden_corpus.py:217-222`;
  - `tests/integration/test_calibre_table_rows.py:176` (`_paragraphs`) and `:331` (`_link_landings`).
- Test: `tests/unit/test_kfx_introspect.py` (create)

**Interfaces:**
- Produces: `iter_entries(nodes) -> Iterator[IonStruct]`. It yields every storyline entry depth-first, pre-order: a container before its children.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_kfx_introspect.py
import pytest

from tests._kfx_introspect import iter_entries


def _node(eid, *children):
    node = {"$155": eid}
    if children:
        node["$146"] = list(children)
    return node


@pytest.mark.unit
def test_iter_entries_walks_nested_containers_in_reading_order():
    tree = [
        _node(1),
        _node(2, _node(3, _node(4, _node(5), _node(6)))),
        _node(7),
    ]
    assert [e["$155"] for e in iter_entries(tree)] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.unit
def test_iter_entries_ignores_a_non_list_146():
    # An entry's $145 content reference has no $146, but a $145 *fragment*
    # holds strings under $146; the walker must never descend into strings.
    assert [e["$155"] for e in iter_entries([{"$155": 1, "$146": "text"}])] == [1]
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/test_kfx_introspect.py -v`
Expected: FAIL with `ImportError: cannot import name 'iter_entries'`.

- [ ] **Step 3: Implement**

Append to `tests/_kfx_introspect.py`:

```python
def iter_entries(nodes):
    """Every storyline entry under `nodes`, depth-first, containers first.

    `$259`'s `$146` is flat for ordinary text, but a table is a `$278` whose
    `$146` holds row groups, rows and cells (#219). A reader that looks only
    at the top level silently loses the cell text, so tests walk with this.
    """
    for node in nodes or ():
        yield node
        children = node.get("$146") if hasattr(node, "get") else None
        if isinstance(children, list):
            yield from iter_entries(children)
```

- [ ] **Step 4: Migrate the flat walkers**

In each walker listed under **Files**, replace the one-level loop over a storyline's children, `for entry in val(story)["$146"]:` (or the local equivalent, such as `for e in v["$146"]:`), with `for entry in iter_entries(val(story)["$146"]):`, and add `iter_entries` to the file's `from tests._kfx_introspect import …` line.
- Leave a walker unchanged if it deliberately checks the *top level*, for example one that asserts the storyline's first entry carries `$790`. If you leave one, add a short comment saying why.
- In `tests/integration/test_calibre_table_rows.py`, keep `_paragraphs` returning text entries only: skip entries without `$145`, as it already does.

- [ ] **Step 5: Run the suites and see them pass unchanged**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider && .venv/bin/python -m pytest -q -m tier3_strict && .venv/bin/python -m pytest tests/integration/test_calibre_table_rows.py -m slow -q`
Expected: every test passes. The walkers return the same entries, because no storyline nests yet.

- [ ] **Step 6: Commit**

```bash
git add tests/_kfx_introspect.py tests/unit/test_kfx_introspect.py tests/unit/test_position_map.py tests/unit/test_native_generator.py tests/integration/test_golden_corpus.py tests/integration/test_calibre_table_rows.py
git commit -m "test: walk the storyline recursively before tables nest (#219)"
```

---

### Task 2: Which tables go native

**Files:**
- Modify: `plugin/kfxgen/converter.py`. Add the constants and `_table_is_native` next to `_CELL_TAGS` (~line 50) and `_anchors_follow_rows` (~line 590).
- Test: `tests/unit/test_converter.py`, in a new section after the "note anchors between table rows" tests.

**Interfaces:**
- Produces: `_table_is_native(table) -> bool`, and `_MAX_NATIVE_CELL_CHARS = 2000`.

- [ ] **Step 1: Write the failing tests**

```python
# --- native table eligibility (#219) ----------------------------------------


def _first_table(html):
    return next(e for e in _doc(html).iter() if _conv._local_tag(e.tag) == "table")


@pytest.mark.unit
def test_a_plain_table_goes_native():
    assert _conv._table_is_native(_first_table(_ISSUE_219_TABLE))


@pytest.mark.unit
def test_thead_tbody_tfoot_colspan_rowspan_go_native():
    assert _conv._table_is_native(
        _first_table(
            "<table><thead><tr><th colspan='2'>H</th></tr></thead>"
            "<tbody><tr><td rowspan='2'>a</td><td>b</td></tr><tr><td>c</td></tr></tbody>"
            "<tfoot><tr><td>f</td><td>g</td></tr></tfoot></table>"
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        "<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>",
        '<table><tr><td><img src="a.png"/></td></tr></table>',
        "<table><tr><td><svg/></td></tr></table>",
        "<table><tr><td><p>one</p><p>two</p></td></tr></table>",
        "<table><caption>only a caption</caption></table>",
        "<table><td>cell with no row</td></table>",
    ],
    ids=["nested", "img", "svg", "two-paragraph-cell", "no-rows", "cell-outside-row"],
)
def test_tables_that_fall_back_to_rows(html):
    assert not _conv._table_is_native(_first_table(html))


@pytest.mark.unit
def test_a_cell_over_the_chunk_size_falls_back():
    # The generator cuts text at CHUNK_SIZE (2,000); inside a table that cut
    # would turn one cell into two and shift every later column (#226).
    long_cell = "x" * (_conv._MAX_NATIVE_CELL_CHARS + 1)
    assert not _conv._table_is_native(
        _first_table(f"<table><tr><td>{long_cell}</td></tr></table>")
    )


@pytest.mark.unit
def test_max_native_cell_chars_matches_the_generator_chunk_size():
    from kfxgen.native_generator import NativeKFXGenerator

    assert _conv._MAX_NATIVE_CELL_CHARS == NativeKFXGenerator.CHUNK_SIZE
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -q -k "native or falls_back or chunk_size" -p no:cacheprovider`
Expected: FAIL with `AttributeError: module 'kfxgen.converter' has no attribute '_table_is_native'`.

- [ ] **Step 3: Implement**

In `converter.py`, after `_CELL_TAGS`:

```python
# Native table layout (#219). A table the first version can't express
# correctly keeps 5.8.8's one paragraph per row instead.

#: Block-level tags a cell may hold at most one of. Two or more would need a
#: cell holding several paragraphs, which v1 doesn't write.
_CELL_BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
    "ul", "ol", "li", "dl", "section", "article", "figure",
}
#: Content v1 can't place inside a cell.
_NON_TEXT_TAGS = {
    "img", "svg", "image", "math", "video", "audio", "object", "embed", "iframe",
}
#: `NativeKFXGenerator.CHUNK_SIZE`. Longer text is cut into two storyline
#: entries, which inside a row would be two cells (#226).
_MAX_NATIVE_CELL_CHARS = 2000
```

After `_anchors_follow_rows`:

```python
def _table_is_native(table):
    """True when `table` can be written as a real KFX table (#219).

    Anything else keeps rows as paragraphs: a nested table, an image or other
    object, a cell holding more than one block, a cell longer than the
    generator's chunk size, a cell outside a row, or no rows at all.
    """
    rows = 0
    for e in table.iter():
        tag = _local_tag(e.tag)
        if tag is None:
            continue
        if tag == "tr":
            rows += 1
        elif tag == "table" and e is not table:
            return False
        elif tag in _NON_TEXT_TAGS:
            return False
        elif tag in _CELL_TAGS:
            if _local_tag(e.getparent().tag) != "tr":
                return False
            blocks = [
                d for d in e.iter()
                if d is not e and _local_tag(d.tag) in _CELL_BLOCK_TAGS
            ]
            if len(blocks) > 1:
                return False
            if len("".join(e.itertext())) > _MAX_NATIVE_CELL_CHARS:
                return False
    return rows > 0
```

- [ ] **Step 4: Run them to see them pass**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: decide which tables can be written as native KFX tables (#219)"
```

---

### Task 3: The structured table block

**Files:**
- Modify: `plugin/kfxgen/converter.py` (new helpers after `_table_is_native`)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `_table_is_native`, `_anchors_follow_rows`, `_is_empty_anchor`, `_own_anchor_ids`, `_walk_inline(elem, flags=frozenset(), style_resolver=None, is_root=True, base_href=None)`, `normalize_runs_with_anchors(segments) -> (text, spans, {id: offset})`, `compute_block_style(css)`, `_dedupe_keep_order`.
- Produces:
  - `_table_block(table, style_resolver=None, base_href=None) -> (caption_block | None, table_block, trailing_ids)`;
  - `table_block` has the shape given in the spec ("Converter: a structured table block");
  - each row is `{"group": "head" | "body" | "foot", "anchor_ids": [...], "cells": [...]}`;
  - each cell is `{"text", "spans", "anchor_ids", "anchor_offsets", "block_style", "header", "colspan", "rowspan"}`.

- [ ] **Step 1: Write the failing tests**

```python
def _block(html, **kw):
    caption, table, trailing = _conv._table_block(_first_table(html), **kw)
    return caption, table, trailing


def _cells(table):
    return [[c["text"] for c in r["cells"]] for r in table["table"]["rows"]]


@pytest.mark.unit
def test_table_block_keeps_rows_and_cells():
    _, table, _ = _block(_ISSUE_219_TABLE)
    assert table["type"] == "table"
    assert _cells(table) == [["Year", "A", "B"], ["1", "100", "200"], ["2", "110", "220"]]
    assert table["text"] == "Year A B\n1 100 200\n2 110 220"
    assert [r["group"] for r in table["table"]["rows"]] == ["body"] * 3


@pytest.mark.unit
def test_table_block_row_groups_header_cells_and_spans():
    _, table, _ = _block(
        "<table><thead><tr><th colspan='2'>H</th></tr></thead>"
        "<tbody><tr><td rowspan='2'>a</td><td>b</td></tr><tr><td>c</td></tr></tbody>"
        "<tfoot><tr><td>f</td><td>g</td></tr></tfoot></table>"
    )
    rows = table["table"]["rows"]
    assert [r["group"] for r in rows] == ["head", "body", "body", "foot"]
    head = rows[0]["cells"][0]
    assert (head["header"], head["colspan"], head["rowspan"]) == (True, 2, 1)
    assert rows[1]["cells"][0]["rowspan"] == 2


@pytest.mark.unit
@pytest.mark.parametrize("raw, expected", [("0", 1), ("-3", 1), ("x", 1), ("5000", 1000), (" 3 ", 3)])
def test_span_attributes_are_clamped(raw, expected):
    _, table, _ = _block(f"<table><tr><td colspan='{raw}'>a</td></tr></table>")
    assert table["table"]["rows"][0]["cells"][0]["colspan"] == expected


@pytest.mark.unit
def test_cell_emphasis_and_anchor_offsets_are_cell_relative():
    _, table, _ = _block(
        '<table><tr><td>ab</td><td>x <em>cd</em> <a id="k"></a>e</td></tr></table>'
    )
    cell = table["table"]["rows"][0]["cells"][1]
    assert cell["text"] == "x cd e"
    assert cell["spans"] == [(2, 2, frozenset({I}))]
    assert cell["anchor_offsets"]["k"] == 5
    assert "k" in table["anchor_ids"]  # block level too, for chapter assembly


@pytest.mark.unit
def test_notes_layout_anchor_after_row_stays_with_row():
    _, table, _ = _block(
        '<table><tr><td>1.</td><td>First.</td></tr><a id="n1"></a>'
        '<tr><td>2.</td><td>Second.</td></tr><a id="n2"></a></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [["n1"], ["n2"]]


@pytest.mark.unit
def test_anchor_before_each_row_belongs_to_the_next_row():
    _, table, trailing = _block(
        '<table><a id="n1"></a><tr><td>1.</td></tr><a id="n2"></a><tr><td>2.</td></tr></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [["n1"], ["n2"]]
    assert trailing == []


@pytest.mark.unit
def test_an_anchor_only_after_the_last_row_carries_past_the_table():
    _, table, trailing = _block(
        '<table><tr><td>a</td></tr><tr><td>b</td></tr><a id="ch2"></a></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [[], []]
    assert trailing == ["ch2"]


@pytest.mark.unit
def test_caption_becomes_its_own_paragraph():
    caption, table, _ = _block(
        "<table><caption>Census</caption><tr><td>a</td></tr></table>"
    )
    assert caption["text"] == "Census"
    assert "Census" not in table["text"]


@pytest.mark.unit
def test_table_own_id_is_kept_separately():
    _, table, _ = _block('<table id="t"><tr id="r"><td id="c">a</td></tr></table>')
    assert table["table"]["anchor_ids"] == ["t"]
    assert table["table"]["rows"][0]["anchor_ids"] == ["r"]
    assert table["table"]["rows"][0]["cells"][0]["anchor_ids"] == ["c"]
    assert table["anchor_ids"] == ["t", "r", "c"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -q -k "table_block or span_attributes or cell_emphasis or notes_layout or anchor_before_each_row_belongs or only_after_the_last_row_carries or caption_becomes or own_id_is_kept" -p no:cacheprovider`
Expected: FAIL with `AttributeError: ... '_table_block'`.

- [ ] **Step 3: Implement**

```python
_ROW_GROUPS = {"thead": "head", "tbody": "body", "tfoot": "foot"}


def _span_attr(cell, name):
    """colspan/rowspan as an int in 1..1000; anything malformed counts as 1."""
    try:
        n = int((cell.get(name) or "1").strip())
    except ValueError:
        return 1
    return min(max(n, 1), 1000)


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
    }


def _table_block(table, style_resolver=None, base_href=None):
    """A native table as one block, plus its caption and any anchors left over.

    Returns (caption_block or None, table_block, trailing_ids). Anchors between
    rows follow 5.8.8's rule (`_anchors_follow_rows`): in calibre's notes
    layout an anchor after a row belongs to that row, otherwise to the next
    row; anchors with no row left to take them carry past the table. (#219)
    """
    rows, carry, caption = [], [], None

    def take(container, group):
        nonlocal carry, caption
        follow = _anchors_follow_rows(container)
        last = None
        for child in container:
            tag = _local_tag(child.tag)
            if tag in _ROW_GROUPS:
                take(child, _ROW_GROUPS[tag])
                last = None
            elif tag == "caption" and caption is None:
                text, spans, marks = normalize_runs_with_anchors(
                    _walk_inline(child, style_resolver=style_resolver, base_href=base_href)
                )
                if text:
                    ids = list(marks)
                    caption = {
                        "text": text, "spans": spans, "block_style": None,
                        "anchor_ids": ids,
                        "anchor_offsets": {a: marks.get(a, 0) for a in ids},
                    }
            elif tag == "tr":
                last = {
                    "group": group,
                    "anchor_ids": carry + _own_anchor_ids(child),
                    "cells": [
                        _table_cell(c, style_resolver, base_href)
                        for c in child
                        if _local_tag(c.tag) in _CELL_TAGS
                    ],
                }
                carry = []
                rows.append(last)
            elif _is_empty_anchor(child):
                ids = _own_anchor_ids(child)
                if follow and last is not None:
                    last["anchor_ids"].extend(ids)
                else:
                    carry.extend(ids)

    take(table, "body")
    own = _own_anchor_ids(table)
    every = _dedupe_keep_order(
        own
        + [a for r in rows for a in r["anchor_ids"]]
        + [a for r in rows for c in r["cells"] for a in c["anchor_ids"]]
    )
    block = {
        "type": "table",
        "text": "\n".join(
            " ".join(c["text"] for c in r["cells"] if c["text"]) for r in rows
        ),
        "spans": [],
        "block_style": None,
        "anchor_ids": every,
        "anchor_offsets": dict.fromkeys(every, 0),
        "table": {"anchor_ids": own, "rows": rows},
    }
    return caption, block, carry
```

- [ ] **Step 4: Run them to see them pass**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: build a structured block for a native table (#219)"
```

---

### Task 4: Put native tables into the block stream

**Files:**
- Modify: `plugin/kfxgen/converter.py`:
  - `extract_blocks_from_html` (signature ~line 999, `_walk` ~line 1150, `_walk_element` after the background-image check ~line 1190);
  - `_attach_anchor_keys` (~line 772);
  - `extract_chapters_from_oeb` (~line 1860).
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `_table_is_native`, `_table_block`.
- Produces:
  - `extract_blocks_from_html(..., native_tables=False)`;
  - `extract_chapters_from_oeb(oeb_book, log, metadata=None, cover_href=None, native_tables=False)`;
  - each table dict, row and cell gains `anchor_keys` / `anchor_offsets` keyed `"<file>#<id>"`, like a block's.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_native_tables_become_one_block_between_paragraphs():
    blocks = _conv.extract_blocks_from_html(
        _doc(f"<p>Before.</p>{_ISSUE_219_TABLE}<p>After.</p>"), native_tables=True
    )
    assert [b.get("type", "text") for b in blocks] == ["text", "table", "text"]


@pytest.mark.unit
def test_native_tables_off_keeps_rows_as_paragraphs():
    blocks = _conv.extract_blocks_from_html(_doc(_ISSUE_219_TABLE))
    assert [b["text"] for b in blocks] == ["Year A B", "1 100 200", "2 110 220"]


@pytest.mark.unit
def test_an_ineligible_table_falls_back_to_rows_with_native_tables_on():
    blocks = _conv.extract_blocks_from_html(
        _doc('<table><tr><td>a</td><td><img src="i.png"/></td></tr></table>'),
        native_tables=True,
    )
    assert all(b.get("type", "text") != "table" for b in blocks)


@pytest.mark.unit
def test_only_fallback_tables_count_for_the_warning():
    seen = []
    _conv.extract_blocks_from_html(
        _doc(f'{_ISSUE_219_TABLE}<table><tr><td><img src="i.png"/></td></tr></table>'),
        native_tables=True,
        tables_seen=seen,
    )
    assert len(seen) == 1


@pytest.mark.unit
def test_anchor_keys_reach_rows_and_cells():
    blocks = _conv.extract_blocks_from_html(
        _doc('<table id="t"><tr id="r"><td id="c">a</td></tr></table>'),
        native_tables=True,
        base_href="ch.xhtml",
    )
    tbl = blocks[0]["table"]
    assert tbl["anchor_keys"][-1] == "ch.xhtml#t"
    assert tbl["rows"][0]["anchor_keys"] == ["ch.xhtml#r"]
    assert tbl["rows"][0]["cells"][0]["anchor_keys"] == ["ch.xhtml#c"]
    assert tbl["rows"][0]["cells"][0]["anchor_offsets"] == {"ch.xhtml#c": 0}


@pytest.mark.unit
def test_toc_entries_into_one_table_do_not_make_empty_chapters():
    # Review Focus 1: several TOC entries pointing at rows of one native table
    # all resolve to the table's block, so they must not produce empty chapters.
    notes = (
        "<p>Notes intro.</p><table>"
        + "".join(
            f'<tr><td>{n}.</td><td>Note {n}.</td></tr><a id="n{n}"></a>'
            for n in (1, 2, 3)
        )
        + "</table>"
    )
    oeb = _contents_book(("Chapter One", "<p>One.</p>"), ("Notes", notes))
    oeb.toc = oeb.toc + [_TOCNode(f"{n}", f"ch1.xhtml#n{n}") for n in (1, 2, 3)]
    chapters = extract_chapters_from_oeb(oeb, _silent_log(), native_tables=True)
    assert all(c.get("blocks") or c.get("text", "").strip() for c in chapters)
    tables = [b for c in chapters for b in c.get("blocks") or () if b.get("type") == "table"]
    assert len(tables) == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -q -k "native_tables or ineligible_table or only_fallback or reach_rows_and_cells or toc_entries_into_one_table" -p no:cacheprovider`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'native_tables'`.

- [ ] **Step 3: Implement**

1. **The parameter.** Add `native_tables=False` to `extract_blocks_from_html`'s signature, and to its docstring: "When set, an eligible `<table>` becomes one `{"type": "table"}` block (#219); otherwise each row is its own paragraph."

2. **The native branch.** In `_walk_element`, directly after the background-image block's `return` and before `is_block = elem.tag in block_tags`:

```python
        if native_tables and _local_tag(elem.tag) == "table" and _table_is_native(elem):
            caption, table, trailing = _table_block(elem, style_resolver, base_href)
            if caption is not None:
                ids = pending_ids[:] + caption["anchor_ids"]
                pending_ids.clear()
                caption["anchor_ids"] = _dedupe_keep_order(ids)
                caption["anchor_offsets"] = {
                    a: caption["anchor_offsets"].get(a, 0) for a in caption["anchor_ids"]
                }
                blocks.append(caption)
            if pending_ids:
                # Anchors carried from before the table name its start.
                table["table"]["anchor_ids"] = _dedupe_keep_order(
                    pending_ids + table["table"]["anchor_ids"]
                )
                table["anchor_ids"] = _dedupe_keep_order(pending_ids + table["anchor_ids"])
                table["anchor_offsets"] = dict.fromkeys(table["anchor_ids"], 0)
                pending_ids.clear()
            blocks.append(table)
            pending_ids.extend(trailing)
            return
```

3. **The warning count.** In `_walk`, count a table only when it fell back:

```python
            if (
                tables_seen is not None
                and _local_tag(elem.tag) == "table"
                and len(blocks) > start
                and not any(b.get("type") == "table" for b in blocks[start:])
            ):
                tables_seen.append(blocks[start])
```

4. **Anchor keys for rows and cells.** In `_attach_anchor_keys`, inside the loop after the block's own keys are set:

```python
        tbl = block.get("table")
        if tbl:
            for part in [tbl] + tbl["rows"] + [c for r in tbl["rows"] for c in r["cells"]]:
                by_part = part.get("anchor_offsets") or {}
                ids = part.get("anchor_ids", ())
                part["anchor_keys"] = [f"{normalized}#{a}" for a in ids] if normalized else []
                part["anchor_offsets"] = (
                    {f"{normalized}#{a}": by_part.get(a, 0) for a in ids} if normalized else {}
                )
```

   After the bare-filename key is given to `blocks[0]`, also give it to the table's own keys when `blocks[0]` is a table:

```python
        if blocks[0].get("table"):
            blocks[0]["table"]["anchor_keys"] = [normalized] + blocks[0]["table"]["anchor_keys"]
```

5. **The chapter-level parameter.** Add `native_tables=False` to `extract_chapters_from_oeb`, and pass `native_tables=native_tables` in its `extract_blocks_from_html(...)` call.

6. **Log how many tables went native.** Observability: a user report then says which path each table took. In `_warn_flattened_tables(chapters, table_blocks, log)`, before its early `return`:

```python
    native = sum(
        1 for ch in chapters for b in ch.get("blocks") or () if b.get("type") == "table"
    )
    if native:
        log.info(f"  {native} table{'s' if native != 1 else ''} written as native Kindle tables (#219)")
```

   Test, in Step 1's list:

```python
@pytest.mark.unit
def test_native_tables_are_counted_in_the_log():
    log = _silent_log()
    log.info = MagicMock()
    extract_chapters_from_oeb(_table_book(f"<p>One.</p>{_ISSUE_219_TABLE}"), log, native_tables=True)
    assert any("1 table written as native" in str(c) for c in log.info.call_args_list)
```

- [ ] **Step 4: Run them, and the whole converter file, to see them pass**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -q -p no:cacheprovider`
Expected: all pass. The existing rows-as-paragraphs tests pass unchanged, because `native_tables` defaults to False.

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: emit a native table as one block when asked (#219)"
```

---

### Task 5: Marker chunks and positions in the generator

**Files:**
- Modify: `plugin/kfxgen/native_generator.py`:
  - `_build_chapter_content`: the per-block loop at ~line 3029, position assignment at ~3134-3142, `chapter_start_positions` at ~3160;
  - `_build_position_data` (~1090-1138).
- Test: `tests/unit/test_native_generator.py` and `tests/unit/test_position_map.py`

**Interfaces:**
- Consumes: table blocks from Task 4. Tests build them directly with the helper below.
- Produces:
  - chunk shapes `{"type": "open", "node": "table"|"head"|"body"|"foot"|"row", "anchor_keys", "anchor_offsets"}` and `{"type": "close"}`;
  - cell text chunks carry `"cell": {"header", "colspan", "rowspan"}`;
  - `ch_data["chunk_positions"][i]` is `None` for a `close` chunk.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_native_generator.py`:

```python
def _table_block(rows, own_keys=()):
    """A converter-shaped native table block (#219); rows are lists of cell texts."""
    return {
        "type": "table",
        "text": "\n".join(" ".join(r) for r in rows),
        "spans": [], "block_style": None,
        "anchor_ids": [], "anchor_keys": list(own_keys), "anchor_offsets": {},
        "table": {
            "anchor_ids": [], "anchor_keys": list(own_keys), "anchor_offsets": {},
            "rows": [
                {"group": "body", "anchor_ids": [], "anchor_keys": [], "cells": [
                    {"text": t, "spans": [], "anchor_ids": [], "anchor_keys": [],
                     "anchor_offsets": {}, "block_style": None, "header": False,
                     "colspan": 1, "rowspan": 1}
                    for t in r
                ]}
                for r in rows
            ],
        },
    }


def _content(blocks, title="Chapter"):
    gen = NativeKFXGenerator()
    return gen._build_chapter_content(
        [{"title": title, "text": "x", "blocks": blocks}]
    )


@pytest.mark.unit
def test_table_emits_marker_chunks_around_cells():
    ch = _content([{"text": "Before.", "spans": []}, _table_block([["a", "b"], ["c", "d"]])])
    kinds = [(c["type"], c.get("node"), c.get("text")) for c in ch["all_chunks"]]
    assert kinds == [
        ("text", None, "Chapter"),
        ("text", None, "Before."),
        ("open", "table", None), ("open", "body", None),
        ("open", "row", None), ("text", None, "a"), ("text", None, "b"), ("close", None, None),
        ("open", "row", None), ("text", None, "c"), ("text", None, "d"), ("close", None, None),
        ("close", None, None), ("close", None, None),
    ]


@pytest.mark.unit
def test_close_markers_take_no_position_and_opens_take_one():
    ch = _content([_table_block([["a"]])])
    pos = [(c["type"], p) for c, p in zip(ch["all_chunks"], ch["chunk_positions"])]
    assert [p is None for t, p in pos if t == "close"] == [True, True, True]
    assert all(p is not None for t, p in pos if t != "close")
    real = [p for _, p in pos if p is not None]
    assert real == sorted(real) and len(set(real)) == len(real)


@pytest.mark.unit
def test_empty_cell_is_still_a_cell():
    ch = _content([_table_block([["a", "", "c"]])])
    cells = [c for c in ch["all_chunks"] if "cell" in c]
    assert [c["text"] for c in cells] == ["a", " ", "c"]


@pytest.mark.unit
def test_chapter_start_skips_container_markers():
    # Review Focus 2: a chapter whose heading is omitted and whose first
    # content is a table must still target a text leaf, never a container.
    gen = NativeKFXGenerator()
    ch = gen._build_chapter_content(
        [{"title": "T", "text": "x", "_omit_title_heading": True,
          "blocks": [_table_block([["a"]])]}]
    )
    first = ch["chapter_start_positions"][0]
    idx = ch["chunk_positions"].index(first)
    assert ch["all_chunks"][idx]["type"] == "text"
```

In `tests/unit/test_position_map.py`:

```python
@pytest.mark.unit
def test_table_containers_take_one_position_and_cells_their_length(tmp_path):
    # Amazon's rule (Kindle Previewer 3.106): each container 1 position, each
    # cell's text its length, every eid in $264 and $265 (#219).
    from tests.unit.test_native_generator import _table_block

    frags = _generate(tmp_path, [{"title": "T", "text": "x",
                                  "blocks": [_table_block([["ab", "cde"]])]}])
    entries_265 = _position_entries(frags)          # [(offset, eid)], this file's helper
    eids_264 = set(_section_eids_264(frags))         # this file's helper
    table_eids = _table_eids(frags)                  # table, body, row, two cells, in order
    offsets = {eid: off for off, eid in entries_265}
    t, body, row, c1, c2 = table_eids
    assert [offsets[body] - offsets[t], offsets[row] - offsets[body],
            offsets[c1] - offsets[row], offsets[c2] - offsets[c1]] == [1, 1, 1, 2]
    assert set(table_eids) <= eids_264
```

Add `_table_eids(frags)` to `test_position_map.py`. It returns the `$155` of every entry under the first `$278`, in order, using `iter_entries`. If `_generate`, `_position_entries` or `_section_eids_264` don't exist under those names, write them in this task: generate a KFX with `NativeKFXGenerator().generate_full_book(...)` into `tmp_path`, `load_fragments` it, and read `$265` / `$264` with `val(...)`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_native_generator.py tests/unit/test_position_map.py -q -k "marker or position or empty_cell or chapter_start_skips or containers_take" -p no:cacheprovider`
Expected: FAIL. The table block is treated as a paragraph, so there are no `open` chunks.

- [ ] **Step 3: Implement**

1. **Emit markers.** In `_build_chapter_content`, add this nested helper before the chapter loop. It closes over `all_chunks`:

```python
        def _emit_table_chunks(block):
            """Marker chunks around a native table's cells (#219). `open`
            becomes a container entry with one position; `close` ends it and
            takes none. Cells are ordinary text chunks with a `cell` key."""
            tbl = block["table"]
            inner = {
                k for r in tbl["rows"] for k in (r.get("anchor_keys") or [])
            } | {
                k for r in tbl["rows"] for c in r["cells"]
                for k in (c.get("anchor_keys") or [])
            }
            own = [k for k in (tbl.get("anchor_keys") or []) if k not in inner]
            all_chunks.append({"type": "open", "node": "table", "anchor_keys": own,
                               "anchor_offsets": dict.fromkeys(own, 0)})
            group = None
            for row in tbl["rows"]:
                if row["group"] != group:
                    if group is not None:
                        all_chunks.append({"type": "close"})
                    group = row["group"]
                    all_chunks.append({"type": "open", "node": group})
                keys = row.get("anchor_keys") or []
                all_chunks.append({"type": "open", "node": "row", "anchor_keys": keys,
                                   "anchor_offsets": dict.fromkeys(keys, 0)})
                for cell in row["cells"]:
                    all_chunks.append({
                        "type": "text",
                        "text": cell["text"] or " ",
                        "spans": (cell.get("spans") or []) if cell["text"] else [],
                        "block_style": cell.get("block_style"),
                        "anchor_keys": cell.get("anchor_keys") or [],
                        "anchor_offsets": cell.get("anchor_offsets") or {},
                        "cell": {"header": bool(cell.get("header")),
                                 "colspan": cell.get("colspan", 1),
                                 "rowspan": cell.get("rowspan", 1)},
                    })
                all_chunks.append({"type": "close"})
            if group is not None:
                all_chunks.append({"type": "close"})
            all_chunks.append({"type": "close"})
```

   At the top of the `for block in para_iter:` loop:

```python
                    for block in para_iter:
                        if block.get("type") == "table":
                            _emit_table_chunks(block)
                            continue
```

2. **Positions.** Replace the position loop:

```python
            for chunk_idx in range(start, end):
                if all_chunks[chunk_idx].get("type") == "close":
                    continue  # ends a container; takes no position (#219)
                chunk_positions[chunk_idx] = content_pos_id
                content_pos_id += self.CONTENT_POS_STEP
```

3. **Chapter start.** Replace `chapter_start_positions`:

```python
        def _first_leaf_position(start, end):
            # A TOC target must be a leaf with content, never a container:
            # 5.3.0 pointed the TOC at a wrapper and taps did nothing.
            for i in range(start, end):
                if all_chunks[i].get("type") in ("text", "image"):
                    return chunk_positions[i]
            return chunk_positions[start]

        chapter_start_positions = [
            _first_leaf_position(*chapter_chunk_ranges[i]) for i in range(len(chapters))
        ]
```

4. **The position data.** In `_build_position_data`'s chunk loop:

```python
            for chunk_idx in range(start, end):
                chunk = all_chunks[chunk_idx]
                kind = chunk.get("type") if isinstance(chunk, dict) else "text"
                if kind == "close":
                    continue
                if kind in ("image", "open"):
                    chunk_text_len = 1  # an image or a container takes one slot
                elif isinstance(chunk, dict):
                    chunk_text_len = len(chunk["text"])
                else:
                    chunk_text_len = len(chunk)  # legacy: bare string
```

   Then filter `None` out of `$264` and `$550`:

```python
            pids = [section_positions[ch_idx]] + [
                p for p in chunk_positions[start:end] if p is not None
            ]
```

```python
        all_position_ids.extend(p for p in chunk_positions if p is not None)
```

5. **Other readers.** Run `grep -n 'all_chunks\|chunk\["text"\]' plugin/kfxgen/native_generator.py` and look at every loop that reads a chunk's `text` without checking its `type`. Guard each one with `chunk.get("type") == "text"`, or skip `open`/`close`.

- [ ] **Step 4: Run them to see them pass, and check nothing else moved**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider && .venv/bin/python -m pytest -q -m tier3_strict`
Expected: all pass. `tier3_strict` stays byte-identical, because no golden fixture has a native table yet (the converter default is off).

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py tests/unit/test_position_map.py
git commit -m "feat: container markers and positions for native tables (#219)"
```

---

### Task 6: Nested storyline entries and table styles

**Files:**
- Modify: `plugin/kfxgen/native_generator.py`:
  - `build_fragment_259` (~1574);
  - new `build_table_style_157` and `build_cell_style_157` after `build_fragment_157` (~1271);
  - `_allocate_style` (~3280);
  - the per-entry list loop (~3500-3585) and the `build_fragment_259(...)` call (~3587).
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: the marker chunks and positions from Task 5.
- Produces:
  - `build_fragment_259(..., container_nodes=None)`. `chunk_kinds[i]` may be `"open"` or `"close"`, and `container_nodes[i]` names the node for an `open`.
  - `build_table_style_157(entity_name)`.
  - `build_cell_style_157(entity_name, align=None, bold=False, colspan=1, rowspan=1, font_family=None)`.

- [ ] **Step 1: Write the failing tests**

```python
def _storyline(tmp_path, blocks):
    gen = NativeKFXGenerator()
    out = tmp_path / "t.kfx"
    gen.generate_full_book("T", "A", [{"title": "Chapter", "text": "x", "blocks": blocks}],
                           output_path=str(out))
    frags = load_fragments(out)
    story = [f for f in frags if str(f.ftype) == "$259"][-1]
    styles = {str(f.fid): val(f) for f in frags if str(f.ftype) == "$157"}
    return val(story)["$146"], styles


@pytest.mark.unit
def test_table_nests_like_amazons_minimal_table(tmp_path):
    top, styles = _storyline(tmp_path, [{"text": "Before.", "spans": []},
                                         _table_block([["a1", "b1"], ["a2", "b2"]])])
    table = top[2]
    assert str(table["$159"]) == "$278"
    assert table["$150"] is False
    assert [str(k) for k in table] == ["$155", "$159", "$157", "$150", "$456", "$457", "$146"]
    (body,) = table["$146"]
    assert str(body["$159"]) == "$454" and set(map(str, body)) == {"$155", "$159", "$146"}
    rows = body["$146"]
    assert [str(r["$159"]) for r in rows] == ["$279", "$279"]
    assert all(set(map(str, r)) == {"$155", "$159", "$146"} for r in rows)
    cell = rows[0]["$146"][0]
    assert str(cell["$159"]) == "$269" and "$145" in cell
    cell_style = styles[str(cell["$157"])]
    assert str(cell_style["$633"]) == "$320"
    table_style = styles[str(table["$157"])]
    assert table_style["$83"] == 4286611584


@pytest.mark.unit
def test_790_goes_on_the_first_leaf_not_a_container(tmp_path):
    gen = NativeKFXGenerator()
    out = tmp_path / "t.kfx"
    gen.generate_full_book("T", "A", [{"title": "Chapter", "text": "x",
                                       "_omit_title_heading": True,
                                       "blocks": [_table_block([["a"]])]}],
                           output_path=str(out))
    story = [f for f in load_fragments(out) if str(f.ftype) == "$259"][-1]
    carriers = [e for e in iter_entries(val(story)["$146"]) if "$790" in e]
    assert len(carriers) == 1 and str(carriers[0]["$159"]) == "$269"


@pytest.mark.unit
def test_header_colspan_rowspan_reach_the_cell_style(tmp_path):
    block = _table_block([["H"], ["a"]])
    block["table"]["rows"][0]["group"] = "head"
    head = block["table"]["rows"][0]["cells"][0]
    head.update(header=True, colspan=2)
    block["table"]["rows"][1]["cells"][0]["rowspan"] = 2
    top, styles = _storyline(tmp_path, [block])
    table = next(e for e in top if str(e["$159"]) == "$278")
    head_group, body = table["$146"]
    assert (str(head_group["$159"]), str(body["$159"])) == ("$151", "$454")
    h = styles[str(head_group["$146"][0]["$146"][0]["$157"])]
    assert h["$148"] == 2 and str(h["$13"]) == "$361" and str(h["$34"]) == "$320"
    b = styles[str(body["$146"][0]["$146"][0]["$157"])]
    assert b["$149"] == 2 and "$148" not in b
```

No-table books staying byte-identical through the `build_fragment_259` rewrite is checked by Step 4's `tier3_strict` run, which compares every golden byte for byte.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_native_generator.py -q -k "nests_like_amazons or 790_goes or header_colspan" -p no:cacheprovider`
Expected: FAIL. The storyline is flat; `open`/`close` chunks have no entry kind yet (KeyError or a wrong shape).

- [ ] **Step 3: Implement**

1. **Style builders,** after `build_fragment_157`:

```python
    def build_table_style_157(self, entity_name):
        """$157 for a native table node, as Kindle Previewer writes it (#219)."""
        value = IonStruct(
            IS("$16"), IonStruct(IS("$307"), IonDecimal("1"), IS("$306"), IS("$505")),
            IS("$65"), IonStruct(IS("$307"), IonDecimal("100"), IS("$306"), IS("$314")),
            IS("$42"), IonStruct(IS("$307"), IonDecimal("1"), IS("$306"), IS("$310")),
            IS("$173"), IS(entity_name),
            IS("$83"), 4286611584,
        )
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_cell_style_157(
        self, entity_name, align=None, bold=False, colspan=1, rowspan=1, font_family=None
    ):
        """$157 for a table cell: Previewer's padding and vertical centring,
        plus the cell's own alignment, header weight and spans (#219)."""
        lh = lambda v: IonStruct(IS("$307"), IonDecimal(v), IS("$306"), IS("$310"))  # noqa: E731
        pct = lambda v: IonStruct(IS("$307"), IonDecimal(v), IS("$306"), IS("$314"))  # noqa: E731
        value = IonStruct(
            IS("$633"), IS("$320"),
            IS("$52"), lh("0.03125"), IS("$53"), pct("0.117"),
            IS("$54"), lh("0.03125"), IS("$55"), pct("0.117"),
            IS("$173"), IS(entity_name),
        )
        if align in ALIGN_MAP:
            value[IS("$34")] = IS(ALIGN_MAP[align])
        if bold:
            value[IS("$13")] = IS("$361")
        if colspan > 1:
            value[IS("$148")] = colspan
        if rowspan > 1:
            value[IS("$149")] = rowspan
        if font_family:
            value[IS("$11")] = font_family
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)
```

2. **`_allocate_style` takes a builder:**

```python
        def _allocate_style(kind, builder=None, **attrs):
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
```

3. **Per-entry lists.** Add `entry_nodes = []` next to the other lists. At the top of the chunk loop:

```python
                if chunk.get("type") in ("open", "close"):
                    node = chunk.get("node")
                    entry_styles.append(
                        _allocate_style("_tbl", builder=self.build_table_style_157)
                        if node == "table" else story_names[ch_idx]
                    )
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                    entry_kinds.append(chunk["type"])
                    entry_image_specs.append(None)
                    entry_emphasis_spans.append(None)
                    entry_nodes.append(node)
                    continue
                entry_nodes.append(None)
```

   Also append `None` to `entry_nodes` in the image branch before its `continue`.

   In the body-text `else:` branch, before `entry_styles.append(_allocate_style("", **attrs))`, route cells:

```python
                    cell = chunk.get("cell")
                    if cell is not None:
                        cattrs = {
                            "align": bs.get("align") or ("center" if cell["header"] else None),
                            "bold": bool(cell["header"]),
                            "colspan": cell["colspan"],
                            "rowspan": cell["rowspan"],
                        }
                        if fam:
                            cattrs["font_family"] = fam
                        entry_styles.append(
                            _allocate_style("_td", builder=self.build_cell_style_157, **cattrs)
                        )
                        entry_link_targets.append(None)
                        entry_link_styles.append(None)
                        entry_link_text_lengths.append(None)
                    else:
                        entry_styles.append(_allocate_style("", **attrs))
                        entry_link_targets.append(None)
                        entry_link_styles.append(None)
                        entry_link_text_lengths.append(None)
```

   The span handling after this branch is shared, so a cell's emphasis and links work as a paragraph's do. Pass `container_nodes=entry_nodes` to `build_fragment_259`.

4. **`build_fragment_259` builds the tree.** Add the `container_nodes=None` parameter and a module constant:

```python
#: Storyline node types for native table containers (#219).
_TABLE_NODE_TYPES = {
    "table": "$278", "head": "$151", "body": "$454", "foot": "$455", "row": "$279",
}
```

   In the body:
   - replace `children = []` with `root = []`, `stack = [root]` and `first_leaf = True`;
   - replace every `children.append(entry)` with `stack[-1].append(entry)`;
   - replace both `if i == 0: entry[IS("$790")] = 1` with:

```python
                if first_leaf:
                    entry[IS("$790")] = 1
                    first_leaf = False
```

   At the top of the loop body, after `kind = …`:

```python
            if kind == "close":
                stack.pop()
                continue
            if kind == "open":
                node = container_nodes[i]
                entry = IonStruct(IS("$155"), position, IS("$159"), IS(_TABLE_NODE_TYPES[node]))
                if node == "table":
                    self.symtab.create_local_symbol(story_name)
                    spacing = IonStruct(IS("$307"), IonDecimal("0.9"), IS("$306"), IS("$318"))
                    entry[IS("$157")] = IS(story_name)
                    entry[IS("$150")] = False
                    entry[IS("$456")] = spacing
                    entry[IS("$457")] = IonStruct(IS("$307"), IonDecimal("0.9"), IS("$306"), IS("$318"))
                entry[IS("$146")] = []
                stack[-1].append(entry)
                stack.append(entry[IS("$146")])
                continue
```

   The storyline value becomes `IonStruct(IS("$176"), IS(entity_name), IS("$146"), root)`. Update the docstring: the storyline is flat except that a native table (#219) nests `$278` → row groups → `$279` → cells.

- [ ] **Step 4: Run them to see them pass, and confirm no-table books are unchanged**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider && .venv/bin/python -m pytest -q -m tier3_strict`
Expected: all pass, and `tier3_strict` byte-identical.

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat: write native tables as nested storyline entries (#219)"
```

---

### Task 7: Links into rows and cells, and the feature version

**Files:**
- Modify: `plugin/kfxgen/native_generator.py`: `build_fragment_585` (~438) and its call in `generate_full_book` (~2177).
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: Tasks 5-6. The anchor code (`key_to_chunk`) already maps a key to the first chunk declaring it, and `open` rows and cell text chunks now carry keys.
- Produces:
  - `build_fragment_585(table_version=1)`;
  - `_table_feature_version(chapters) -> int` (3 if any native cell has `rowspan > 1`, else 1).

- [ ] **Step 1: Write the failing tests**

```python
def _anchor_targets(frags):
    return {str(val(f)["$180"]): val(f)["$183"] for f in frags if str(f.ftype) == "$266"}


@pytest.mark.unit
def test_link_into_a_cell_targets_the_cell_and_to_a_row_targets_the_row(tmp_path):
    from kfxgen.inline_style import make_link_flag

    block = _table_block([["a", "b"], ["c", "d"]])
    block["table"]["rows"][1]["anchor_keys"] = ["ch.xhtml#r2"]
    cell = block["table"]["rows"][0]["cells"][1]
    cell["anchor_keys"], cell["anchor_offsets"] = ["ch.xhtml#cb"], {"ch.xhtml#cb": 0}
    link = {"text": "see b and row 2", "spans": [
        (4, 1, frozenset({make_link_flag("ch.xhtml#cb")})),
        (10, 5, frozenset({make_link_flag("ch.xhtml#r2")})),
    ]}
    gen = NativeKFXGenerator()
    out = tmp_path / "t.kfx"
    gen.generate_full_book("T", "A", [{"title": "C", "text": "x", "blocks": [link, block]}],
                           output_path=str(out))
    frags = load_fragments(out)
    story = [f for f in frags if str(f.ftype) == "$259"][-1]
    by_eid = {e["$155"]: e for e in iter_entries(val(story)["$146"])}
    targets = [by_eid[t["$155"]] for t in _anchor_targets(frags).values()]
    kinds = sorted(str(e["$159"]) for e in targets)
    assert kinds == ["$269", "$279"]


@pytest.mark.unit
@pytest.mark.parametrize("rowspan, expected", [(1, 1), (2, 3)])
def test_yj_table_version_follows_rowspan(tmp_path, rowspan, expected):
    block = _table_block([["a"], ["b"]])
    block["table"]["rows"][0]["cells"][0]["rowspan"] = rowspan
    gen = NativeKFXGenerator()
    out = tmp_path / "t.kfx"
    gen.generate_full_book("T", "A", [{"title": "C", "text": "x", "blocks": [block]}],
                           output_path=str(out))
    f585 = next(val(f) for f in load_fragments(out) if str(f.ftype) == "$585")
    tables = [e for e in f585["$590"] if e["$492"] == "yj_table"]
    assert tables[0]["$589"]["version"]["$587"] == expected
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_native_generator.py -q -k "link_into_a_cell or yj_table_version" -p no:cacheprovider`
Expected:
- The link test passes already if Tasks 5-6 carried the keys correctly. If it passes, keep it as a guard; it's not a TDD failure to chase.
- The version test FAILs for `rowspan=2`, with version 1.

- [ ] **Step 3: Implement**

```python
def _table_feature_version(chapters):
    """`yj_table` version the book needs: 3 when a native table uses rowspan
    (firmware 5.8.7+, Kindle Previewer 3.106), otherwise 1 (#219)."""
    for chapter in chapters:
        for block in chapter.get("blocks") or ():
            tbl = block.get("table") if isinstance(block, dict) else None
            if tbl and any(c.get("rowspan", 1) > 1 for r in tbl["rows"] for c in r["cells"]):
                return 3
    return 1
```

- Make `build_fragment_585(self, table_version=1)` use `make_version(table_version)` for `yj_table`.
- In `generate_full_book`, call `self.build_fragment_585(table_version=_table_feature_version(chapters))`.
- Leave the other call (~line 419) at the default.

- [ ] **Step 4: Run them to see them pass**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider && .venv/bin/python -m pytest -q -m tier3_strict`
Expected: all pass, and `tier3_strict` byte-identical.

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat: links into table cells and rows; yj_table version for rowspan (#219)"
```

---

### Task 8: Turn it on, with an off switch

**Files:**
- Modify:
  - `plugin/__init__.py` (`options` set ~line 60, `_apply_prefs` ~line 85);
  - `plugin/prefs.py:15`;
  - `plugin/config.py:18-30`;
  - `plugin/kfxgen/converter.py` (`convert_oeb_to_kfx`, ~line 2513).
- Test: `tests/unit/test_converter.py` (next to the font-embedding toggle tests, ~line 1225)

**Interfaces:**
- Produces:
  - the output option `kfxgen_disable_native_tables` (default False);
  - the preference `disable_native_tables`;
  - `convert_oeb_to_kfx` passes `native_tables=not getattr(opts, "kfxgen_disable_native_tables", False)` to `extract_chapters_from_oeb`.

- [ ] **Step 1: Write the failing tests**

Model them on the `kfxgen_disable_font_embedding` tests at `tests/unit/test_converter.py:1225-1260`. Read those first, and reuse their book builder and opts stub.

```python
@pytest.mark.unit
def test_native_tables_are_on_by_default(tmp_path):
    kfx = _convert_table_book(tmp_path, opts=None)
    story_types = _storyline_node_types(kfx)
    assert "$278" in story_types


@pytest.mark.unit
def test_disabling_native_tables_restores_rows(tmp_path):
    class Opts:
        kfxgen_disable_native_tables = True

    assert "$278" not in _storyline_node_types(_convert_table_book(tmp_path, opts=Opts()))


@pytest.mark.unit
def test_disabled_output_is_byte_identical_to_a_book_built_without_native_support(tmp_path):
    class Opts:
        kfxgen_disable_native_tables = True

    a = _convert_table_book(tmp_path / "a", opts=Opts()).read_bytes()
    chapters = _conv.extract_chapters_from_oeb(_table_oeb(tmp_path / "b"), _silent_log())
    b = _generate_bytes(chapters, tmp_path / "b")
    assert a == b
```

Helpers to add in the same section:
- `_table_oeb(dir)` builds an `EpubAsOeb` of an EPUB with `_ISSUE_219_TABLE` between two paragraphs (`EpubBuilder` + `_xhtml_page` from `tests.fixtures.golden.inputs`);
- `_convert_table_book(dir, opts)` calls `converter.convert_oeb_to_kfx(_table_oeb(dir), str(dir / "t.kfx"), opts=opts, log=_silent_log())` and returns the path;
- `_generate_bytes(chapters, dir)` runs `NativeKFXGenerator().generate_full_book("Table Book", "Author", chapters, output_path=...)` with the same metadata `convert_oeb_to_kfx` uses, and returns the bytes;
- `_storyline_node_types(path)` returns `{str(e["$159"]) for e in iter_entries(...)}` over every `$259`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -q -k "native_tables_are_on or disabling_native or disabled_output" -p no:cacheprovider`
Expected: FAIL. `test_native_tables_are_on_by_default` finds no `$278`, because `convert_oeb_to_kfx` doesn't pass the flag yet.

- [ ] **Step 3: Implement**

`plugin/__init__.py`, in the `options` set:

```python
        OptionRecommendation(
            name="kfxgen_disable_native_tables",
            recommended_value=False,
            help=(
                "Write every table as one paragraph per row instead of as a "
                "native Kindle table. Native tables are on by default; enable "
                "this if a book's tables display badly on your Kindle."
            ),
        ),
```

In `_apply_prefs`, after the font block, in the same `try`:

```python
            if prefs["disable_native_tables"]:
                opts.kfxgen_disable_native_tables = True
                log.info("  Native tables disabled by plugin setting")
```

`plugin/prefs.py`:

```python
prefs.defaults["disable_native_tables"] = False
```

`plugin/config.py`: add a second checkbox built the same way as `self.disable_fonts`:
- label: "Write tables as one paragraph per row";
- tooltip: "Use this if a book's tables display badly on your Kindle. By default kfxgen writes native Kindle tables.";
- load it from `prefs["disable_native_tables"]` and save it in `save_settings`.

`converter.py`, in `convert_oeb_to_kfx`:

```python
    native_tables = not getattr(opts, "kfxgen_disable_native_tables", False)
    if not native_tables:
        log.info("  Native tables disabled (kfxgen_disable_native_tables=True)")
    chapters = extract_chapters_from_oeb(
        oeb_book, log, metadata=metadata, cover_href=cover_href,
        native_tables=native_tables,
    )
```

- [ ] **Step 4: Run them to see them pass**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all pass, except golden and integration tests that pin rows for `table_cells`. Those are regenerated in Task 9 and fail here for that reason only. Note which ones fail.

- [ ] **Step 5: Commit**

```bash
git add plugin/__init__.py plugin/prefs.py plugin/config.py plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: native tables on by default, with an opt-out (#219)"
```

---

### Task 9: Goldens, tier 2 decode and the calibre-gated tests

**Files:**
- Modify: `tests/fixtures/golden/inputs.py`: `make_table_cells`, plus a new `make_table_layout` registered in `GOLDEN_INPUTS`.
- Regenerate: `tests/fixtures/golden/expected/table_cells.kfx`, and create `tests/fixtures/golden/expected/table_layout.kfx`.
- Modify: `tests/integration/test_golden_corpus.py`. Replace the row-shape assertions of `test_fixture_table_rows_are_separate_paragraphs` (:444) with a native-shape test.
- Modify: `tests/integration/test_calibre_table_rows.py`. Its row-style tests pin rows as paragraphs; move them under `kfxgen_disable_native_tables`, and add native-table cases.
- Modify: `tests/integration/test_kfxlib_diff.py`. Assert that the decoded EPUB of `table_layout` contains a `<table>` with the right cells.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the golden input and the failing golden test**

`make_table_layout` builds one chapter:
- a paragraph;
- a table with `<thead><tr><th>Year</th><th colspan='2'>Population</th></tr></thead>`;
- two body rows, one using `rowspan='2'`;
- a cell with `id="c1811"`;
- a following paragraph holding `<a href="#c1811">1811</a>`.

In `test_golden_corpus.py`:

```python
@pytest.mark.tier3
@pytest.mark.integration
def test_fixture_table_layout_is_a_native_table(tmp_path):
    """table_layout: thead, colspan, rowspan and a link into a cell (#219)."""
    from tests.fixtures.golden.inputs import make_table_layout

    written = tmp_path / "t.kfx"
    written.write_bytes(_build_fresh("table_layout", make_table_layout, tmp_path))
    frags = load_fragments(written)
    entries = [e for f in by_type(frags, "$259") for e in iter_entries(val(f)["$146"])]
    types = [str(e["$159"]) for e in entries]
    assert types.count("$278") == 1
    assert "$151" in types and "$454" in types and types.count("$279") == 3
```

Rewrite `test_fixture_table_rows_are_separate_paragraphs` as `test_fixture_table_cells_is_a_native_table`. It asserts that the cell strings, in storyline order, are `["Year", "Population", "1801", "8,893", "1811", "12,289"]`, and that they sit inside one `$278`.

- [ ] **Step 2: Run to see them fail, then regenerate**

Run: `.venv/bin/python -m pytest tests/integration/test_golden_corpus.py -q -m tier3 -k table -p no:cacheprovider`
Expected: `table_layout` missing, and `table_cells` shape mismatch.

Then run `.venv/bin/python -m tests.fixtures.golden.regenerate`.
Expected: only `table_cells` and the new `table_layout` change. **If any other golden changes, stop and find out why before going on.**

- [ ] **Step 3: Tier 2**

In `test_kfxlib_diff.py`, add a test that decodes `table_layout` with the vendored KFX Input and converts it to EPUB (the file's existing `decode_book` path). It asserts that:
- no warnings or errors were logged;
- the EPUB's XHTML has one `<table>`, with `colspan="2"`, `rowspan="2"` and the cell texts.

Run: `.venv/bin/python -m pytest tests/integration/test_kfxlib_diff.py -q -p no:cacheprovider`
Expected: PASS. If the vendored plugin zip is missing, the test skips; install it (CONTRIBUTING, tier 2) and rerun, because this check is required before Task 10.

- [ ] **Step 4: Calibre-gated**

In `test_calibre_table_rows.py`:
- keep every existing case, converting that book with `--kfxgen-disable-native-tables` (pass it to `ebook-convert`);
- add a `converted_native` fixture that converts the same cases without the flag;
- add `test_native_centered_table_centers_its_cells`: in `CENTER`, every cell text entry is `$34 $320`;
- run `test_each_note_link_lands_on_its_own_note` against both builds.

Run: `.venv/bin/python -m pytest tests/integration/test_calibre_table_rows.py tests/integration/test_calibre_list_markers.py -m slow -q -p no:cacheprovider`
Expected: all pass on calibre 9.14.0.

- [ ] **Step 5: Full check and commit**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider && .venv/bin/python -m pytest -q -m tier3_strict && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: all pass.

```bash
git add tests/fixtures/golden/ tests/integration/
git commit -m "test: golden, tier-2 and calibre coverage for native tables (#219)"
```

---

### Task 10: Device gate (merge blocker)

**Files:**
- Create: `research/make_table_sideload.py`. Its output goes to `test_books/table-native/`, which is git-ignored because it only holds `.kfx` files.

**Interfaces:**
- Consumes: the branch's converter and generator.

- [ ] **Step 1: Write the sideload builder**

Model it on `research/make_search_sideload.py`:
- the docstring states the hypothesis, the decision table and the build command, and the dated result is filled in afterwards;
- it builds with `EpubBuilder` → `EpubAsOeb` → `converter.convert_oeb_to_kfx`.

Build one source EPUB, with an NCX and 6 chapters of ~12 filler paragraphs each:
1. a plain 3×4 numeric table;
2. a table with `thead`, `colspan` and `rowspan`;
3. a long table of 60 rows;
4. a **wide** table, 8 columns of 3–5 words each;
5. a notes table in the #223 layout (anchor after each row), with 10 note links from chapter 1;
6. a chapter whose TOC entry points at a cell `id`, and a body link into a cell.

Convert it twice, with distinct titles and authors so neither replaces the other:
- `"Table Gate Native"`, author `"kfxgen test"`, default options;
- `"Table Gate Rows"`, author `"kfxgen test"`, opts `kfxgen_disable_native_tables = True`.

Print a table of structural facts from `tests._kfx_introspect`: the count of `$278`, `$279` and `$269` cells, and the `yj_table` version.

- [ ] **Step 2: Build and check the files before sideloading**

Run: `.venv/bin/python research/make_table_sideload.py`
Expected:
- both files are written;
- the Native file has 6 `$278`;
- `yj_table` is 3 (for the rowspan table).

Then decode both with the tier-2 path (`tests/integration/test_kfxlib_diff.py`'s decoder) and confirm there are no warnings.

- [ ] **Step 3: Device checks, all three devices**

On the Voyage (7th gen, 5.13.6), the Oasis (10th gen, 5.18.2.1.1) and the Paperwhite (11th gen, 5.19.2), **reading the firmware off each device**, sideload both files and record for each:

| Check | Pass when |
|---|---|
| TOC button | present in both files |
| TOC jumps | each of the 6 chapters opens at its start |
| Tables render | rows and columns, the header row distinct, colspan/rowspan cells spanning |
| Wide table | readable: fits, wraps, or offers pan/zoom. Record which. |
| Long table | page turns forward and back through all 60 rows |
| Progress | rises through the book, never stuck at 0% or 100% |
| Notes | each of the 10 note links opens the page holding its own note |
| Link into a cell | lands on the page with that row |
| Rows file | unchanged from 5.8.8 |

- [ ] **Step 4: Decide**

- **All pass on all three devices:**
  - add the results to the docstring;
  - add a `native_tables` item to `tests/device/checklist.py`, following the existing `CHECKS` entries;
  - continue to Task 11.
- **The TOC button is missing, or navigation breaks, on any device:**
  - stop, and don't merge;
  - post the table on #219, naming the device and firmware;
  - leave the branch unmerged, and 5.8.8's rows stay;
  - the next step is a narrower spike, one table with no row groups, to find which node the reader rejects.
- **Only the wide table fails:** file an issue for the table viewer (`$629`/`$630`, `yj_table_viewer`), and continue. Record which option below you chose, and why, on #219:
  - keep native tables, with wide tables falling back to rows by column count;
  - or ship as is.

- [ ] **Step 5: Commit**

```bash
git add research/make_table_sideload.py tests/device/checklist.py
git commit -m "research: device gate for native tables (#219)" \
  -m "Device-verified: Voyage 7th gen (2014), firmware <read> — <checks>" \
  -m "Device-verified: Oasis 10th gen (2019), firmware <read> — <checks>" \
  -m "Device-verified: Paperwhite 11th gen (2021), firmware <read> — <checks>"
```

(The `<read>` and `<checks>` fields are filled in from Step 3's results, since they can only be known after the run.)

---

### Task 11: Corpus A/B, docs, PR

**Files:**
- Modify: `CHANGELOG.md` (an Unreleased entry; the version is bumped in the release PR), `README.md` (the feature list, if it mentions table limits), `plugin/README.md` (the option).
- No new code.

- [ ] **Step 1: Gutenberg 90 through the shim**

Rerun the #221 extractor-level A/B (method: memory `kfx-corpus-ab-diff-method`; scripts in the #221 PR description) with `native_tables=True` against v5.8.8.
Expected:
- non-whitespace text is identical in all 90 books;
- chapter titles and lengths are identical;
- every native table's cell texts, joined row by row, equal the 5.8.8 row texts.

- [ ] **Step 2: Real calibre A/B**

Convert the Gutenberg 90 with v5.8.8 and with this branch, each in its own `CALIBRE_CONFIG_DIRECTORY`.
Expected:
- all 180 conversions succeed;
- paragraphs outside tables have identical text and styles;
- every table is either native or rows. Report the counts, and the fallback reasons grouped by rule.
- KFX Input decodes a sample of 10 books with native tables without warnings.

- [ ] **Step 3: Docs**

The CHANGELOG entry covers:
- what changed: rows and columns, per-cell alignment (this is #224 for native tables), links into cells;
- the fallback rules;
- the off switch;
- the device table from Task 10;
- the limits that remain: borders, widths, images in cells, nested tables, multi-paragraph cells, the viewer, #227's progress weighting.

- [ ] **Step 4: Open the PR**

Push `feat/219-native-tables`. The PR description has:
- the spec link;
- the device table;
- both A/B results;
- "Closes #219" only if the device gate passed on all three devices, otherwise "Refs #219".

---

## Tech-debt checklist (tech-debt-planning)

- **System boundaries:** reads EPUB/OEB through calibre and writes KFX; reference behaviour comes from Kindle Previewer 3.106 and KFX Input. No other systems.
- **Non-code artifacts:**
  - the Amazon reference structure is recorded in the spec;
  - the switch is a versioned plugin option, not a hidden constant;
  - `yj_table` versions are derived from content, not configured.
- **Change impact:** tier3_strict byte-identity for no-table books, corpus A/B (shim and calibre), tier-2 decode, and the three-device gate. Evaluation criteria are fixed in the spec's Success criteria before implementation.
- **Dependencies:** no new packages. The table node types and style keys live behind `_TABLE_NODE_TYPES` and the two style builders, so if Amazon's encoding needs a change, it happens in one place.
- **Data flow:** converter block → marker chunks → nested entries, all in one pipeline and tested end to end by the golden and calibre-gated tests.
- **Feedback loops:** N/A. The output doesn't feed later inputs.
- **Access and consumers:** consumers are Kindle readers and KFX Input. There are no credentials or access controls; N/A.
- **Observability:**
  - the conversion log keeps the #221 warning line, now only for tables that fell back;
  - add one `log.info` line per book, "N native tables", in `extract_chapters_from_oeb` next to the warning, so a user report says which path a table took.
- **Human in the loop:** the device gate (Task 10) is the approval step. The merge is the maintainer's.
- **Governance:** `Device-verified:` trailers record who checked what on which firmware.
- **Ownership:** the maintainer owns the feature. The opt-out stays until a later release decides whether it can go.
- **Context:** N/A, this is not an agent system.

## Self-Review

- **Spec coverage:**
  - eligibility → Task 2;
  - the block shape and anchors between rows → Task 3;
  - the block stream, the warning and anchor keys → Task 4;
  - markers and positions → Task 5;
  - the tree and styles → Task 6;
  - links and the feature version → Task 7;
  - the switch → Task 8;
  - tests across tiers → Task 9;
  - the device gate → Task 10;
  - corpus and docs → Task 11.

  The spec's edge cases map to Review Focus 1-5, and the rest are in Tasks 3 and 5. The spec's "observability" line is in the tech-debt section. Its task home is Task 4 Step 3, where the log line goes next to the warning.
- **Placeholders:**
  - Task 10's trailer fields depend on the device results and are filled in then.
  - Tasks 5 and 8 name test helpers to write "if they don't exist under those names"; each says exactly what the helper does.
  - Checked and removed:
    - a placeholder test in Task 6 (`tier3_strict` covers it);
    - hedges on `make_link_flag` and the `$590` key (both verified in the code);
    - the observability log line had no code (now Task 4, Step 3, item 6);
    - an operator-precedence bug in Task 5's cell `spans`.
- **Type consistency:**
  - chunk keys (`type`, `node`, `anchor_keys`, `anchor_offsets`, `cell`), block keys (`table.rows[].group/anchor_ids/cells`) and function names (`_table_is_native`, `_table_block`, `_table_feature_version`, `build_table_style_157`, `build_cell_style_157`, `container_nodes`) are the same in every task;
  - `_MAX_NATIVE_CELL_CHARS` is tied to `CHUNK_SIZE` by a test.
