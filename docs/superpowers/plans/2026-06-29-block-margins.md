# Block Horizontal Margins Implementation Plan (Plan C of #9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry per-element `margin-left` and `margin-right` from the source EPUB through to KFX block styles (blockquotes, indented blocks), via Calibre's Stylizer with the existing graceful fallback.

**Architecture:** Extends Plan B's `block_style` machinery. `compute_block_style` reads two more CSS props; the Stylizer resolver returns them; `build_fragment_157` overrides `$48` (margin-left) and emits `$50` (margin-right); `_build_chapter_content` threads them through the existing `_allocate_style` cache.

**Tech Stack:** Python 3.13, pytest, lxml/Calibre OEB + Stylizer, vendored `kfxlib_minimal`.

## Global Constraints

- Python via the repo venv at `/private/tmp/claude-501/-Users-justin-GitHub-kfxgen-public/0a357a5c-3454-41ea-a7c4-2fcc79a97b37/scratchpad/venv/bin`; never bare `python`.
- Lint gate: BOTH `ruff check .` and `ruff format --check .`, pinned **ruff==0.15.1**. Run both before every commit.
- KFX symbols (authoritative): margin-left = `$48`, margin-right = `$50`. Length units: em=`$308`, rem=`$505`, %=`$314`, pt=`$318`, px=`$319`, mm=`$316`.
- `parse_css_length` (already present) returns `(mag, unit_sym)` or `None`; it already rejects negative/zero magnitudes and unsupported units — reuse it (negative margins must NOT be applied).
- `block_style` carries pre-parsed `(mag, unit_sym)` tuples for margins (same shape as `indent`).
- **Byte-stable defaults:** with no source margins, output is identical to today — `$48` stays `0.5`/`$314`, no `$50`. Do not change the existing defaults.
- margin-top (`$47`), margin-bottom (`$49`), and padding are OUT of scope.

---

### Task 1: `compute_block_style` reads margin-left / margin-right

**Files:**
- Modify: `plugin/kfxgen/inline_style.py` (`compute_block_style`)
- Test: `tests/unit/test_inline_style.py`

**Interfaces:**
- Consumes: `parse_css_length` (existing).
- Produces: `compute_block_style(css)` now returns `{"align", "indent", "margin_left", "margin_right"}`; the two new keys are `(mag, unit_sym)` tuples or `None`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_compute_block_style_margins():
    bs = ist.compute_block_style({"margin-left": "2em", "margin-right": "1em"})
    assert bs["margin_left"] == ("2", "$308")
    assert bs["margin_right"] == ("1", "$308")


@pytest.mark.unit
def test_compute_block_style_margins_absent_and_negative():
    bs = ist.compute_block_style({})
    assert bs["margin_left"] is None and bs["margin_right"] is None
    # negative margins are dropped (no clipping), like negative indent
    bs2 = ist.compute_block_style({"margin-left": "-3em"})
    assert bs2["margin_left"] is None
```

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_inline_style.py -q -k compute_block_style_margins`
Expected: FAIL (`KeyError: 'margin_left'`).

- [ ] **Step 3: Implement**

In `compute_block_style`, add the two reads and include them in the returned dict:

```python
def compute_block_style(css):
    """Map a computed-CSS dict to kfxgen's block_style shape.

    `css` is a mapping that supports .get(prop) returning CSS strings (e.g. a
    Calibre Stylizer Style, or a plain dict in tests). Returns
    {"align": <keyword or None>, "indent"/"margin_left"/"margin_right":
    <(mag, unit_sym) or None>}. The align keyword is mapped to a symbol later,
    in build_fragment_157.
    """
    align = None
    raw_align = (css.get("text-align") or "").strip().lower()
    if raw_align in ALIGN_MAP:
        align = raw_align
    indent = parse_css_length(css.get("text-indent") or "")
    margin_left = parse_css_length(css.get("margin-left") or "")
    margin_right = parse_css_length(css.get("margin-right") or "")
    return {
        "align": align,
        "indent": indent,
        "margin_left": margin_left,
        "margin_right": margin_right,
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `<venv>/bin/pytest tests/unit/test_inline_style.py -q`
Expected: PASS (new + existing).

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
<venv>/bin/ruff format plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git add plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git commit -m "feat(css): compute_block_style reads margin-left/right (#9)"
```

---

### Task 2: `build_fragment_157` — `margin_left` override + `margin_right` emit

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`build_fragment_157`)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Produces: `build_fragment_157(..., margin_left=None, margin_right=None)`. `margin_left` (a `(mag, unit_sym)` tuple) overrides the default `$48` (0.5%/`$314`); `margin_right` (tuple) emits `$50`. Both `None` → byte-identical to today (`$48`=0.5%, no `$50`).

- [ ] **Step 1: Write the failing tests**

```python
    def test_margin_left_overrides_48(self):
        from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
        gen = NativeKFXGenerator()
        frag = gen.build_fragment_157(entity_name="sml", margin_left=("2", "$308"))
        ml = frag.value[IS("$48")]
        assert ml[IS("$307")] == IonDecimal("2")
        assert ml[IS("$306")] == IS("$308")

    def test_margin_right_emits_50(self):
        from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
        gen = NativeKFXGenerator()
        frag = gen.build_fragment_157(entity_name="smr", margin_right=("1", "$308"))
        mr = frag.value[IS("$50")]
        assert mr[IS("$307")] == IonDecimal("1")
        assert mr[IS("$306")] == IS("$308")

    def test_margins_default_byte_stable(self):
        from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
        gen = NativeKFXGenerator()
        frag = gen.build_fragment_157(entity_name="smd")
        ml = frag.value[IS("$48")]
        assert ml[IS("$307")] == IonDecimal("0.5")
        assert ml[IS("$306")] == IS("$314")
        assert IS("$50") not in frag.value
```

(These are methods of `TestBuildFragment157`, which carries a class-level `pytestmark = pytest.mark.unit`.)

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k "margin_left_overrides or margin_right_emits or margins_default_byte"`
Expected: FAIL (`TypeError: unexpected keyword 'margin_left'`).

- [ ] **Step 3: Implement**

Add `margin_left=None, margin_right=None` to the signature (after `text_indent=None`). The `$48` entry currently hardcodes `IonStruct(IS("$307"), IonDecimal("0.5"), IS("$306"), IS("$314"))`. Compute it conditionally before the `value = IonStruct(...)` construction (mirroring `indent_struct`):

```python
        # $48 = margin-left; default 0.5%, overridden per element.
        if margin_left is not None:
            margin_left_struct = IonStruct(
                IS("$307"), IonDecimal(margin_left[0]),
                IS("$306"), IS(margin_left[1]),
            )
        else:
            margin_left_struct = IonStruct(
                IS("$307"), IonDecimal("0.5"), IS("$306"), IS("$314")
            )
```

Use `margin_left_struct` in place of the inline `$48` value in the `value = IonStruct(...)`. Then after the value is built (near the other conditional `value[IS(...)] = ...` blocks), add:

```python
        # $50 = margin-right; emitted only when the source specifies it.
        if margin_right is not None:
            value[IS("$50")] = IonStruct(
                IS("$307"), IonDecimal(margin_right[0]),
                IS("$306"), IS(margin_right[1]),
            )
```

- [ ] **Step 4: Run, verify pass**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k "margin or style or align or text_indent or bold or italic"`
Expected: PASS (new + existing style tests confirm defaults unchanged).

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
<venv>/bin/ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(css): margin-left override + margin-right in build_fragment_157 (#9)"
```

---

### Task 3: Stylizer resolver returns margin-left / margin-right

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`_build_style_resolver`'s `resolve` closure)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Produces: the resolver's returned css dict now includes `margin-left` and `margin-right` (so the real Stylizer path provides them to `compute_block_style`).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.unit
def test_blocks_block_style_margins_from_resolver():
    doc = _doc("<blockquote>quoted</blockquote><p>plain</p>")

    def resolver(elem):
        txt = "".join(elem.itertext())
        if "quoted" in txt:
            return {"margin-left": "2em", "margin-right": "1em"}
        return {}

    blocks = _conv.extract_blocks_from_html(doc, style_resolver=resolver)
    assert blocks[0]["block_style"]["margin_left"] == ("2", "$308")
    assert blocks[0]["block_style"]["margin_right"] == ("1", "$308")
    assert blocks[1]["block_style"]["margin_left"] is None
```

(`blockquote` is already in `extract_blocks_from_html`'s block-tag set.)

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_converter.py -q -k block_style_margins`
Expected: FAIL (`KeyError: 'margin_left'` — Task 1 must be merged first; the resolver test here exercises `compute_block_style` via `extract_blocks_from_html`, so this passes once Task 1 lands and only needs the resolver change for the REAL Stylizer path).

- [ ] **Step 3: Implement**

In `_build_style_resolver`, extend the `resolve` closure's returned dict:

```python
        def resolve(elem):
            try:
                st = stylizer.style(elem)
                return {
                    "text-align": st.get("text-align"),
                    "text-indent": st.get("text-indent"),
                    "margin-left": st.get("margin-left"),
                    "margin-right": st.get("margin-right"),
                }
            except Exception:
                return None
```

(The test above uses a fake resolver, so it validates the extraction→compute path; this change wires the REAL Stylizer to supply margins — verified in Task 5's real-Stylizer smoke.)

- [ ] **Step 4: Run, verify pass (full converter suite)**

Run: `<venv>/bin/pytest tests/unit/test_converter.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
<venv>/bin/ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat(css): Stylizer resolver returns margin-left/right (#9)"
```

---

### Task 4: Thread margins through `_build_chapter_content`

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`_build_chapter_content` plain-text entry branch)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: `chunk["block_style"]["margin_left"]`/`["margin_right"]` (Tasks 1,3), `build_fragment_157(margin_left=, margin_right=)` (Task 2), the `_allocate_style` cache.
- Produces: plain-text entries whose block has margins get a `$157` allocated with those margins (deduped). No-margin chunks resolve to the same cached style as before (byte-stable).

- [ ] **Step 1: Write the failing integration test**

```python
def test_block_margin_left_produces_overridden_48(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
    gen = NativeKFXGenerator()
    chapters = [{
        "title": "Ch",
        "text": "quoted line",
        "blocks": [{
            "text": "quoted line", "spans": [],
            "block_style": {"align": None, "indent": None,
                            "margin_left": ("2", "$308"), "margin_right": None},
        }],
    }]
    gen.generate_full_book(title="T", author="A", chapters=chapters,
                           output_path=str(tmp_path / "o.kfx"))
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    hit = [
        f for f in styles
        if IS("$48") in f.value
        and f.value[IS("$48")].get(IS("$307")) == IonDecimal("2")
        and f.value[IS("$48")].get(IS("$306")) == IS("$308")
    ]
    assert hit, "expected a $157 with margin-left overridden to 2em"
```

(This is a module-level function — add `@pytest.mark.unit` above it, matching the other module-level integration tests. Use a `g(struct, key)` helper if IonStruct lacks `.get`, as in earlier tests.)

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k block_margin_left_produces`
Expected: FAIL (no overridden `$48` — margins not threaded).

- [ ] **Step 3: Implement**

In the plain-text entry branch (the `bs = chunk.get("block_style") or {}` block, ~line 2606), add the two margin attrs:

```python
                    bs = chunk.get("block_style") or {}
                    attrs = {"font_size": chapters[ch_idx].get("font_size", 1.0)}
                    if bs.get("align"):
                        attrs["align"] = bs["align"]
                    if bs.get("indent"):
                        attrs["text_indent"] = bs["indent"]
                    if bs.get("margin_left"):
                        attrs["margin_left"] = bs["margin_left"]
                    if bs.get("margin_right"):
                        attrs["margin_right"] = bs["margin_right"]
                    entry_styles.append(_allocate_style("", **attrs))
```

When no margins are present, `attrs` is unchanged from Plan B → same cached style → byte-stable.

- [ ] **Step 4: Run, verify pass (full generator + converter suites)**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py tests/unit/test_converter.py -q`
Expected: PASS (new integration test + all existing; defaults unchanged).

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
<venv>/bin/ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(css): thread block margins through chapter content (#9)"
```

---

### Task 5: Byte-stable guard + verification gates

**Files:**
- Modify: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: the full pipeline (Tasks 1–4).

- [ ] **Step 1: Extend the default-stability test**

Add a guard asserting that an unstyled book keeps `$48`=0.5%/`$314` and emits no `$50`:

```python
def test_no_block_style_keeps_default_margins(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
    gen = NativeKFXGenerator()
    chapters = [{"title": "Ch", "text": "plain body text"}]  # no blocks
    gen.generate_full_book(title="T", author="A", chapters=chapters,
                           output_path=str(tmp_path / "o.kfx"))
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    for f in styles:
        if IS("$48") in f.value:
            assert f.value[IS("$48")][IS("$306")] == IS("$314")  # default % unit
        assert IS("$50") not in f.value  # margin-right never default
```

(Add `@pytest.mark.unit`.)

- [ ] **Step 2: Run the full unit suite**

Run: `<venv>/bin/pytest tests/unit -q`
Expected: PASS (all).

- [ ] **Step 3: Lint repo-wide + commit**

```bash
<venv>/bin/ruff check . && <venv>/bin/ruff format --check .
git add -A
git commit -m "test(css): default-margin stability guard (#9)"
```

- [ ] **Step 4: Real-Stylizer smoke (anti-silent-no-op; manual, controller-run)**

The unit tests use a fake resolver; the `EpubAsOeb` shim can't drive the real Stylizer. Build an EPUB with a `<blockquote>` styled `margin-left: 2em` (CSS file or inline `style=`), convert it through the real Calibre Stylizer (branch code via the input-plugin → `create_oebbook` → `convert_oeb_to_kfx` harness, no install), decode the `.kfx`, and assert a `$157` carries `$48` with a non-`$314` unit (the blockquote's em margin). If absent, the resolver didn't supply margins — investigate before proceeding.

- [ ] **Step 5: Gutenberg corpus + device gates (manual, release-time)**

Run the 90-book corpus crash/no-regression check (content gate; margins don't change text retention). On a physical Kindle, confirm blockquotes/indented blocks render with the expected left (and right) offset. Record on #9.

---

## Self-review

- **Spec coverage:** margin-left `$48` override (Tasks 1,2,4) ✓; margin-right `$50` emit (Tasks 1,2,4) ✓; Stylizer supplies margins (Task 3) ✓; negatives rejected (reuses `parse_css_length`, tested Task 1) ✓; byte-stable defaults (Tasks 2,5) ✓; real-Stylizer smoke + corpus + device (Task 5) ✓. Vertical margins/padding explicitly out of scope (spec) — not a gap.
- **Placeholders:** none — every code/test step has concrete code.
- **Type consistency:** `block_style` keys `margin_left`/`margin_right` are `(mag, unit_sym)` tuples or `None` in Tasks 1,3,4; `build_fragment_157(margin_left=, margin_right=)` signature matches across Tasks 2,4; `$48`/`$50` symbols and unit symbols consistent throughout.
