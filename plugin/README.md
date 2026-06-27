# kfxgen — Calibre KFX Output Plugin

Native KFX (Kindle Format 10) generation for Calibre, in pure Python — no Kindle
Previewer, no external tools. This directory is the installable plugin; see the
[repository README](../README.md) for the full project overview, validation
results, and architecture.

## What it does

Registers a **KFX** output format in Calibre. Conversion runs Calibre's
EPUB/OEB pipeline, then generates the KFX natively
(`kfxgen/native_generator.py`) — chapters, navigation tree, sections, and body
images — and serializes it with a minimal vendored Ion/KFX library
(`kfxgen/kfxlib_minimal/`). Converts in seconds and runs anywhere Calibre runs.

## Install

Quit Calibre, then from a checkout:

```bash
./build_plugin.sh --install
```

Or in the GUI: **Preferences → Plugins → Load plugin from file**, choose
`dist/kfxgen-plugin-<version>.zip`, and restart Calibre. See
[`INSTALLATION.md`](../INSTALLATION.md) for details and troubleshooting.

## Use

GUI: select a book → **Convert books** → set Output format to **KFX** → OK.

Command line:

```bash
ebook-convert mybook.epub mybook.kfx
```

## Fast-Fonts

Fast-Fonts (OpenType fonts whose `calt` feature bolds the first part of each
word) are **installed on the Kindle device**, not embedded in the book — KFX's
renderer applies the device font's OpenType features directly, which older
Kindle formats do not. kfxgen does not embed or process fonts; convert
normally and select the installed font on the device.

## Layout

```
plugin/
├── __init__.py              Calibre plugin wrapper (version, metadata)
└── kfxgen/
    ├── __init__.py          Version source of truth
    ├── converter.py         OEB → chapter list
    ├── native_generator.py  Chapter list → KFX bytes
    ├── _img_tokens.py       Shared inline-image token definition
    └── kfxlib_minimal/      Ion/KFX serialization library
```

## License

GPL v3 — see [`../LICENSE`](../LICENSE).

Copyright © 2025-2026 Justin Loutsch
