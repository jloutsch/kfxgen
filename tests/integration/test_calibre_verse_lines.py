"""Lines set as paragraphs with no gap, through calibre's real Stylizer (#331).

Verse one `<p>` per line, an index, a cast list: short unindented paragraphs
the book sets with `margin: 0`. kfxgen put a line of space ($47 1lh) above
each, so they came out double-spaced. A run of such lines is now tight: its
first line keeps the space, so a stanza break shows, and the lines after it
have no $47. Prose set the same way, unindented with no gap, keeps the line
of space, which is all that separates its paragraphs.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """
p.l, p.ix, p.np { margin: 0; }
div.st { margin: 1em 0; }
"""

_LONG = (
    "The rain kept on through the afternoon, and nobody went out to the "
    "boats until the wind dropped and the light came back over the hill. "
)

#: (file, case id, markup, (text, $47 in line heights or None) for each
#: paragraph between the case's heading and its END line).
CASES = [
    (
        "c1",
        "STANZAS",
        '<div class="st"><p class="l">Line a1</p><p class="l">Line a2</p></div>'
        '<div class="st"><p class="l">Line b1</p><p class="l">Line b2</p></div>',
        [("Line a1", "1"), ("Line a2", None), ("Line b1", "1"), ("Line b2", None)],
    ),
    (
        "c1",
        "INDEX",
        "".join(f'<p class="ix">Entry {n}, {n * 7}</p>' for n in range(1, 5)),
        [("Entry 1, 7", "1")] + [(f"Entry {n}, {n * 7}", None) for n in range(2, 5)],
    ),
    (
        "c1",
        "PROSE",
        f'<p class="np">“Yes.”</p><p class="np">{_LONG}</p><p class="np">“No.”</p>'
        f'<p class="np">{_LONG}</p>',
        [("“Yes.”", "1"), (_LONG.strip(), "1"), ("“No.”", "1"), (_LONG.strip(), "1")],
    ),
]


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    return _convert(calibre, "verse", [c[:3] for c in CASES], CSS, native=True)


@pytest.mark.parametrize(
    "cid,expected", [(c[1], c[3]) for c in CASES], ids=[c[1] for c in CASES]
)
def test_lines_with_no_gap_through_the_real_stylizer(converted, cid, expected):
    by_case, calibre_version = converted
    got = []
    for text, style in by_case[cid]["rows"]:
        above = {str(k): v for k, v in style.items()}.get("$47")
        got.append((text, None if above is None else str(above["$307"])))
    assert got == expected, f"calibre {calibre_version}, case {cid}"
