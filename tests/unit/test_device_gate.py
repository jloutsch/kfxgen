"""Guards on the tier-4 device gate itself (#109).

Tier 4 is the only oracle that proves a KFX renders, and it was the one tier
with nothing mechanical behind it: `pytest.ini` declared the `device` marker,
`CONTRIBUTING.md` documented it as the example to copy, and *no test carried
it*.

#109 describes that as exiting 0 and reading as a pass. Measured, it is
narrower than that: `pytest -m device` with nothing to collect exits 5, which
is a failure code. The real gap is that the tier had no *content* — no
procedure to perform, no record to leave, and nothing that distinguishes "no
tests exist" from "here is what should have been checked and was not". An
empty selection reports the absence of tests, never the absence of testing.

These tests run in tier 1, on every PR, and fail if that state returns.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.device.checklist import (
    CHECKS,
    DEVICES,
    check_by_id,
    results_for,
    validate_result,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.tier1
@pytest.mark.unit
def test_device_marker_collects_at_least_one_test():
    """`pytest -m device` must not be a no-op.

    Runs the real collection rather than grepping for the decorator, because
    the thing being guarded is the command's behaviour: an empty selection
    exits 0 and reports success, which is exactly how the gate went unnoticed.
    """
    # `-o addopts=` clears the ini addopts for this child run. Two reasons:
    # it drops the `-m "not device"` default that would otherwise fight the
    # `-m device` here, and it drops `--verbose`, which overrides `-q` and
    # makes collection print a `<Function ...>` tree with no node ids to count.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-m",
            "device",
            "--collect-only",
            "-q",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # Count collected node ids rather than matching a summary phrase. An empty
    # selection prints "no tests collected (N deselected)" and exits 0 — the
    # first draft of this test asserted on the absence of "no tests ran", which
    # pytest never emits here, so it passed while zero device tests existed.
    # That is the very failure being guarded against, one level up.
    collected = [ln for ln in proc.stdout.splitlines() if "::" in ln]
    assert collected, (
        "`pytest -m device` collects nothing, so the tier-4 gate passes "
        "vacuously — the failure #109 was opened for. Exit code was "
        f"{proc.returncode}.\n{proc.stdout[-2000:]}"
    )


@pytest.mark.tier1
@pytest.mark.unit
def test_every_check_names_a_procedure_and_its_provenance():
    """A checklist entry with no procedure cannot be performed, and one with
    no issue behind it is a check nobody can justify keeping."""
    assert CHECKS, "the device checklist is empty"
    for check in CHECKS:
        assert check.procedure.strip(), f"{check.id} has no procedure"
        assert check.issues, f"{check.id} cites no issue it stands for"
        assert check.title.strip(), f"{check.id} has no title"


@pytest.mark.tier1
@pytest.mark.unit
def test_device_inventory_records_model_and_generation():
    """#109's own argument: "a physical Kindle" is not a record. "Paperwhite"
    spans several revisions with known behavioural differences, so the model
    generation has to be recorded.

    Firmware is deliberately *not* asserted here. It is a last-known value used
    to pre-fill a template, not a fact the inventory can guarantee — the Oasis
    was recorded as terminal and then updated anyway.
    """
    assert DEVICES, "no devices recorded"
    for device in DEVICES.values():
        assert device.model, f"{device.id} has no model recorded"
        assert device.generation, f"{device.id} has no generation recorded"


@pytest.mark.tier1
@pytest.mark.unit
class TestSignoffValidation:
    """A sign-off is the per-change record #109 asks for, so an entry that
    omits what makes it evidence has to be rejected rather than counted."""

    def _result(self, **over):
        base = {
            "check": CHECKS[0].id,
            "device": "paperwhite-11",
            "firmware": "5.19.2",
            "outcome": "pass",
        }
        base.update(over)
        return base

    def test_accepts_a_well_formed_pass(self):
        assert validate_result(self._result()) == []

    def test_rejects_unknown_check(self):
        assert validate_result(self._result(check="not-a-check"))

    def test_rejects_unknown_device(self):
        assert validate_result(self._result(device="kindle-fire"))

    def test_rejects_missing_firmware(self):
        assert validate_result(self._result(firmware=""))

    def test_accepts_a_firmware_the_inventory_has_not_seen(self):
        """The validator must never reject a true reading.

        It used to. The Oasis was recorded as terminal at 5.18.2 and anything
        else was rejected as a transcription error — then the Oasis updated to
        5.18.2.1.1, and the tooling built to keep the record honest would have
        thrown out the honest record. "No further updates" was a claim about
        Amazon's plans, not a property of the device, and the validator had no
        business enforcing it.

        Firmware must be *present*. What it says is the device's business.
        """
        for fw in ("5.18.2", "5.18.2.1.1", "5.20.0"):
            assert (
                validate_result(self._result(device="oasis-10", firmware=fw)) == []
            ), f"rejected a real firmware reading: {fw}"

    def test_rejects_unknown_outcome(self):
        assert validate_result(self._result(outcome="probably fine"))


@pytest.mark.tier1
@pytest.mark.unit
def test_results_for_selects_only_the_named_check(tmp_path):
    signoff = {
        "results": [
            {
                "check": CHECKS[0].id,
                "device": "oasis-10",
                "firmware": "5.18.2",
                "outcome": "pass",
            },
            {
                "check": CHECKS[1].id,
                "device": "oasis-10",
                "firmware": "5.18.2",
                "outcome": "pass",
            },
        ]
    }
    path = tmp_path / "signoff.json"
    path.write_text(json.dumps(signoff))
    got = results_for(json.loads(path.read_text()), CHECKS[0].id)
    assert len(got) == 1
    assert got[0]["check"] == CHECKS[0].id


@pytest.mark.tier1
@pytest.mark.unit
def test_check_by_id_round_trips():
    for check in CHECKS:
        assert check_by_id(check.id) is check
