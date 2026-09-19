"""Guards on the tier-2 gate's record (#99).

Tier 2 decodes kfxgen's output with jhowell's full `kfxlib`, which is not in
this repository — the packaged plugin states no terms, so it is left out. CI
therefore has nothing to run tier 2 against and skips all 67 of its tests on
every pull request, and has since the suite was added. A skip is not a failure,
so a green run reports coverage that never happened. #91 reasoned from exactly
that and was wrong.

The zip cannot be committed, so tier 2 cannot become an ordinary CI gate. What
*can* run in CI is a check on the record of the last real run. These tests are
tier 1: no zip, no upstream import, nothing but a small JSON file, so they run
on every PR the way tier 2 cannot.

What this catches: the vendored pin moving without a tier-2 run behind it. That
is the moment tier 2 has something to say — a refreshed upstream is a decoder
whose agreement with ours is newly unverified — and the moment it is easiest to
skip, because the refresh itself looks like a chore.

What this does NOT catch, stated plainly so nobody reads more into a pass than
is there: a change to *kfxgen's own* generator that breaks decode compatibility
without touching the pin. The record would still match and these tests would
still pass. Only running tier 2 tells you that, and running it needs the zip.
A passing record means "tier 2 was run against this upstream", never "kfxgen
decodes correctly today".
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

pytestmark = [pytest.mark.tier1, pytest.mark.unit]

VENDOR = Path(__file__).resolve().parent.parent / "fixtures" / "vendor"
PIN_FILE = VENDOR / "kfx_input_plugin.version.txt"
RECORD_FILE = VENDOR / "kfx_input_plugin.lastrun.json"

WRITE_IT = "Run `python3 scripts/tier2_record.py` with the upstream zip present."


def _record() -> dict:
    assert RECORD_FILE.is_file(), (
        f"{RECORD_FILE.name} is missing, so there is no evidence tier 2 has ever "
        f"run against the vendored pin. {WRITE_IT}"
    )
    return json.loads(RECORD_FILE.read_text())


def test_record_names_the_pin_that_is_actually_vendored():
    """The recorded run must be against the pin in the tree.

    This is the whole gate. A pin refresh changes which decoder we are claiming
    agreement with; if the record still names the old one, the claim is stale
    and nobody has checked.
    """
    pin = PIN_FILE.read_text().strip()
    recorded = _record()["upstream_pin"]
    assert recorded == pin, (
        f"the vendored pin is {pin} but the last recorded tier-2 run was against "
        f"{recorded}. The upstream decoder changed and nothing has re-checked "
        f"that it still agrees with ours. {WRITE_IT}"
    )


def test_record_describes_a_run_that_did_something():
    """A record of zero passing tests is not a record of a run.

    `pytest -m tier2` with nothing collected exits 0, which is how tier 4 spent
    its whole life reporting success (#109). The same shape would be available
    here if the count were not checked.
    """
    passed = _record().get("tests_passed", 0)
    assert isinstance(passed, int) and passed > 0, (
        f"the record claims {passed!r} passing tier-2 tests. A run that collected "
        f"nothing is not a run. {WRITE_IT}"
    )


def test_record_says_which_upstream_was_actually_read():
    """The record must name the version it read out of the zip, and the zip.

    `upstream_pin` is copied from the sidecar, so on its own it is a claim
    about a file rather than about a run. `kfxlib_version` comes from
    `kfxlib/version.py` inside the archive that was tested, and
    `zip_sha256_prefix` identifies the archive itself.

    The digest is what distinguishes two plugins carrying the same library.
    A contributor ran this suite against the `kfxlib` inside KFX *Output*
    instead of KFX Input; both ship it, so at equal versions the sidecar check
    passes and a record could be written naming an upstream that was never the
    one under test. Same version, different bytes, different record.
    """
    rec = _record()
    for field in ("kfxlib_version", "zip_sha256_prefix"):
        assert rec.get(field), (
            f"the record has no {field!r}, so it describes the sidecar rather "
            f"than the archive that was read. {WRITE_IT}"
        )
    assert rec["kfxlib_version"] == rec["upstream_pin"], (
        f"the record was written against kfxlib {rec['kfxlib_version']} while "
        f"the sidecar pin says {rec['upstream_pin']}. Those must agree — "
        "`test_vendored_pin_matches_the_zip_it_describes` should have caught "
        "this before a record was written at all."
    )


def test_record_is_not_dated_in_the_future():
    """A date ahead of today means a wrong clock or a hand-edited record.

    Worth one line: every other assertion here trusts this file, and the point
    of the file is to be trustworthy without the zip.
    """
    ran_on = date.fromisoformat(_record()["ran_on"])
    assert ran_on <= date.today(), (
        f"the record is dated {ran_on}, which is in the future. It was edited by "
        "hand or written with a wrong clock; re-run the script."
    )
