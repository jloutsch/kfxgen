#!/usr/bin/env python3
"""Tier-4 sign-off: generate a template, summarise a result, or audit history.

Tier 4 is the only oracle that proves a KFX renders, and #109 is about it
leaving no per-change record. This script is the writing end of that record;
`pytest -m device` is the reading end.

    python scripts/device_signoff.py --template > signoff.json
    KFXGEN_DEVICE_SIGNOFF=signoff.json pytest -m device
    python scripts/device_signoff.py --summary signoff.json
    python scripts/device_signoff.py --trailers v5.7.2..HEAD

`--summary` prints the block to paste into release notes or a PR, in the form
CONTRIBUTING asks for: model, generation, firmware, and what was checked.

`--trailers` answers the question #109 says is currently unanswerable — "was
this change device-checked?" — by listing `Device-verified:` trailers over a
commit range.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.device.checklist import (  # noqa: E402
    CHECKS,
    DEVICES,
    results_for,
    validate_result,
)

TRAILER = "Device-verified"


def _current_build() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def template() -> dict:
    """A sign-off with every check and device present and nothing invented.

    Pre-filled with the firmware of the terminal device, because that cannot
    move and re-typing a constant is how transcription errors get in. The
    Paperwhite's is left blank on purpose — it can change, so it has to be read
    off the device each run.
    """
    results = []
    for check in CHECKS:
        for device_id, device in sorted(DEVICES.items()):
            results.append(
                {
                    "check": check.id,
                    "device": device_id,
                    "firmware": device.firmware or "",
                    "outcome": "",
                    "note": "",
                }
            )
    return {
        "build": _current_build(),
        "book": "",
        "results": results,
    }


def summarise(path: Path) -> int:
    signoff = json.loads(path.read_text())
    problems = []
    for result in signoff.get("results") or []:
        problems += [f"{result}: {p}" for p in validate_result(result)]

    print(f"Device sign-off — build {signoff.get('build') or '<unrecorded>'}")
    if signoff.get("book"):
        print(f"Book: {signoff['book']}")
    print()
    print("| Device | Firmware | Status | Check | Result |")
    print("|---|---|---|---|---|")
    for check in CHECKS:
        for result in results_for(signoff, check.id):
            device = DEVICES.get(result.get("device"))
            if device is None:
                continue
            status = "terminal" if device.terminal else "current"
            outcome = result.get("outcome") or "—"
            note = result.get("note") or ""
            cell = f"{outcome}{' — ' + note if note else ''}"
            print(
                f"| {device.model} {device.generation} | "
                f"{result.get('firmware', '')} | {status} | {check.title} | {cell} |"
            )

    if problems:
        print("\nProblems:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    return 0


def trailers(rev_range: str) -> int:
    """List `Device-verified:` trailers over a commit range.

    Prints commits that carry one and, separately, the count that do not — the
    absence is the interesting half, and a report that only showed hits would
    read as coverage.
    """
    out = subprocess.run(
        ["git", "log", "--format=%H%x1f%s%x1f%b%x1e", rev_range],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    verified, plain = [], 0
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
        lines = [ln.strip() for ln in body.splitlines() if ln.startswith(TRAILER)]
        if lines:
            verified.append((sha[:9], subject, lines))
        else:
            plain += 1

    for sha, subject, lines in verified:
        print(f"{sha}  {subject}")
        for line in lines:
            print(f"           {line}")
    print(f"\n{len(verified)} commit(s) carry a {TRAILER} trailer; {plain} do not.")
    if not verified:
        print(
            f"No {TRAILER} trailers in {rev_range}. That is not a failure — most "
            "changes do not need hardware — but it does mean nothing in this "
            "range is answerable as device-checked.",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--template",
        action="store_true",
        help="print a blank sign-off covering every check and device",
    )
    g.add_argument(
        "--summary", metavar="FILE", help="print a sign-off as a release-notes table"
    )
    g.add_argument(
        "--trailers",
        metavar="RANGE",
        help="list Device-verified trailers over a commit range",
    )
    g.add_argument(
        "--checklist",
        action="store_true",
        help="print the procedures without running pytest",
    )
    args = ap.parse_args()

    if args.template:
        print(json.dumps(template(), indent=2))
        return 0
    if args.summary:
        return summarise(Path(args.summary))
    if args.trailers:
        return trailers(args.trailers)
    if args.checklist:
        for device in DEVICES.values():
            print(f"  device: {device.label}{' (terminal)' if device.terminal else ''}")
        print()
        from tests.device.checklist import procedure_text

        for check in CHECKS:
            print(procedure_text(check))
            print()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
