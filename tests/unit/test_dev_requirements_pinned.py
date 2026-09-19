"""Guards on the development dependency pins.

`requirements.txt` pins runtime dependencies exactly, and says why: a security
-sensitive parser should never be whatever version happened to resolve on the
day someone installed. `requirements-dev.txt` had no pins at all — every entry
a lower bound — so the tools that decide whether a change is acceptable were
free to differ between two people reading the same instructions.

That is not hypothetical. CI installed `ruff==0.15.1` from a literal in
`test.yml` while this file said `ruff>=0.1.0`. A contributor installing as
CONTRIBUTING describes got whatever was current, and a newer ruff reformats
code the pinned one considers already formatted — so the gate could fail on a
tree their own tooling called clean, for reasons it would never show them.

These tests are tier 1: they read two text files and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.tier1, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
DEV_REQS = REPO / "requirements-dev.txt"
WORKFLOW = REPO / ".github" / "workflows" / "test.yml"

#: A requirement line, ignoring comments, blanks and `-r` includes.
_REQ_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?P<op>[<>=!~]+)?")


def _requirements() -> list[tuple[str, str, str]]:
    out = []
    for raw in DEV_REQS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_RE.match(line)
        if m:
            out.append((m.group("name"), m.group("op") or "", line))
    return out


def test_every_dev_dependency_is_pinned_exactly():
    """No lower bounds. A range is a decision deferred to whoever installs next."""
    loose = [line for _, op, line in _requirements() if op != "=="]
    assert not loose, (
        "these development dependencies are not pinned exactly:\n  "
        + "\n  ".join(loose)
        + "\nPin them with `==`. A range means two people following CONTRIBUTING "
        "can run different tools against the same tree and disagree about it."
    )


def test_ci_installs_the_ruff_pin_from_this_file():
    """The lint gate must not carry its own copy of the version.

    Two literals drift. This one did, and the failure mode was invisible from
    the contributor's side: their ruff said the tree was clean and CI's said it
    was not.
    """
    workflow = WORKFLOW.read_text()
    assert "grep -E '^ruff==' requirements-dev.txt" in workflow, (
        "the ruff-lint job no longer reads its pin from requirements-dev.txt. "
        "If it hardcodes a version again, that literal and this file will "
        "diverge and nothing will report it."
    )
    hardcoded = re.findall(r"pip install ruff==[\d.]+", workflow)
    assert not hardcoded, (
        f"the workflow hardcodes a ruff version again: {hardcoded}. "
        "Read it from requirements-dev.txt instead."
    )


def test_ruff_is_actually_pinned_for_that_grep_to_find():
    """The workflow fails loudly when this is missing; fail earlier and locally."""
    names = {name.lower(): op for name, op, _ in _requirements()}
    assert names.get("ruff") == "==", (
        "requirements-dev.txt has no exact `ruff==` line. The lint job greps "
        "for one and exits non-zero without it, so CI would fail with a message "
        "about a pin rather than about the code."
    )
