"""Tier 4: the checks a human performs on hardware (#109).

Excluded from every default run by `pytest.ini`'s `addopts`, so this costs a
normal `pytest` invocation nothing. Run it deliberately:

    pytest -m device                      # prints the checklist, fails unrun
    KFXGEN_DEVICE_SIGNOFF=signoff.json pytest -m device

These **fail** rather than skip when there is no sign-off. A skip is how a
gate goes quiet — #99 is tier 2 skipping on every PR since it was added, and
#92, #98 and #106 are the same shape. A release gate that reports "nothing to
do" when nobody has touched a Kindle is worse than one that reports nothing at
all, because it looks like a pass.

Generate a template to fill in with:

    python scripts/device_signoff.py --template > signoff.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.device.checklist import (
    CHECKS,
    DEVICES,
    SIGNOFF_ENV,
    procedure_text,
    results_for,
    signoff_path,
    validate_result,
)

pytestmark = pytest.mark.device


def _load_signoff():
    path = signoff_path()
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        pytest.fail(
            f"{SIGNOFF_ENV} points at {path!r}, which does not exist. "
            "Generate one with: python scripts/device_signoff.py --template"
        )
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        pytest.fail(f"{path} is not valid JSON: {exc}")


@pytest.fixture(scope="session")
def signoff():
    return _load_signoff()


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.id)
def test_device_check_signed_off(check, signoff):
    """One checklist entry. Fails with the procedure when it has not been run.

    The failure message *is* the checklist — running `pytest -m device` with no
    sign-off prints exactly what to do on the device, which is why these are
    tests rather than a document nobody opens.
    """
    if signoff is None:
        pytest.fail(
            "No device sign-off recorded.\n\n"
            f"{procedure_text(check)}\n\n"
            f"Record the result and re-run with {SIGNOFF_ENV}=<file>. "
            "Template: python scripts/device_signoff.py --template"
        )

    results = results_for(signoff, check.id)
    if not results:
        pytest.fail(f"No result recorded for this check.\n\n{procedure_text(check)}")

    problems = []
    for result in results:
        problems += [f"{result!r}: {p}" for p in validate_result(result)]
    assert not problems, (
        "Sign-off entries are not usable as evidence:\n  " + "\n  ".join(problems)
    )

    failed = [r for r in results if r.get("outcome") == "fail"]
    assert not failed, f"{check.title} failed on hardware:\n  " + "\n  ".join(
        f"{r['device']} (fw {r['firmware']}): {r.get('note', '')}" for r in failed
    )


def test_both_devices_were_exercised(signoff):
    """A pass on one device is evidence; it is not the same claim as a pass on
    both, and the record should not let the two blur.

    The Oasis is on terminal firmware: it stands for every reader that will
    never update, and for changes that remove a fallback it is the stricter
    test (#126). The Paperwhite is on current firmware and is the only one that
    can see render-side drift as Amazon moves. Neither substitutes for the
    other.
    """
    if signoff is None:
        pytest.fail(
            "No device sign-off recorded, so no device was exercised. "
            f"Set {SIGNOFF_ENV}=<file>."
        )

    seen = {r.get("device") for r in (signoff.get("results") or [])}
    missing = sorted(set(DEVICES) - seen)
    assert not missing, (
        "Only part of the tier-4 hardware was exercised. Missing: "
        + ", ".join(f"{d} ({DEVICES[d].label})" for d in missing)
        + ".\nRecord a result for both, or state in the release notes which "
        "device the claim rests on."
    )


def test_signoff_names_the_build_it_covers(signoff):
    """A result with no build attached is the version-granularity problem #109
    describes: it says the project was checked, not that *this change* was."""
    if signoff is None:
        pytest.fail(f"No device sign-off recorded. Set {SIGNOFF_ENV}=<file>.")
    build = (signoff.get("build") or "").strip()
    assert build, (
        "Sign-off does not name the build it covers. Record the commit sha or "
        "version, so 'was this change device-checked?' is answerable later."
    )
