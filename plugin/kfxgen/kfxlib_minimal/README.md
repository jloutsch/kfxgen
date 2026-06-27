# kfxlib_minimal

A trimmed fork of Calibre's `kfxlib`, vendored into kfxgen so the plugin
runs inside Calibre's bundled Python interpreter without pulling in
heavy upstream dependencies (pypdf, PIL, etc.). Only the Ion structures
and serialization utilities needed for KFX generation are kept.

## Local modifications (vendored fork — track upstream sync cost)

Each entry below is a local modification that creates merge friction
with future upstream syncs. Audit when bumping the upstream baseline.

| Date | Issue | PR | Modification |
|---|---|---|---|
| 2025-12-31 | — | (initial) | Trimmed upstream `kfxlib` to the minimum surface needed by kfxgen (Ion binary/text/symbol-table, kfx/yj container, message logging). Heavy deps (pypdf, PIL, lxml-only paths) removed. |
| 2026-01-05 | — | (foundation) | `standard_symbols.py` and `yj_symbol_catalog.py` populated for native KFX generation (Phase 1 — gap fix + standard symbols). |
| 2026-02-17 | — | v5.1.0 | Lint pass: unused imports removed, lambda assignment replaced. Mechanical only. |
| 2026-03-02 | — | v5.2.0 | TOC off-by-one fixes touched serialization paths in this directory. See `docs/kfx_toc_research/` for the full story. |
| 2026-05-03 | issue 47 | PR 66 | `Deserializer.extract` length-field bound (`MAX_DECODE_SIZE`, default 64 MB). Negative-size and oversized-size paths raise distinct errors BEFORE the slice. Single choke point defends every length-bounded read in `ion_binary.py`. |
| 2026-05-03 | issue 47 | (PR D) | `MAX_DECODE_SIZE` accepts `KFXGEN_MAX_DECODE_SIZE` env override at import time. Default unchanged (64 MB). See [SECURITY.md → Advanced configuration](../../../SECURITY.md). |
| 2026-05-03 | — | PR 56/PR 67 | Pre-commit framework added at repo root; ruff format/lint may have touched files in this directory. Mechanical only. |

To regenerate this list:

```bash
git log --diff-filter=AM --pretty='%h %ad %s' --date=short -- plugin/kfxgen/kfxlib_minimal/
```

## Why a fork instead of pinning Calibre's `kfxlib`?

Calibre ships `kfxlib` as part of its KFX *input* plugin, not as an
installable library. Vendoring is the path of least resistance for a
KFX *output* plugin that needs the same Ion primitives. Re-syncing
with upstream is manual; the table above is the audit trail.
