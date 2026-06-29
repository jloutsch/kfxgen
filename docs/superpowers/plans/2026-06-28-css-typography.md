# CSS Typography Subset Implementation Plan (Plan B of #9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry per-element `text-align` and `text-indent` from the source EPUB through to KFX styles, resolved via Calibre's Stylizer with a graceful no-Calibre fallback.

**Architecture:** Pure helpers in `inline_style.py` parse/map CSS values to KFX symbols; `converter.py` resolves per-element computed CSS via a Stylizer-backed `style_resolver` and attaches `block_style` to each block; `native_generator.py` overrides the fixed `$34`/`$36` in `build_fragment_157` and threads `block_style` through the existing `_allocate_style` cache. Builds directly on Plan A's `blocks` data model.

**Tech Stack:** Python 3.13, pytest, lxml/Calibre OEB + Stylizer, the vendored `kfxlib_minimal`.

## Global Constraints

- Python invoked as `python3.13` / the repo venv at `/private/tmp/claude-501/-Users-justin-GitHub-kfxgen-public/0a357a5c-3454-41ea-a7c4-2fcc79a97b37/scratchpad/venv/bin`; never bare `python`.
- Lint gate is BOTH `ruff check .` and `ruff format --check .`, pinned **ruff==0.15.1**. Run both before every commit.
- KFX `$306` length-unit symbols: em=`$308`, rem=`$505`, %=`$314`, pt=`$318`, px=`$319`, mm=`$316`, lh=`$310`.
- text-align symbols (`$34`): center=`$320`, justify=`$321`, left=`$59`, right=`$61`.
- A source CSS length maps to KFX by unit-mapping, not numeric conversion. Unsupported units (`vw`,`vh`,`ch`,`ex`), `auto`, and zero magnitude → no override.
- **Behaviors:** honor every source text-align incl. `left` (fall back to justify only when source is unset); when a paragraph has non-zero text-indent, suppress its `$47` padding-top.
- **Byte-stable defaults:** blocks with no `block_style` (no Stylizer / unset CSS) must produce output identical to Plan A. Verified in Task 8.
- `block_style` carries the raw align keyword (str) and a pre-parsed `(mag, unit_sym)` indent; `ALIGN_MAP` is applied only in `build_fragment_157`.

---

### Task 1: `parse_css_length`

**Files:**
- Modify: `plugin/kfxgen/inline_style.py`
- Test: `tests/unit/test_inline_style.py`

**Interfaces:**
- Produces: `parse_css_length(value: str) -> tuple[str, str] | None` — returns `(magnitude_str, unit_symbol)` or `None`. Magnitude preserved as a trimmed string (e.g. `"1.5"`); unit mapped to its `$306` symbol.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_parse_css_length_units():
    assert ist.parse_css_length("1.5em") == ("1.5", "$308")
    assert ist.parse_css_length("2rem") == ("2", "$505")
    assert ist.parse_css_length("5%") == ("5", "$314")
    assert ist.parse_css_length("12pt") == ("12", "$318")
    assert ist.parse_css_length("3px") == ("3", "$319")
    assert ist.parse_css_length("4mm") == ("4", "$316")


@pytest.mark.unit
def test_parse_css_length_rejects():
    assert ist.parse_css_length("") is None
    assert ist.parse_css_length("auto") is None
    assert ist.parse_css_length("0") is None
    assert ist.parse_css_length("0em") is None
    assert ist.parse_css_length("2vw") is None
    assert ist.parse_css_length("3ch") is None
    assert ist.parse_css_length("inherit") is None
```

(Add `from kfxgen import inline_style as ist` if not already imported at the top of the test file — it is, from Plan A.)

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_inline_style.py -q -k parse_css_length`
Expected: FAIL (`AttributeError: parse_css_length`).

- [ ] **Step 3: Implement**

Append to `plugin/kfxgen/inline_style.py`:

```python
import re

#: CSS length unit -> KFX $306 unit symbol.
_CSS_UNIT_TO_KFX = {
    "em": "$308",
    "rem": "$505",
    "%": "$314",
    "pt": "$318",
    "px": "$319",
    "mm": "$316",
}

_LENGTH_RE = re.compile(r"^\s*([+-]?[0-9]*\.?[0-9]+)\s*(em|rem|%|pt|px|mm)\s*$", re.I)


def parse_css_length(value):
    """Parse a CSS length string into (magnitude_str, kfx_unit_symbol).

    Returns None for empty/auto/inherit, unsupported units, or a zero
    magnitude (no override needed). Magnitude is returned as a trimmed
    string so the caller can hand it to IonDecimal unchanged.
    """
    if not value:
        return None
    m = _LENGTH_RE.match(value)
    if not m:
        return None
    mag, unit = m.group(1), m.group(2).lower()
    try:
        if float(mag) == 0.0:
            return None
    except ValueError:
        return None
    # Normalize "2.0" -> "2", "1.50" -> "1.5" without forcing a float repr.
    mag = mag.strip()
    if "." in mag:
        mag = mag.rstrip("0").rstrip(".")
    return (mag, _CSS_UNIT_TO_KFX[unit])
```

- [ ] **Step 4: Run, verify pass**

Run: `<venv>/bin/pytest tests/unit/test_inline_style.py -q -k parse_css_length`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
<venv>/bin/ruff format plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git add plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git commit -m "feat(css): parse_css_length CSS-length-to-KFX-unit mapper (#9)"
```

---

### Task 2: `ALIGN_MAP` + `compute_block_style`

**Files:**
- Modify: `plugin/kfxgen/inline_style.py`
- Test: `tests/unit/test_inline_style.py`

**Interfaces:**
- Consumes: `parse_css_length` (Task 1).
- Produces: `ALIGN_MAP: dict[str,str]`; `compute_block_style(css: dict) -> dict` returning `{"align": str|None, "indent": tuple|None}`. `align` is the raw CSS keyword (one of left/right/center/justify) or `None`; `indent` is a `(mag, unit_sym)` tuple or `None`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_align_map_values():
    assert ist.ALIGN_MAP == {
        "left": "$59", "right": "$61", "center": "$320", "justify": "$321",
    }


@pytest.mark.unit
def test_compute_block_style_align():
    assert ist.compute_block_style({"text-align": "center"})["align"] == "center"
    assert ist.compute_block_style({"text-align": "left"})["align"] == "left"
    assert ist.compute_block_style({"text-align": "JUSTIFY"})["align"] == "justify"
    assert ist.compute_block_style({"text-align": "start"})["align"] is None
    assert ist.compute_block_style({})["align"] is None


@pytest.mark.unit
def test_compute_block_style_indent():
    assert ist.compute_block_style({"text-indent": "1.5em"})["indent"] == ("1.5", "$308")
    assert ist.compute_block_style({"text-indent": "0"})["indent"] is None
    assert ist.compute_block_style({})["indent"] is None
```

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_inline_style.py -q -k "align_map or compute_block_style"`
Expected: FAIL (`AttributeError: ALIGN_MAP` / `compute_block_style`).

- [ ] **Step 3: Implement**

Append to `plugin/kfxgen/inline_style.py`:

```python
#: CSS text-align keyword -> KFX $34 value symbol.
ALIGN_MAP = {"left": "$59", "right": "$61", "center": "$320", "justify": "$321"}


def compute_block_style(css):
    """Map a computed-CSS dict to kfxgen's block_style shape.

    `css` is a mapping that supports .get(prop) returning CSS strings (e.g. a
    Calibre Stylizer Style, or a plain dict in tests). Returns
    {"align": <keyword or None>, "indent": <(mag, unit_sym) or None>}.
    The align keyword is mapped to a symbol later, in build_fragment_157.
    """
    align = None
    raw_align = (css.get("text-align") or "").strip().lower()
    if raw_align in ALIGN_MAP:
        align = raw_align
    indent = parse_css_length(css.get("text-indent") or "")
    return {"align": align, "indent": indent}
```

- [ ] **Step 4: Run, verify pass**

Run: `<venv>/bin/pytest tests/unit/test_inline_style.py -q -k "align_map or compute_block_style"`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
<venv>/bin/ruff format plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git add plugin/kfxgen/inline_style.py tests/unit/test_inline_style.py
git commit -m "feat(css): ALIGN_MAP + compute_block_style (#9)"
```

---

### Task 3: `extract_blocks_from_html` accepts a `style_resolver`

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`extract_blocks_from_html`, currently line ~86)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `inline_style.compute_block_style` (Task 2).
- Produces: `extract_blocks_from_html(element, style_resolver=None) -> list[dict]`; each block dict gains `"block_style": {"align","indent"} | None`. When `style_resolver` is `None`, `block_style` is `None` (Plan A behavior). `style_resolver` is a callable `elem -> css_dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_blocks_block_style_from_resolver():
    doc = _doc("<p>centered</p><p>plain</p>")  # _doc helper exists from Plan A

    def resolver(elem):
        # first <p> centered + indented, second has nothing
        txt = "".join(elem.itertext())
        if "centered" in txt:
            return {"text-align": "center", "text-indent": "2em"}
        return {}

    blocks = converter.extract_blocks_from_html(doc, style_resolver=resolver)
    assert blocks[0]["block_style"] == {"align": "center", "indent": ("2", "$308")}
    assert blocks[1]["block_style"] == {"align": None, "indent": None}


@pytest.mark.unit
def test_blocks_block_style_none_without_resolver():
    doc = _doc("<p>x</p>")
    blocks = converter.extract_blocks_from_html(doc)
    assert blocks[0]["block_style"] is None
```

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_converter.py -q -k block_style`
Expected: FAIL (`TypeError: unexpected keyword 'style_resolver'` or `KeyError: 'block_style'`).

- [ ] **Step 3: Implement**

Add the import near the other inline_style import (converter.py line ~19):

```python
from .inline_style import FLAG_BOLD, FLAG_ITALIC, compute_block_style, normalize_runs
```

Change the signature and the block-building loop in `extract_blocks_from_html`. The current loop appends `{"text": text, "spans": spans}` for each block element; add `block_style`:

```python
def extract_blocks_from_html(element, style_resolver=None):
    ...
    for elem in body.iter():
        if elem.tag in block_tags:
            if any(child.tag in block_tags for child in elem):
                continue
            text, spans = normalize_runs(_walk_inline(elem))
            if text:
                bstyle = None
                if style_resolver is not None:
                    css = style_resolver(elem)
                    if css is not None:
                        bstyle = compute_block_style(css)
                blocks.append({"text": text, "spans": spans, "block_style": bstyle})
            continue
        if _local_tag(elem.tag) == "img":
            ...
            blocks.append({"text": _make_img_token(href, alt), "spans": [], "block_style": None})
```

Also add `"block_style": None` to the no-block fallback return at the end (`[{"text": text, "spans": [], "block_style": None}]`).

- [ ] **Step 4: Run, verify pass (incl. existing converter tests)**

Run: `<venv>/bin/pytest tests/unit/test_converter.py -q`
Expected: PASS (new + all existing). `extract_text_from_html` is unaffected (it joins `b["text"]`).

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
<venv>/bin/ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat(css): block_style via style_resolver in extract_blocks_from_html (#9)"
```

---

### Task 4: Stylizer-backed resolver in `extract_chapters_from_oeb`

**Files:**
- Modify: `plugin/kfxgen/converter.py` (`extract_chapters_from_oeb` spine loop; add a `_build_style_resolver` helper)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `extract_blocks_from_html(element, style_resolver=...)` (Task 3).
- Produces: a `_build_style_resolver(oeb_book, item, log) -> callable | None` helper; the spine loop passes its result into `extract_blocks_from_html`. On any failure or outside Calibre, returns `None` (so `block_style` stays `None`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_style_resolver_none_outside_calibre(monkeypatch):
    # calibre.ebooks.oeb.stylizer is absent in CI -> resolver is None
    import logging
    r = converter._build_style_resolver(object(), object(), logging.getLogger("t"))
    assert r is None


@pytest.mark.unit
def test_chapters_carry_block_style_with_fake_stylizer(monkeypatch, simple_oeb_centered):
    # simple_oeb_centered: an EpubAsOeb whose one spine item is
    # <p class="c">Title</p><p>body</p> with .c { text-align:center }.
    # Monkeypatch _build_style_resolver to a fake so the test needs no Calibre.
    def fake_builder(oeb, item, log):
        def resolver(elem):
            cls = (elem.get("class") or "")
            return {"text-align": "center"} if "c" in cls.split() else {}
        return resolver

    monkeypatch.setattr(converter, "_build_style_resolver", fake_builder)
    import logging
    chapters = converter.extract_chapters_from_oeb(simple_oeb_centered, logging.getLogger("t"))
    blocks = chapters[0].get("blocks", [])
    assert any((b.get("block_style") or {}).get("align") == "center" for b in blocks)
```

If `simple_oeb_centered` doesn't exist, build it with the same EpubBuilder/`_xhtml_page` shim used by the other `extract_chapters_from_oeb` tests; the class+CSS only needs to survive into the spine item's lxml (the fake resolver reads the `class` attribute, so no real CSS cascade is required).

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_converter.py -q -k "style_resolver or block_style_with_fake"`
Expected: FAIL (`AttributeError: _build_style_resolver`).

- [ ] **Step 3: Implement**

Add the helper near the top of `converter.py` (after imports). The Stylizer constructor signature was confirmed by spike (calibre 9.10.0): `Stylizer(tree, path, oeb, opts, profile=None, extra_css='', user_css='', base_css='')` — so the positional call below (`tree, path, oeb, opts, profile`) is correct. The try/except still wraps it so any cross-version drift degrades to `None` (no per-element CSS) rather than raising into the pipeline.

**Anti-silent-no-op note:** because the fallback returns `None` on failure and the unit tests use a *fake* resolver, a broken real Stylizer would make Plan B a silent no-op that the unit suite cannot catch (the `EpubAsOeb` test shim has no `opts`/oeb for Stylizer, so it always yields `None`). The *only* thing that exercises the real Stylizer is a conversion through the actual Calibre pipeline — validated in Task 8 Step 4a (real-pipeline smoke). Do not consider Task 4 "working" on unit tests alone.

```python
def _build_style_resolver(oeb_book, item, log):
    """Return a callable elem->computed-CSS-dict using Calibre's Stylizer, or
    None when Calibre/Stylizer is unavailable or construction fails. Never
    raises — failure degrades to no per-element block styling."""
    try:
        from calibre.ebooks.oeb.stylizer import Stylizer
    except Exception:
        return None
    try:
        data = getattr(item, "data", None)
        if data is None:
            return None
        profile = getattr(getattr(oeb_book, "opts", None), "output_profile", None)
        stylizer = Stylizer(
            data, getattr(item, "href", "") or "", oeb_book,
            getattr(oeb_book, "opts", None), profile,
        )

        def resolve(elem):
            try:
                st = stylizer.style(elem)
                return {
                    "text-align": st.get("text-align"),
                    "text-indent": st.get("text-indent"),
                }
            except Exception:
                return None

        return resolve
    except Exception as e:
        log.warn(f"  Stylizer unavailable ({e}); skipping per-element CSS")
        return None
```

In the spine loop where `extract_blocks_from_html(item.data)` is called (Task-3 code path), build and pass the resolver:

```python
            resolver = _build_style_resolver(oeb_book, item, log)
            blocks = extract_blocks_from_html(item.data, style_resolver=resolver)
            text = "\n\n".join(b["text"] for b in blocks)
```

- [ ] **Step 4: Run, verify pass**

Run: `<venv>/bin/pytest tests/unit/test_converter.py -q`
Expected: PASS (new + existing). The no-Calibre default keeps `block_style=None`, so existing chapter tests are unchanged.

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/converter.py tests/unit/test_converter.py
<venv>/bin/ruff format plugin/kfxgen/converter.py tests/unit/test_converter.py
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat(css): Stylizer-backed style_resolver with fallback (#9)"
```

---

### Task 5: `build_fragment_157` — `align` override

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`build_fragment_157`)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: `inline_style.ALIGN_MAP` (Task 2).
- Produces: `build_fragment_157(..., align=None)`; when `align` is a keyword in `ALIGN_MAP`, `value[IS("$34")]` is set to the mapped symbol instead of the default `$321`. `align=None` → unchanged default.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_align_overrides_text_align():
    from kfxgen.kfxlib_minimal.ion import IS
    gen = NativeKFXGenerator()
    assert gen.build_fragment_157(entity_name="sa", align="center").value[IS("$34")] == IS("$320")
    assert gen.build_fragment_157(entity_name="sb", align="left").value[IS("$34")] == IS("$59")
    assert gen.build_fragment_157(entity_name="sc", align="right").value[IS("$34")] == IS("$61")


@pytest.mark.unit
def test_align_default_is_justify():
    from kfxgen.kfxlib_minimal.ion import IS
    gen = NativeKFXGenerator()
    assert gen.build_fragment_157(entity_name="sd").value[IS("$34")] == IS("$321")
```

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k "align_overrides or align_default"`
Expected: FAIL (`TypeError: unexpected keyword 'align'`).

- [ ] **Step 3: Implement**

Add `align=None` to the signature (after `italic=False`). Import the map at top of `native_generator.py` (with the other inline_style import if present, else add `from .inline_style import ALIGN_MAP`). In the value construction, the `$34` entry currently hardcodes `IS("$321")`. Replace that literal with a computed value:

```python
        # $34 = text-align; default justify ($321), overridden per element.
        text_align = IS(ALIGN_MAP[align]) if align in ALIGN_MAP else IS("$321")
```

and use `text_align` where `IS("$321")` was in the IonStruct.

- [ ] **Step 4: Run, verify pass**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k "align or style or margin or bold or italic or underline or line_height"`
Expected: PASS (new + existing style tests confirm default unchanged).

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
<venv>/bin/ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(css): text-align override in build_fragment_157 (#9)"
```

---

### Task 6: `build_fragment_157` — `text_indent` + padding suppression

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`build_fragment_157`)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Produces: `build_fragment_157(..., text_indent=None)`; `text_indent` is a `(mag, unit_sym)` tuple. When set, `value[IS("$36")]` uses that magnitude+unit AND the `$47` padding-top is omitted. `text_indent=None` → `$36`=0 default and padding-top behavior unchanged.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.unit
def test_text_indent_sets_36_and_omits_padding():
    from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
    gen = NativeKFXGenerator()
    frag = gen.build_fragment_157(entity_name="si", text_indent=("1.5", "$308"))
    ind = frag.value[IS("$36")]
    assert ind[IS("$307")] == IonDecimal("1.5")
    assert ind[IS("$306")] == IS("$308")
    assert IS("$47") not in frag.value  # padding-top suppressed


@pytest.mark.unit
def test_no_text_indent_keeps_default():
    from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
    gen = NativeKFXGenerator()
    frag = gen.build_fragment_157(entity_name="sj")
    ind = frag.value[IS("$36")]
    assert ind[IS("$307")] == IonDecimal("0")
    assert IS("$47") in frag.value  # padding-top present by default (non-heading)
```

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k "text_indent or keeps_default"`
Expected: FAIL (`TypeError: unexpected keyword 'text_indent'`).

- [ ] **Step 3: Implement**

Add `text_indent=None` to the signature. The `$36` entry currently hardcodes `IonDecimal("0"), IS("$306"), IS("$314")`. Replace with a computed value:

```python
        # $36 = text-indent; default 0%, overridden per element.
        if text_indent is not None:
            indent_struct = IonStruct(
                IS("$307"), IonDecimal(text_indent[0]),
                IS("$306"), IS(text_indent[1]),
            )
        else:
            indent_struct = IonStruct(
                IS("$307"), IonDecimal("0"), IS("$306"), IS("$314")
            )
```

and use `indent_struct` for `$36` in the IonStruct. Then guard the padding-top block: it currently is `if not is_heading: value[IS("$47")] = ...`. Change to:

```python
        # Body text gets padding-top for paragraph spacing; headings rely on
        # margin-top. A non-zero first-line indent replaces inter-paragraph
        # spacing (print convention), so suppress padding-top when indented.
        if not is_heading and text_indent is None:
            value[IS("$47")] = IonStruct(
                IS("$307"), IonDecimal("1"), IS("$306"), IS("$310")
            )
```

- [ ] **Step 4: Run, verify pass**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k "text_indent or keeps_default or style or margin or align"`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
<venv>/bin/ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(css): text-indent + padding-top suppression in build_fragment_157 (#9)"
```

---

### Task 7: Thread `block_style` through `_build_chapter_content`

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`_build_chapter_content`: `_append_text_with_spans`, the block iteration, the title-strip first-block rebuild, and the plain-text entry-style branch)
- Test: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: `chapter["blocks"][i]["block_style"]` (Tasks 3–4), `build_fragment_157(align=, text_indent=)` (Tasks 5–6), the existing `_allocate_style` cache.
- Produces: text chunks carry `chunk["block_style"]`; each plain-text entry's `$157` is allocated with that block's `align`/`indent` (deduped). Headings/links/images unaffected. No-`block_style` chunks resolve to the existing body style (byte-stable).

- [ ] **Step 1: Write the failing integration test**

```python
@pytest.mark.unit
def test_block_style_produces_aligned_indented_157(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
    gen = NativeKFXGenerator()
    chapters = [{
        "title": "Ch",
        "text": "centered indented line",
        "blocks": [{
            "text": "centered indented line", "spans": [],
            "block_style": {"align": "center", "indent": ("2", "$308")},
        }],
    }]
    gen.generate_full_book(title="T", author="A", chapters=chapters,
                           output_path=str(tmp_path / "o.kfx"))
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    # A style must exist with text-align center, text-indent 2em, no padding-top.
    hit = [
        f for f in styles
        if f.value.get(IS("$34")) == IS("$320")
        and IS("$36") in f.value
        and f.value[IS("$36")].get(IS("$306")) == IS("$308")
        and IS("$47") not in f.value
    ]
    assert hit, "expected a centered+indented $157 with padding-top suppressed"
```

(Use `f.value.get(...)` via the IonStruct `in`/`[]` protocol; if `.get` is unavailable, write a small `g(struct,key)` helper as used in earlier tests.)

- [ ] **Step 2: Run, verify fail**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py -q -k block_style_produces`
Expected: FAIL (no such style — block_style not threaded yet).

- [ ] **Step 3: Carry `block_style` onto chunks**

In `_append_text_with_spans` (line ~2217), add a `block_style` parameter and include it in the emitted chunk dict:

```python
        def _append_text_with_spans(chunk_text, para_text, para_spans, block_style):
            ...
                all_chunks.append({
                    "type": "text", "text": piece, "spans": pspans,
                    "block_style": block_style,
                })
```

At the block iteration call site (line ~2326) pass it:

```python
                    para_spans = block.get("spans", [])
                    block_style = block.get("block_style")
                    for chunk in _emit_text_chunks(para):
                        if chunk["type"] == "image":
                            all_chunks.append(chunk)
                        else:
                            _append_text_with_spans(
                                chunk["text"], para, para_spans, block_style
                            )
```

In the title-strip first-block rebuild (line ~2305) carry block_style onto the replacement dict:

```python
                            iter_blocks[0] = {
                                "text": remainder,
                                "spans": rebased_spans,
                                "block_style": first.get("block_style"),
                            }
```

- [ ] **Step 4: Allocate per-chunk style from `block_style`**

In the `$259` build loop's plain-text branch (the final `else` that does `entry_styles.append(story_names[ch_idx])`, line ~2561), replace with a block-style-aware allocation. (Leave the heading branch and the link branch untouched.)

```python
                else:
                    bs = chunk.get("block_style") or {}
                    attrs = {"font_size": chapter.get("font_size", 1.0)}
                    if bs.get("align"):
                        attrs["align"] = bs["align"]
                    if bs.get("indent"):
                        attrs["text_indent"] = bs["indent"]
                    entry_styles.append(_allocate_style("", **attrs))
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
```

When `bs` has neither align nor indent, `_allocate_style("", font_size=fs)` returns the same cached `body_name` as before → byte-identical. Ensure `build_fragment_157` is called by `_allocate_style` with `align`/`text_indent` kwargs (it accepts them from Tasks 5–6).

- [ ] **Step 5: Run, verify pass (full generator + converter suites)**

Run: `<venv>/bin/pytest tests/unit/test_native_generator.py tests/unit/test_converter.py -q`
Expected: PASS (new integration test + all existing; defaults unchanged).

- [ ] **Step 6: Lint + commit**

```bash
<venv>/bin/ruff check plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
<venv>/bin/ruff format plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git add plugin/kfxgen/native_generator.py tests/unit/test_native_generator.py
git commit -m "feat(css): thread block_style through chapter content (#9)"
```

---

### Task 8: Byte-stable guard + full-suite verification

**Files:**
- Modify: `tests/unit/test_native_generator.py`

**Interfaces:**
- Consumes: the full pipeline (Tasks 1–7).

- [ ] **Step 1: Add a default-stability test**

```python
@pytest.mark.unit
def test_no_block_style_emits_default_align_and_indent(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS, IonDecimal
    gen = NativeKFXGenerator()
    chapters = [{"title": "Ch", "text": "plain body text"}]  # no blocks/block_style
    gen.generate_full_book(title="T", author="A", chapters=chapters,
                           output_path=str(tmp_path / "o.kfx"))
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    # Every body style keeps justify ($321) and indent 0; none carry a non-% indent unit.
    for f in styles:
        if IS("$34") in f.value:
            assert f.value[IS("$34")] in (IS("$321"), IS("$320"))  # justify or heading-center if any
        if IS("$36") in f.value:
            assert f.value[IS("$36")][IS("$306")] == IS("$314")  # default % unit, value 0
```

- [ ] **Step 2: Run the full unit suite**

Run: `<venv>/bin/pytest tests/unit -q`
Expected: PASS (all).

- [ ] **Step 3: Lint repo-wide + commit**

```bash
<venv>/bin/ruff check . && <venv>/bin/ruff format --check .
git add -A
git commit -m "test(css): default-stability guard for typography subset (#9)"
```

- [ ] **Step 4a: Real-pipeline smoke (anti-silent-no-op gate for Task 4)**

The unit tests only exercise a *fake* `style_resolver`; the `EpubAsOeb` shim
can't drive the real Stylizer (no `opts`/oeb). This step is the only check that
the Stylizer path actually fires. Build a small EPUB with a CSS file setting
`p.ctr { text-align: center }` and `p.ind { text-indent: 2em }` (or inline
`style=` attributes), convert it through the **actual plugin** (dev build via
`build_plugin.sh --install`, then `ebook-convert in.epub out.kfx`; restore the
merged build after), and decode `out.kfx` with `kfxlib_minimal` (reuse
`tools/`-style loading). Assert at least one `$157` has `$34`=`$320` (center) and
one has a non-default `$36`. If none appear, the resolver silently returned
`None` — investigate the Stylizer construction before proceeding. (Standalone
`create_oebbook` was found impractical for a unit test: it needs the full EPUB
input pipeline, so this real-conversion smoke is the substitute.)

- [ ] **Step 4: Gutenberg corpus regression gate (manual, not automated)**

Obtain the 90-book Project Gutenberg corpus (per the README) and place/symlink it into `research/gutenberg-top-90/`. Run the Calibre-path baseline against the branch's Stylizer code (dev build or calibre-debug harness, not the installed plugin) and diff fidelity vs the committed `research/gutenberg-top-90-baseline*/BASELINE.md`. This is a content/crash gate — text-retention numbers should be unchanged; any movement or new failure flags a regression. If output legitimately changed, regenerate and commit the affected snapshot. Record the result on #9. (Not part of CI.)

- [ ] **Step 5: Device verification note (release gate)**

Convert an EPUB with centered text and first-line indents through the built plugin and confirm on a physical Kindle: centered paragraphs render centered, indented paragraphs show first-line indent without doubled paragraph spacing. Record on #9.

---

## Self-review

- **Spec coverage:** text-align (Tasks 2,5,7) ✓; text-indent + padding suppression (Tasks 1,2,6,7) ✓; Stylizer + fallback (Tasks 3,4) ✓; unit mapping (Task 1) ✓; honor-all-incl-left (Task 2 keyword passthrough + Task 5 ALIGN_MAP) ✓; byte-stable defaults (Tasks 5,6,7 design + Task 8 guard) ✓; corpus + device gates (Task 8) ✓. Margins explicitly out of scope (spec) — not a gap.
- **Placeholders:** none — every code/test step has concrete code; the one genuine unknown (Stylizer constructor signature) is called out with a probe command and a never-raises fallback.
- **Type consistency:** `block_style = {"align": str|None, "indent": (mag,unit_sym)|None}` used identically in Tasks 2,3,4,7; `parse_css_length`/`compute_block_style`/`build_fragment_157(align=, text_indent=)`/`_build_style_resolver` signatures match across tasks; `text_indent` is always a `(mag, unit_sym)` tuple from Task 1 through Task 7.
