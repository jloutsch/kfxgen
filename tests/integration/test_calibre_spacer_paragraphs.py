"""The space a spacer paragraph (`<p>&#160;</p>`, `<p><br/></p>`) leaves,
as Kindle Previewer 4.0.3 writes it (#663; my probes, 2026-11-01, pages with
a DOCTYPE, `p { margin: 1em 0 }`).

Previewer spaces a spacer as an <hr> that draws nothing with a height (QA on
#649): 0.6 of its margins, in its own ems, and 0.6 of a 19px line, whatever
its font size. Its margin-top collapses with the block above, its
margin-bottom with the block below, the line between. kfxgen added one
blank line (1.2em) and dropped the spacer's margins: 1 lh where Previewer
writes 1.594 between margin-less paragraphs, 1.833 for 2.26 between 1em ones,
and less at a file's start, where nothing above takes the spacer's
margin-top (QA on #662).

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_hidden_rules_and_wrappers import (  # noqa: E402
    _first_spaces,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

SP = "<p style='margin:1em 0'>&#160;</p>"
BX = "border:1px solid black;padding:0.5em"


def _p(label, style="margin:1em 0"):
    return f"<p style='{style}'>{label} words.</p>"


def _z(label):
    return _p(label, "margin:0")


def _sp(style):
    return f"<p style='{style}'>&#160;</p>"


FILES = {
    # First in a file: nothing above takes the spacer's margin-top.
    "a.xhtml": SP + _z("A1") + _p("A2"),
    "b.xhtml": SP + f"<div style='{BX}'>{_p('B1')}</div>" + _p("B2"),
    "c.xhtml": SP + _p("C1") + _p("C2"),
    "d.xhtml": _sp("margin:0") + _p("D1") + _p("D2"),
    "e.xhtml": SP + SP + _p("E1") + _p("E2"),
    # Mid-file.
    "m.xhtml": _p("M0") + SP + _z("M1")
    + _p("N0") + SP + _p("N1")
    + _p("O0") + SP + f"<div style='{BX}'>{_p('O1')}</div>" + _p("O2"),
    # Between margin-less paragraphs: the spacer's own share, by its style.
    "v.xhtml": _z("P0") + SP + _z("P1")
    + _z("Q0") + _sp("margin:1em 0;font-size:200%") + _z("Q1")
    + _z("S0") + "<p style='margin:1em 0'><br/></p>" + _z("S1")
    + _z("T0") + _sp("margin:2em 0") + _z("T1")
    + _z("U0") + _sp("margin:1em 0;font-size:50%") + _z("U1")
    + _z("V0") + _sp("margin:0") + _z("V1"),
}  # fmt: skip

#: Previewer's $47 above each, in lh: a box's on its container.
PREVIEWER = {
    "A1": 1.594,
    "B1": 1.594,
    "C1": 1.927,
    "D1": 1.427,
    "E1": 3.021,
    "M1": 1.927,
    "N1": 2.26,
    "O1": 1.927,
    "P1": 1.594,
    "Q1": 2.594,
    "S1": 1.594,
    "T1": 2.594,
    "U1": 1.094,
    "V1": 0.594,
}


def _near(a, b):
    return a is not None and abs(a - b) < 0.002


def test_a_spacer_is_spaced_as_previewer_spaces_it(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "s663.epub", tmp / "s663.kfx"
    _build(epub, FILES, [(f"T{i}", n) for i, n in enumerate(FILES)], "s663")
    _ebook_convert(calibre, epub, kfx, True)
    got = _first_spaces(kfx)
    wrong = {
        k: got.get(k) for k, want in PREVIEWER.items() if not _near(got.get(k), want)
    }
    assert not wrong, f"calibre {version}: {wrong}, Previewer {PREVIEWER}"
