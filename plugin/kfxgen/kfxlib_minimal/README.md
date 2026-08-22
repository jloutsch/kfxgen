# kfxlib_minimal

Mostly a trimmed fork of Calibre's `kfxlib`, copied into kfxgen so the plugin
runs inside Calibre's bundled Python interpreter without pulling in
heavy upstream dependencies (pypdf, PIL, etc.). Only the Ion structures
and serialization utilities needed for KFX generation are kept.

**The directory is not uniform.** `standard_symbols.py` is original kfxgen work
and `__init__.py` is original packaging; everything else is derived from
upstream. See [`standard_symbols.py` is not vendored code](#standard_symbolspy-is-not-vendored-code)
below before drawing any conclusion about provenance from this directory's name.

## Upstream baseline (#18)

The derived files come from jhowell's `kfxlib` (bundled in the *KFX Input*
Calibre plugin; copyright "2016-2025, John Howell"). Update this on every
re-sync. Per-file copyright headers are authoritative where they disagree with
this directory-level summary.

| Field | Value |
|-------|-------|
| Fork baseline (approx.) | jhowell `kfxlib` ≈ late-2025; the fork carries no `version.py` |
| Latest upstream compared | `kfxlib` **20260520** (KFX Input 2.33.0) — re-measured 2026-08-21 (#91) |
| Drift at that comparison | `YJ_symbols` table version **10** (unchanged); `ROOT_FRAGMENT_TYPES` identical. Our catalog holds **842** symbols (`$10`–`$851`), upstream **843** (`$10`–`$852`). The one extra entry is itself an unnamed placeholder. **Benign for generation.** |

### `YJ_symbols` catalog: what the numbers mean (#91)

The earlier reading of this drift as "13 catalog symbols behind (`$835`–`$846`,
`$851`)" was measuring the wrong thing. Those 13 entries differ from upstream's
only by a trailing `?`, which is an annotation meaning "this id exists but has
never been observed in real content" — `ion_symbol_table.py:266` strips it on
load, so `$835?` and `$835` are the same symbol at the same id. The annotation
has no effect on encoding or decoding. The only real difference is table length.

Length is safe to lag, and deliberately not bumped. `StandardSymbolTable`
imports the shared table with `max_id = len(YJ_SYMBOLS.symbols)` — currently
**842** — and every reader truncates its own copy to that length, so the ids
kfxgen emits resolve identically on a device whose table is longer. Raising it
to 843 would shift every local symbol id by one and rewrite every generated
file, in exchange for a placeholder kfxgen never emits. Against a format whose
only real verification is a device sideload, that is a bad trade.

For scale: Kindle Previewer 3.106 declares `max_id` **844** (`+9=853`), which is
why upstream `kfxlib` 20260520 warns when decoding Amazon's own output. That
warning is about upstream's table, not ours, and appears on Amazon-produced
files only.

### What is actually checked, and what still needs a human

What *would* break generation is upstream renumbering an id inside the range we
already declare — every `$NNN` kfxgen emits would then mean something else.
`test_yj_symbol_catalog_tracks_upstream` (tier-2) narrows that window, but read
what it can and cannot see before trusting it on a re-sync.

Both catalogs are **pure positional placeholders**: entry `i` is the literal
string `"$" + str(10 + i)`, with no semantic names anywhere in either file. So
comparing names across the two proves nothing — they match by construction for
any file of this shape, *including* one where Amazon inserted a symbol and
shifted everything above it. The first version of this test compared names and
was therefore vacuous; it could not fail.

The only per-entry information that moves under a shift is the trailing `?`.
Upstream only ever *drops* one, as symbols get observed in the wild, so a `?`
appearing where ours has none is the signature of an insertion. Simulating an
insertion at every one of our 842 ids, the test fires for ids **10–820** and
stays quiet above **~821**, where our entries are uniformly `?` and a shift is
invisible.

So the guarantees, in descending strength:

| Checked | Strength |
|---|---|
| `YJ_symbols` version equality | Real — a wholesale redefinition bumps it |
| Our length ≤ upstream's | Real — we cannot declare a `max_id` upstream lacks |
| Upstream still purely positional | Real — fires if names ever appear, at which point a genuine name comparison becomes possible and should replace the heuristic |
| No `?` gained inside our range | Strong heuristic — blind above id ~821 |

When bumping the upstream baseline, a green test is not by itself evidence that
ids did not move in the top ~30 entries. Nothing kfxgen emits currently lives
there, which is why this is acceptable rather than merely tolerated.

Drift detection + re-sync procedure: `research/kfx-format-baseline/`
(a fixed Amazon-engine conversion whose symbol/fragment inventory is diffed
across Kindle Previewer / `kfxlib` versions).

## Local modifications (track upstream sync cost)

Each entry below is a local modification that creates merge friction
with future upstream syncs. Audit when bumping the upstream baseline.

| Date | Issue | PR | Modification |
|---|---|---|---|
| 2025-12-31 | — | (initial) | Trimmed upstream `kfxlib` to the minimum surface needed by kfxgen (Ion binary/text/symbol-table, kfx/yj container, message logging). Heavy deps (pypdf, PIL, lxml-only paths) removed. |
| 2026-01-05 | — | (foundation) | `yj_symbol_catalog.py` populated for native KFX generation (Phase 1 — gap fix). Derived from upstream; ~97% shared with `kfxlib` 20260520. |
| 2026-01-05 | — | (foundation) | `standard_symbols.py` added. **Not derived from upstream** — see the note below. Original kfxgen work that happens to live in this directory. |
| 2026-02-17 | — | v5.1.0 | Lint pass: unused imports removed, lambda assignment replaced. Mechanical only. |
| 2026-03-02 | — | v5.2.0 | TOC off-by-one fixes touched serialization paths in this directory. |
| 2026-05-03 | issue 47 | PR 66 | `Deserializer.extract` length-field bound (`MAX_DECODE_SIZE`, default 64 MB). Negative-size and oversized-size paths raise distinct errors BEFORE the slice. Single choke point defends every length-bounded read in `ion_binary.py`. |
| 2026-05-03 | issue 47 | (PR D) | `MAX_DECODE_SIZE` accepts `KFXGEN_MAX_DECODE_SIZE` env override at import time. Default unchanged (64 MB). See [SECURITY.md → Advanced configuration](../../../SECURITY.md). |
| 2026-05-03 | — | PR 56/PR 67 | Pre-commit framework added at repo root; ruff format/lint may have touched files in this directory. Mechanical only. |
| 2026-08-21 | — | PR 110 | Corrected `standard_symbols.py`'s copyright header, which a directory-wide attribution sweep (#4) had credited to John Howell. See below. |

To regenerate this list:

```bash
git log --diff-filter=AM --pretty='%h %ad %s' --date=short -- plugin/kfxgen/kfxlib_minimal/
```

### `standard_symbols.py` is not vendored code

Every file here except `standard_symbols.py` and `__init__.py` is derived from
upstream `kfxlib`. Those two are not, and the distinction is easy to lose
because of where they sit.

Measured against `kfxlib` 20260520: no upstream module defines these names, no
`standard_symbols.py` exists upstream at all, and **0 of its 247 symbols** appear
anywhere in that source as string literals. `STANDARD_SYMBOLS` holds *local*
symbol names — content and style names such as `c0`, `c1AJ-ad`, `s28K`, `sV5` —
harvested by decoding Kindle Previewer output. Same provenance as the rest of
kfxgen's format knowledge: observation of Amazon's own files, not upstream source.

It lives here because `StandardSymbolTable` subclasses the vendored
`LocalSymbolTable`. That places it in the same GPL v3 combined work, which it
already was project-wide, but subclassing does not transfer authorship.

Commit `b2a1782` (#4) added an identical attribution header to six files in this
directory in one pass. It was right about five and wrong about this one; the
header now names the actual author. Worth remembering when the next
directory-wide sweep is tempting — this directory is not uniform.

For contrast, `__init__.py` carries no attribution header and keeps none. It is
original packaging work, but what it re-exports is upstream's API surface, which
puts it closer to derived than `standard_symbols.py` rather than further.

## Why a fork instead of pinning Calibre's `kfxlib`?

Calibre ships `kfxlib` as part of its KFX *input* plugin, not as an
installable library. Keeping a trimmed copy is the path of least
resistance for a KFX *output* plugin that needs the same Ion primitives.
Re-syncing with upstream is manual; the table above is the audit trail.
