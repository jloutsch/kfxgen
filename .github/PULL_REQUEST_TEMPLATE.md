<!--
Delete any section that does not apply. The device block is the one worth not
deleting reflexively — see CONTRIBUTING.md "Tier 4: device verification".
-->

## What this changes

<!-- The defect or the behaviour, not the diff. -->

## Why

<!-- What goes wrong without it. Link the issue. -->

## Verification

<!--
Which tiers actually ran, and what would have failed if the change were wrong.
"Tests pass" is not evidence a test can discriminate — say what you mutated to
check it fails.
-->

- [ ] tier 1–3 pass (`pytest`)
- [ ] `pytest -m tier3_strict` pass, or goldens intentionally regenerated and the shape change explained above
- [ ] corpus sweep, if the change touches conversion (`KFXGEN_CORPUS_DIR=… pytest -m slow -k corpus`)

## Device verification (tier 4)

CONTRIBUTING makes this **mandatory before merging** for anything touching
`$259`, `$260`, `$264`, `$265`, `$550`, or `$164`/`$417` — the fragments whose
breakage no structural test can see.

- [ ] Not applicable — this change cannot affect rendering
- [ ] Verified on hardware, and the commit carries a `Device-verified:` trailer

If verified, paste the sign-off table
(`python scripts/device_signoff.py --summary signoff.json`):

<!--
| Device | Firmware | Status | Check | Result |
|---|---|---|---|---|
-->

If not applicable, say why in one line. A device claim with no model and no
firmware is not a record (#109).
