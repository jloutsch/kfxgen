# Contributing to kfxgen

Thanks for your interest. This file documents the test-oracle hierarchy and
gate ladder pinned by issue
issue 42.

## Local setup

```sh
pip install -r requirements-dev.txt
pre-commit install                          # pre-commit hooks (lint, format, path/binary guards)
pre-commit install --hook-type pre-push     # pre-push hook (tier-1 unit tests)
```

The hooks (issue 56) give fast
local feedback. They are NOT a CI replacement — outside contributors won't
have hooks installed, so CI remains the canonical gate. Bypass with
`SKIP=hook-id git commit` or `git push --no-verify` if you have a real
reason; CI will still catch it.

## Oracle hierarchy

KFX is a closed format. Tests are only as good as the oracle they check against.
Tag every test with the tier of oracle it relies on, so reviewers can tell
"verified" apart from "self-consistent."

| Tier | Marker | Oracle | Cost | Where it runs |
|------|--------|--------|------|---------------|
| 1 | `tier1` | In-process Python invariants (entity-id uniqueness, position-map subset relations, fragment-graph consistency — encoded in `tests/unit/test_kfx_invariants.py` and `tests/unit/test_position_map.py`) | < 1 s | Pre-push hook + every PR |
| 2 | `tier2` | Calibre `kfxlib` differential decode (round-trips our output through an independent decoder) | seconds | Every PR |
| 3 | `tier3` | Golden-file diff against synthetic regression corpus under `tests/fixtures/golden/expected/` (see [Golden corpus](#golden-corpus) below) | seconds | Every PR |
| 4 | `device` | Manual verification on a physical Kindle (Paperwhite/Oasis/Voyage) | minutes, manual | Release tags only |

A tier-1 pass means "kfxgen is internally consistent." A tier-2 pass means
"another implementation can decode it." A tier-3 pass means "it matches a
build that worked on a real device." A tier-4 pass means "it works on a real
device today." Don't conflate them.

## Gate ladder

| Gate | Required tiers | Command |
|------|---------------|---------|
| Pre-push (local) | tier1 | `pytest -m tier1` (wired up in issue 56) |
| CI on PR | tier1 + tier2 + tier3 | `pytest -m "not device"` (already the default in `pytest.ini`) |
| Release tag | + device | Manual device run by maintainer; release notes must reference which devices were tested |

Skipping `device` in CI is intentional — the runner has no Kindle attached.
Test failures under `device` block release tags but never block PR merges.

## Marker conventions

Markers are declared in `pytest.ini`. Add the appropriate tier marker to every
new test:

```python
import pytest

@pytest.mark.tier1
def test_position_map_subset_invariant():
    ...

@pytest.mark.tier2
def test_kfxlib_round_trip():
    ...

@pytest.mark.tier3
def test_matches_corpus_baseline():
    ...

@pytest.mark.device
def test_nav_pane_renders_on_paperwhite():
    ...
```

Composite categorization markers (`unit`, `integration`, `slow`, `benchmark`,
`critical`) are orthogonal to tier — use them in addition, not instead.

## The upstream kfxlib copy

The tier-2 differential decode test (`tests/integration/test_kfxlib_diff.py`)
compares output from our trimmed `kfxlib_minimal` copy against Calibre's
upstream `kfxlib` (jhowell's KFX Input plugin). That third-party plugin is
**not redistributed in this repository**, and the reason is narrower than "the
license forbids it": the `kfxlib` *source* inside it is marked GPL v3, which is
the grant kfxgen already relies on to include a modified copy of part of it
(see `NOTICE`). What is not redistributed is the *packaged plugin zip* — a
third-party build artifact that ships with no `LICENSE` file and no
package-level terms, so nothing states how it may be passed on.

To run the tier-2 test, supply the zip locally at
`tests/fixtures/vendor/kfx_input_plugin.zip` using the procedure below; the
test skips cleanly when the file is absent, so CI without it still passes.

**Setup / refresh procedure** (supply the zip, or refresh it when upstream
Calibre's KFX Input plugin ships a new version worth diffing against):

```bash
cp "$HOME/Library/Preferences/calibre/plugins/KFX Input.zip" \
   tests/fixtures/vendor/kfx_input_plugin.zip
unzip -p tests/fixtures/vendor/kfx_input_plugin.zip kfxlib/version.py \
   | sed -E 's/^__version__ *= *"([^"]+)".*/\1/' \
   > tests/fixtures/vendor/kfx_input_plugin.version.txt
pytest -m tier2
```

The version sidecar lets `git diff` show kfxlib version changes in
plain text. The zip itself is gitignored and must not be committed; only
the `.version.txt` sidecar is tracked.

If `pytest -m tier2` fails after the refresh, the upstream parser has
moved relative to ours. Investigate before committing — the divergence
is the test's whole point.

## Golden corpus

The tier-3 corpus lives at `tests/fixtures/golden/`:

- `inputs.py` — Python builders that construct synthetic EPUBs covering
  distinct regression shapes (minimal, body images, with cover, multi
  chapter). Each fixture is paired with a shape-assertion test that
  guards against fixture rot.
- `expected/<name>.kfx` — committed golden KFX bytes, produced by
  running each input through `converter.convert_oeb_to_kfx`.
- `regenerate.py` — script that rebuilds every `expected/*.kfx`.

**Why synthetic, not device-verified real-book bytes:** the
device-verified KFX outputs that motivated this corpus live in the
maintainer's local `research/` directory and are 8+ MB each,
gitignored, and derived from copyrighted EPUBs — committing them is
a copyright + repo-bloat non-starter. Synthetic fixtures cover the
same shapes (`$164`/`$417` cover, `$175`-bearing `$259` image
entries, multi-section `$265` maps) at ~7 KB each.

**Diff strategy:** structural only (fragment-type counts and per-type
top-level key sets). The generator currently emits ~240 byte
differences between two consecutive runs of the same input — a
SHA-256-byte-identical layer would fail on every test run. Tracked
as issue 89; once that
lands, a `tier3_strict` byte-identical layer can be added on top of
the structural diff.

### Updating goldens after an intentional generator change

If your change deliberately alters KFX output, the structural diff
will fail until you regenerate the goldens:

```bash
python -m tests.fixtures.golden.regenerate
git diff --stat tests/fixtures/golden/expected/
pytest -m tier3
git add tests/fixtures/golden/expected/ tests/fixtures/golden/inputs.py
```

The PR description must state explicitly which fragment-shape
properties changed and why — golden churn is the tier-3 oracle saying
"output drifted," and reviewers need to confirm the drift is intended.
If the structural fingerprint changes but no shape assertion fires,
also confirm that the existing fixture set still meaningfully covers
the new shape; otherwise add a fixture and a new shape assertion to
`test_golden_corpus.py`.

## Threat model

If you are adding code that touches EPUB input parsing, output paths, or
binary serialization, read [SECURITY.md](SECURITY.md) first. The scope is
*adversarial EPUB author, single-user blast radius* — kfxgen relies on
Calibre for the EPUB-parsing surface and defends the KFX-generation surface
itself.

## KFX correctness invariants

The rules recovered from device testing are not yet collected in one place.
Where they live today: `tests/unit/test_kfx_invariants.py` and
`tests/unit/test_position_map.py` encode the ones that can be asserted,
`CHANGELOG.md` records how each was found, and `plugin/kfxgen/native_generator.py`
carries the reasoning next to the code that depends on it. Writing the missing
explainer is tracked in issue 100.

(An earlier version of this file pointed at a `MEMORY.md` at the project root.
No such file has ever existed here, so anyone who followed that pointer found
nothing.)

If you change anything that touches `$259`, `$260`, `$264`, `$265`, `$550`, or
`$164`/`$417`, real-device validation (tier 4) is mandatory before merging.
Tier-1 invariant tests (issue 43) encode many of these rules, but not all of
them — when in doubt, test on device.

## Public-domain corpus sweep

`tests/integration/test_public_corpus.py` runs real books through the whole
pipeline and checks invariants that the unit suite cannot. It exists because
unit tests and the synthetic golden corpus both passed while kfxgen was silently
discarding 44% of one book's body text, and while every internal link it emitted
resolved against nothing. Each assertion maps to a bug that shipped.

The corpus is not committed — point the test at a local directory of `.epub`
files (the Gutenberg top-90 set is what it was built against):

```bash
export KFXGEN_CORPUS_DIR=/path/to/corpus
pytest tests/integration/test_public_corpus.py -m slow
```

Without `KFXGEN_CORPUS_DIR` the tests skip, so a normal run is unaffected.

Invariants alone will not catch text quietly going missing. For that, record a
baseline before a change and diff against it after:

```bash
KFXGEN_CORPUS_WRITE_BASELINE=1 KFXGEN_CORPUS_BASELINE=corpus-baseline.json \
  pytest tests/integration/test_public_corpus.py -m slow -k baseline   # record

KFXGEN_CORPUS_BASELINE=corpus-baseline.json \
  pytest tests/integration/test_public_corpus.py -m slow -k baseline   # compare
```

The baseline is keyed by filename and is a local artifact — keep it out of the
repo, alongside the corpus itself.
