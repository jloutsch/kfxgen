# KFX embedded-font fragment reference (#16, Phase 0 for #15)

Phase 0 spike for #15 (embed `@font-face` fonts in native KFX output). Records
the real font-fragment shapes and symbols read off known-good KFX files so #15
emits them correctly instead of guessing.

## Reference source

jhowell's `KFX Output` plugin is **not installed** locally, so the "generate a
reference via KFX Output" recipe in `tools/README.md` was not used. Instead, the
structure below was read directly off **KDP-produced KFX files** (Amazon output
embeds fonts). Any KDP `.kfx` that ships fonts works; typical counts observed
across a handful of them:

| Source (KDP) | `$262` fonts | `$418` raw fonts |
|--------------|-------------|------------------|
| nonfiction title | 16 | 16 |
| cookbook | 14 | 14 |
| how-to title | 13 | 13 |
| novel (used for the dumps below) | 12 | 12 |

Any font-embedding KDP `.kfx` is a usable reference; the novel above was decoded
via the vendored upstream `kfxlib` (`YJ_Book.decode_book()`) to read the shapes.
To find one, decode candidates and check for `$262`/`$418` fragments.

## The font model — three pieces

Embedded fonts in KFX are the direct analog of the image-resource pair kfxgen
already emits (`$164` metadata + `$417` raw bytes, see
`native_generator.py::build_fragment_164` / `build_fragment_417`).

### 1. `$418` — raw font bytes (analog of `$417`)

A `RAW_FRAGMENT_TYPE` whose value is an `IonBLOB` of the `.ttf`/`.otf` bytes.
The fragment's `fid` is the font's *location* string (e.g. `resource/rsrcNNN`).
Emit exactly like `build_fragment_417` does for images:
`YJFragment(fid=IS(location), ftype=IS("$418"), value=IonBLOB(font_bytes))`.

### 2. `$262` — the `@font-face` declaration (analog of `$164`)

An `IonStruct`. Real example (shape preserved; family name genericized):

```
{$11: 'part0000-Bold',                # font-family name (the join key)
 $12: $350,                           # font-style  (value $350 = default "normal")
 $13: $350,                           # font-weight (value $350 = default "normal")
 $15: $350,                           # font-stretch(value $350 = default "normal")
 $165: 'resource/rsrcNNN'}            # location -> the $418 raw-font fragment fid
```

Field meanings (from upstream `kfxlib/yj_to_epub_properties.py`):

| Symbol | CSS descriptor | Notes |
|--------|----------------|-------|
| `$11`  | `font-family`  | name string; the value a `$157` style sets to apply the font |
| `$12`  | `font-style`   | `italic` / `normal` / `oblique`; `$350` = default → omitted by readers |
| `$13`  | `font-weight`  | `bold` / `normal` / `0`; `$350` = default |
| `$15`  | `font-stretch` | `$350` = default (`normal`) |
| `$165` | (location)     | matches the `$418` fragment `fid` (like `$164.$165 → $417.fid`) |

`$350` is the "default / normal" enum value; upstream `process_fonts` pops any
of `$12/$13/$15` whose value is `$350`. So a plain regular face carries only
`$11` + `$165`; bold/italic faces set `$13`/`$12` to a non-`$350` weight/style
value. KDP namespaces family names as `part0000-<Name>` (e.g.
`part0000-<FamilyName> Regular`).

### 3. `$157` style — applies a font

A content style sets `$11` (font-family) to the `$262` font's name to render
text in that face. Real example:

```
{..., $11: 'part0000-<familyname> regular', ...}  # matches a $262 $11 (lowercased)
```

So the linkage chain is:
`$157.$11 (family name)` → `$262` with matching `$11` → `$262.$165` (location)
→ `$418` fragment with that `fid` (the bytes).

## Symbols — already available, no catalog change needed

The issue assumed `kfxlib_minimal` "has no font symbols." That is only true of
generator *code*: every symbol the font model uses is a standard YJ symbol that
already resolves via the imported catalog. Verified with
`LocalSymbolTable(catalog=SymbolTableCatalog(add_global_shared_symbol_tables=True))`:

```
$262 -> 262   $418 -> 418   $165 -> 165   $11 -> 11
$12  -> 12    $13  -> 13    $15  -> 15    $350 -> 350
$417 -> 417   $164 -> 164
```

So #15 can emit `IS("$262")`, `IS("$418")`, etc. directly — no additions to
`kfxlib_minimal/yj_symbol_catalog.py` are required. `IonBLOB` emission already
exists (used for `$417` images), so `$418` reuses that path.

## What #15 needs to do (hand-off)

1. Carry `@font-face` font files from the OEB manifest (the `.ttf`/`.otf`
   resources) into the generator instead of dropping them.
2. For each font: emit a `$418` raw-font fragment (BLOB, `fid` = a chosen
   location name) and a `$262` `@font-face` fragment (`$11` family name,
   `$165` → the location, plus `$12`/`$13`/`$15` only for non-regular faces).
3. Map source CSS `font-family` (and weight/style) onto the emitted family
   names, and set `$11` on the relevant `$157` styles so text uses the font.
4. Confirm whether a format-capabilities flag ($593) is needed to advertise
   font support (not observed as required in the reference; verify on-device).
5. Device gate: fonts render pass/fail only on a physical Kindle — sideload and
   confirm the embedded face actually displays.

The existing image path (`build_fragment_164` + `build_fragment_417` +
`extract_images_from_oeb`) is the closest template to copy.
