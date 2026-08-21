# kfxlib_minimal

A trimmed fork of Calibre's `kfxlib`, copied into kfxgen so the plugin
runs inside Calibre's bundled Python interpreter without pulling in
heavy upstream dependencies (pypdf, PIL, etc.). Only the Ion structures
and serialization utilities needed for KFX generation are kept.

## Upstream baseline (#18)

Derived from jhowell's `kfxlib` (bundled in the *KFX Input* Calibre plugin;
copyright "2016-2025, John Howell"). Update this on every re-sync.

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

What *would* break generation is upstream renaming or renumbering an id inside
the range we already declare — every `$NNN` kfxgen emits would then mean
something else. That is asserted by
`tests/integration/test_kfxlib_diff.py::test_yj_symbol_catalog_is_prefix_of_upstream`
(tier-2), which checks the prefix property rather than a symbol count, so it
stays quiet when Amazon appends and fires only on the change that matters.

Drift detection + re-sync procedure: `research/kfx-format-baseline/`
(a fixed Amazon-engine conversion whose symbol/fragment inventory is diffed
across Kindle Previewer / `kfxlib` versions).

## Local modifications (track upstream sync cost)

Each entry below is a local modification that creates merge friction
with future upstream syncs. Audit when bumping the upstream baseline.

| Date | Issue | PR | Modification |
|---|---|---|---|
| 2025-12-31 | — | (initial) | Trimmed upstream `kfxlib` to the minimum surface needed by kfxgen (Ion binary/text/symbol-table, kfx/yj container, message logging). Heavy deps (pypdf, PIL, lxml-only paths) removed. |
| 2026-01-05 | — | (foundation) | `standard_symbols.py` and `yj_symbol_catalog.py` populated for native KFX generation (Phase 1 — gap fix + standard symbols). |
| 2026-02-17 | — | v5.1.0 | Lint pass: unused imports removed, lambda assignment replaced. Mechanical only. |
| 2026-03-02 | — | v5.2.0 | TOC off-by-one fixes touched serialization paths in this directory. |
| 2026-05-03 | issue 47 | PR 66 | `Deserializer.extract` length-field bound (`MAX_DECODE_SIZE`, default 64 MB). Negative-size and oversized-size paths raise distinct errors BEFORE the slice. Single choke point defends every length-bounded read in `ion_binary.py`. |
| 2026-05-03 | issue 47 | (PR D) | `MAX_DECODE_SIZE` accepts `KFXGEN_MAX_DECODE_SIZE` env override at import time. Default unchanged (64 MB). See [SECURITY.md → Advanced configuration](../../../SECURITY.md). |
| 2026-05-03 | — | PR 56/PR 67 | Pre-commit framework added at repo root; ruff format/lint may have touched files in this directory. Mechanical only. |

To regenerate this list:

```bash
git log --diff-filter=AM --pretty='%h %ad %s' --date=short -- plugin/kfxgen/kfxlib_minimal/
```

## Why a fork instead of pinning Calibre's `kfxlib`?

Calibre ships `kfxlib` as part of its KFX *input* plugin, not as an
installable library. Keeping a trimmed copy is the path of least
resistance for a KFX *output* plugin that needs the same Ion primitives.
Re-syncing with upstream is manual; the table above is the audit trail.
