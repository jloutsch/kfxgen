# kfxgen — Open-source KFX generator for Calibre

[![Tests](https://github.com/jloutsch/kfxgen/actions/workflows/test.yml/badge.svg)](https://github.com/jloutsch/kfxgen/actions/workflows/test.yml)
[![Version](https://img.shields.io/github/v/release/jloutsch/kfxgen?label=release&color=blue)](https://github.com/jloutsch/kfxgen/releases/latest)
[![License: GPL v3](https://img.shields.io/badge/license-GPL%20v3-green.svg)](https://www.gnu.org/licenses/gpl-3.0.html)
[![Calibre](https://img.shields.io/badge/calibre-5.0%2B-orange.svg)](https://calibre-ebook.com/)

A Calibre output plugin that generates Kindle KFX files in Python, without invoking Amazon's Kindle Previewer. Tested on Kindle devices. Designed, directed, and device-tested by Justin Loutsch, with development assistance from Claude.

The plugin has been thoroughly tested for bugs: every release runs the full [pytest suite in CI](https://github.com/jloutsch/kfxgen/actions/workflows/test.yml) and is exercised against a 90-book corpus with per-book correctness metrics. See [Compatibility and validation](#compatibility-and-validation) below for the results and links to the committed test reports.

## Why KFX

KFX (Kindle Format 10) is Amazon's modern ebook format. Compared to MOBI/AZW3 it offers better typography, custom font support, and improved layout on modern Kindle devices.

## Why this plugin

The official KFX Output plugin for Calibre depends on Kindle Previewer, which is closed-source, Windows/Mac-only (Linux needs Wine), and converts in minutes per book. kfxgen converts in seconds, runs anywhere Calibre runs, and ships as a single Python plugin with no external binary dependencies.

**Custom fonts that actually render.** This is the main reason kfxgen exists. KFX honors the font you've selected on the Kindle — including custom fonts you've installed on the device — and applies that font's OpenType features (ligatures, contextual alternates), which AZW3/MOBI did not do reliably. kfxgen writes `override_kindle_font: false` into the KFX so the device font is respected rather than overridden. The font lives on the Kindle, not in the file — kfxgen does not embed font files.

## Compatibility and validation

Each release is exercised against a 90-book corpus of public-domain EPUBs from Project Gutenberg covering English, German, Dutch, Latin, ancient text (Beowulf, Odyssey), heavily illustrated books (NASA Mars Rovers — 436 body images), drama (Complete Works of Shakespeare — 1,169 TOC entries), poetry, and reference works (CIA World Factbooks).

Current state:

| Pipeline | Conversion success | Word-content retention |
|---|---|---|
| Direct-Python (`research/baseline_runner.py`) | 90/90 | 90/90 books at ~100% |
| Calibre plugin path (`ebook-convert`) | 90/90 | 87/90 books at ≥97.7% |

The three calibre-path outliers (Mars Rovers, Lady of the Lake, Southern Literature) are verse/image-heavy books that Calibre's OEB pipeline normalizes differently. Tracked as a known follow-up.

Per-book reports with source-vs-output metrics live in [`research/gutenberg-top-90-baseline/BASELINE.md`](research/gutenberg-top-90-baseline/BASELINE.md) (direct-Python) and [`research/gutenberg-top-90-baseline-calibre/BASELINE.md`](research/gutenberg-top-90-baseline-calibre/BASELINE.md) (Calibre plugin). Future regressions can be diffed against these committed snapshots.

## Installation

```bash
# Build the plugin
./build_plugin.sh

# Install in Calibre (quit the Calibre GUI first)
./build_plugin.sh --install
```

Or from the Calibre GUI: download the latest prebuilt plugin zip from the [releases page](https://github.com/jloutsch/kfxgen/releases/latest), then Preferences → Plugins → Load plugin from file, restart Calibre.

See [`INSTALLATION.md`](INSTALLATION.md) for detailed steps and troubleshooting.

## Usage

From the Calibre GUI: select a book, click Convert books, set Output format to KFX, OK.

From the command line:

```bash
ebook-convert mybook.epub mybook.kfx
```

## How it works

kfxgen generates KFX natively in Python — no template files, no external tools.

The pipeline:

1. **EPUB intake.** Calibre's OEB pipeline parses the EPUB into manifest, spine, and TOC structures.
2. **Chapter extraction** (`plugin/kfxgen/converter.py`). Each spine item becomes a chapter; TOC entries supply display titles. Calibre's split-at-page-break chapters are absorbed back into their parent. Body images are pulled from the manifest as JPEG/PNG resources.
3. **Native generation** (`plugin/kfxgen/native_generator.py`). Chapter text is chunked into `$259` content entries with their own `$157` styles, sections (`$260`) are emitted with inline display, position IDs are assigned across separate content and section ranges, and the navigation tree is built so the Kindle TOC button activates correctly. Body images become nested `$259:$271` entries pointing at `$164/$417` resource pairs.
4. **Serialization** uses a minimal Ion/KFX library (`plugin/kfxgen/kfxlib_minimal/`, derived from Calibre's `kfxlib`) to produce a valid KFX container.

The technical decisions behind these structures — why the position-ID envelope matters, why `$265` excludes section positions, what makes the nav-pane activate — are explained in the inline comments of `plugin/kfxgen/native_generator.py`.

## Architecture

```
plugin/
├── __init__.py                       Calibre plugin wrapper (version, metadata)
└── kfxgen/
    ├── __init__.py                   Version source of truth
    ├── converter.py                  EPUB → chapter list (used by ebook-convert)
    ├── native_generator.py           Chapter list → KFX bytes
    └── kfxlib_minimal/               Ion/KFX serialization library
        ├── kfx_container.py
        ├── ion_binary.py
        └── ...

research/                             Development helpers, not in the plugin
├── convert_epub_to_kfx.py            Direct-Python EPUB → KFX (no Calibre)
├── baseline_runner.py                Run direct-Python pipeline over a corpus
├── baseline_runner_calibre.py        Run Calibre plugin pipeline over a corpus
└── gutenberg-top-90-baseline*/       Committed regression baselines

tests/                                pytest suite (unit + integration)
```

## Requirements

- Python 3.13+ (Calibre 9.x ships its own Python)
- Calibre 5.0+

## Development

```bash
# Run the test suite
pytest tests/

# Build the plugin (produces dist/kfxgen-plugin-<version>.zip)
./build_plugin.sh

# Build + install into the local Calibre (quit Calibre GUI first)
./build_plugin.sh --install

# Run the direct-Python pipeline over the full Gutenberg corpus
python3 research/baseline_runner.py --all

# Same, via the Calibre plugin (slower; uses ebook-convert)
python3 research/baseline_runner_calibre.py --all
```

The version is single-sourced from `plugin/kfxgen/__init__.py`. `./build_plugin.sh` reads it; the Calibre wrapper at `plugin/__init__.py` reads it; the converter logs it. Bump it in one place per release.

## Limitations

- The Calibre plugin path produces lower text retention than the direct-Python path on verse-heavy or image-heavy books (Mars Rovers, Lady of the Lake, Southern Literature) — Calibre's OEB pipeline normalizes `<br/>`, stanzas, and alt-text differently. See [`research/gutenberg-top-90-baseline-calibre/BASELINE.md`](research/gutenberg-top-90-baseline-calibre/BASELINE.md).
- TOC entries that navigate within a spine file via `#anchor` (e.g. dictionary-style A/B/C/… sub-entries) surface as separate chapters rather than nested in-page anchors. Reading order and content are preserved.
- Fonts are not embedded in the KFX. Custom fonts render via the font installed on the Kindle (see [Why this plugin](#why-this-plugin)); this is by design, but it means a font that isn't installed on the device won't travel with the file.
- Inline emphasis (italic / bold within a paragraph) and most source CSS typography are not yet carried through; paragraph-level styles (font size, line height, alignment, margins, headings) are. KFX supports a subset of CSS, so this is a coverage gap rather than a hard limit — tracked in [#9](https://github.com/jloutsch/kfxgen/issues/9).
- Images are embedded at source resolution with no downscaling or recompression, so heavily illustrated books can produce very large KFX files (a 469-image book yielded ~300 MB). Image optimization is tracked in [#11](https://github.com/jloutsch/kfxgen/issues/11).

## Version history

See [`CHANGELOG.md`](CHANGELOG.md) for release notes, and the [releases page](https://github.com/jloutsch/kfxgen/releases/latest) for the current version.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Good first-issue areas:

- Cross-pipeline parity for verse and image-heavy books
- Custom font plumbing through the native generator
- In-page anchor TOC support (sub-chapter navigation)
- Additional test corpora beyond Project Gutenberg

## Security

Adversarial-EPUB threat model and reporting process: see [`SECURITY.md`](SECURITY.md).

## License

GPL v3 — see [LICENSE](LICENSE) for the full text, and [NOTICE](NOTICE) for third-party attribution.

Copyright © 2025-2026 Justin Loutsch &lt;justin.loutsch@gmail.com&gt;

## Credits

- **kfxlib** by John Howell — Ion/KFX libraries, GPL v3. The `kfxlib_minimal/` directory is a modified subset of this work; see [NOTICE](NOTICE) and `plugin/kfxgen/kfxlib_minimal/README.md`.
- **Calibre** by Kovid Goyal — plugin framework

---

This plugin is for legitimate personal use. Respect copyright laws and publisher rights when converting books.
