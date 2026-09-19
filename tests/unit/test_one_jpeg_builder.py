"""One place decides how a test JPEG is built.

`MINIMAL_JPEG` reported no dimensions for the life of this repo — its padding
sits inside the DQT without widening that segment's declared length, so a
parser following lengths never reaches the SOF0 it does have. #163 is the
consequence: the `with_cover` golden pinned the unknown-size branch, and a
change that rewrote all 90 corpus books passed its byte gate untouched.

Part of why that stayed invisible is that there was no single place where "how
we build a test JPEG" was decided. Three modules had grown their own builder,
each written from scratch rather than copied, so no two agreed on what a
correct one looked like and none of them could be wrong in a way anyone noticed.

These tests read files. They are tier 1 and cost nothing.

Why the second check is about the SOF0 and not the magic bytes: a dozen modules
construct `\\xff\\xd8\\xff\\xe0` legitimately — validating the magic-byte sniff,
building deliberately malformed covers, testing truncated data. None of those
is a JPEG that claims a size. The SOF0 is the segment that carries width and
height, so building one is exactly the act this file is here to centralise, and
matching on it catches the real thing without a long allowlist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.tier1, pytest.mark.unit]

TESTS = Path(__file__).resolve().parent.parent
HELPERS = TESTS / "_helpers.py"

#: A function that builds a JPEG. Names, since a builder is usually named for
#: what it makes.
_BUILDER_RE = re.compile(r"^\s*def\s+(_?jpeg\w*|make_jpeg\w*|_jpeg\w*)\s*\(", re.M)

#: A SOF0 marker being *written*, in either spelling. `\xff\xc0` in a bytes
#: literal, or `ffc0` inside a hex string.
_SOF0_RE = re.compile(r"\\xff\\xc0|ffc0[0-9a-f]{4}")

WHY = (
    "Use `tests._helpers.jpeg_of(width, height)`. One builder, so that a defect "
    "in how a test JPEG is made is a defect in one place — see #163, where a "
    "fixture image that silently reported no size cost a golden its whole "
    "purpose."
)


def _modules() -> list[Path]:
    return [
        p
        for p in TESTS.rglob("*.py")
        if p != HELPERS
        and "__pycache__" not in p.parts
        and p.name != Path(__file__).name
    ]


def test_only_helpers_defines_a_jpeg_builder():
    """A second builder is a second opinion about what a valid JPEG is."""
    offenders = []
    for path in _modules():
        for m in _BUILDER_RE.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(TESTS.parent)}: def {m.group(1)}(")
    assert not offenders, (
        "a JPEG builder outside tests/_helpers.py:\n  "
        + "\n  ".join(offenders)
        + f"\n{WHY}"
    )


def test_only_helpers_writes_a_sof0():
    """Building a SOF0 is building a JPEG that claims a size.

    The three copies this replaced were each written from scratch rather than
    copied, so a check on the *name* would have missed every one of them. What
    they had in common was hand-assembling this segment.
    """
    offenders = []
    for path in _modules():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SOF0_RE.search(line):
                offenders.append(f"{path.relative_to(TESTS.parent)}:{i}")
    assert not offenders, (
        "a SOF0 segment is being assembled outside tests/_helpers.py:\n  "
        + "\n  ".join(offenders)
        + f"\n{WHY}"
    )


def test_the_one_builder_is_where_this_says_it_is():
    """Guard the guard: both checks above pass vacuously if `jpeg_of` moves."""
    source = HELPERS.read_text(encoding="utf-8")
    assert _BUILDER_RE.search(source), (
        "tests/_helpers.py no longer defines a JPEG builder, so the two checks "
        "in this module now assert that nobody builds JPEGs anywhere."
    )
