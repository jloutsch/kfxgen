# Design: Block horizontal margins — margin-left + margin-right (Plan C of #9)

Date: 2026-06-29
Issue: #9 (Carry inline emphasis and more CSS typography through to KFX styles)
Status: Approved (brainstorming) — pending spec review
Predecessors: Plan A (inline emphasis, PR #19) and Plan B (text-align + text-indent, PR #22), both shipped and device-verified.

## Problem

Block-level horizontal indentation from the source EPUB — `margin-left` /
`margin-right` on blockquotes, pull-quotes, and indented verse — is dropped.
These render flush with body text, losing the visual offset the author intended.

## Scope

In:
- per-element **margin-left** (`$48`)
- per-element **margin-right** (`$50`)

Out (deferred, separate effort):
- **Vertical margins** (`margin-top` `$47`, `margin-bottom` `$49`). margin-top
  collides with kfxgen's forced paragraph-spacing default (`$47` = 1lh), and the
  existing spacing model + Plan B's indent-suppression already cover vertical
  spacing for the common case. Low marginal value, higher reconciliation risk.
- **Padding** (a separate symbol set, `$52`–`$58`).

## Symbol reference (authoritative — jhowell kfxlib `yj_to_epub_properties.py`)

| CSS property | KFX symbol |
|---|---|
| margin-left | `$48` |
| margin-right | `$50` |
| margin-top | `$47` (out of scope) |
| margin-bottom | `$49` (out of scope) |
| margin (shorthand) | `$46` |

Note on the current code: `build_fragment_157` emits `$48` = 0.5% on every
non-heading style, with a comment calling it "margin-bottom: 0.5%". Per the
authoritative catalog `$48` is **margin-left**, so kfxgen has been emitting a
harmless ~0.5% left margin (the comment is wrong; the render is fine). `$47`
(commented "padding-top: 1lh") is likewise actually **margin-top** and is what
produces paragraph spacing. This design uses the authoritative meanings and does
NOT change the existing defaults — it only overrides `$48` per-block when the
source specifies a margin-left, and adds `$50` per-block when the source
specifies a margin-right. Fixing the stale comments is a trivial side cleanup.

## Architecture

### Data model

`block_style` (from Plan B) gains two keys:

```python
"block_style": {
    "align": str | None,            # Plan B
    "indent": (mag, unit_sym) | None,   # Plan B
    "margin_left": (mag, unit_sym) | None,   # NEW
    "margin_right": (mag, unit_sym) | None,  # NEW
}
```

### Pure helper (`inline_style.compute_block_style`)

Also read `margin-left` and `margin-right` from the computed-CSS dict via the
existing `parse_css_length`. `parse_css_length` already rejects negative and
zero magnitudes and unsupported units (returns `None`), so negative margins
safely become no-overrides — no left-edge clipping (the Plan B negative-indent
lesson). Returns the two new keys (values may be `None`).

### Generation (`native_generator.build_fragment_157`)

Add `margin_left=None` and `margin_right=None`:
- **margin-left (`$48`)**: when `margin_left` is a `(mag, unit_sym)` tuple, emit
  `$48` with that magnitude+unit (overriding the 0.5% default for this style);
  when `None`, keep the existing `$48` = 0.5% default.
- **margin-right (`$50`)**: when `margin_right` is set, emit `$50` with that
  magnitude+unit; when `None`, emit nothing (currently `$50` is never emitted).
- Both `None` → output byte-identical to today.

### Threading (`native_generator._build_chapter_content`)

The plain-text entry branch already builds `attrs` from `block_style` (Plan B).
Extend it to add `margin_left` / `margin_right` to `attrs` only when present,
then `_allocate_style("", **attrs)`. Distinct margin combinations dedupe via the
existing cache. When no margins apply, `attrs` is unchanged from Plan B → same
cached style → byte-stable.

## Behaviors

- Honor source `margin-left` / `margin-right` on block elements (blockquotes,
  indented blocks). The source value **replaces** (not adds to) the 0.5% `$48`
  default for that block.
- Negative or zero margins → no override (safe).
- Independent of text-indent (`$36`) and alignment (`$34`) — all can co-apply.

## Byte-stability

Blocks with no source horizontal margins produce identical output: `$48` stays
0.5%, no `$50`. Only blocks that specify margin-left/right change. Guarded by the
default-stability test (extended to assert `$48` = 0.5% / `$314` and `$50`
absent on an unstyled book).

## Error handling

- Stylizer unavailable / per-element failure → `block_style` `None` (Plan A/B
  fallback), no margins applied.
- Unmappable / negative / zero margin values → omitted (no override).

## Testing

- **Unit (no Calibre):** `compute_block_style` margin-left/right (valid, zero,
  negative, unmappable); `build_fragment_157` with `margin_left` (overrides
  `$48`) and `margin_right` (emits `$50`), and byte-stable defaults when both
  `None`.
- **Extraction:** `extract_blocks_from_html` with a fake `style_resolver`
  returning `margin-left` for a `<blockquote>` → `block_style.margin_left` set.
- **Integration:** a chapter whose block has `margin_left` → generated KFX has a
  `$157` with `$48` = the source value (not 0.5%).
- **Real-Stylizer smoke (anti-silent-no-op):** convert an EPUB containing a
  `<blockquote>` with a CSS `margin-left` through the real Calibre Stylizer
  (branch code, no install) and assert a `$157` carries the non-default `$48`.
  The fake-resolver unit tests cannot exercise the real Stylizer.
- **Gutenberg corpus:** crash/no-regression run across the 90-book corpus
  (content gate; margins don't change text retention).
- **Device (release gate):** blockquotes/indented blocks render with the
  expected left (and right) offset on a physical Kindle.

## Risks

1. **Stylizer margin value form** — same as Plan B's indent; `parse_css_length`
   maps whatever unit appears (em/%/pt/px/mm/rem) and skips the rest. Verified
   in the real-Stylizer smoke.
2. **`$48` override vs the 0.5% default** — the override replaces the default per
   block; non-margin blocks keep 0.5% (byte-stable). The mislabeled-comment
   situation is documented; defaults are not changed.
3. **Negative margins** — already rejected by `parse_css_length` (no clipping).

## Boundary with related issues

- Builds directly on Plan B's `block_style` / `_allocate_style` machinery.
- Vertical margins + the stale `$47`/`$48` comment cleanup: this spec corrects
  the comments and uses authoritative symbols, but vertical margins remain
  deferred.
- Completes the "more CSS typography" portion of #9 (emphasis + align + indent +
  horizontal margins); #9 can close after this ships.
