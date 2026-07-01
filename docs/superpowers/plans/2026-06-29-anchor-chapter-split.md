# Within-file `#anchor` Chapter Splitting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split spine files at TOC `#anchor` targets so each TOC entry becomes its own chapter, restoring tappable TOC and correct Go-To navigation on real books (#23).

**Architecture:** Global block-coordinate model. `extract_blocks_from_html` records per-block `anchor_ids`. A new `_assemble_chapters_by_coordinate` resolves each TOC entry to a `(spine_index, block_index)` coordinate and slices content between consecutive boundaries, replacing the spine-index dedup logic in `extract_chapters_from_oeb`.

**Tech Stack:** Python 3.13, lxml, pytest. All code in `plugin/kfxgen/converter.py`; tests in `tests/unit/test_converter.py` (+ a new integration check).

## Global Constraints

- Run tests with `.venv/bin/python -m pytest` (the venv has hypothesis/lxml/PIL).
- Lint gate: `.venv/bin/python -m ruff check` AND `ruff format --check`, ruff pinned `0.15.1`. Run both before every commit.
- Chapter dicts consumed downstream have shape `{"title": str, "text": str, "blocks": list[dict]}` with optional `font_size`, `_omit_title_heading`, `_omit_from_toc`, `toc_links`. Each block is `{"text": str, "spans": list, "block_style": dict|None}`; this plan adds `"anchor_ids": list[str]`.
- Snap-to-block only — no mid-paragraph character splitting (spec: 99.6% of real anchors are block-level).
- `page-list` is never consulted; only `oeb_book.toc` is read.
- Branch: `fix/23-anchor-chapter-split`. Commit after every task.

---

### Task 1: Per-block `anchor_ids` in `extract_blocks_from_html`

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`extract_blocks_from_html`, ~lines 114-181; add helpers `_own_anchor_ids`, `_subtree_anchor_ids`, `_dedupe_keep_order` above it)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Produces: `extract_blocks_from_html(element, style_resolver=None) -> list[dict]` where each block dict gains `"anchor_ids": list[str]`. Existing keys (`text`, `spans`, `block_style`) unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_converter.py` (import `extract_blocks_from_html` in the existing `from kfxgen.converter import (...)` block):

```python
def _xhtml_raw(body_inner):
    src = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"{body_inner}</body></html>"
    )
    return etree.fromstring(src)


class TestBlockAnchorIds:
    def test_id_on_block_element(self):
        blocks = extract_blocks_from_html(_xhtml_raw('<h2 id="c1">One</h2>'))
        assert blocks[0]["anchor_ids"] == ["c1"]

    def test_id_on_container_attaches_to_first_leaf(self):
        el = _xhtml_raw('<div id="c1"><p>First</p><p>Second</p></div>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["text"] == "First"
        assert blocks[0]["anchor_ids"] == ["c1"]
        assert blocks[1]["anchor_ids"] == []

    def test_standalone_anchor_between_blocks(self):
        el = _xhtml_raw('<p>Before</p><a id="c2"></a><p>After</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["anchor_ids"] == []
        assert blocks[1]["anchor_ids"] == ["c2"]

    def test_legacy_a_name_anchor(self):
        el = _xhtml_raw('<a name="c3"></a><p>Body</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["anchor_ids"] == ["c3"]

    def test_inline_anchor_snaps_to_containing_block(self):
        el = _xhtml_raw('<p>Mid <a id="c4">word</a> here</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["anchor_ids"] == ["c4"]

    def test_empty_id_block_carries_forward(self):
        el = _xhtml_raw('<p id="c5"></p><p>Real</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["text"] == "Real"
        assert blocks[0]["anchor_ids"] == ["c5"]

    def test_block_without_anchor_has_empty_list(self):
        blocks = extract_blocks_from_html(_xhtml_raw("<p>Plain</p>"))
        assert blocks[0]["anchor_ids"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestBlockAnchorIds -v`
Expected: FAIL — `KeyError: 'anchor_ids'`.

- [ ] **Step 3: Implement**

Add helpers immediately above `extract_blocks_from_html` (uses the existing `_local_tag`):

```python
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


def _dedupe_keep_order(items):
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out
```

Replace the body of `extract_blocks_from_html` (everything after `block_tags` is built, i.e. from `blocks = []` to the final `return`) with a recursive walk:

```python
    blocks = []
    pending_ids = []  # anchors awaiting the next leaf block (containers, standalone <a>)

    def _walk(elem, parent_is_block):
        is_block = elem.tag in block_tags
        has_block_child = any(child.tag in block_tags for child in elem)

        if is_block and not has_block_child:
            text, spans = normalize_runs(_walk_inline(elem))
            ids = pending_ids[:]
            pending_ids.clear()
            ids.extend(_subtree_anchor_ids(elem))
            if text:
                bstyle = None
                if style_resolver is not None:
                    css = style_resolver(elem)
                    if css is not None:
                        bstyle = compute_block_style(css)
                blocks.append(
                    {
                        "text": text,
                        "spans": spans,
                        "block_style": bstyle,
                        "anchor_ids": _dedupe_keep_order(ids),
                    }
                )
            else:
                pending_ids.extend(ids)  # empty anchor block: carry ids forward
            return

        if _local_tag(elem.tag) == "img" and not parent_is_block:
            ids = pending_ids[:]
            pending_ids.clear()
            ids.extend(_own_anchor_ids(elem))
            href = elem.get("src", "") or ""
            alt = elem.get("alt", "") or ""
            blocks.append(
                {
                    "text": _make_img_token(href, alt),
                    "spans": [],
                    "block_style": None,
                    "anchor_ids": _dedupe_keep_order(ids),
                }
            )
            return

        pending_ids.extend(_own_anchor_ids(elem))
        for child in elem:
            _walk(child, parent_is_block=is_block)

    for child in body:
        _walk(child, parent_is_block=False)

    if blocks:
        return blocks

    # Fallback: no block elements — flat extraction, no spans (unchanged rule).
    text = body.xpath("string()")
    lines = [line.strip() for line in text.split("\n")]
    text = " ".join(line for line in lines if line)
    if not text:
        return []
    return [
        {
            "text": text,
            "spans": [],
            "block_style": None,
            "anchor_ids": _dedupe_keep_order(pending_ids),
        }
    ]
```

- [ ] **Step 4: Run tests to verify they pass + no regression**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py tests/unit/test_emphasis_spans.py tests/integration/test_golden_corpus.py -q`
Expected: PASS (anchor tests green; existing block/emphasis/golden tests unaffected — `anchor_ids` is additive).

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/python -m ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
.venv/bin/python -m ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: record per-block anchor_ids in extract_blocks_from_html (#23)"
```

---

### Task 2: Coordinate helpers (`_href_fragment`, `_anchor_block_index`)

**Files:**
- Modify: `plugin/kfxgen/converter.py` (add both helpers near `_normalize_href`, ~line 309)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Produces: `_href_fragment(href: str) -> str` (text after `#`, else `""`); `_anchor_block_index(blocks: list[dict]) -> dict[str, int]` (anchor id → first block index).

- [ ] **Step 1: Write the failing tests**

```python
from kfxgen.converter import _href_fragment, _anchor_block_index  # add to imports


class TestCoordinateHelpers:
    def test_href_fragment(self):
        assert _href_fragment("ch.xhtml#c2") == "c2"
        assert _href_fragment("ch.xhtml") == ""
        assert _href_fragment("") == ""

    def test_anchor_block_index_first_wins(self):
        blocks = [
            {"anchor_ids": ["a"]},
            {"anchor_ids": ["b", "a"]},
            {"anchor_ids": []},
        ]
        assert _anchor_block_index(blocks) == {"a": 0, "b": 1}
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestCoordinateHelpers -v`
Expected: FAIL — `ImportError: cannot import name '_href_fragment'`.

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestCoordinateHelpers -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/python -m ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
.venv/bin/python -m ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: add _href_fragment and _anchor_block_index helpers (#23)"
```

---

### Task 3: `_assemble_chapters_by_coordinate` — happy path

**Files:**
- Modify: `plugin/kfxgen/converter.py` (add `_assemble_chapters_by_coordinate` + `_leading_chapter_title` above `extract_chapters_from_oeb`, ~line 366)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `spine_items_ordered: list[dict]` items shaped `{"href": str, "text": str, "blocks": list[dict]}`; `toc_entries: list[dict]` shaped `{"title": str, "href": str, "level": int}`; `log`.
- Produces: `_assemble_chapters_by_coordinate(spine_items_ordered, toc_entries, log) -> list[dict] | None`. Returns chapter dicts (`title`/`text`/`blocks`) or `None` when no coordinate resolves (caller falls back).

- [ ] **Step 1: Write the failing tests**

```python
from kfxgen.converter import _assemble_chapters_by_coordinate  # add to imports


def _spine_item(href, blocks):
    """blocks: list of (text, anchor_ids) tuples."""
    return {
        "href": href,
        "text": "\n\n".join(t for t, _ in blocks),
        "blocks": [
            {"text": t, "spans": [], "block_style": None, "anchor_ids": list(a)}
            for t, a in blocks
        ],
    }


class TestCoordinateAssembly:
    def test_multi_anchor_split_within_one_file(self):
        spine = [
            _spine_item(
                "book.xhtml",
                [("I", ["c1"]), ("Body one", []), ("II", ["c2"]), ("Body two", [])],
            )
        ]
        toc = [
            {"title": "I", "href": "book.xhtml#c1"},
            {"title": "II", "href": "book.xhtml#c2"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["I", "II"]
        assert chapters[0]["text"] == "I\n\nBody one"
        assert chapters[1]["text"] == "II\n\nBody two"

    def test_one_file_per_chapter(self):
        spine = [
            _spine_item("a.xhtml", [("Alpha", [])]),
            _spine_item("b.xhtml", [("Beta", [])]),
        ]
        toc = [
            {"title": "Alpha", "href": "a.xhtml"},
            {"title": "Beta", "href": "b.xhtml"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["Alpha", "Beta"]

    def test_split_sibling_spans_files(self):
        # chap.xhtml is in the TOC; chap_split_001.xhtml is an orphan sibling
        # between two TOC anchors -> absorbed into the first chapter.
        spine = [
            _spine_item("chap.xhtml", [("One", ["c1"])]),
            _spine_item("chap_split_001.xhtml", [("One continued", [])]),
            _spine_item("chap2.xhtml", [("Two", ["c2"])]),
        ]
        toc = [
            {"title": "One", "href": "chap.xhtml#c1"},
            {"title": "Two", "href": "chap2.xhtml#c2"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["One", "Two"]
        assert "One continued" in chapters[0]["text"]

    def test_returns_none_when_no_toc_entry_in_spine(self):
        spine = [_spine_item("a.xhtml", [("Alpha", [])])]
        toc = [{"title": "Ghost", "href": "missing.xhtml"}]
        assert _assemble_chapters_by_coordinate(spine, toc, _silent_log()) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestCoordinateAssembly -v`
Expected: FAIL — `ImportError: cannot import name '_assemble_chapters_by_coordinate'`.

- [ ] **Step 3: Implement (happy path + None; edge cases added in Task 4)**

```python
def _leading_chapter_title(head_blocks, first_spine_item):
    """Title for front matter that precedes the first TOC anchor.

    Use the first block's text when it is short enough to be a heading,
    otherwise a neutral 'Front Matter' label."""
    if head_blocks:
        t = (head_blocks[0].get("text") or "").strip()
        if 0 < len(t) <= 60 and "\n" not in t:
            return t
    return "Front Matter"


def _assemble_chapters_by_coordinate(spine_items_ordered, toc_entries, log):
    """Resolve each TOC entry to a (spine_index, block_index) coordinate and
    slice content between consecutive coordinates into chapters. Returns None
    when no TOC entry resolves to a spine item (caller falls back)."""
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
                f"  TOC entry {entry['title']!r} out of document order; "
                f"skipping split"
            )
            continue
        coords.append((fi, si, entry["title"]))
        prev_flat = fi
        last_block_in_file[si] = bi

    if not coords:
        return None

    def _mk(title, block_slice):
        text = "\n\n".join(b["text"] for b in block_slice if b.get("text"))
        if not text.strip():
            return None
        return {"title": title, "text": text, "blocks": list(block_slice)}

    chapters = []

    first_fi = coords[0][0]
    if first_fi > 0:
        head = flat[0:first_fi]
        ch = _mk(_leading_chapter_title(head, spine_items_ordered[0]), head)
        if ch:
            chapters.append(ch)

    for k, (fi, si, title) in enumerate(coords):
        if k + 1 < len(coords):
            end = coords[k + 1][0]
        else:
            end = file_offset[si] + len(spine_blocks[si])
        ch = _mk(title, flat[fi:end])
        if ch:
            chapters.append(ch)

    last_si = coords[-1][1]
    for si in range(last_si + 1, len(spine_items_ordered)):
        item = spine_items_ordered[si]
        if not _has_real_text(item["text"]):
            log.info(
                f"  Skipping image-only orphan {_normalize_href(item['href'])}"
            )
            continue
        norm = _normalize_href(item["href"])
        stem = norm.rsplit(".", 1)[0] if "." in norm else norm
        ch = _mk(stem or f"Section {si + 1}", spine_blocks[si])
        if ch:
            chapters.append(ch)

    return chapters
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestCoordinateAssembly -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/python -m ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
.venv/bin/python -m ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: global-coordinate chapter assembly, happy path (#23)"
```

---

### Task 4: Assembly edge cases (front matter, missing anchor, non-monotonic, tail orphan)

**Files:**
- Modify: `plugin/kfxgen/converter.py` (no code change expected — Task 3 already implements these; this task proves them and fixes anything that fails)
- Test: `tests/unit/test_converter.py`

**Interfaces:** none new.

- [ ] **Step 1: Write the failing/locking tests**

```python
class TestCoordinateAssemblyEdges:
    def test_front_matter_becomes_leading_chapter(self):
        spine = [
            _spine_item(
                "book.xhtml",
                [("Copyright 2026", []), ("I", ["c1"]), ("Body", [])],
            )
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["Copyright 2026", "I"]
        # Front matter is NOT merged into Chapter I
        assert "Copyright" not in chapters[1]["text"]

    def test_missing_anchor_snaps_after_previous(self):
        spine = [
            _spine_item(
                "book.xhtml",
                [("I", ["c1"]), ("Mid", []), ("II body", [])],
            )
        ]
        toc = [
            {"title": "I", "href": "book.xhtml#c1"},
            {"title": "II", "href": "book.xhtml#ghost"},  # missing -> block 1
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["I", "II"]
        assert chapters[0]["text"] == "I"
        assert chapters[1]["text"] == "Mid\n\nII body"

    def test_non_monotonic_toc_skips_split(self):
        spine = [
            _spine_item("book.xhtml", [("I", ["c1"]), ("II", ["c2"])])
        ]
        toc = [
            {"title": "II", "href": "book.xhtml#c2"},  # block 1 first
            {"title": "I", "href": "book.xhtml#c1"},  # block 0 -> backward, skipped
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["II"]

    def test_tail_orphan_recovered_as_separate_chapter(self):
        spine = [
            _spine_item("ch.xhtml", [("Nine", ["c9"])]),
            _spine_item("license.xhtml", [("Project Gutenberg License text", [])]),
        ]
        toc = [{"title": "IX", "href": "ch.xhtml#c9"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert chapters[0]["title"] == "IX"
        assert chapters[1]["title"] == "license"
        assert "License text" in chapters[1]["text"]
```

- [ ] **Step 2: Run to verify status**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestCoordinateAssemblyEdges -v`
Expected: PASS if Task 3 is correct. If any FAIL, fix `_assemble_chapters_by_coordinate` minimally (do NOT change tests) until green — the most likely fix sites are the `last_block_in_file` snap (missing-anchor) and the `fi <= prev_flat` guard (non-monotonic).

- [ ] **Step 3: Lint + commit**

```bash
.venv/bin/python -m ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
.venv/bin/python -m ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "test: lock #23 assembly edge cases (front matter, missing anchor, order, tail)"
```

---

### Task 5: Wire assembly into `extract_chapters_from_oeb` + Gatsby integration test

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`extract_chapters_from_oeb` — replace the `if toc_entries:` mapping block, ~lines 433-589, with the call below; remove the now-dead `dropped_toc_titles` machinery)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `_assemble_chapters_by_coordinate` (Task 3).
- Produces: `extract_chapters_from_oeb` unchanged signature/return; now splits on anchors.

- [ ] **Step 1: Write the failing integration test**

```python
def _multi_block_spine(href, blocks):
    """Build a real XHTML spine item from (tag, id, text) tuples so the live
    extract_blocks path (not a hand-built block list) is exercised."""
    parts = []
    for tag, anchor_id, text in blocks:
        idattr = f' id="{anchor_id}"' if anchor_id else ""
        parts.append(f"<{tag}{idattr}>{text}</{tag}>")
    body = "".join(parts)

    class _Item:
        def __init__(self):
            self.href = href
            self.data = _xhtml_raw(body)
            self.media_type = "application/xhtml+xml"

    return _Item()


class TestGatsbyShapedSplit:
    def test_within_file_anchors_split_into_chapters(self):
        # h-0 holds title + chapters I..III via within-file anchors
        spine = [
            _multi_block_spine(
                "h-0.xhtml",
                [
                    ("h1", "title", "The Great Gatsby"),
                    ("div", "chapter-1", "Chapter one prose."),
                    ("div", "chapter-2", "Chapter two prose."),
                    ("div", "chapter-3", "Chapter three prose."),
                ],
            ),
            _multi_block_spine(
                "h-1.xhtml", [("div", "chapter-4", "Chapter four prose.")]
            ),
        ]
        toc = [
            _TOCNode("Title", "h-0.xhtml#title"),
            _TOCNode("I", "h-0.xhtml#chapter-1"),
            _TOCNode("II", "h-0.xhtml#chapter-2"),
            _TOCNode("III", "h-0.xhtml#chapter-3"),
            _TOCNode("IV", "h-1.xhtml#chapter-4"),
        ]
        oeb = _OEBBook(spine=spine, toc=toc)
        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        titles = [c["title"] for c in chapters]
        assert titles == ["Title", "I", "II", "III", "IV"]
        assert "Chapter two prose." in chapters[2]["text"]
        assert "Chapter two prose." not in chapters[1]["text"]
```

Note: `spine` and `toc` are plain lists; `_OEBBook(spine=..., toc=...)`, `_TOCNode`, and `_xhtml_raw` (from Task 1) already exist in the test file.

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestGatsbyShapedSplit -v`
Expected: FAIL — current code collapses to fewer chapters (titles list shorter / wrong).

- [ ] **Step 3: Implement the wiring**

In `extract_chapters_from_oeb`, replace the entire `if toc_entries:` block (the spine-index mapping, the per-entry loop, the orphan-recovery loop, and the `if chapters: ... return` / `else:` at the end of that block — roughly lines 433-589) with:

```python
    if toc_entries:
        chapters = _assemble_chapters_by_coordinate(
            spine_items_ordered, toc_entries, log
        )
        if chapters:
            log.info(
                f"Assembled {len(chapters)} chapters from TOC coordinates"
            )
            _replace_title_page(chapters, metadata, log)
            return chapters
        log.info("TOC produced no chapters; using spine items as chapters")
```

Leave the spine-parsing above and the `# Fallback: use each spine item as a chapter` block below unchanged.

- [ ] **Step 4: Run to verify pass + full regression**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py tests/integration -q`
Expected: PASS. Existing one-file-per-chapter TOC tests (`TestTOCMappingPreservesContent`, `TestHalfTitlePage`, corpus, golden) still pass — coordinate model is a superset.

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/python -m ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
.venv/bin/python -m ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat: split chapters on within-file #anchors in extract_chapters_from_oeb (#23)"
```

---

### Task 6: Scale test (~400 chapters) — measure-first

**Files:**
- Test: `tests/unit/test_converter.py` (or `tests/integration/test_epub_corpus.py` if it needs the generator)

**Interfaces:** none new. Asserts the generator handles many chapters within the #20 position envelope.

- [ ] **Step 1: Write the test**

```python
import sys as _sys
import os as _os

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", "..", "plugin"))


class TestHighChapterCountScale:
    def test_400_chapter_book_converts_and_stays_in_envelope(self, tmp_path):
        from kfxgen.native_generator import NativeKFXGenerator
        from kfxgen.kfxlib_minimal.ion import IS
        from tests._kfx_introspect import by_type, val, load_fragments

        chapters = [
            {"title": f"Chapter {i}", "text": f"Chapter {i}\n\nBody of chapter {i}."}
            for i in range(400)
        ]
        out = tmp_path / "scale.kfx"
        NativeKFXGenerator().generate_full_book(
            title="Scale", author="T", chapters=chapters, output_path=str(out)
        )
        frags = load_fragments(out)

        # All $260 sections present.
        assert len(by_type(frags, "$260")) == 400

        # Content positions stay below SECTION_POS_BASE; sections at/above it.
        content_max = 0
        for f in by_type(frags, "$259"):
            v = val(f)
            for e in (v.get(IS("$146")) or v.get(IS("$181")) or []):
                if hasattr(e, "get") and e.get(IS("$155")) is not None:
                    content_max = max(content_max, int(e.get(IS("$155"))))
        assert content_max < NativeKFXGenerator.SECTION_POS_BASE, (
            f"content position {content_max} entered the section range — "
            f"envelope exceeded; escalate to #30"
        )
```

- [ ] **Step 2: Run to verify status (this is the measurement)**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestHighChapterCountScale -v`
Expected: PASS if 400 chapters stay in-envelope. **If it FAILS** (content positions reach the section range), STOP: the measure-first result says the envelope is exceeded — comment on issue **#30** with the observed `content_max`, mark this test `@pytest.mark.xfail(reason="#30 position-range rework")`, commit, and surface to the user. Do NOT widen ranges in this PR.

- [ ] **Step 3: Lint + commit**

```bash
.venv/bin/python -m ruff check tests/unit/test_converter.py
.venv/bin/python -m ruff format tests/unit/test_converter.py
git add tests/unit/test_converter.py
git commit -m "test: high-chapter-count scale gate for #23 (measure-first, fallback #30)"
```

---

### Task 7: Full suite, golden refresh, lint, version, CHANGELOG, device gate

**Files:**
- Modify: `plugin/kfxgen/__init__.py` (version bump), `CHANGELOG.md`
- Possibly: `tests/fixtures/golden/expected/*.kfx` (only if golden bytes changed)

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (12 tier-2 skip without the vendored zip). Fix any regression before continuing.

- [ ] **Step 2: Golden corpus check + refresh if needed**

Run: `.venv/bin/python -m pytest tests/integration/test_golden_corpus.py -q -m "tier3 or tier3_strict"`
If `tier3_strict` fails, the golden fixtures (one-file-per-chapter inputs) changed shape. Confirm the structural diff is intended, then:
```bash
.venv/bin/python -m tests.fixtures.golden.regenerate
.venv/bin/python -m pytest tests/integration/test_golden_corpus.py -q -m "tier3 or tier3_strict"
git add tests/fixtures/golden/expected/
```
Note: the golden inputs are one-file-per-chapter, so the coordinate model should produce identical chapters and golden bytes should NOT change. If they do, investigate before regenerating.

- [ ] **Step 3: Version + CHANGELOG**

Bump `version` in `plugin/kfxgen/__init__.py` (5.3.21 → 5.3.22). Prepend a CHANGELOG entry titled `## 5.3.22 — Within-file #anchor chapter splitting (#23)` summarizing: anchor-aware block extraction, global block-coordinate assembly, edge cases, measure-first scale (fallback #30), device-verified.

- [ ] **Step 4: Lint gate (pinned ruff)**

```bash
.venv/bin/python -m ruff check plugin/ tests/
.venv/bin/python -m ruff format --check plugin/ tests/
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: bump 5.3.22, CHANGELOG for #23 anchor chapter splitting"
```

- [ ] **Step 6: Device verification gate (manual — STOP for the user)**

Generate a Gatsby `.kfx` (and one high-chapter book) and have the user sideload to a physical Kindle. Confirm: (1) every TOC entry I–IX is tappable and lands on the right chapter; (2) the Go-To pane lists chapters at plausible pages. Do NOT mark #23 done or open the PR as "verified" until the user confirms on-device. If nav is wrong, debug before merge.

---

## Self-Review

- **Spec coverage:** Component 1 → Task 1; Component 2 → Task 2; Component 3 → Tasks 3+5; edge cases → Task 4; scale → Task 6; testing (unit/Gatsby/scale/device) → Tasks 1–7; #30 fallback → Task 6 Step 2.
- **Placeholders:** none — every code/test step has full code.
- **Type consistency:** block dict gains `anchor_ids: list[str]` (Task 1), consumed by `_anchor_block_index` (Task 2) and `_assemble_chapters_by_coordinate` (Task 3); chapter dict shape (`title`/`text`/`blocks`) matches downstream `_replace_title_page`/`_rebuild_contents_page`/generator consumers.
