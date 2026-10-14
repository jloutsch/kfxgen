"""Drop caps through calibre's real Stylizer (#240).

calibre keeps a `::first-letter` rule on the paragraph's style for KFX, read
with `Style.pseudo_classes(filter_css)`, a method. A first version read it
as a dict: every element's CSS then failed to resolve, and every size and
indent in the book went with it, while the unit tests' fake style passed.
So this also checks a sized paragraph and a sized word still come through.

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
p.fl3::first-letter { float: left; font-size: 3em; line-height: 1; }
p.fl5::first-letter { float: left; font-size: 5em; line-height: 1; }
p.raised::first-letter { font-size: 3em; }
span.dropcap { float: left; font-size: 3.2em; line-height: 1; }
p.small { font-size: 0.8em; }
"""

REST = "nce upon a time there was a long opening paragraph to drop beside."
MAIN = " ".join(["Main text of the book, at the reader's own size."] * 12)

#: (file, case id, markup, [(text, ($125, $126) or None, $16 or None)]).
CASES = [
    ("c1", "MAIN", f"<p>{MAIN}</p><p>{MAIN}</p>", [(MAIN, None, None)] * 2),
    ("c1", "FIRST3", f'<p class="fl3">O{REST}</p>', [("O" + REST, (3, 1), None)]),
    ("c1", "FIRST5", f'<p class="fl5">O{REST}</p>', [("O" + REST, (4, 1), None)]),
    (
        "c1",
        "QUOTED",
        f'<p class="fl3">“O{REST}</p>',
        [("“O" + REST, (3, 2), None)],
    ),
    (
        "c1",
        "SPAN",
        f'<p><span class="dropcap">O</span>{REST}</p>',
        [("O" + REST, (3, 1), None)],
    ),
    (
        "c1",
        "SPAN2",
        '<p><span class="dropcap">Th</span>ere was once a long paragraph.</p>',
        [("There was once a long paragraph.", (3, 2), None)],
    ),
    ("c1", "RAISED", f'<p class="raised">O{REST}</p>', [("O" + REST, None, None)]),
    (
        "c1",
        "SMALL",
        '<p class="small">Small print, still sized.</p>',
        [("Small print, still sized.", None, "0.8")],
    ),
]


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    return _convert(calibre, "dropcaps", [c[:3] for c in CASES], CSS, native=True)


@pytest.mark.parametrize(
    "cid,expected", [(c[1], c[3]) for c in CASES], ids=[c[1] for c in CASES]
)
def test_drop_caps_through_the_real_stylizer(converted, cid, expected):
    by_case, calibre_version = converted
    got = []
    for text, style in by_case[cid]["rows"]:
        style = {str(k): v for k, v in style.items()}
        drop = (int(style["$125"]), int(style["$126"])) if "$125" in style else None
        size = style.get("$16")
        got.append((text, drop, None if size is None else str(size["$307"])))
    assert got == expected, f"calibre {calibre_version}"
