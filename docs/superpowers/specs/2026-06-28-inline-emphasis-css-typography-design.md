# Design: Inline emphasis + CSS typography subset (#9)

Date: 2026-06-28
Issue: #9 (Carry inline emphasis and more CSS typography through to KFX styles)
Status: Approved (brainstorming) — pending spec review

## Problem

The native generator emits a fixed set of paragraph-level `$157` styles. Inline
formatting (`<em>/<i>/<strong>/<b>`) is dropped entirely, and most per-element
CSS typography from the source EPUB is not carried through. Books lose italic
and bold *within* paragraphs, and lose per-element indent/alignment/spacing.

## Scope

In scope:
1. **Inline emphasis** — italic, bold, bold-italic — as KFX character spans.
2. **CSS subset** — `text-align`, `text-indent`, `margin-top`/`margin-bottom`,
   resolved per element.

Out of scope (non-goals): color, letter-spacing, floats/positioning, and any
CSS KFX cannot represent. `oblique` is mapped to italic.

## Feasibility — confirmed symbols

The issue claimed the `$157` vocabulary "includes the needed symbols". The
bundled `yj_symbol_catalog.py` is **anonymized** (symbols are `"$10"`, `"$13"`,
`"$14?"`) and carries no semantic meaning; the meanings used today were
reverse-engineered and hardcoded in `native_generator.py`. font-style/italic was
used nowhere and was unverified.

Resolved authoritatively from jhowell's upstream `kfxlib`
(`KFX Input.zip` → `kfxlib/yj_to_epub_properties.py`):

| CSS property | symbol | values |
|---|---|---|
| font-style | `$12` | `$382`=italic, `$381`=oblique |
| font-weight | `$13` | `$361`=bold (+ `$357`=300, `$359`=500, `$363`=900) |
| text-align | `$34` | `$320`=center, `$321`=justify, `$59`=left, `$61`=right |
| text-indent | `$36` | (length value) |
| margin | `$46` shorthand, `$47`=top, `$48`=left, `$49`=bottom, `$50`=right | |
| font-family | `$11` | (bonus — the symbol #15/#16 need) |

Note: the authoritative margin mapping (`$47`=margin-top, `$48`=margin-left,
`$49`=margin-bottom) **disagrees with the current `build_fragment_157` code
comments** (which label `$48` "margin-bottom" and `$47` "padding-top"). Current
output is device-tested and renders correctly, so this must be reconciled
carefully — see Risks.

## Architecture

### Data model (converter → generator)

Add `chapter["blocks"]`: ordered list of paragraph blocks, each:

```python
{
  "kind": "text",
  "runs": [(text, flags), ...],          # flags: subset of {"italic","bold"}
  "spans": [(start, length, flags), ...], # offsets into normalized block text
  "block_style": {                        # None/empty when unavailable
     "align": "...", "indent": "...",
     "margin_top": "...", "margin_bottom": "...",
  },
}
```

`spans` cover only non-default runs, as character offsets into the block's
**normalized** text. The existing flat `text` string is still produced; the
title-strip, cover, image-only, and `toc_links` heuristics keep using it
unchanged. `blocks` is **additive**: when absent (tests passing raw text, or the
no-Stylizer fallback), the generator uses today's flat path. This bounds blast
radius.

### Extraction (`converter.py`)

- Generalize `_walk_paragraph_with_imgs` to walk inline content accumulating
  italic/bold from ancestor `<em>/<i>/<strong>/<b>`, returning `(segment, flags)`
  pairs. IMG tokens stay as point markers.
- Normalize whitespace across the styled segments collectively, emitting
  normalized text + offset spans (only where flags != default).
- Block style via **Calibre Stylizer**
  (`calibre.ebooks.oeb.stylizer.Stylizer`) when available: compute effective
  `text-align`/`text-indent`/margins per element. Wrap in try/except → fall back
  to no block style (today's behavior) outside Calibre. Mirrors the
  image-optimizer's calibre-optional pattern.

### Generation (`native_generator.py`)

- `build_fragment_157`: add `italic` (`$12`→`$382`); accept per-element
  `align`/`indent`/`margin_top`/`margin_bottom` instead of fixed values. Add a
  **style cache** keyed by the style tuple to dedup near-identical `$157`
  fragments (prevents proliferation across thousands of paragraphs).
- Emphasis styles: a small fixed set — italic, bold, bold-italic — referenced by
  spans.
- `build_fragment_259` already emits `$142` spans for links; extend it to emit
  emphasis spans (`$142` with `$143` start / `$144` length / `$157` style, no
  `$179`). A chunk may carry both link and emphasis spans → multiple `$142`
  entries.
- `_build_chapter_content`: when `blocks` is present, build text chunks from
  blocks, slicing each block's spans onto the resulting chunk char ranges (after
  `.strip()` and length-split) and rebasing offsets. Title/heading/cover/toc/
  image-only logic is preserved.

## Data flow

1. `extract_chapters_from_oeb` builds chapters; for each block it computes runs +
   spans (inline tags) and block_style (Stylizer).
2. Chapter carries both `text` (flat, for heuristics) and `blocks` (structured).
3. `_build_chapter_content` consumes `blocks` → typed chunks, each with its span
   list (offsets rebased to the chunk) and a resolved/`cached` `$157` style name.
4. `build_fragment_259` emits each chunk's entry with `$142` emphasis spans;
   `build_fragment_157` emits the deduped styles.

## Edge cases

- Nested emphasis (`<strong>` inside `<em>`) → flags combine → bold-italic.
- Adjacent/overlapping spans of equal flags merged.
- Emphasis crossing an inline image → split at the image boundary (images are
  separate chunks).
- Whitespace at run boundaries handled by collective normalization.
- Degenerate/empty runs dropped.
- Style proliferation bounded by the style cache.

## Error handling

- Stylizer import/compute failure → block_style omitted, never raises (fallback
  to current behavior). Emphasis (structural tags) is independent of Stylizer
  and still works.
- Unknown/oblique font-style → italic. Unmappable CSS values → omitted.

## Testing

- **Unit (no Calibre):** `<em>/<i>/<strong>/<b>` and nesting → assert `$142`
  emphasis spans + italic/bold `$157`, via the existing OEB test shim.
- **CSS subset:** unit-test the style-mapping function with a mocked
  computed-style dict; test the graceful no-op fallback when Stylizer is absent.
- **Golden:** add an emphasis-bearing EPUB→KFX fixture. Assert default
  (non-emphasis) styles stay **byte-stable** to catch margin-reconciliation
  regressions.
- **Device (release gate):** italic/bold actually render on a Kindle.

## Risks

1. **Margin reconciliation (highest).** Adopt the authoritative mapping, but the
   rule is: default styles stay byte-identical (locked by golden test);
   per-element margins are only *added* when Stylizer reports non-default values.
   No change to the existing default style bytes.
2. **Chunk/span slicing.** Rebasing block spans onto post-strip, length-split
   chunk text is fiddly; covered by targeted unit tests with multi-chunk
   paragraphs.
3. **Stylizer availability/signature** across Calibre versions — isolated behind
   the try/except fallback; CSS subset simply degrades to off when it can't run.

## Boundary with related issues

- #15/#16 (font embedding): this builds the inline-run/span model fonts need;
  `font-family` is `$11` (recorded above) and slots into the same span/style
  machinery later.
- #18 (catalog drift): this work consumed jhowell's upstream catalog to resolve
  unverified symbols — a concrete instance of the sync process #18 proposes.
