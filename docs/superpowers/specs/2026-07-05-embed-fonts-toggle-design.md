# `kfxgen_embed_fonts` toggle — design

**Goal:** a user-facing toggle to enable/disable embedding the book's own
`@font-face` fonts, so the user can deliberately choose between publisher
typography (embedded fonts) and the font installed/selected on the Kindle.
Completes #15's planned Phase 6 escape hatch. Target release: **5.5.0**.

## Motivation

As of 5.4.x, a book's embedded `@font-face` fonts travel into the KFX by default
(#15, device-verified). Some users want the opposite for a given conversion —
force the device/installed font (e.g. a Fast-Font, or their preferred reading
font) rather than the publisher's. A single boolean gives that certainty in
either direction.

## Design

Mirrors the existing `kfxgen_embed_original_images` option exactly.

### 1. Plugin option (`plugin/__init__.py`)

Add to the `options` set:

```python
OptionRecommendation(
    name="kfxgen_embed_fonts",
    recommended_value=True,
    help=(
        "Embed the book's own @font-face fonts into the KFX so its "
        "typography renders on-device. Turn OFF to use the font "
        "installed/selected on the Kindle instead."
    ),
)
```

Calibre auto-renders this as a checkbox in the conversion dialog's **KFX Output**
tab and exposes it to `ebook-convert` as `--kfxgen-embed-fonts` /
`--disable-kfxgen-embed-fonts`. No custom config widget — same as the image
option.

### 2. Converter gate (`converter.py::convert_oeb_to_kfx`)

Where the font table is built today, gate on the option:

```python
if getattr(opts, "kfxgen_embed_fonts", True):
    font_table = build_font_table(oeb_book, log)
else:
    from .font_table import FontTable  # noqa: PLC0415
    font_table = FontTable([])
    log.info("  Font embedding disabled (kfxgen_embed_fonts=False)")
```

Everything downstream is unchanged. An empty `FontTable` means: no `$262`/`$418`
fragments, no `$11` on `$157` styles, and `override_kindle_font` stays `False`
(it is `True` only when `self.font_table.faces` is non-empty). So the
device/installed font is used.

### Data flow

`opts.kfxgen_embed_fonts` → the gate picks `build_font_table(...)` vs empty
`FontTable([])` → `generate_full_book(font_table=...)` (untouched).

## Behavior

| Setting | Result |
|---------|--------|
| **ON** (default) | Today's behavior — the book's `@font-face` fonts embed. |
| **OFF** | Font embedding is fully bypassed: no `$262`/`$418` fragments, no `$11` on styles, `override_kindle_font=False`, device/installed font used. Identical to converting the same book when it has no embeddable fonts (the pre-#15 font behavior). Note: unrelated 5.4.x rendering fixes — e.g. inherited `text-align` (#33) — still apply, so this is not byte-identical to a pre-5.4.0 build. |

`getattr(opts, "kfxgen_embed_fonts", True)` keeps non-plugin callers working: the
golden-corpus shim (`opts=None`) and any direct converter callers default to
embedding, so their existing behavior/output is unchanged.

## Testing

- **Unit (no Calibre):** convert a font-embedding book through the shim path with
  `opts.kfxgen_embed_fonts=False` → assert the generated fragments contain **no**
  `$262`/`$418`, and the `$490` `override_kindle_font` is `False`. With the flag
  `True` or absent → fonts embed as now. Reuses `EpubAsOeb` + the committed
  `test_books/font-matching-test` fixture (rules injected as in the existing
  integration test, since the real Stylizer needs Calibre).
- **CACE:** `tier3_strict` golden corpus stays byte-identical. The goldens are
  no-font books passed `opts=None` → default True → `build_font_table` returns an
  empty table anyway, so the toggle (either value) produces the same bytes.

## Scope / non-goals

- Just the boolean toggle. **No** per-font selection, no subsetting options, no
  font-format conversion, no separate "override device font" sub-toggle.
- No change to how fonts are extracted or embedded when ON — only whether.

## Release

New feature → **5.5.0**. CHANGELOG entry; version bump per the tag-and-release
rule (tag `v5.5.0` after merge).
