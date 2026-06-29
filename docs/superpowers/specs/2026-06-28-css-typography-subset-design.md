# Design: CSS typography subset — text-align + text-indent (Plan B of #9)

Date: 2026-06-28
Issue: #9 (Carry inline emphasis and more CSS typography through to KFX styles)
Status: Approved
Predecessor: Plan A (inline emphasis) shipped in PR #19, device-verified.

## Problem

The native generator applies a fixed block style: `text-align: justify` and
`text-indent: 0` on every paragraph, ignoring the source EPUB's per-element
typography. Books lose intentional centering (titles, verse, captions) and
first-line indentation.

## Scope

In:
- per-element **text-align** (`$34`)
- per-element **text-indent** (`$36`)

Out (separate efforts):
- **margins** — the current `build_fragment_157` margin symbols (`$46`/`$47`/`$48`)
  contradict jhowell's authoritative catalog (`$47`=margin-top, `$48`=margin-left,
  `$49`=margin-bottom). kfxgen's output is device-correct in its own context, so
  adding per-element margins needs a reference-KFX symbol verification first.
  Tracked separately.

## Library survey (40 EPUBs from a personal library)

- Paragraph `text-align`: 26/40 unset, 7 justify, 1 left, ~6 center (the center
  count is mostly titlepage/cover CSS, not whole-book centering).
- Explicit center/right on specific elements: 15/40 (38%).
- Non-zero first-line `text-indent`: 35/40 (88%).

Implications: blanket `text-align: left` is rare (1/40), so honoring source
alignment including `left` is low-risk. First-line indent is near-universal, so
text-indent is high-value — but it collides with kfxgen's inter-paragraph
spacing model (see Behaviors).

## Feasibility — KFX units map directly

KFX's `$306` length-unit enum (from jhowell `KFX Input`/`yj_to_epub_properties.py`):

| unit | symbol |
|---|---|
| em | `$308` |
| rem | `$505` |
| % | `$314` |
| pt | `$318` |
| px | `$319` |
| mm | `$316` |
| lh | `$310` |

So a source CSS length becomes a KFX length by **unit mapping**, not numeric
conversion: `1.5em` → `$307=1.5, $306=$308`. Unsupported units (`vw`, `vh`,
`ch`, `ex`) and `auto` are dropped (no override).

text-align values (confirmed, matches current code): `$320`=center,
`$321`=justify, `$59`=left, `$61`=right.

## Architecture

### Data model (converter → generator)

`chapter["blocks"]` entries (from Plan A: `{"text", "spans"}`) gain an optional:

```python
"block_style": {"align": str | None, "indent": (mag: str, unit_sym: str) | None}
```

`block_style` is `None` (or absent) when no source style applies or the
resolver is unavailable. Plan A's flat `text` and the `spans` are unchanged.

### Extraction (`converter.py`)

- `extract_blocks_from_html(element, style_resolver=None)` gains a
  `style_resolver` callable: `elem -> dict | None` returning that element's
  computed CSS (at least `text-align`, `text-indent`). For each block element it
  calls the resolver and runs `compute_block_style` to populate `block_style`.
- `extract_chapters_from_oeb` builds a **Stylizer per spine item**
  (`calibre.ebooks.oeb.stylizer.Stylizer`) and wraps it as the resolver. The
  construction is behind try/except: any failure (or non-Calibre environment)
  yields `style_resolver=None` → no `block_style` → Plan A behavior. Mirrors the
  image-optimizer's calibre-optional pattern.
- Synthesized/text-replaced chapters (title page, half-title, rebuilt contents)
  already drop `blocks` (Plan A); they get no `block_style` either.

### Pure helpers (`inline_style.py`, Calibre-independent, unit-tested)

- `ALIGN_MAP = {"left": "$59", "right": "$61", "center": "$320", "justify": "$321"}`
- `parse_css_length(value: str) -> tuple[str, str] | None` — parse magnitude +
  unit, map unit to its `$306` symbol; return `None` for unsupported unit,
  `auto`, empty, or a zero magnitude.
- `compute_block_style(css: dict) -> dict` — read `text-align` (return the CSS
  keyword string if it is one of left/right/center/justify, else `None`; the
  keyword→symbol mapping happens later in `build_fragment_157`, not here) and
  `text-indent` (via `parse_css_length`; `None` when zero/unset/unmappable).
  Returns `{"align", "indent"}` (values may be `None`). `block_style` therefore
  carries a human-readable align keyword and a pre-parsed `(mag, unit_sym)`
  indent — `ALIGN_MAP` is applied only at `$157` build time.

### Generation (`native_generator.py`)

- `build_fragment_157` gains `align=None` and `text_indent=None`:
  - `align` (a CSS keyword) overrides the default `$34` via `ALIGN_MAP`; when
    `None`, keep the current `$321` default.
  - `text_indent` (a `(mag, unit_sym)` tuple) sets `$36` to that value AND
    **omits the `$47` padding-top** for that style; when `None`, keep `$36`=0 and
    the existing padding-top.
  - Both `None` → output byte-identical to today.
- `_build_chapter_content` passes each block's `block_style` (`align`, `indent`)
  into the existing `_allocate_style` cache so distinct combinations dedupe into
  shared `$157` fragments and register in `extra_style_names` as today.

## Behaviors

- **text-align:** honor every source value including `left`; fall back to the
  justify default only when the source specifies nothing.
- **text-indent:** honor the source value (unit-mapped). When a paragraph's
  indent is non-zero, suppress kfxgen's inter-paragraph `padding-top` for it
  (print convention: first-line indent OR block spacing, not both). Zero/unset
  indent keeps the existing spacing.

## Byte-stability

Books with no Stylizer, or whose paragraphs are unset / justify+zero-indent,
produce identical output. Only books that specify other alignment or non-zero
indent change — the feature working as intended. Guarded by a no-block-style
stability test.

## Error handling

- Stylizer import/instantiation/lookup failure → resolver `None` or per-element
  `None`; never raises. Falls back to Plan A behavior.
- Unmappable CSS values (unsupported unit, `auto`, malformed) → omitted.

## Testing

- **Unit (no Calibre):** `parse_css_length` (each unit + rejects), `ALIGN_MAP`
  via `compute_block_style` (incl. `left`, unset, unmappable),
  `compute_block_style` indent zero/non-zero.
- **Generator:** `build_fragment_157` with `align` (each value) and
  `text_indent` (sets `$36`, omits `$47`); byte-stable defaults when both `None`.
- **Extraction:** `extract_blocks_from_html` with a **fake `style_resolver`**
  asserting `block_style` flows onto blocks; `None` resolver → no `block_style`.
- **Integration:** a chapter with a fake resolver yielding center + indent →
  generated KFX has a `$157` with `$34`=center and `$36`=indent and no `$47`.
- **Stylizer real construction:** behind the fallback; probed during
  implementation (confirm the value form Stylizer returns; unit mapping or skip
  handles either string-with-unit or pt-resolved numbers).
- **Gutenberg corpus regression gate (final):** run the 90-book Project
  Gutenberg baseline and diff fidelity vs the committed
  `research/gutenberg-top-90-baseline*/BASELINE.md` snapshots. This is a
  **content/crash gate, not a styling check** — it measures text retention
  (`source_chars`→`chapter_chars`→`kfx_chars`), which Plan B does not affect, so
  the numbers should be unchanged; any movement or new failure flags an
  unintended regression. Its real value: the Calibre-path runner is the only
  thing that exercises the *real* Calibre Stylizer across 90 structurally-diverse
  books (verse, multilingual, 1,169-entry TOCs, image-heavy), which the
  fake-resolver unit tests cannot. The corpus EPUBs are not committed to this
  public repo; obtain them (per the README's corpus instructions) and place or
  symlink them into `research/gutenberg-top-90/` to run.
  Exercise the branch's Stylizer code via a dev build or calibre-debug harness,
  not the installed plugin. If Plan B intentionally changes any book's output,
  regenerate and commit the affected baseline snapshot as a deliberate update.
- **Device (release gate, manual):** centered/indented text renders correctly on
  a physical Kindle.

## Risks

1. **Stylizer value form** varies across Calibre versions (string-with-unit vs
   resolved pt). Mitigated: `parse_css_length` maps whatever unit appears; pt is
   supported; unrecognized forms are skipped, never crash.
2. **Indent/padding interaction** changes spacing on ~88% of books. Mitigated by
   the indent-suppresses-padding rule + device verification before release.
3. **text-align overriding the reader's Kindle justification preference** for
   books that specify alignment — accepted (honoring source intent; consistent
   with kfxgen already forcing justify today).

## Boundary with related issues

- Builds directly on Plan A's `block`/`block_style` data model and the
  `_allocate_style` cache.
- Margins: separate effort, gated on the `$46`/`$47`/`$48`/`$49` symbol
  verification.
- #20 (position-map conformance) and #21 (deferred minors) are independent.
