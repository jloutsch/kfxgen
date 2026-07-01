# Progress / Session Handoff

Living pick-up-where-we-left-off note. Last updated: 2026-07-01.

## Current state

- On branch `feat/15-native-font-embed`. Branch version bumped to **5.4.0**
  (`plugin/kfxgen/__init__.py`); `main` is still 5.3.23.
- Test suite: `.venv/bin/python -m pytest` → 482 passed, 12 skipped
  (tier-2 needs the vendored `KFX Input.zip`, absent by default), 0 xfail.
  Tree-wide `ruff check` + `ruff format --check` both clean.
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

**Code complete on `feat/15-native-font-embed` — device gate is the only thing
left before release.** Plan: `docs/superpowers/plans/2026-07-01-native-font-embed.md`.

Implemented (Tasks 0–11, all committed, 482 tests green, no-font output
byte-identical via tier3_strict goldens):
- `plugin/kfxgen/font_table.py` — `Face`, `faces_from_rules`, `FontTable.match`,
  `build_font_table` (aggregates Calibre `@font-face` rules + OEB manifest bytes;
  TTF/OTF only, WOFF skipped with a warning).
- `native_generator.py` — `build_fragment_418` (raw font BLOB) + `build_fragment_262`
  (`@font-face` decl); `build_fragment_157` gained a `font_family` (`$11`) param;
  body + `_emphasis_style` resolve each run via `match()` and set `$11`, suppressing
  synthetic bold/italic when a real face exists.
- `inline_style.py` / `converter.py` — block-level `font_family` capture + the
  converter builds the `FontTable` and passes it to `generate_full_book`.
- Tests: `tests/unit/test_font_table.py`, font tests in `test_native_generator.py`,
  and `tests/integration/test_font_integration.py` (real four-face fixture at
  `test_books/font-matching-test/`, Charis SIL under OFL).

**Remaining before merge/release:**
1. **Device gate (only on physical Kindle).** Sideload a font-embedded conversion;
   confirm the embedded face renders (not the device default), that real bold/italic
   render distinctly, that a regular-only family with a `<b>` run shows faux bold,
   and that a no-font book is unchanged. Decide whether a `$593` capability flag is
   needed — the #16 reference did not observe it as required; verify on-device and
   record the outcome in `docs/kfx-embedded-fonts-reference.md`.
2. `/tech-debt-review`, then open the PR (held pending the device gate).

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
