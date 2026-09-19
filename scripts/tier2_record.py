#!/usr/bin/env python3
"""Run the tier-2 differential decode and record that it happened (#99).

Tier 2 parses kfxgen's output with jhowell's full `kfxlib`, taken from the KFX
Input plugin. That zip is not in this repository — its packaged form states no
terms, so it is left out — which means CI has nothing to run tier 2 against and
skips all 67 of its tests on every pull request. A skip is not a failure, so a
green run reports coverage that did not happen.

This does not fix that, and it is not supposed to. It makes the gap answerable:
after a real local run, it writes what ran, against which upstream pin, and
when. `tests/unit/test_tier2_record.py` then checks that record on every PR
without needing the zip, and fails when the pin moves without a tier-2 run
behind it. Refreshing the vendored copy is exactly when tier 2 has something to
say, and exactly when it is easiest to forget.

Usage:
    python3 scripts/tier2_record.py          # run tier 2, write the record
    python3 scripts/tier2_record.py --check  # print the record, change nothing
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "tests" / "fixtures" / "vendor"
PIN_FILE = VENDOR / "kfx_input_plugin.version.txt"
ZIP_FILE = VENDOR / "kfx_input_plugin.zip"
RECORD_FILE = VENDOR / "kfx_input_plugin.lastrun.json"
SUITE = "tests/integration/test_kfxlib_diff.py"

#: pytest's tail line, e.g. "66 passed in 0.69s" or "1 failed, 65 passed in 1s".
_COUNT_RE = re.compile(r"(\d+) (passed|failed|skipped|error)")


def read_pin() -> str:
    if not PIN_FILE.is_file():
        sys.exit(f"no pin file at {PIN_FILE.relative_to(REPO)}")
    return PIN_FILE.read_text().strip()


def run_tier2() -> tuple[dict[str, int], bool]:
    """Run the tier-2 suite and return its counts and whether it passed."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-m", "tier2", "-q", "--no-header"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    counts = {kind: int(n) for n, kind in _COUNT_RE.findall(tail)}
    print(proc.stdout[-2000:] if proc.stdout else proc.stderr[-2000:])
    return counts, proc.returncode == 0


def main(argv: list[str]) -> int:
    if "--check" in argv:
        if not RECORD_FILE.is_file():
            print("no record")
            return 1
        print(RECORD_FILE.read_text().rstrip())
        return 0

    pin = read_pin()
    if not ZIP_FILE.is_file():
        sys.exit(
            f"no upstream zip at {ZIP_FILE.relative_to(REPO)} — tier 2 cannot run.\n"
            "See CONTRIBUTING.md -> The upstream kfxlib copy."
        )

    counts, ok = run_tier2()
    passed = counts.get("passed", 0)
    if not ok or passed == 0:
        sys.exit(
            "tier 2 did not pass, so nothing was recorded. Fix the failure, or "
            "if upstream changed deliberately, refresh the pin first."
        )
    if counts.get("skipped"):
        sys.exit(
            f"{counts['skipped']} tier-2 test(s) skipped — a partial run is not a "
            "run. Record refused."
        )

    RECORD_FILE.write_text(
        json.dumps(
            {
                "upstream_pin": pin,
                "ran_on": date.today().isoformat(),
                "tests_passed": passed,
                "suite": SUITE,
            },
            indent=2,
        )
        + "\n"
    )
    rel = RECORD_FILE.relative_to(REPO)
    print(f"recorded: {passed} tier-2 tests passed against pin {pin} -> {rel}")
    print("Commit this file alongside the pin it describes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
