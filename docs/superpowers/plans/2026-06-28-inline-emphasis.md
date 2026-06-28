# Inline Emphasis Implementation Plan (Plan A of #9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry inline `<em>/<i>/<strong>/<b>` emphasis from the source EPUB through to KFX as italic/bold/bold-italic character spans.

**Architecture:** The converter produces, per paragraph, normalized text plus a list of `(start, length, flags)` emphasis spans (additive `chapter["blocks"]`; the flat `text` string is unchanged for existing heuristics). The generator emits each span as a `$142` character span referencing a deduped italic/bold `$157` style — reusing the existing `$142` link-span mechanism and the existing `_allocate_style` cache.

**Tech Stack:** Python 3.13, pytest, lxml (Calibre OEB), the vendored `kfxlib_minimal` Ion library.

**Scope note:** This is Plan A (inline emphasis only). The CSS subset (text-align, text-indent, margins) from the spec is Plan B, a separate plan after this lands — it needs CSS length→KFX unit conversion and the margin-symbol reconciliation, which are independent of emphasis.

## Global Constraints

- Python invoked as `python3.13` (or the repo venv); never bare `python`.
- Lint gate is BOTH `ruff check .` and `ruff format --check .`, pinned to **ruff==0.15.1**. Run both before every commit.
- Tests run with `pytest tests/unit -q`. Unit tests carry `@pytest.mark.unit`.
- KFX symbols are authoritative (from jhowell's kfxlib): font-style = `$12`, italic value = `$382`, font-weight = `$13`, bold value = `$361`. The `$142` span uses `$143`=start, `$144`=length, `$157`=style; emphasis spans omit `$179` (that field is links only).
- `flags` is a `frozenset` over the string constants `inline_style.FLAG_ITALIC` / `FLAG_BOLD`.
- Default (non-emphasis) generator output must stay byte-identical — guard with the golden tests in Task 7.

---

### Task 1: `normalize_runs` — whitespace-collapsing span builder

**Files:**
- Create: `plugin/kfxgen/inline_style.py`
- Test: `tests/unit/test_inline_style.py`

**Interfaces:**
- Produces: `FLAG_ITALIC: str`, `FLAG_BOLD: str`; `normalize_runs(segments: list[tuple[str, frozenset]]) -> tuple[str, list[tuple[int, int, frozenset]]]`. Input is `(text, flags)` segments in document order; output is the whitespace-normalized text (same rule as the existing `" ".join(text.split())`: every whitespace run collapses to one space, ends stripped) plus maximal `(start, length, flags)` spans where `flags` is non-empty, offsets into the returned text.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from kfxgen import inline_style as ist
from kfxgen.inline_style import FLAG_ITALIC as I, FLAG_BOLD as B


@pytest.mark.unit
def test_plain_text_no_spans():
    text, spans = ist.normalize_runs([("hello world", frozenset())])
    assert text == "hello world"
    assert spans == []


@pytest.mark.unit
def test_single_italic_run():
    text, spans = ist.normalize_runs(
        [("a ", frozenset()), ("big", frozenset({I})), (" cat", frozenset())]
    )
    assert text == "a big cat"
    assert spans == [(2, 3, frozenset({I}))]


@pytest.mark.unit
def test_whitespace_collapsed_and_stripped():
    text, spans = ist.normalize_runs(
        [("  a\n\n", frozenset()), ("  b  ", frozenset({B}))]
    )
    assert text == "a b"
    assert spans == [(2, 1, frozenset({B}))]


@pytest.mark.unit
def test_bold_italic_combined_flags():
    text, spans = ist.normalize_runs([("x", frozenset({I, B}))])
    assert text == "x"
    assert spans == [(0, 1, frozenset({I, B}))]


@pytest.mark.unit
def test_adjacent_same_flags_merge():
    text, spans = ist.normalize_runs(
        [("ab", frozenset({I})), ("cd", frozenset({I}))]
    )
    assert text == "abcd"
    assert spans == [(0, 4, frozenset({I}))]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python3.13 -m pytest tests/unit/test_inline_style.py -q`
Expected: FAIL (`ModuleNotFoundError` / `AttributeError: normalize_runs`).

- [ ] **Step 3: Implement the module**

```python
"""Inline emphasis run/span computation for KFX styling (#9).

Pure, Calibre-independent: turns a paragraph's ordered (text, flags) segments
into whitespace-normalized text plus character spans, ready to become KFX $142
spans. See docs/superpowers/specs/2026-06-28-inline-emphasis-css-typography-design.md.
"""

FLAG_ITALIC = "italic"
FLAG_BOLD = "bold"


def normalize_runs(segments):
    """Collapse whitespace across (text, flags) segments and return
    (normalized_text, spans). Mirrors the converter's existing
    `" ".join(text.split())` rule: each run of ASCII whitespace becomes a
    single space and leading/trailing space is stripped. `spans` are maximal
    (start, length, flags) ranges with non-empty flags, offset into the text.
    """
    chars = []
    flags_per_char = []
    prev_space = True  # strip leading whitespace
    for text, flags in segments:
        for ch in text:
            if ch.isspace():
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
    # strip trailing space
    while chars and chars[-1] == " ":
        chars.pop()
        flags_per_char.pop()

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
    return text_out, spans
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python3.13 -m pytest tests/unit/test_inline_style.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + commit**

```bash
ruff check plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
ruff format plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git add plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git commit -m "feat(emphasis): normalize_runs span builder for inline styling (#9)"
```

---

### Task 2: Extract emphasis blocks from XHTML

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`_walk_paragraph_with_imgs`, add `extract_blocks_from_html`; refactor `extract_text_from_html` to delegate)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `inline_style.normalize_runs`, `inline_style.FLAG_ITALIC/FLAG_BOLD`.
- Produces: `extract_blocks_from_html(element) -> list[dict]` where each block is `{"text": str, "spans": list[(start, length, frozenset)]}`. `extract_text_from_html(element) -> str` is unchanged externally (now `"\n\n".join(b["text"] ...)`), so existing callers and tests keep working.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from lxml import etree
from kfxgen import converter
from kfxgen.inline_style import FLAG_ITALIC as I, FLAG_BOLD as Bf

XHTML = "{http://www.w3.org/1999/xhtml}"


def _doc(body_inner):
    return etree.fromstring(
        f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body_inner}</body></html>'.encode()
    )


@pytest.mark.unit
def test_blocks_capture_italic_span():
    blocks = converter.extract_blocks_from_html(_doc("<p>a <em>big</em> cat</p>"))
    assert len(blocks) == 1
    assert blocks[0]["text"] == "a big cat"
    assert blocks[0]["spans"] == [(2, 3, frozenset({I}))]


@pytest.mark.unit
def test_blocks_capture_bold_and_nested():
    blocks = converter.extract_blocks_from_html(
        _doc("<p><strong>x <em>y</em></strong></p>")
    )
    assert blocks[0]["text"] == "x y"
    assert blocks[0]["spans"] == [
        (0, 2, frozenset({Bf})),
        (2, 1, frozenset({Bf, I})),
    ]


@pytest.mark.unit
def test_extract_text_unchanged_delegates_to_blocks():
    doc = _doc("<p>one</p><p>two <i>three</i></p>")
    assert converter.extract_text_from_html(doc) == "one\n\ntwo three"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python3.13 -m pytest tests/unit/test_converter.py -q -k "blocks or delegates"`
Expected: FAIL (`AttributeError: extract_blocks_from_html`).

- [ ] **Step 3: Generalize the inline walk to emit (segment, flags)**

In `converter.py`, add the emphasis tag map near the top (after imports):

```python
from .inline_style import FLAG_BOLD, FLAG_ITALIC, normalize_runs

_ITALIC_TAGS = {"em", "i"}
_BOLD_TAGS = {"strong", "b"}
```

Replace `_walk_paragraph_with_imgs` with a flag-accumulating walk that returns `(segment, flags)` pairs (IMG tokens get empty flags):

```python
def _walk_inline(elem, flags=frozenset()):
    """Yield (segment, flags) pairs for inline content, accumulating italic/
    bold from ancestor <em>/<i>/<strong>/<b>. <img> becomes an IMG token
    segment with empty flags so the generator still splits on it."""
    local = _local_tag(elem.tag)
    cur = set(flags)
    if local in _ITALIC_TAGS:
        cur.add(FLAG_ITALIC)
    if local in _BOLD_TAGS:
        cur.add(FLAG_BOLD)
    cur = frozenset(cur)
    parts = []
    if elem.text:
        parts.append((elem.text, cur))
    for child in elem:
        clocal = _local_tag(child.tag)
        if clocal == "img":
            href = child.get("src", "") or ""
            alt = child.get("alt", "") or ""
            parts.append((_make_img_token(href, alt), frozenset()))
        else:
            parts.extend(_walk_inline(child, cur))
        if child.tail:
            parts.append((child.tail, flags))
    return parts
```

- [ ] **Step 4: Add `extract_blocks_from_html` and delegate `extract_text_from_html`**

Refactor the paragraph loop in `extract_text_from_html` into a block builder. Replace the body of `extract_text_from_html` so the block-finding logic lives in `extract_blocks_from_html`, and `extract_text_from_html` joins block texts:

```python
def extract_blocks_from_html(element):
    """Like extract_text_from_html but returns structured blocks:
    [{"text": str, "spans": [(start, length, frozenset)]}], preserving inline
    emphasis as spans and inline <img> as IMG tokens in `text`."""
    body = element.find(".//{http://www.w3.org/1999/xhtml}body")
    if body is None:
        body = element.find(".//body")
    if body is None:
        body = element

    ns = "{http://www.w3.org/1999/xhtml}"
    block_tags = set()
    for tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                "blockquote", "li", "section", "article", "figure"):
        block_tags.add(tag)
        block_tags.add(ns + tag)

    blocks = []
    for elem in body.iter():
        if elem.tag in block_tags:
            if any(child.tag in block_tags for child in elem):
                continue
            text, spans = normalize_runs(_walk_inline(elem))
            if text:
                blocks.append({"text": text, "spans": spans})
            continue
        if _local_tag(elem.tag) == "img":
            parent = elem.getparent()
            if parent is not None and parent.tag in block_tags:
                continue
            href = elem.get("src", "") or ""
            alt = elem.get("alt", "") or ""
            blocks.append({"text": _make_img_token(href, alt), "spans": []})

    if blocks:
        return blocks

    # Fallback: no block elements — flat extraction, no spans (unchanged rule).
    text = body.xpath("string()")
    lines = [line.strip() for line in text.split("\n")]
    text = " ".join(line for line in lines if line)
    return [{"text": text, "spans": []}] if text else []


def extract_text_from_html(element):
    """Plain-text extraction (IMG tokens preserved). Now derived from
    extract_blocks_from_html so the two never diverge."""
    return "\n\n".join(b["text"] for b in extract_blocks_from_html(element))
```

Delete the now-unused old `_walk_paragraph_with_imgs` if nothing else references it (grep first: `grep -rn _walk_paragraph_with_imgs plugin tests`).

- [ ] **Step 5: Run tests, verify pass (including existing converter tests)**

Run: `python3.13 -m pytest tests/unit/test_converter.py -q`
Expected: PASS (new tests + all existing converter tests unchanged).

- [ ] **Step 6: Lint + commit**

```bash
ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat(emphasis): extract emphasis blocks from XHTML (#9)"
```

---

### Task 3: Thread blocks onto chapters

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`extract_chapters_from_oeb` spine extraction + assembly)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Produces: each chapter dict gains `chapter["blocks"]: list[{"text","spans"}]` aligned to the chapter's paragraphs. `blocks` is omitted/empty only when there is no structured text. The flat `chapter["text"]` is unchanged.

- [ ] **Step 1: Write the failing test** (uses the existing OEB shim in the converter tests; mirror how other tests build `oeb_book`)

```python
@pytest.mark.unit
def test_chapter_carries_emphasis_blocks(simple_oeb_with_italic):
    # simple_oeb_with_italic: an OEB shim whose single spine item is
    # <p>plain</p><p>see <em>this</em></p> (build with the same shim helper
    # the other extract_chapters_from_oeb tests use).
    import logging
    chapters = converter.extract_chapters_from_oeb(
        simple_oeb_with_italic, logging.getLogger("t")
    )
    blocks = chapters[0]["blocks"]
    assert any(
        b["spans"] and b["spans"][0][2] == frozenset({I}) for b in blocks
    )
```

If no shim fixture exists, add one mirroring the existing `EpubAsOeb`/spine-item test scaffolding already in `tests/unit/test_converter.py` (reuse that file's helper that wraps XHTML strings as spine items).

- [ ] **Step 2: Run test, verify it fails**

Run: `python3.13 -m pytest tests/unit/test_converter.py -q -k emphasis_blocks`
Expected: FAIL (`KeyError: 'blocks'`).

- [ ] **Step 3: Carry blocks through spine extraction + assembly**

In `extract_chapters_from_oeb`, where each spine item's text is computed (the `extract_text_from_html(item.data)` call), also compute blocks and store them parallel to text:

```python
            blocks = extract_blocks_from_html(item.data)
            text = "\n\n".join(b["text"] for b in blocks)
```

Extend `spine_items_ordered` entries to carry blocks: `spine_items_ordered.append({"href": href, "text": text, "blocks": blocks})`.

Where chapter text is assembled from a spine-index range (`parts = [...text...]; text = "\n\n".join(parts)`), assemble blocks the same way and attach:

```python
            block_parts = []
            for k in range(start, end):
                if spine_items_ordered[k]["text"]:
                    block_parts.extend(spine_items_ordered[k]["blocks"])
            chapter = {"title": entry["title"], "text": text}
            if block_parts:
                chapter["blocks"] = block_parts
            chapters.append(chapter)
```

Apply the same `blocks` attachment in the other chapter-append sites (orphan recovery and the no-TOC fallback). For chapters whose text is synthesized (title-page replacement, rebuilt contents) leave `blocks` unset — those have no source emphasis and fall back to the flat path.

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/unit/test_converter.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat(emphasis): carry emphasis blocks onto chapters (#9)"
```

---

### Task 4: `build_fragment_157` — italic support

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`build_fragment_157`)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Produces: `build_fragment_157(..., italic=False)` adds `value[IS("$12")] = IS("$382")` when `italic` is True; default-arg output is byte-identical to before.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_italic_sets_font_style():
    from kfxgen.kfxlib_minimal.ion import IS
    gen = NativeKFXGenerator()
    frag = gen.build_fragment_157(entity_name="s_it", italic=True)
    assert frag.value[IS("$12")] == IS("$382")


@pytest.mark.unit
def test_no_italic_by_default():
    from kfxgen.kfxlib_minimal.ion import IS
    gen = NativeKFXGenerator()
    frag = gen.build_fragment_157(entity_name="s_plain")
    assert IS("$12") not in frag.value
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python3.13 -m pytest tests/unit/test_native_generator.py -q -k italic`
Expected: FAIL (`KeyError`/`$12` absent or unexpected presence).

- [ ] **Step 3: Add the `italic` parameter**

Add `italic=False` to the signature, and after the underline block insert:

```python
        # $12 = font-style: $382 = italic (authoritative, jhowell kfxlib)
        if italic:
            value[IS("$12")] = IS("$382")
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/unit/test_native_generator.py -q -k "italic or style or align or margin or bold or underline"`
Expected: PASS (new + existing style tests, confirming defaults unchanged).

- [ ] **Step 5: Lint + commit**

```bash
ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(emphasis): font-style italic in build_fragment_157 (#9)"
```

---

### Task 5: `build_fragment_259` — emphasis spans

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`build_fragment_259`)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: italic/bold `$157` style names (allocated by the caller in Task 6).
- Produces: `build_fragment_259(..., emphasis_spans=None)` where `emphasis_spans[i]` is a list of `(start, length, style_name)` for child `i`. Each becomes a `$142` span `IonStruct($143=start, $144=length, $157=style_name)` (no `$179`). Existing `$142` link spans are preserved; emphasis spans append to the same `$142` list.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.unit
def test_emphasis_spans_emit_142():
    from kfxgen.kfxlib_minimal.ion import IS
    gen = NativeKFXGenerator()
    frag = gen.build_fragment_259(
        ["s0"], content_name="content_1", entity_name="l0",
        positions=[1001], outer_position=1000, outer_style="s0",
        chunk_kinds=["text"],
        emphasis_spans=[[(2, 3, "s0it")]],
    )
    child = frag.value[IS("$146")][0][IS("$146")][0]
    spans = child[IS("$142")]
    assert spans[0][IS("$143")] == 2
    assert spans[0][IS("$144")] == 3
    assert spans[0][IS("$157")] == IS("s0it")
    assert IS("$179") not in spans[0]
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python3.13 -m pytest tests/unit/test_native_generator.py -q -k emphasis_spans`
Expected: FAIL (`TypeError: unexpected keyword 'emphasis_spans'`).

- [ ] **Step 3: Emit emphasis spans**

Add `emphasis_spans=None` to the signature. In the text-entry branch, after the existing link-span block (which may set `entry[IS("$142")]`), append emphasis spans to the same list:

```python
            if emphasis_spans and i < len(emphasis_spans) and emphasis_spans[i]:
                existing = entry.get(IS("$142"), [])
                for start, length, style_name in emphasis_spans[i]:
                    self.symtab.create_local_symbol(style_name)
                    existing.append(
                        IonStruct(
                            IS("$143"), start,
                            IS("$144"), length,
                            IS("$157"), IS(style_name),
                        )
                    )
                entry[IS("$142")] = existing
```

- [ ] **Step 4: Run test, verify pass**

Run: `python3.13 -m pytest tests/unit/test_native_generator.py -q -k "emphasis_spans or 259 or storyline"`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(emphasis): \$142 emphasis spans in build_fragment_259 (#9)"
```

---

### Task 6: Wire blocks → chunk spans → emphasis styles in `_build_chapter_content`

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`_build_chapter_content`)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: `chapter["blocks"]` (Task 3), `build_fragment_157(italic=, bold=)` (Task 4), `build_fragment_259(emphasis_spans=)` (Task 5), the existing `_allocate_style` cache.
- Produces: text chunks carry `chunk["spans"]: list[(start, length, frozenset)]` rebased to the chunk; the `$259` build loop passes `emphasis_spans` with resolved style names; new emphasis styles are appended to `extra_style_names`.

- [ ] **Step 1: Write the failing integration test**

```python
@pytest.mark.unit
def test_emphasis_block_produces_italic_span_in_book(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS
    from kfxgen.inline_style import FLAG_ITALIC
    gen = NativeKFXGenerator()
    chapters = [{
        "title": "Ch",
        "text": "a big cat",
        "blocks": [{"text": "a big cat", "spans": [(2, 3, frozenset({FLAG_ITALIC}))]}],
    }]
    out = tmp_path / "out.kfx"
    gen.generate_full_book(title="T", author="A", chapters=chapters,
                           output_path=str(out))
    # An italic $157 ($12 -> $382) must exist among emitted fragments.
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    assert any(
        IS("$12") in f.value and f.value[IS("$12")] == IS("$382")
        for f in styles
    )
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python3.13 -m pytest tests/unit/test_native_generator.py -q -k emphasis_block_produces`
Expected: FAIL (no italic `$157`).

- [ ] **Step 3: Attach block spans to text chunks**

In `_build_chapter_content`, the `else` branch that splits `chapter["text"]` into paragraphs is where blocks attach. When `chapter.get("blocks")` is present, iterate blocks instead of `text.split("\n\n")`, carrying spans. Replace the paragraph loop body so each paragraph processes its block:

```python
                blocks = chapter.get("blocks")
                if blocks is not None:
                    para_iter = blocks
                else:
                    para_iter = [{"text": p, "spans": []} for p in text.split("\n\n")]

                for block in para_iter:
                    para = block["text"].strip()
                    if not para:
                        continue
                    para_spans = block.get("spans", [])
                    for chunk in _emit_text_chunks(para):
                        if chunk["type"] == "image":
                            all_chunks.append(chunk)
                        else:
                            _append_text_with_spans(chunk["text"], para, para_spans)
```

Add a helper (near `_split_long_text`) that rebases paragraph spans onto each emitted text chunk. Because `_emit_text_chunks` strips segments and `_split_long_text` cuts at `CHUNK_SIZE`, map spans by locating the chunk text within the paragraph and intersecting ranges:

```python
        def _append_text_with_spans(chunk_text, para_text, para_spans):
            """Split chunk_text by CHUNK_SIZE and attach the slice of
            para_spans covering each piece, offsets rebased to the piece.
            The chunk_text is a (stripped) substring of para_text; find its
            offset once, then translate spans."""
            base = para_text.find(chunk_text)
            if base < 0:
                base = 0  # defensive: emphasis simply won't apply to this chunk
            pos = 0
            while pos < len(chunk_text):
                piece = chunk_text[pos : pos + self.CHUNK_SIZE]
                p_start = base + pos
                p_end = p_start + len(piece)
                pspans = []
                for s, length, flags in para_spans:
                    a = max(s, p_start)
                    b = min(s + length, p_end)
                    if b > a:
                        pspans.append((a - p_start, b - a, flags))
                all_chunks.append({"type": "text", "text": piece, "spans": pspans})
                pos += self.CHUNK_SIZE
```

(Replace the prior `all_chunks.extend(_split_long_text(chunk["text"]))` path for the blocks case; keep `_split_long_text` for any remaining non-block callers.)

- [ ] **Step 4: Allocate emphasis styles and pass spans to `$259`**

In the `$259` build loop, for each text chunk compute its emphasis spans with resolved style names. Add an emphasis-style allocator using the existing `_allocate_style`, and build `entry_emphasis_spans`:

```python
        from .inline_style import FLAG_BOLD, FLAG_ITALIC

        def _emphasis_style(flags):
            return _allocate_style(
                "_em",
                italic=FLAG_ITALIC in flags,
                bold=FLAG_BOLD in flags,
            )
```

Inside the loop over `chunk_idx`, in the text branch, after appending the entry's other per-chunk lists, build its emphasis spans (default empty):

```python
                chunk_spans = all_chunks[chunk_idx].get("spans", [])
                entry_emphasis_spans.append(
                    [(s, length, _emphasis_style(flags)) for (s, length, flags) in chunk_spans]
                )
```

Initialize `entry_emphasis_spans = []` alongside the other `entry_*` lists, and append `None` for image entries (mirroring `entry_link_targets`). Styles created by `_emphasis_style` must be registered: after the chapter loop, add any `_em` styles from `style_cache` to `extra_style_names` (they are needed in `$419`/`$270`). Simplest: in `_allocate_style`, when `kind == "_em"` and the name is newly created, append it to `extra_style_names`. Pass the new list to `build_fragment_259(..., emphasis_spans=entry_emphasis_spans)`.

- [ ] **Step 5: Run tests, verify pass**

Run: `python3.13 -m pytest tests/unit/test_native_generator.py -q`
Expected: PASS (new integration test + all existing generator tests).

- [ ] **Step 6: Lint + commit**

```bash
ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(emphasis): wire emphasis spans+styles through chapter content (#9)"
```

---

### Task 7: Golden coverage + byte-stable-default guard

**Files:**
- Modify: `tests/unit/test_native_generator.py` (or the golden test module)
- Possibly add: a small emphasis fixture under `tests/fixtures/`

**Interfaces:**
- Consumes: the full pipeline from Tasks 1–6.

- [ ] **Step 1: Add a default-stability test (guards margin/output regressions)**

```python
@pytest.mark.unit
def test_plain_chapter_emits_no_emphasis_fragments(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS
    gen = NativeKFXGenerator()
    chapters = [{"title": "Ch", "text": "plain text only"}]  # no blocks
    gen.generate_full_book(title="T", author="A", chapters=chapters,
                           output_path=str(tmp_path / "o.kfx"))
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    assert all(IS("$12") not in f.value for f in styles)  # no italic anywhere
```

- [ ] **Step 2: Run the full unit suite**

Run: `python3.13 -m pytest tests/unit -q`
Expected: PASS (all). If any existing golden fixture compares bytes, confirm it is unchanged; the no-blocks path must not alter default output.

- [ ] **Step 3: Lint everything + commit**

```bash
ruff check . && ruff format --check .
git add -A
git commit -m "test(emphasis): default-stability guard for inline emphasis (#9)"
```

- [ ] **Step 4: Manual device-verification note (release gate, not automated)**

Convert an EPUB containing `<em>`/`<strong>` runs with the built plugin and confirm italic/bold render on a physical Kindle before tagging a release. Record the result on issue #9. (Device tests are the repo's release gate and are not part of CI.)

---

## Self-review

- **Spec coverage:** Inline emphasis (italic/bold/bold-italic) — Tasks 1–6. Spans as `$142` — Task 5. Nested/combined flags — Tasks 1, 2 tests. Style dedup — reuses existing `_allocate_style` (Task 6). Byte-stable defaults — Task 7. Device gate — Task 7 Step 4. The CSS subset (align/indent/margins) is explicitly deferred to Plan B (stated up front) — not a gap, a scoping decision.
- **Placeholders:** none — every code/test step has concrete code.
- **Type consistency:** `flags` is `frozenset` throughout (Tasks 1–6); span tuples are `(start, length, flags)` in extraction and `(start, length, style_name)` at the `$142` emission boundary (translated in Task 6 Step 4); `normalize_runs`, `extract_blocks_from_html`, `build_fragment_157(italic=)`, `build_fragment_259(emphasis_spans=)` signatures match across tasks.
