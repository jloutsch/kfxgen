# Changelog

## 5.3.17 — GPL attribution for vendored kfxlib

Licensing-compliance release. No runtime behavior change from 5.3.16.

- Restored the GPL v3 copyright/license headers (John Howell) on the six
  `kfxlib_minimal` files that had lost them during the original trim, and
  marked them as a modified subset of Calibre's `kfxlib`.
- Added a top-level `NOTICE` crediting John Howell / `kfxlib` (GPL v3);
  it now ships inside the plugin zip alongside `LICENSE`.

## 5.3.16 — Packaging and release automation

Maintenance release. No runtime behavior change from 5.3.15.

- Releases are now built and published automatically: pushing a `vX.Y.Z`
  tag builds the plugin zip and attaches it to a GitHub Release (the tag
  must match `version` in `plugin/kfxgen/__init__.py`).
- Removed stale references to internal-only documentation paths from a
  test docstring and the `kfxlib_minimal` README.

## 5.3.15 — Crash fix for chapters with no renderable content

Fixes an `IndexError: list index out of range` in
`native_generator._build_chapter_content` that aborted native KFX
generation (then fell through to a Kindle Previewer timeout) for books
ending in a chapter that emits zero content chunks.

- A chapter emits zero chunks when its only body is an `<img>` whose
  href doesn't resolve to a known body resource — e.g. the converter's
  orphan recovery (\#116) appending a book's own `cover.xhtml`, whose
  image is the separately-handled cover (\#32). `chapter_start_positions`
  then indexed `chunk_positions` out of range for a trailing empty
  chapter; a middle empty chapter silently aimed its TOC entry at the
  next chapter.
- Generator (defensive): emits a single placeholder chunk for any
  chapter that would otherwise produce none, so every chapter owns a
  navigable content position and the per-chapter arrays stay aligned.
  No content is lost; the fix is independent of why a chapter is empty.
- Converter (root cause): orphan recovery now skips spine items that
  have no real text once IMG tokens are removed — i.e. image-only pages
  such as the EPUB's own `cover.xhtml`. This stops the junk trailing
  chapter from being created in the first place (no trailing blank
  page), and is consistent with the existing \#6 policy that already
  declines to recover image-only chapters. Text-bearing orphans still
  recover unchanged.
- Regression tests: `TestEmptyChapterDoesNotCrash` (generator, trailing
  and middle image-only chapters) and `TestImageOnlyOrphanSkipped`
  (converter, cover orphan skipped, text orphan still recovered).

## 5.3.14 — Anchor-collapse fix for in-page TOC navigation (\#116)

Fixes catastrophic content loss for EPUBs whose TOC navigates within a
spine file via `#anchors` (glossary-style books, e.g. one entry per
letter pointing into the same file). `extract_chapters_from_oeb` was
absorbing every orphan spine item between an anchored TOC entry and
the next distinct anchor into a single chapter, which the KFX writer
then truncated at the position-id envelope.

- **\#116** dedup-aware forward-extension: a TOC successor sharing the
  current entry's spine index now stops the search instead of being
  skipped through. Higher-index successors still extend (Calibre's
  split-at-page-break case, unchanged). Non-monotonic successors
  (`nxt < start`) skip and keep looking.
- **\#116** orphan recovery: spine items left unclaimed by the TOC
  loop are appended as additional chapters. Titles from
  dedup-dropped TOC entries feed in first so chapter labels are real
  letters (A, B, C, …) instead of internal filename stems.

Impact on the 90-book Project Gutenberg baseline (`research/gutenberg-top-90-baseline/`):
CIA World Factbook 2006 went from 191 KB / 3,025 words (0.2% word
retention) to 11.5 MB / 65,100 words (98.0%). All 7 previously
sub-90% books now ≥97.7%. 0/90 regressions.

Repo/tooling hygiene (not in the plugin payload): two new baseline
runners under `research/` (`baseline_runner.py` direct-Python,
`baseline_runner_calibre.py` ebook-convert) capture per-book metrics
against the committed `BASELINE.md` snapshots. The legacy research
helper `convert_epub_to_kfx.py` was brought to parity with the
production plugin (spine-driven `build_chapters`, IMG-token emission,
body-image extraction, `images=` argument plumbed through).

## 5.3.13 — Security/code-review fix batch

Shipped plugin fixes from the 2026-05-16 review:

- **\#107** half-title pages no longer leak their structural TOC label
  ("Half Title Page") onto the page; recognized as front matter and
  rendered title-only per print convention.
- **\#102** `_safe_write_bytes` accepts `os.PathLike` (e.g. `pathlib.Path`),
  not just `str`; `os.fspath` normalization keeps every traversal/symlink
  guard intact.
- **\#105** replaced deprecated `locale.getdefaultlocale()` (removed in
  Python 3.15) with `locale.getencoding()` + a pre-3.11 fallback.

Repo/tooling hygiene (not in the plugin payload): exact dependency
pins (\#103), unused Pillow removed (\#104), `.gstack/` ignored (\#106),
and `build_plugin.py` now reads the real version source instead of
colliding with `minimum_calibre_version` and mislabeling builds 5.0.0
(\#114).

## 5.3.10 — Kindle home-screen thumbnail extraction (\#39)

Two metadata changes that enable Kindle to extract a cover thumbnail
from a side-loaded KFX on devices that support local thumbnail
generation (Paperwhite, Oasis+):

1. **Cover `$164` now includes `$162: 'image/jpg'`** (or `'image/png'`).
   The original `kp_calibre_converter.py` documentation flagged this
   field as required for "thumbnail display"; we had previously
   omitted it because reference Calibre KFX files don't always
   include it on body images. Cover specifically needs it.
2. **ASIN format changed from `ASIN_<10>` (15 total) to 32-char
   alphanumeric** (no prefix). Reference shape is e.g.
   `'NRHEO70SKAG5UVR2SSWENJ42365REDE2'`. The short prefix-format ASIN
   inhibits thumbnail extraction.

### Device validation

| Kindle model | Pre-fix | Post-fix |
|---|---|---|
| Paperwhite | ❌ no thumbnail | ✅ thumbnail |
| Voyage (older) | ❌ | ❌ (no local extraction at all — firmware limitation) |

The Voyage doesn't extract local thumbnails regardless of file shape;
that's a device limitation, not a kfxgen bug. Paperwhite and newer
devices do, and these two metadata changes are sufficient.

### Tests

`TestThumbnailFix`:
- `test_cover_164_has_mime_type`: cover `$164` includes `$162: 'image/jpg'`
- `test_asin_is_32_chars_no_prefix`: ASIN is 32-char alphanumeric

Both verified to fail on pre-fix code, pass on post-fix. 41/41 unit
tests pass.

### Open question

Which of the two changes is the active ingredient for thumbnail
extraction (could be `$162`, the ASIN format, or both together) is
not isolated. Both are minimal-risk; shipping together.

## 5.3.9 — Suppress redundant chapter-title headings (\#33)

Image-only chapters (map pages, diagram pages) and Title Page chapters no
longer render the chapter-title TOC name as a heading text chunk above
the actual content. Previously, opening "Title Page" showed the literal
words "Title Page" above the typeset book title, and "Maps" / "Family
Tree" showed those names as headings on top of their respective images.

### Implementation

`_build_chapter_content` now skips the title heading chunk when:

1. `_is_cover` flag is set (already from \#32 — synthetic cover chapter).
2. `_omit_title_heading` flag is set (set by `_replace_title_page` in
   the converter for chapters whose body is replaced with title+author
   text, which already conveys the same information as the chapter
   title).
3. The body content (after title-prefix stripping) contains image
   tokens but no real text — auto-detected for chapters whose XHTML
   body is just `<img>` tags.

### Tests

`TestImageOnlyChapterHeadings`:
- `test_image_only_chapter_skips_heading`: image-only chapter's TOC
  target points at the image directly, not at a heading text chunk.
  Real text chapters still keep their heading.
- `test_omit_title_heading_flag_suppresses_heading`: a chapter with
  `_omit_title_heading: True` and 3 body paragraphs produces 3 chunks,
  not 4 (no heading).

Both verified to fail on pre-fix code, pass on post-fix. 39/39 unit
tests pass.

### Device validation (real-book corpus)

- ✅ Title Page shows just the title and author (no redundant
  "Title Page" heading)
- ✅ Map page shows the image without a redundant chapter heading
- ✅ Diagram page shows the image without a redundant chapter heading
- ✅ Real chapters still display their titles
- ✅ Nav-pane "Go to" still navigates to all chapters
- ✅ TOC links, progress, body images, glossary unchanged

## 5.3.8 — Cover image in reading flow (\#32)

When a cover_image is provided to `generate_full_book`, the cover now
appears as the first reading page on Kindle. Previously the cover was
metadata-only — referenced by `$490` for the listing thumbnail but
never rendered as content.

### Implementation

`generate_full_book` prepends a synthetic chapter at index 0 when a
cover is present. The chapter:

- Has no heading text (`_is_cover` flag in `_build_chapter_content`
  skips the title chunk emission).
- Has no TOC entry (`_omit_from_toc` flag filters it out of the
  `$389` nav-pane build).
- Contains a single image entry referencing the cover via the
  standard `image_resources` token mechanism, using the synthetic
  basename `__kfxgen_cover__`.
- Picks up the existing image-style classification — covers (typically
  >600px both dimensions) get `s_img_page` (full-page 100% height).

`toc_link target_chapter_idx` values in subsequent chapters shift by
+1 to account for the new chapter at index 0.

### Tests

`TestCoverInReadingFlow` asserts:
- Exactly one `$259` entry references the cover resource when
  `cover_image` is provided.
- The cover chapter is NOT in the `$389` TOC nav-pane.

`test_cover_referenced_from_259_when_provided` verified to fail on
pre-fix code (got 0 references), pass on post-fix. 37/37 unit tests
pass.

### Out of scope

Kindle home-screen thumbnail extraction for side-loaded PDOC files
remains unreliable. The `$490 cover_image` metadata is correctly set
and matches reference's structure — the file is shape-correct. Kindle's
local thumbnail rendering for personal documents has known issues
unrelated to the KFX file contents.

## 5.3.7 — In-book Contents page hyperlinks (\#30)

Tapping a chapter title link on the in-book Contents page now jumps to
the target chapter on real Kindle devices. This is a long-standing bug
that was never working in any version of kfxgen, including v5.2.0.

### Diagnosis

Reference Calibre KFX uses `$142` character-span markers for inline
hyperlinks; we were using entry-level `$179`. The two look similar in
the structural-diff harness but Kindle treats them differently:

- Entry-level `$179`: structural reference, **not tappable**.
- `$142` character span with `$179` inside: inline hyperlink, **tappable**.

Reference's Contents storyline shape:
```
{$155: 8060, $157: 's20', $159: $269,
 $142: [{$143: 0, $144: 10, $179: 'a7XC', $157: 's852'}],
 $145: {name: 'content_1', $403: 25}}
```

Ours was:
```
{$155: 1064, $157: 's0_link', $159: $269, $145: ...,
 $179: 'toc_anchor_0'}        ← entry-level, non-tappable
```

### Fix

`build_fragment_259` now emits `$142` character spans for link entries:

- Entry's `$157` is the body style (was the link/underline style).
- The link style lives inside the `$142` span as the span's `$157`.
- `$144` is the chunk's text length (so the whole chapter title is the
  tappable region).
- `$179` moves from entry level into the span.

Two new `build_fragment_259` parameters: `link_styles` and
`link_text_lengths`, populated alongside `link_targets` by
`_build_chapter_content`.

### Test coverage

`TestInlineHyperlinks::test_link_emitted_as_142_span_not_entry_level_179`
asserts:
- At least one `$259` entry with `$142` exists when toc_links are passed.
- Link entries do NOT have entry-level `$179`.
- Span has `$143` (start), `$144` (length), `$179` (anchor), `$157` (style).

Verified to fail on pre-fix code (no `$142` spans found), pass on post-fix.
35/35 unit tests pass.

### Device validation (real-book corpus)

- ✅ TOC links on in-book Contents page now jump to target chapters
- ✅ Nav-pane "Go to" still works
- ✅ Progress, body images, glossary, chapter ornaments all unchanged

## 5.3.6 — Per-chapter $145 content fragments (\#2)

Replaces the singleton `content_1` $145 fragment that held every paragraph
in the book (~1.3MB on the test corpus, ~7300 paragraphs) with one $145 per
chapter (`content_1`, `content_2`, ..., `content_N`). Each $259 storyline
references its own chapter's content fragment; $403 indices reset to 0
at the start of every chapter instead of running globally.

### Why

- Reference Calibre KFX emits 149 $145 fragments per book on the test
  corpus; ours had 1.
- Singleton walked the entire book's text array on every nav lookup.
- Reattempt of the v5.3.0/5.3.1 Phase 2 work that was rolled back as
  collateral damage in v5.3.2 — root-cause analysis later showed the
  actual progress regression came from body image emission (fixed in
  v5.3.4/5.3.5), not from per-chapter $145.
- Unblocks \#3 (nested $259 glossary fix) which depends on per-chapter $145
  to address its content correctly.

### Impact

- Test corpus: 1 → 73 $145 fragments (one per chapter).
- No user-visible change. Real-device validated: progress, nav-pane
  "Go to", glossary, body images all unchanged.

### Test coverage

`TestPerChapterContentFragments` asserts:
- $145 count == chapter count
- Storyline `lN` children reference `content_{N+1}` with $403 reset to 0

Both verified to fail on pre-fix code, pass on post-fix code.

## 5.3.5 — Body image rendering on Kindle (\#4)

Body images now render on Kindle. Closes the rendering half of \#4 (the
position-map guard shipped in v5.3.4 covered the progress regression).
Real-device validated on the test corpus: maps, diagrams, chapter
ornaments all visible; nav-pane, progress, and glossary unchanged.

### Three bugs fixed

1. **Image entries used the wrong style.** Image $259 entries pointed
   at the per-chapter body text style (`s1`), which has no
   image-specific layout attributes. Kindle accepted the structure but
   couldn't render the image. Fix: `build_fragment_157_image()` emits
   dedicated image styles matching the reference shape, with three
   variants picked by image dimensions:
   - `s_img_sm` (3em × 3em) for small square ornaments (≤300px both
     dims, ratio ≤ 1.4)
   - `s_img` (9.626% height) for wide rule-style decorations
   - `s_img_page` (100% height) for full-page images like maps

2. **Image tokens were silently dropped.** The v5.3.2 rollback left
   `_walk_paragraph_with_imgs` discarding every `<img>` tag inside
   `<p>`/`<div>` blocks; only bare `<img>` directly under `<body>` got
   tokenized. Result: all image references on the test corpus ended up
   stuffed into a single back-matter chapter (the appendix file with
   `<body>`-level imgs). Fix: restore proper IMG token emission. Image
   refs now distribute across 67/71 storylines instead of 1/71.

3. **Image positions weren't navigable.** Reference includes ALL image
   `$259` entry positions in `$265` (108/108 on the test corpus). The
   v5.3.4 fix went the wrong direction by excluding image positions
   from `$264`/`$550` to match `$265`'s exclusion. Right answer:
   include image positions everywhere. Without `$265` entries, image
   positions are unresolvable, and Kindle can't navigate to chapters
   whose TOC target sits in an image-heavy storyline (diagram and
   map pages). Fix: image chunks get a synthetic +1 char-offset slot in
   `$265`, and `$264`/`$550` restore the original "include all chunk
   positions" behavior.

### Test coverage

`TestImagePositionsConsistent` now asserts both halves of the position-
map invariant: image entry positions appear in $265, and every $264/$550
content position is a subset of $265. The class was renamed (was
`TestImagePositionsExcludedFromIndex`, named for the wrong invariant).

Body image emission re-enabled in `converter.py`. Cover-in-reading-flow
(cover never appears as the first reading page) and image-only-chapter
heading (literal "Title Page" text rendered above the title-page image)
are pre-existing limitations, surfaced now that body images render.
Tracked as \#32 and \#33 respectively.

## 5.3.4 — Defensive position-map guard for body image emission (\#4)

Root-caused the v5.3.x progress=100%-at-start regression that fires when
body images are emitted: image-chunk position IDs were being written
into `$264` (positions index per section) and `$550` (page-break list)
but were already excluded from `$265` (char-offset → position-id map),
because images aren't char-addressable. Position IDs that exist in
`$264`/`$550` but have no `$265` entry can't be resolved by Kindle's
progress walk, so progress falls back to the degenerate end-of-book
state.

Fix applies the same image-exclusion logic to `$264` and `$550` that
already governed `$265`. All three position-related fragments now stay
internally consistent regardless of how many image chunks a chapter
contains.

Body image emission stays disabled in `converter.py` (v5.3.2 behavior
preserved) — Kindle accepts the new structure but doesn't actually
render the embedded images yet, which is a separate rendering bug.
This change is the defensive guard for the next image-rendering
attempt: when emission is re-enabled, progress will not regress.

Device-validated on the test corpus: progress shows sensible % at start,
nav-pane "Go to" jumps to the right chapter.

New regression test (`TestImagePositionsExcludedFromIndex`) asserts
that every content position appearing in `$264`/`$550` also appears in
`$265` — confirmed to fail on the broken code, pass on the fix.

## 5.3.3 — Narrow split-recovery in extract_chapters_from_oeb (\#17)

Issue \#1's original fix (commit 4de5168, v5.2.0) used wide forward-
absorption: every TOC entry absorbed all spine items up to the next
TOC-referenced item. This recovered split-at-page-break chapters but
caused the *last* TOC entry to absorb unrelated trailing back-matter
(e.g. `next-reads.xhtml`), shifting KFX nav positions and breaking
nav-pane navigation for end-of-book sections (diagram pages, glossary,
appendix).

Narrowed: chapters now own only their own spine item by default, and
extend forward only when a *later* TOC entry has a valid spine index
further downstream (meaning there genuinely are orphan spine items
between this TOC anchor and the next). Last TOC entry stops at its
own spine item — no trailing absorption.

Also adds two diagnostic warn-logs for visibility into TOC mapping
edge cases (entries dropped because their href isn't in spine; entries
skipped because their spine index was already claimed).

Verified on real Kindle device with the test corpus:
- Diagram pages, glossary, and appendix reachable in nav-pane
- Other chapters' nav unchanged

The unrelated `is_glossary` plumbing that was prepared for the disabled
$269→$270 patch in the original v5.2.1 work is *not* included — Phase 3
nested $259 was reverted in v5.3.2 and the flag is dead code now.

## 5.3.2 — Roll back roadmap-\#7 features that broke real Kindle nav

Bisecting on a real Kindle device exposed multiple Kindle-side
incompatibilities in the v5.3.0/5.3.1 output. None of these were
caught by the structural-diff harness (the file looked correct) — they
manifested as user-visible nav and progress regressions:

- **Kindle nav-pane TOC button missing** — Phase 3 nested `$259`
  storyline structure made Kindle stop recognizing the book as having
  navigable structure. **Reverted to flat `$259` (v5.2.0 shape).**
- **Progress indicator stuck at 100%** at the start of the book —
  Phase 4a body image emission (`$164`/`$417` resource pairs registered
  in `$419`/`$270`) triggered the regression. Cover image alone is
  fine. **Body image emission disabled (no body images render).**
- **`SECTION_POS_BASE` widening** (5.3.1) was speculative and untested
  on device. **Reverted to 10000.**

To untangle interactions, a few additional features were also rolled
back as collateral damage. They'll need re-implementation that doesn't
trip Kindle the same way:

- Per-chapter `$145` content fragments (\#2 reopens) — back to
  singleton `content_1` like v5.2.0.
- Manifest TOC fallback (\#6 reopens) — Title Page and Maps drop again.
- Position-range ceiling assertion (\#19 reopens) — removed; chunk
  positions may arithmetically collide with section positions like
  v5.2.0 did.
- Inline image entries in `$259` (\#3 / \#4 reopen) — `<img>` tags
  drop during text extraction.

Tests covering the reverted features deleted (`test_nested_storyline`,
`test_image_content_rewriting`, `test_resource_pipeline`,
`TestPerChapterContentFragments`, `TestPositionRangeCeiling`,
`test_toc_entry_in_manifest_only_is_recovered`).

What stays from v5.3.0/5.3.1:

- `$157` style sharing (\#5) — still active; no impact on nav.
- Cover-image href tracking (PR \#20 review fix) — still relevant for
  the cover-only case; no body-image collisions to worry about now.
- Centralized version (\#23) — single source of truth in
  `plugin/kfxgen/__init__.py`.
- `build_plugin.sh` refresh (\#18) — still works.
- `tools/` (kfxdiff, kfxanalyze, glossary_compare) — still useful for
  diagnosing future structural divergence.

What needs new work (issues reopened):

- **\#2** per-chapter `$145` — needs an implementation that doesn't
  break Kindle progress display.
- **\#3** nested `$259` storylines — needs a shape Kindle recognizes
  as structured (current attempt loses TOC button).
- **\#4** image / resource pipeline — needs a way to emit body image
  resources without breaking progress display.
- **\#6** manifest TOC fallback — needs to handle near-empty bodies
  without confusing Kindle (likely tied to \#4).

Verified on Kindle device with the test corpus:
- Cover image renders ✓
- Kindle nav-pane TOC button visible ✓
- Tap TOC entry in nav-pane jumps to chapter ✓
- Progress indicator shows sensible % at start ✓
- Glossary readable (chapter symbols not rendered) ✓
- In-book Contents page links page-turn rather than jump (this never
  worked in any version including v5.2.0; tracked separately).

## 5.3.1 — Centralize version string

Single source of truth in `plugin/kfxgen/__init__.py`. Functional
output unchanged from 5.3.0.

## 5.3.0 — Structural-match parity with Calibre KFX Output

The roadmap-\#7 release. Closes the major structural deltas between
kfxgen's native generator and Calibre's KFX Output (jhowell) gold-
standard, while preserving the 20–30× speedup over Kindle Previewer 3.

### Generator changes

- **Per-chapter `$145` content fragments** (\#2, PR \#14). Each chapter
  now owns its own `$145` instead of a singleton 1.3 MB `content_1`
  shared across the book. Per-chapter `$403` indices reset to 0.
- **Nested `$259` storyline structure** (\#3, PR \#16). One outer entry
  per chapter wrapping nested `$146` children — the structural fix for
  the glossary "definitions running together" rendering bug.
  Makes the disabled `$269→$270` block-display patch obsolete.
- **`$157` style sharing** (\#5, PR \#13). Identical attribute fingerprints
  share one fragment instead of cloning per chapter (test corpus:
  143 → 6 styles).
- **Image / resource pipeline** (\#4, PRs \#20 + \#21). `$164` (manifest)
  and `$417` (blob) pairs emitted for every spine-referenced image in
  the OEB manifest. `<img>` tags in body XHTML are preserved as
  placeholder tokens through text extraction, then rewritten into
  nested `$259` image children with `$159: $271`, `$175: <resource>`,
  `$584: <alt>`. Cover-image handling unchanged.

### Converter changes

- **Manifest fallback for TOC entries missing from spine** (\#6, PR \#13).
  When a TOC href doesn't resolve to any spine item, falls back to
  `oeb_book.manifest` lookup (exact href, then basename). `extract_cover_image`
  now returns `(bytes, href)` so the body-image pipeline can exclude
  the cover regardless of which discovery method located it.
- **Image format pre-filtering** (PR \#20 review). Unsupported formats
  (GIF, WebP, etc.) are detected in `extract_images_from_oeb` with a
  warning that names the offending file, instead of being silently
  dropped in the generator.

### Tooling

- **`tools/`** (PR \#22): `kfxdiff.py`, `kfxanalyze.py`, `glossary_compare.py`
  for structural comparison against gold-standard reference KFX files.
  Used during the roadmap-\#7 work; kept for future regression analysis.
- **`tests/benchmarks/`** (PR \#22): performance regression guards
  ensuring the speedup over jhowell's plugin survives future refactors.

### Bug fixes

- `$265` outer-position survival assertion (Phase 3 review). Guards
  against silent TOC breakage if a future change reorders char-offset
  accumulation.
- Cover double-emit prevented for EPUBs that define their cover via
  OPF `<guide>` rather than `metadata.cover` (PR \#20 review).
- `image_resources` keyed by basename so `<img src="../images/x.jpg">`
  in spine XHTML resolves against manifest `OEBPS/images/x.jpg`.
- Title Page and Maps in image-only XHTML files now recover as their
  own sections (Phase 4b side effect — `<img>` token preservation
  makes their bodies non-empty for spine extraction).

### Tests

- 65 unit + benchmark tests (was 13 at v5.2.0).
- New `tests/unit/test_position_map.py` with 4 nav-invariant regression
  guards (Z3 sentinel, `$265` content-only, range no-overlap, TOC
  points to content).
- New per-feature suites: `test_converter.py`, `test_nested_storyline.py`,
  `test_resource_pipeline.py`, `test_image_content_rewriting.py`.

### Real-book corpus structural diff

| Metric | v5.2.0 | v5.3.0 | Calibre KFX Output reference |
|---|---|---|---|
| `$145/$259/$260` | 71/71/71 | 73/73/73 | 76/76/76 |
| `$157` styles | 143 | 6 | 80 |
| `$164/$417` resources | 1/1 | 46/46 | 46/46 |
| Inline `$259:$271` images | 0 | 203 | (similar) |
| Total fragments | 441 | 474 | 642 |
| File size | 3.2 MB | 8.6 MB | 8.1 MB |

## Notes

- A separate v5.2.1 narrowed split-fix is tracked under \#17. It was
  never released; the wide-absorption fix in v5.2.0 (commit 4de5168)
  is what's currently shipped on `main` for issue \#1. The narrowed
  variant remains an open candidate pending a Kindle device test.
- `is_glossary` plumbing that was prepared for the disabled
  `$269→$270` patch was made obsolete by Phase 3's nested `$259`
  structure and is not part of any release.

## 5.2.0 — TOC off-by-one fix and diagnostic tools

Pre-roadmap baseline.
