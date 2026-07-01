# Dynamic section-position base — guaranteed content/section disjointness (#30)

**Status:** Approved design — ready for implementation planning
**Issue:** #30 · **Related:** #23 (introduced the high-chapter scale that exposed this; left an `xfail` gate pointing here), #20 (position-map conformance)
**Branch:** `feat/30-dynamic-section-base`

## Problem

kfxgen assigns two fixed position-id ranges in `native_generator.py`:

- content positions (`$259` outer + chunk children) start at `CONTENT_POS_BASE = 1000`, step `CONTENT_POS_STEP = 2`;
- section positions (`$260` `$155`) start at `SECTION_POS_BASE = 10000`, step `SECTION_POS_STEP = 2`.

Content grows with both chapter and chunk count:
`content_max = CONTENT_POS_BASE + CONTENT_POS_STEP × (num_chapters + num_chunks)`.
For a realistic large book (≈1,200 chapters × several paragraphs each) `content_max`
reaches ≈17,798, climbing through and past the section range (10,000–≈12,398). Where
an even content value equals an even section value, jhowell's reader logs
`duplicate eid …`. #23's scale gate (`TestHighChapterCountScale`) is `xfail(strict=True)`
against this issue.

Device testing (#23) showed the overlap is tolerated in practice: a 1,170-chapter book
renders and navigates on a physical Kindle. So this is correctness hardening — remove the
overlap by construction — not a device-breaking bug fix.

## Goal (Option 1: provable no-overlap, low-risk-first)

Guarantee content and section eid ranges are **disjoint at any chapter count**, while
keeping **normal books byte-identical** (no re-verification for the common case). Only
books that would otherwise overflow change, and only in their section-eid values.

Explicitly out of scope: a single shared eid namespace (the jhowell-reference style) —
that would change eid values for every book and require full device re-verification.
Also out of scope: reducing `CONTENT_POS_STEP` (would change every content eid and only
move the overflow cliff rather than remove it).

## Design

### Mechanism (`native_generator.py`)

1. Content-position assignment is unchanged. Track the largest content eid emitted as
   `content_max` (the final `content_pos_id` value after the assignment loop, minus one
   step, i.e. the last id actually assigned).
2. Compute the section base dynamically rather than using the constant directly:
   ```
   section_base = max(SECTION_POS_BASE,
                      _round_up_to_step(content_max + SECTION_POS_STEP))
   ```
   where `_round_up_to_step` rounds up so the section base stays aligned to
   `SECTION_POS_STEP` (keeps section eids even, matching today).
   - Normal book (`content_max < SECTION_POS_BASE`): `section_base = SECTION_POS_BASE`
     (10,000) → identical output.
   - Overflow book: sections relocate to just above the content range, with **minimal
     padding** (one step), keeping section eids as low as possible — the code's history
     warns that very high section bases (100,000) broke Kindle's progress display, so the
     lower the relocated base, the safer.
3. Build `section_positions` from `section_base` (contiguous, step `SECTION_POS_STEP`, as
   today). By construction every section eid `> content_max ≥` every content eid, so the
   two sets are disjoint.

`SECTION_POS_BASE` remains the floor/default. `CONTENT_POS_BASE`, both steps, and the
content-assignment loop are unchanged.

### The invariant, stated correctly

The guarantee that matters is "content eids and section eids are disjoint sets," not
"content < a fixed 10,000." The fixed constant was only ever a proxy. Tests assert
disjointness directly.

## Ripple / affected code

- Existing position-map tests run on small fixtures (content well under 10,000), so they
  stay byte-identical and green with no change.
- `TestHighChapterCountScale` (the `xfail` gate) is rewritten to assert content∩section
  eid sets are empty and every section eid appears in `$265` (the #23 rule), then the
  `xfail` marker is removed.
- No production code outside `native_generator.py` classifies eids by comparing to
  `SECTION_POS_BASE` in a way that breaks for overflow books (the classifier lives in
  tests and introspection helpers, which operate on small fixtures). If a broader use is
  found during implementation, switch it to set-membership (is-this-a-$260-eid) rather
  than a numeric threshold.

## Edge cases

- Empty / single-chapter / tiny books: `content_max` small → `section_base` stays
  `SECTION_POS_BASE` → unchanged.
- Alignment: `_round_up_to_step` keeps the relocated base aligned to `SECTION_POS_STEP`
  so section eids remain even (consistent with current output).
- A book whose `content_max` is just below `SECTION_POS_BASE` keeps sections at 10,000
  (no overlap, no relocation).

## Testing

- **Unit (generator):** a large synthetic book (≈1,200 chapters × multi-paragraph) →
  assert content and section eid sets are disjoint, section eids ⊆ `$265`, and the book
  converts without error. Replaces the `xfail`.
- **Unit (boundary):** a book engineered so `content_max` lands just above
  `SECTION_POS_BASE` → assert `section_base` relocated above `content_max` and ranges are
  disjoint.
- **Regression:** the existing golden corpus and position-map suites stay green (normal
  fixtures unchanged); golden bytes are NOT regenerated.
- **Device (manual gate):** one sideload of a huge book (Complete Works of Shakespeare)
  to confirm the relocated ~20k section eids still navigate on a physical Kindle — the
  same check already run for #23.

## Success criteria

- For any chapter/chunk count, content and section eid ranges are disjoint (no
  `duplicate eid` from range collision).
- Normal books produce byte-identical output (golden bytes unchanged, no device
  re-verification).
- The #23 scale gate passes (asserting disjointness) with the `xfail` removed.
- A huge book still navigates on-device.
