# Contributing to kfxgen

Thanks for your interest. This file documents the test-oracle hierarchy and
gate ladder pinned by issue
issue 42.

## Local setup

```sh
python3.13 -m venv .venv                    # `.venv` at the repo root, gitignored
.venv/bin/pip install -r requirements-dev.txt
pre-commit install                          # pre-commit hooks (lint, format, path/binary guards)
pre-commit install --hook-type pre-push     # pre-push hook (tier-1 unit tests)
```

The venv path matters to more than tidiness: the tier-1 pre-push hook runs
`.venv/bin/pytest` when that exists and falls back to `pytest` on PATH when it
does not. A system Python usually lacks `hypothesis` (four `tests/unit`
modules import it), so the fallback fails at collection — a missing
environment that reads as a broken gate. If you keep your environment
somewhere else, or work in a git worktree where `.venv` was never created,
either symlink it to `.venv` or expect to run tier-1 by hand.

The hooks (issue 56) give fast
local feedback. They are NOT a CI replacement — outside contributors won't
have hooks installed, so CI remains the canonical gate. Bypass with
`SKIP=hook-id git commit` or `git push --no-verify` if you have a real
reason; CI will still catch it.

### If you have a global `core.hooksPath`

`pre-commit install` refuses to run when `core.hooksPath` is set, and git
ignores `.git/hooks` entirely while it is — so the two commands above can
leave you believing the gate is installed when nothing runs. Check first:

```sh
git config --get core.hooksPath        # prints nothing and exits 1 when unset —
                                       # that "failure" is the good case
```

If it prints a path, pick one of the two remedies below. Both need the install
itself run with the setting neutralised, because `pre-commit install` refuses
outright while it is present:

```sh
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0= \
  pre-commit install --hook-type pre-push
```

**Option A — point this repo at its own hooks.** Simplest, and needs no
chaining:

```sh
git config --local core.hooksPath "$(git rev-parse --git-path hooks)"
```

The cost is that whatever lives in your global hooks directory no longer runs
*for this repo*. If that directory holds something you rely on everywhere — a
secret scanner, for instance — you want Option B instead.

**Option B — chain from the global hook to the repo's.** Keeps both. Add this
to the `pre-push` file in your global hooks directory (the `pre-commit` file
there needs the same treatment, with `pre-push` swapped for `pre-commit`):

```sh
#!/usr/bin/env bash
LOCAL_HOOK="$(git rev-parse --git-dir)/hooks/pre-push"
if [[ -x "$LOCAL_HOOK" ]]; then
  exec "$LOCAL_HOOK" "$@"
fi
```

Forwarding `"$@"` is not optional — pre-push hooks are handed the remote name
and URL as arguments and the ref list on stdin, and `exec` preserves both.

Then verify it fires rather than assuming, because a pre-push hook that is
present but never invoked reports nothing and looks exactly like one that
passes:

```sh
git init --bare /tmp/hooktest.git
git push /tmp/hooktest.git HEAD        # the tier-1 run must appear in the output
rm -rf /tmp/hooktest.git
```

## Oracle hierarchy

KFX is a closed format. Tests are only as good as the oracle they check against.
Tag every test with the tier of oracle it relies on, so reviewers can tell
"verified" apart from "self-consistent."

| Tier | Marker | Oracle | Cost | Where it runs |
|------|--------|--------|------|---------------|
| 1 | `tier1` | In-process Python invariants (entity-id uniqueness, position-map subset relations, fragment-graph consistency — encoded in `tests/unit/test_kfx_invariants.py` and `tests/unit/test_position_map.py`) | < 1 s | Pre-push hook + every PR |
| 2 | `tier2` | Calibre `kfxlib` differential decode (round-trips our output through an independent decoder) | seconds | Every PR |
| 3 | `tier3` | Golden-file diff against synthetic regression corpus under `tests/fixtures/golden/expected/` (see [Golden corpus](#golden-corpus) below) | seconds | Every PR |
| 4 | `device` | Manual verification on the physical Kindles listed under [Tier 4](#tier-4-device-verification) | minutes, manual | Release tags only |

A tier-1 pass means "kfxgen is internally consistent." A tier-2 pass means
"another implementation can decode it." A tier-3 pass means "it matches a
build that worked on a real device." A tier-4 pass means "it works on a real
device today." Don't conflate them.

## Gate ladder

| Gate | Required tiers | Command |
|------|---------------|---------|
| Pre-push (local) | tier1 | `pytest -m tier1` (wired up in issue 56) |
| CI on PR | tier1 + tier2 + tier3 | `pytest tests/unit tests/integration` (the `pytest.ini` default `-m` applies) |
| CI on PR | tier3_strict | `pytest -m tier3_strict` — a separate step, because `addopts` excludes it |
| Release tag | + device | Manual device run by maintainer; release notes must reference which devices were tested |

Skipping `device` in CI is intentional — the runner has no Kindle attached.
Test failures under `device` block release tags but never block PR merges.

### Tier 4: device verification

The hardware, pinned. "A physical Kindle" is not a record — `Paperwhite` alone
spans several revisions whose behaviour differs (the CHANGELOG thumbnail table
has a Voyage failing where a Paperwhite succeeds), so generation and firmware
both get written down (#109).

| Device | Firmware last seen |
|---|---|
| Paperwhite, 11th generation (2021) | 5.19.2 |
| Oasis, 10th generation (2019) | 5.18.2.1.1 |
| Voyage, 7th generation (2014) | 5.13.56 (3731990038) |

**Read the firmware off the device every run** — Settings → Device Options →
Device Info. The table records what was last seen, not what is guaranteed.
This file used to list the Oasis as "terminal — no further updates" at 5.18.2,
and the sign-off validator rejected any other value for it as a transcription
error. The Oasis then shipped 5.18.2.1.1, at which point the tooling built to
keep the record honest would have thrown out the honest record. Nothing about
firmware is assumed now.

Test both, and understand what each buys. The Oasis is an older model kept
some way behind current firmware, so it stands in for readers who lag — and
for a change that removes a fallback it is the *stricter* test (#126 dropped
`$31` with no fallback, which would have degraded silently there). The
Paperwhite tracks current firmware and is the one that can observe render-side
drift as Amazon moves. A pass on one is real evidence; it is not the same
claim as a pass on both.

Run the checklist:

```bash
pytest -m device                       # prints the procedures, fails unrun
python scripts/device_signoff.py --template > signoff.json
# ... perform the checks, fill in outcomes and the Paperwhite's firmware ...
KFXGEN_DEVICE_SIGNOFF=signoff.json pytest -m device
python scripts/device_signoff.py --summary signoff.json   # release-notes table
```

These tests **fail** rather than skip when nothing has been signed off. A skip
is how a gate goes quiet: #99 is tier 2 skipping on every PR since it was
added, and #92, #98 and #106 are the same shape. Before #109 the `device`
marker was carried by no test at all, so `pytest -m device` collected nothing
— exit 5, "no tests collected". That is a failure code rather than a false
pass, but it reports the absence of tests, never the absence of testing, and
it tells a reader nothing about what should have been checked.

### Recording a device pass

Put a trailer on the commit, one line per device, so `git log` can answer "was
this change checked on hardware?":

```
Device-verified: Oasis 10th gen (2019), firmware 5.18.2.1.1 — TOC taps, return links
Device-verified: Paperwhite 11th gen (2021), firmware 5.19.2 — TOC taps, return links
```

Firmware is read per run for both devices. List the trailers over a range
with:

```bash
python scripts/device_signoff.py --trailers v5.7.2..HEAD
```

The trailer is the per-change record; the CHANGELOG entry is the per-release
one. Both are wanted — the CHANGELOG says the release was verified, the trailer
says which change was.

`tier3_strict` needs its own invocation for a mechanical reason worth knowing:
`pytest.ini` puts `-m "not slow and not device and not tier3_strict"` in
`addopts`, and that applies to every plain `pytest` call. Only an explicit `-m`
on the command line overrides it. A step that merely adds paths does not.

Note what tier-2 does *not* contribute here. It skips whenever the vendored
KFX Input zip is absent, which is every CI run — see issue 99. The row above
lists it because it runs locally when the zip is present, not because CI
verifies it.

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

# Tier 4 is a checklist a human performs; add entries to
# tests/device/checklist.py rather than writing a new test function.
@pytest.mark.device
def test_device_check_signed_off(check, signoff):
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

Running it also needs `Pillow` and `beautifulsoup4`, which upstream `kfxlib`
imports and kfxgen does not. Both are in `requirements-dev.txt`; if you set up
before they were added, re-run `pip install -r requirements-dev.txt` or the
suite errors at setup rather than skipping. That gap went unnoticed for as long
as nobody ran tier-2, which is the point #99 is about.

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

**Diff strategy:** two layers.

1. **Structural** (`tier3`) — fragment-type counts and per-type top-level key
   sets. Runs by default.
2. **Byte-identical** (`tier3_strict`) — SHA-256 of the whole file against the
   committed golden. Excluded from `pytest.ini`'s `addopts`, so a plain
   `pytest` will not run it; CI invokes it explicitly with `-m tier3_strict`.

The strict layer was opt-in because the generator used to emit ~240 byte
differences between two consecutive runs of the same input, which would have
failed every run. Issue 96 fixed that — image style symbols were being
allocated by iterating an unordered set, and CPython randomizes string hashing
per interpreter. The layer now passes in well under a second and gates every
PR.

When it fails, the bytes moved. That is either a regression or an intentional
output change whose goldens need regenerating (see below). Regenerating to make
CI green without first confirming the drift was intended defeats the gate.

### Updating goldens after an intentional generator change

If your change deliberately alters KFX output, the goldens must be
regenerated. Verify **both** layers afterwards — a change that moves bytes
without moving structure passes `tier3` while the `tier3_strict` step that
blocks the merge still fails, so checking only the first tells you nothing
about the gate you are actually up against:

```bash
python -m tests.fixtures.golden.regenerate
git diff --stat tests/fixtures/golden/expected/
pytest -m tier3
pytest tests/integration/test_golden_corpus.py -m "tier3_strict and not device"
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

Start with **[docs/kfx-generation-explained.md](docs/kfx-generation-explained.md)**.
It covers the pipeline, the fragment graph, what each symbol means and how
confident we are of it, and the pitfalls that have already cost real debugging.

The underlying sources it draws on, when you need more than the summary:
`tests/unit/test_kfx_invariants.py` and `tests/unit/test_position_map.py` encode
the rules that can be asserted, `CHANGELOG.md` records how each was found, and
`plugin/kfxgen/native_generator.py` carries the reasoning next to the code that
depends on it.

(An earlier version of this file pointed at a `MEMORY.md` at the project root.
No such file has ever existed here, so anyone who followed that pointer found
nothing.)

If you change anything that touches `$259`, `$260`, `$264`, `$265`, `$550`, or
`$164`/`$417`, real-device validation (tier 4) is mandatory before merging.
Tier-1 invariant tests (issue 43) encode many of these rules, but not all of
them — when in doubt, test on device.

Mandatory means the PR carries the evidence: run the checklist, and put a
`Device-verified:` trailer on the commit naming model, firmware and what was
checked. See [Tier 4](#tier-4-device-verification). Without it the claim is
unanswerable a month later, which is what #109 was opened about.

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

The invariants are also only as good as what they look for. `nav_junk` matches
four literal strings, which turned out to report one failing book where there
were nine, and that one was our own generated heading (#136). When an
invariant's scope is in doubt, measure it directly before trusting the count:

```bash
python research/measure_contents_leak.py          # #132 and #133
```

Keep book titles and author names out of anything committed — this repo is
public and the corpus is real books. Chapter titles are the usual way one slips
in, because a chapter is so often titled after the book or its author; that
script prints only recognised structural labels, chapter indices and block
counts in place of free text. It does print Gutenberg ids, which name a book as
precisely as a title does — the corpus membership is already public in
`research/corpus_ids.json`, but pairing an id with a defect is a judgement
call, so treat the output as a corpus diagnostic rather than as copy for an
issue or a PR.
