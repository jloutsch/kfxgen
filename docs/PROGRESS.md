# Progress / Session Handoff

Living pick-up-where-we-left-off note. Last updated: 2026-06-30.

## Current state

- On `main`, clean, synced with origin. Current version: **5.3.23**
  (`plugin/kfxgen/__init__.py`).
- Calibre has the **5.3.23 build** of kfxgen installed locally
  (`./build_plugin.sh --install` rebuilds/installs from the working tree).
- Test suite: `.venv/bin/python -m pytest` → 444 passed, 12 skipped
  (tier-2 needs the vendored `KFX Input.zip`, absent by default), 0 xfail.
- Lint gate (both required): `.venv/bin/python -m ruff check` AND
  `ruff format --check`, ruff pinned **0.15.1**.

## Shipped recently (all device-verified where applicable)

| Issue | Ver | What |
|-------|-----|------|
| #20 | 5.3.21 | Section position-map conformance — jhowell `KFX Input` can now decode kfxgen output (`$264/$265/$550`); section eids added to `$265`. |
| #23 | 5.3.22 | Within-file `#anchor` chapter splitting — global block-coordinate model; Gatsby 3→9 chapters. Spec/plan in `docs/superpowers/{specs,plans}/2026-06-29-anchor-chapter-split*`. |
| #30 | 5.3.23 | Dynamic `$260` section base — content/section eids disjoint by construction at any scale; normal books byte-identical. Spec/plan `docs/superpowers/{specs,plans}/2026-06-30-dynamic-section-base*`. |
| #16 | — | Phase-0 spike (docs) for fonts: `docs/kfx-embedded-fonts-reference.md`. |

## Open issues + next steps

### #15 — Embed `@font-face` fonts in native KFX output (the big one; unblocked by #16)

Already scoped by the #16 spike (`docs/kfx-embedded-fonts-reference.md`). Key facts:
- Font model = image-resource pattern: **`$418`** raw font BLOB (analog of `$417`),
  **`$262`** `@font-face` (`$11` family, `$165`→`$418` location, `$12/$13/$15` =
  style/weight/stretch, `$350` = default value), **`$157`** style applies via `$11`.
- All font symbols already resolve in `kfxlib_minimal` (no catalog change).
- Template to copy: `native_generator.py::build_fragment_164` + `build_fragment_417`
  + `extract_images_from_oeb`.
- **#15 is generator-code-only.** Suggested phasing: (1) carry font files from OEB
  manifest → emit `$418`+`$262`; (2) map source CSS `font-family`/weight/style onto
  emitted family names + set `$11` on `$157` styles; (3) device gate.
- Reference KFX with real fonts (no plugin install needed): KDP books in the Calibre
  library — `Fatal Intrusion`, `Accidental Medicine`, `Cravings`.
- Biggest/riskiest remaining item; needs physical-Kindle rounds (fonts render pass/fail
  only on-device). Run as a phased plan, not one shot.

### #30 follow-through (low priority)

Done, but note: relocated section eids reach ~20k for pathological 1,000+ chapter
books; device-verified once (Shakespeare, 88% progress readout OK). No action unless
a much larger book surfaces.

### #18 — Track KFX format / kfxlib drift (independent, moderate)

Record which upstream `kfxlib` version/commit the vendored `kfxlib_minimal` fork came
from; add a mechanism to detect when upstream or the KFX symbol set drifts. No device
gate. Thematically tied to #15 (the "missing font symbols" gap is exactly this).

## How work has been run this session (workflow for continuity)

1. Investigate/scope → **brainstorming** skill → design spec in
   `docs/superpowers/specs/`.
2. **writing-plans** skill → phased TDD plan in `docs/superpowers/plans/`.
3. **subagent-driven-development** → fresh implementer + reviewer per task, opus
   whole-branch review, consolidated fix wave. Ledger at `.superpowers/sdd/progress.md`
   (git-ignored).
4. **Device gate** — the only render test for kfxgen `.kfx` is a physical-Kindle
   sideload (Previewer rejects raw `.kfx`; KFX Input round-trip to EPUB works as a
   conformance check since 5.3.21).
5. `/tech-debt-review` before merge; squash-merge PR + delete branch.

## Gotchas worth remembering

- Physical-Kindle sideload is the ONLY visual render test. `ebook-convert book.kfx
  out.epub` (jhowell KFX Input) is a conformance check, not a render.
- Upstream `kfxlib` for differential decode lives in the (gitignored)
  `KFX Input.zip`; extract it to inspect fragment shapes / decode with warnings.
- Content position ids are always even (base 1000, step 2). Section base is dynamic
  (#30): `max(SECTION_POS_BASE, content_max + SECTION_POS_STEP)`.
- Decoding a huge book via upstream kfxlib emits tens of thousands of benign
  "incorrect name None" / "content exceeds maximum" warnings — scale noise, not errors.

## Local scratch (not in repo)

- Desktop device-test files: `gatsby_issue23.kfx`, `shakespeare_issue23.kfx`,
  `shakespeare_issue30.kfx` — safe to delete.
