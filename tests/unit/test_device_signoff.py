"""Tests for the tier-4 sign-off machinery itself (#109).

Two things are covered here that #109's own argument demands.

**The checklist has to actually gate.** `tests/device/` is excluded from every
default run, so nothing in CI executes it — which is precisely the shape that
made tier 4 worth fixing. If those tests rotted into always-passing, the suite
would stay green and the gate would be decorative again. The subprocess tests
below run the real checklist against controlled sign-offs and assert it accepts
and rejects the right things.

**The record-writing end has to work.** `scripts/device_signoff.py` produces
the template a human fills in and the summary that reaches release notes; a
template missing a check is a check that silently never gets performed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.device.checklist import (
    CHECKS,
    DEVICES,
    SIGNOFF_ENV,
    Device,
    procedure_text,
    signoff_path,
    validate_result,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    """Import scripts/device_signoff.py, which is not a package."""
    path = REPO_ROOT / "scripts" / "device_signoff.py"
    spec = importlib.util.spec_from_file_location("device_signoff", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signoff_script = _load_script()


def _complete_signoff() -> dict:
    """A sign-off that should pass every check."""
    data = signoff_script.template()
    data["build"] = "abc1234"
    data["book"] = "pg64317 (corpus)"
    for result in data["results"]:
        result["outcome"] = "pass"
        if result["device"] == "paperwhite-11":
            result["firmware"] = "5.19.2"
    return data


def _run_checklist(tmp_path, signoff: dict | None, *, env_value=None):
    """Run the real tier-4 checklist and return (exit code, output).

    `-o addopts=` clears the ini defaults, which otherwise exclude `device`
    and force `--verbose`.
    """
    env = dict(os.environ)
    env.pop(SIGNOFF_ENV, None)
    if signoff is not None:
        path = tmp_path / "signoff.json"
        path.write_text(json.dumps(signoff))
        env[SIGNOFF_ENV] = str(path)
    if env_value is not None:
        env[SIGNOFF_ENV] = env_value
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-m", "device", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


@pytest.mark.tier1
@pytest.mark.unit
class TestChecklistActuallyGates:
    """The device tier never runs in CI, so these run it here.

    Without them the checklist is unverified code guarding the project's most
    important oracle — the same "installed but reports nothing" shape as #92,
    #98, #99 and #106.
    """

    def test_passes_on_a_complete_signoff(self, tmp_path):
        code, out = _run_checklist(tmp_path, _complete_signoff())
        assert code == 0, out[-3000:]

    def test_fails_with_nothing_signed_off(self, tmp_path):
        code, out = _run_checklist(tmp_path, None)
        assert code != 0
        # The failure has to carry the procedure, or it is a red mark that
        # tells the operator nothing about what to do on the device.
        assert CHECKS[0].procedure[:40] in out

    def test_fails_when_a_recorded_result_is_a_failure(self, tmp_path):
        data = _complete_signoff()
        data["results"][0]["outcome"] = "fail"
        data["results"][0]["note"] = "third entry landed at the start of the book"
        code, out = _run_checklist(tmp_path, data)
        assert code != 0
        assert "failed on hardware" in out

    def test_fails_when_only_one_device_was_exercised(self, tmp_path):
        data = _complete_signoff()
        data["results"] = [r for r in data["results"] if r["device"] != "oasis-10"]
        code, out = _run_checklist(tmp_path, data)
        assert code != 0
        assert "oasis-10" in out

    def test_fails_when_the_signoff_file_is_absent(self, tmp_path):
        code, out = _run_checklist(
            tmp_path, None, env_value=str(tmp_path / "nope.json")
        )
        assert code != 0
        assert "does not exist" in out


@pytest.mark.tier1
@pytest.mark.unit
class TestTemplate:
    def test_covers_every_check_on_every_device(self):
        data = signoff_script.template()
        pairs = {(r["check"], r["device"]) for r in data["results"]}
        expected = {(c.id, d) for c in CHECKS for d in DEVICES}
        assert pairs == expected, (
            "a template that omits a pair is a check that silently never gets performed"
        )

    def test_prefills_terminal_firmware_and_leaves_the_movable_one_blank(self):
        data = signoff_script.template()
        by_device = {r["device"]: r for r in data["results"]}
        assert by_device["oasis-10"]["firmware"] == DEVICES["oasis-10"].firmware
        assert by_device["paperwhite-11"]["firmware"] == "", (
            "the Paperwhite's firmware can move, so it must be read off the "
            "device each run rather than pre-filled"
        )

    def test_leaves_outcomes_empty(self):
        data = signoff_script.template()
        assert all(r["outcome"] == "" for r in data["results"])

    def test_a_blank_template_does_not_satisfy_the_gate(self, tmp_path):
        """The template must not be self-certifying."""
        code, _ = _run_checklist(tmp_path, signoff_script.template())
        assert code != 0


@pytest.mark.tier1
@pytest.mark.unit
class TestTrailerParsing:
    """`--trailers` answers "was this change device-checked?", so its parsing
    is separated from the `git log` call and tested directly."""

    def _log(self, *records):
        return "\x1e".join(records) + "\x1e"

    def test_finds_a_trailer_and_counts_commits_without_one(self):
        log = self._log(
            "sha1\x1fadd a thing\x1f"
            "Device-verified: Oasis 10th gen, firmware 5.18.2 — TOC",
            "sha2\x1fdocs tweak\x1f",
        )
        verified, plain = signoff_script.parse_trailer_log(log)
        assert plain == 1
        assert len(verified) == 1
        assert verified[0][1] == "add a thing"
        assert "Oasis" in verified[0][2][0]

    def test_collects_one_trailer_per_device(self):
        body = (
            "Device-verified: Oasis 10th gen (2019), firmware 5.18.2 — TOC\n"
            "Device-verified: Paperwhite 11th gen (2021), firmware 5.19.2 — TOC\n"
        )
        verified, _ = signoff_script.parse_trailer_log(
            self._log(f"sha1\x1fboth devices\x1f{body}")
        )
        assert len(verified[0][2]) == 2

    def test_prose_beginning_with_the_key_is_not_a_trailer(self):
        """The real case from this repo's own history, which is why the parser
        requires the colon.

        Commit 58f3f14 (#126) carries:

            Device-verified on a Paperwhite running 5.19.2: the same source
            built both ways

        That is a sentence, not a `Key: value` trailer — the colon falls after
        the firmware, not after the key. A first draft of this parser matched
        the bare word, counted that line, and led me to report that a trailer
        already existed in the history. It did not. Matching commentary as
        evidence is the exact failure this issue exists to prevent, so the
        distinguishing case is pinned here rather than an invented one.
        """
        body = (
            "Device-verified on a Paperwhite running 5.19.2: the same source "
            "built both ways\n"
        )
        verified, plain = signoff_script.parse_trailer_log(
            self._log(f"sha1\x1fadopt $44 baseline-style\x1f{body}")
        )
        assert verified == []
        assert plain == 1

    def test_a_body_discussing_the_convention_is_not_a_trailer(self):
        body = "We should add a Device-verified: trailer to this sort of change.\n"
        verified, plain = signoff_script.parse_trailer_log(
            self._log(f"sha1\x1ftalking about it\x1f{body}")
        )
        assert verified == []
        assert plain == 1

    def test_empty_log_is_not_an_error(self):
        assert signoff_script.parse_trailer_log("") == ([], 0)


@pytest.mark.tier1
@pytest.mark.unit
class TestSummaryRows:
    def test_one_row_per_recorded_result_naming_model_and_status(self):
        rows = signoff_script.summary_rows(_complete_signoff())
        assert len(rows) == len(CHECKS) * len(DEVICES)
        oasis = [r for r in rows if r["device"].startswith("Oasis")]
        assert oasis and all(r["status"] == "terminal" for r in oasis)
        pw = [r for r in rows if r["device"].startswith("Paperwhite")]
        assert pw and all(r["status"] == "current" for r in pw)

    def test_skips_results_naming_an_unknown_device(self):
        data = _complete_signoff()
        data["results"].append(
            {
                "check": CHECKS[0].id,
                "device": "kindle-fire",
                "firmware": "9",
                "outcome": "pass",
            }
        )
        rows = signoff_script.summary_rows(data)
        assert all("fire" not in r["device"].lower() for r in rows)

    def test_summarise_reports_a_bad_entry_as_a_nonzero_exit(self, tmp_path, capsys):
        data = _complete_signoff()
        data["results"][0]["firmware"] = ""
        path = tmp_path / "s.json"
        path.write_text(json.dumps(data))
        assert signoff_script.summarise(path) == 1

    def test_summarise_accepts_a_clean_signoff(self, tmp_path, capsys):
        path = tmp_path / "s.json"
        path.write_text(json.dumps(_complete_signoff()))
        assert signoff_script.summarise(path) == 0
        assert "| Device |" in capsys.readouterr().out


@pytest.mark.tier1
@pytest.mark.unit
class TestChecklistHelpers:
    def test_procedure_text_carries_everything_an_operator_needs(self):
        text = procedure_text(CHECKS[0])
        assert CHECKS[0].id in text
        assert CHECKS[0].title in text
        assert CHECKS[0].procedure in text
        for issue in CHECKS[0].issues:
            assert issue in text

    def test_label_shows_pinned_firmware_for_a_terminal_device(self):
        assert "5.18.2" in DEVICES["oasis-10"].label

    def test_label_flags_firmware_that_must_be_read_per_run(self):
        assert "per run" in DEVICES["paperwhite-11"].label

    def test_signoff_path_reads_the_environment(self, monkeypatch):
        monkeypatch.delenv(SIGNOFF_ENV, raising=False)
        assert signoff_path() is None
        monkeypatch.setenv(SIGNOFF_ENV, "/tmp/x.json")
        assert signoff_path() == "/tmp/x.json"

    def test_empty_result_reports_every_missing_field_at_once(self):
        """One problem at a time would mean one round trip per field."""
        problems = validate_result({})
        assert len(problems) >= 3

    def test_a_device_with_no_pinned_firmware_accepts_any_recorded_value(self):
        d = Device(id="x", model="M", generation="1st", firmware=None, terminal=False)
        assert d.firmware is None
        assert (
            validate_result(
                {
                    "check": CHECKS[0].id,
                    "device": "paperwhite-11",
                    "firmware": "5.20.0",
                    "outcome": "pass",
                }
            )
            == []
        )


@pytest.mark.tier1
@pytest.mark.unit
def test_contributing_documents_the_same_hardware_the_code_pins():
    """Doc drift here is what #109 is about: a record naming a device the code
    does not know, or a device the docs never mention, is unreadable later."""
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text()
    for device in DEVICES.values():
        assert device.model in text, f"{device.model} missing from CONTRIBUTING"
        assert device.generation.split()[0] in text
        if device.firmware:
            assert device.firmware in text, (
                f"{device.id}'s pinned firmware {device.firmware} is not in "
                "CONTRIBUTING, so a reader cannot date the evidence"
            )
