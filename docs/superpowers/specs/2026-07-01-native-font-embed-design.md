# Native KFX font embedding (#15) — design

Date: 2026-07-01. Issue: #15 (embed EPUB `@font-face` fonts in native KFX
output). Phase-0 reference: `docs/kfx-embedded-fonts-reference.md` (#16).

## Goal

Carry a source EPUB's embedded `@font-face` fonts through kfxgen's native KFX
generator so text renders in those fonts on a Kindle, with **full face
matching** (regular / bold / italic / bold-italic selected per run).

## Scope (locked during brainstorming)

| Decision | Choice |
|---|---|
| Application ambition (v1) | **Full face matching** — pick the correct face per run by family + weight + style. |
| Source font formats | **TTF/OTF only** (magic-byte validated). WOFF/WOFF2/other → **skip + warn**, run falls back to current behavior. |
| Missing-face fallback | Fall back to the **regular embedded face of that family** and keep today's **synthetic** weight/style (faux bold/slant). |
| Family granularity | **Block-level `font-family`** + **per-run** weight/style. Intra-paragraph family switches (inline `<code>`, mixed-script spans) inherit the block family. |
| Mapping source | Calibre `Stylizer.font_face_rules` (already constructed in `_build_style_resolver`) + computed `font-family` per block. |

**Deferred (not v1):** WOFF/WOFF2 decoding (needs a decoder — `fonttools`, plus
`brotli` for WOFF2 — vendored into Calibre's bundled Python; see #15 comment),
font subsetting, per-run (intra-block) family resolution, `font-stretch`.

## Font model (from #16 reference)

Embedded fonts are the direct analog of the image resource pair kfxgen already
emits (`$164` metadata + `$417` bytes):

- **`$418`** — raw font BLOB. `fid` = a location string. Analog of `$417`.
- **`$262`** — the `@font-face` declaration. Analog of `$164`:
  - `$11` = font-family name (the join key; a `$157` style sets this to apply the font)
  - `$165` = location string → matches the `$418` `fid` (plain string, like `$164.$165`)
  - `$12` = font-style (`$382` italic; omit when `$350`/normal)
  - `$13` = font-weight (`$361` bold; omit when `$350`/normal)
  - `$15` = font-stretch — always omitted in v1
- **`$157`** style applies a font by setting `$11` to the `$262`'s family name.

Linkage: `$157.$11` → `$262` with matching `$11` → `$262.$165` → `$418.fid`.

All symbols already resolve via the imported catalog — **no `kfxlib_minimal`
change**. `IonBLOB` emission already exists (used by `$417`).

## Architecture

Two layers sharing one source of truth (the per-item `Stylizer`).

### Carriage layer — `font_table.py` (new)

`build_font_table(stylizers, oeb_book, log) → FontTable`, built once before
generation:

1. Aggregate `stylizer.font_face_rules` across all spine HTML items; **dedup**
   by resolved `src` href + descriptors (fonts in shared CSS repeat per item).
2. Per rule: resolve the `src` URL → OEB manifest font item → bytes. A `src`
   may list several `url()` entries; **prefer a TTF/OTF entry** over WOFF2.
3. Validate magic bytes — accept `\x00\x01\x00\x00`, `OTTO`, `true`, `ttcf`.
   **Skip + warn** on WOFF (`wOFF`), WOFF2 (`wOF2`), and anything else.
4. Produce faces, each:
   `{css_family (normalized), weight, style, stretch, bytes, location_name,
   emitted_family_name}`.

`FontTable` exposes `.faces` (for carriage) and `.match(...)` (for application).

### Application layer

- `inline_style.compute_block_style` also captures the computed `font-family`
  **list** (ordered, quotes stripped, lowercased) → `block_style["font_family"]`.
- When a `$157` style is allocated for a run, resolve
  `(block font_family list, run bold, run italic)` through `FontTable.match`.

### Component summary

| Component | Change |
|---|---|
| `font_table.py` (new) | `build_font_table(...) → FontTable`; `.faces`; `.match(family_list, bold, italic)` |
| `native_generator.build_fragment_418` (new) | raw font BLOB — analog of `build_fragment_417` |
| `native_generator.build_fragment_262` (new) | `@font-face` decl — analog of `build_fragment_164` |
| `inline_style.compute_block_style` | capture `font-family` list |
| `native_generator.build_fragment_157` + `_allocate_style` | accept resolved family → set `$11`; add family to style cache key; gate synthetic `$13`/`$12` |
| generator assembly | append `$418`+`$262` fragments alongside image `$164`+`$417` |
| `converter.py` | build `FontTable`; thread resolved family into style allocation |

## Data flow

```
OEB: TTF/OTF font items + CSS @font-face rules
        │
  build_font_table() ── resolve src→href→bytes, validate TTF/OTF, dedup ──► FontTable
        │                                                                     │
        ├─ carriage:  per face → build_fragment_418 + build_fragment_262  ──► book fragments
        │                                                                     │
        └─ application: block extraction (Stylizer) captures font-family list
                 └─ _allocate_style(font_family, bold, italic, …)
                        └─ FontTable.match() → (emitted_family | None, weight_ok, style_ok)
                               └─ build_fragment_157: set $11; synthetic $13/$12 only if not satisfied
```

## Face-matching algorithm

`FontTable.match(css_family_list, bold, italic) → (emitted_family | None, weight_ok, style_ok)`

1. For each family in the ordered list (already normalized), find embedded
   faces of that family. Use the **first family that has any embedded face**.
   No family in the list is embedded → `(None, False, False)`.
2. Among that family's faces, look for an **exact face**: weight matches
   (bold → weight ≥ 600) and style matches (italic vs normal). Found →
   `(face.emitted_family, True, True)`.
3. No exact face → **fall back to the regular face** of the family (weight
   normal + style normal; else any face). Return its family with
   `weight_ok = not bold`, `style_ok = not italic` (True means "nothing to
   synthesize").

`build_fragment_157` then:
- `$11 = emitted_family` when non-`None`.
- synthetic `$13` (bold) only when **bold AND not weight_ok**.
- synthetic `$12` (italic) only when **italic AND not style_ok**.
- `emitted_family is None` → **byte-for-byte today's behavior** (no `$11`,
  synthetic as before). This is the CACE guardrail: no-font books and
  unembedded-family runs stay identical.

## Emitted family naming

Deterministic, unique, lowercased slug per face, e.g. `merriweather-700i`.
Internal only — kfxgen owns both `$262.$11` and `$157.$11`, so both get the
**identical** string (sidesteps the KDP lowercase-mismatch quirk in the #16
reference). Uniqueness enforced via the dedup index.

## Phase 0 confirmed (2026-07-01)

Verified against a real 4-face EPUB (family names genericized here per repo
policy). Findings that reshape Tasks 2/4:

- `stylizer.font_face_rules` is a list of **`css_parser.css.cssfontfacerule.CSSFontFaceRule`**
  objects — **not dicts**. They have neither `.get` nor `__getitem__`.
- Field access is `rule.style.getPropertyValue(<prop>)`:
  - `font-family` → e.g. `'"Family"'` (quoted string; `normalize_family` strips quotes)
  - `src` → e.g. `'url(fonts/regular.ttf)'` (`url()` form, href relative to the
    CSS file; `extract_src_urls` parses it, and `build_manifest_lookup`'s
    basename fallback resolves the relative path)
  - `font-weight` → `'normal'` / `'bold'`; `font-style` → `'normal'` / `'italic'`
- Manifest font bytes are **de-obfuscated** by Calibre's OEB import (magic
  `00010000` TTF observed) — confirms the de-obfuscation assumption.
- The 4 faces were R/B/I/BI of one family — the canonical face-matching case.

**Accessor requirement:** `faces_from_rules` must read fields via a small
adapter that handles a `CSSFontFaceRule` (`.style.getPropertyValue`) AND a
plain dict (`.get`, used by the pure unit tests). Building the OEB for a
Stylizer outside the plugin needs `create_oebbook(log, opf, opts)` then
`oeb.opts = opts` (the real output pipeline attaches `opts`; Stylizer reads it).

## Fragment shapes

```
$418:  YJFragment(fid=IS(location), ftype=IS("$418"), value=IonBLOB(bytes))

$262:  { $11:  <emitted_family>,      # join key
         $165: <location>,            # → $418 fid (plain string)
         $13:  $361  (bold)   — omitted when normal ($350),
         $12:  $382  (italic) — omitted when normal }
         # $15 (stretch) always omitted in v1
```

Descriptors reflect the **actual face** (honest declaration) even though
selection is by the unique `$11` name — hedges if a Kindle build weight-matches,
and mirrors observed KDP output. Weight/style symbols reuse those already in
`build_fragment_157` (`$361`/`$382`/`$350`).

## Testing

**Unit (tier-1, no Calibre):**
- `FontTable.match`: exact match; family-miss → `None`; regular fallback with
  correct `weight_ok`/`style_ok`; all bold×italic combinations; family-list
  fallback stacks.
- `build_font_table`: TTF/OTF accept; WOFF/WOFF2/other skip+warn; dedup across
  stylizers; `src`→href resolution; multi-`url()` prefers TTF/OTF. Fake
  stylizer objects (`font_face_rules` = list of dicts) + fake manifest.
- `build_fragment_418` / `build_fragment_262`: structure, symbol registration,
  descriptor omission when default, IonBLOB.
- `build_fragment_157` + `compute_block_style`: `$11` set; synthetic `$13`/`$12`
  gating; family capture + normalization.

**Regression guardrail (CACE):** a no-font book produces **byte-identical**
output to current (the `emitted_family is None` path).

**Integration (tier-2, needs Stylizer):** convert a tiny EPUB with one embedded
TTF `@font-face`; assert `$418`/`$262` present and `$157` carries `$11`;
round-trip decode via jhowell KFX Input confirms the font survives.

## Phasing (TDD, subagent-driven per handoff workflow)

- **Phase 0** — spike: verify `font_face_rules` dict keys and whether `$11`/`$165`
  want plain strings vs symbols, read off a real font EPUB. Confirm Calibre
  de-obfuscates fonts on import. Pin constants into this spec.
- **Phase 1** — Carriage: `FontTable` + `$418`/`$262` emit + assembly.
- **Phase 2** — Application: family capture, `match()`, `$157` `$11` + synthetic
  gating, `_allocate_style` threading + golden regression.
- **Phase 3** — Device gate (only real render test).
- **Phase 4** — docs/CHANGELOG/version bump, `/tech-debt-review`, PR.

## Device gate (Phase 3)

Build an EPUB embedding a visually unmistakable TTF (distinct letterforms) as
body text, plus a real bold and italic face. Sideload to a physical Kindle,
confirm: (1) body renders in the embedded face; (2) real bold/italic render
distinctly; (3) fallback (bold run, regular-only family) shows faux bold; (4) a
no-font book renders unchanged. Decide the `$593` capability flag here — the #16
reference did not observe it as required; if fonts don't display, add it and
re-test.

## Risks / boundaries

- **CACE:** adding `$11` plumbing must not disturb existing output. Guaranteed by
  the `emitted_family is None` path + byte-identical regression test.
- **Size:** whole fonts carried (no subsetting) — larger `.kfx`. Acceptable v1.
- **Obfuscation:** assumes Calibre de-obfuscates on import; OEB manifest bytes
  should be clear. Verify in Phase 0.
- **Deferred:** WOFF/WOFF2, subsetting, intra-block family, `font-stretch`.
