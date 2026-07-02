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

## Device-gate outcome (#15, resolved 2026-07-02)

Fonts embed and render on a physical Kindle. Verified by decoding kfxgen's own
output with jhowell's `kfxlib` (installed locally as *KFX Input.zip*) and by
sideloading three test books (see `test_books/font-matching-test/` + the
inline-emphasis variant). Open questions 4 and 5 above are now answered:

- **Item 4 — `$593` capability flag: NOT required.** Fonts render without any
  `kindle_capability_metadata` entry. The list stays empty.
- **Item 5 — device gate: PASS.** Regular, bold, italic, and bold-italic
  embedded faces all render distinctly; a family that ships only a regular face
  gets synthesized bold; non-embedded families fall back to the device font.

Faults found and fixed to get there (all verified via `kfxlib` decode +
device):

1. **`$262` must be keyed `fid="$262"`** in a final (non-KPF-prepub) KFX — it is
   a `ROOT_FRAGMENT_TYPE`, so `fid` must equal the ftype. The family name lives
   only in `$11`; multiple faces are separate `$262` fragments all sharing
   `fid="$262"` (mirrors `kfxlib/kpf_book.py`, which re-keys prepub family-named
   `$262` to `"$262"`). Keying `$262` by family name → *"Root fragment has
   unexpected id"*.
2. **Register font fragments** in the `$270` entity map and `$419` entity index:
   each `$418` location must be listed (self-keyed `$262` registers as
   `[262, 262]`). Otherwise → *"missing from entity map"* and the reader ignores
   them.
3. **`override_kindle_font = True`** in `$490` when fonts are embedded, else the
   device font wins by default.
4. **Attach conversion `opts` to the OEB** (`_ensure_oeb_opts`) — Calibre's
   OEB has no `.opts` in the output-plugin path, so the Stylizer (which parses
   `@font-face` and computes per-element CSS) could not be constructed.
5. **Read inherited `font-family` via `Style[prop]`, not `Style.get()`** — the
   latter returns `None` for values inherited from `<body>`, which is where most
   books declare the family.

Both inline emphasis (`<b>`/`<i>`/`<strong>`/`<em>`) and block-level CSS emphasis
(`p { font-weight: bold }`) select the correct embedded face: `compute_block_style`
captures computed block weight/style (read inheritance-aware via `_computed_value`),
and the body run and each emphasis span compose block emphasis with inline flags
before matching (#15, was #34).
