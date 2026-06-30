# Within-file `#anchor` chapter splitting (#23)

**Status:** Approved design — ready for implementation planning
**Issue:** #23 · **Related:** #20 (position-map conformance, fixed in v5.3.21), #30 (guaranteed position-range fallback, deferred)
**Branch:** `fix/23-anchor-chapter-split`

## Problem

On real books that navigate by within-file `#anchor` links (e.g. Project
Gutenberg's *The Great Gatsby*), kfxgen collapses many TOC entries into a few
mega-chapters. `extract_chapters_from_oeb` maps each TOC entry to a spine
**index** and dedups entries that share an index (`_normalize_href` drops the
`#anchor`). Gatsby's 12 TOC entries → 3 chapters. Downstream symptoms: the
rebuilt Contents page isn't tappable, and the Go-To pane is sparse with wrong
page numbers.

## Prevalence (measured)

Surveyed 90 Project Gutenberg books + 1,456 modern EPUBs from the maintainer's
library:

- **Within-file-anchor collapse affects 89% of Gutenberg and 24% of modern
  books with a TOC** — the dominant pattern for archival content and common in
  modern commercial EPUBs. Worth fixing broadly.
- **Anchor location of real chapter-nav targets** (TOC nav only, page-list
  excluded), 32,717 anchors in collapsed spine files of affected modern books:
  - block element: **79.0%**
  - inline anchor at block start (`<a>`/`<span id>` before any text): **20.6%**
  - genuinely mid-paragraph: **0.4% (123)** — and those are Calibre/MOBI
    `filepos`-style conversion artifacts.
  - Gutenberg corpus: **100% block**.
- **Scale:** books split into up to ~1,140 chapters (Shakespeare); modern
  median 11, max 341 TOC entries in a single spine file.

**Conclusion:** snapping anchors to block boundaries resolves 99.6% of real
chapter anchors exactly and degrades gracefully (chapter starts at the
containing block) on the 0.4%. Character-level mid-paragraph splitting is not
built — it would add code and risk to the #9 emphasis-span path for cases that
do not occur in practice.

## Scope

**In:** snap-to-block anchor splitting with robust block-boundary resolution;
measure-first scale handling.
**Out:** mid-paragraph character-level splitting; page-list / print-page
navigation. Guaranteed position-range rework is deferred to #30.

## Architecture (Approach A: global block-coordinate model)

The block-extractor change is shared; the chapter-assembly core is rewritten to
resolve TOC entries to `(spine_index, block_index)` coordinates and slice
content between consecutive boundaries.

### Component 1 — Anchor-aware block extraction

`extract_blocks_from_html` gains a per-block `"anchor_ids": list[str]`. During
the body walk, each emitted leaf block carries:

- the `id` of the block element itself;
- the `id`s of **ancestor container** elements whose first leaf this block is
  (so `<div id="chapter-1">` → its first `<p>`);
- the `id` / `<a name>` of standalone anchor elements seen since the previous
  block;
- `id`s of inline descendants within the block (a mid-block anchor snaps to its
  containing block).

Backward-compatible: adds one key; existing `text` / `spans` / `block_style`
consumers are unaffected.

### Component 2 — Anchor → block-index resolution

Per spine item, build `anchor_to_block: dict[str, int]` mapping each anchor id
to the **first** block index that carries it (first occurrence wins; duplicates
ignored). A TOC fragment not found in the map resolves to block 0 of that spine
item and logs a warning — never crashes.

### Component 3 — Global-coordinate chapter assembly

Rewrite the core of `extract_chapters_from_oeb`:

1. Resolve each TOC entry to a coordinate `(spine_index, block_index)`:
   `spine_index` from the normalized href; `block_index` from
   `anchor_to_block[fragment]`, or `0` when there is no fragment or it is
   unresolved.
2. Walk TOC entries in order. Each chapter owns the block range from its
   coordinate up to the next entry's coordinate, spanning spine items as
   needed. `text` and `blocks` are sliced from the owned block range.
3. This unifies the three cases under one model:
   - multiple anchors in one spine file → split into multiple chapters;
   - Calibre split-at-page-break siblings → one chapter spans sibling spine
     items (no TOC entry between coordinates);
   - one file per chapter → coordinate at block 0 of each.

The current `already claimed` spine-index dedup branch (the bug) is removed.

## Edge cases / error handling

- **Content before the first anchor in a spine item** attaches to the previous
  chapter; for the very first spine item it merges into the first TOC chapter
  (matches today's "first chapter owns the head" behavior).
- **Unresolved / missing anchor** → block 0 + warning.
- **Non-monotonic / out-of-spine-order TOC** → keep the existing guard that
  skips backward coordinates.
- **Orphan spine items** not referenced by any TOC entry → existing recovery
  path, re-expressed in coordinates (image-only orphans still skipped).
- **page-list anchors** are never consulted — only `oeb_book.toc` is read,
  which excludes the page-list (confirmed by survey).

## Scale handling (measure-first; fallback #30)

Add a generated high-chapter-count test (≈400 chapters) asserting:

- conversion succeeds;
- content position IDs stay within the known-good envelope
  (`< SECTION_POS_BASE`, sections in their range);
- the #20 `$264`/`$265`/`$550` invariants hold.

If the measurement shows the envelope is exceeded, escalate to **#30**
(guaranteed position-range rework). The measure-first test otherwise just locks
in that large books work.

## Testing

- **Unit (`tests/unit`):** anchor-id extraction (block id; container id → first
  leaf; standalone `<a name>`; mid-block snap; duplicate; missing); coordinate
  assembly (multi-anchor split; split-sibling span; one-file-per-chapter;
  orphan recovery; content-before-first-anchor).
- **Corpus/integration:** a Gatsby-shaped fixture → assert 9 chapters (I–IX)
  plus front/back matter, each with distinct, resolvable `toc_links`; verify
  the rebuilt Contents page has one tappable entry per chapter.
- **Scale:** the high-chapter test above.
- **Device (manual gate):** sideload Gatsby + one high-chapter book; confirm
  tappable TOC and correct Go-To navigation on a physical Kindle.

## Success criteria

- Gatsby converts to 9 chapters (I–IX) + front/back matter; every TOC entry is
  a distinct, tappable target; Go-To lists every chapter at a plausible page.
- The within-file-anchor pattern works for both Gutenberg and modern books.
- No regression on one-file-per-chapter books (existing corpus + golden tests).
- Device-verified TOC navigation.
