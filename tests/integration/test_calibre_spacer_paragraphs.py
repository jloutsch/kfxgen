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
from tests.integration.test_calibre_paragraph_margins import _spacing  # noqa: E402
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
    + _z("V0") + _sp("margin:0") + _z("V1")
    # Its line is its own line height, 19px only for `normal` (QA on #667).
    + _z("W0") + _sp("margin:0;line-height:1.5") + _z("W1")
    + _z("X0") + _sp("margin:0;line-height:2") + _z("X1")
    + _z("Y0") + _sp("margin:0;line-height:3") + _z("Y1")
    + _z("Z0") + _sp("margin:0;line-height:40px") + _z("Z1"),
    # A line height the book writes, even 1.2 or `normal`, is the spacer's
    # own: `normal` 4/3 of its font size, rounded to a px; only an unset one
    # is 19px, whatever the font size around it (#668).
    "l.xhtml": _z("B0") + _sp("margin:0;line-height:normal") + _z("BB")
    + _z("C0") + _sp("margin:0;font-size:200%;line-height:normal") + _z("CC")
    + _z("D0") + _sp("margin:0;font-size:200%;line-height:1.2") + _z("DD")
    + _z("G0") + _sp("margin:0;font-size:150%;line-height:normal") + _z("GG")
    + _z("H0") + _sp("margin:0;font-size:50%;line-height:normal") + _z("HH")
    + "<div style='font-size:200%'>" + _z("F0") + _sp("margin:0") + _z("FF")
    + "</div>",
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
    "W1": 0.75,
    "X1": 1.0,
    "Y1": 1.5,
    "Z1": 1.25,
    "BB": 0.656,
    "CC": 1.344,
    "DD": 1.188,
    "GG": 1.0,
    "HH": 0.344,
    # In FF's own line heights, at its 2em.
    "FF": 0.297,
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


def test_a_margin_less_spacer_before_a_table_keeps_its_line(calibre):  # noqa: F811
    """`<p style="margin:0">`, a `margin:0` spacer, a table: no margin waits
    above the table, so only the spacer's line carries it, 0.594 lh, as
    Previewer's 0.625 on the table less its cells' 0.031 (QA on #667,
    #668: dropping it failed no test)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "u668.epub", tmp / "u668.kfx"
    _build(
        epub,
        {
            "u.xhtml": _z("J0")
            + _sp("margin:0")
            + "<table><tr><td>J1 cell</td></tr></table>"
            + _p("J2")
        },
        [("T", "u.xhtml")],
        "u668",
    )
    _ebook_convert(calibre, epub, kfx, True)
    below = _spacing(kfx)["J0"][1]
    assert _near(below, 0.594), f"calibre {version}: J0 $49 = {below}, want 0.594"


def test_a_spacer_before_a_table_keeps_its_space(calibre):  # noqa: F811
    """1em paragraph, spacer, table: Previewer puts the whole stack on the
    table, 1.958 lh with its cells' padding (QA on #667). kfxgen writes the
    space before a table below the paragraph above (#553): the paragraph's
    1em collapsed with the spacer's 0.6em, its 0.7125em line and its 0.6em
    margin-bottom, 2.3125em, 1.927 lh. The spacer's line was lost: 0.531."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "t663.epub", tmp / "t663.kfx"
    _build(
        epub,
        {
            "t.xhtml": _p("K0")
            + SP
            + "<table><tr><td>K1 cell</td></tr></table>"
            + _p("K2")
        },
        [("T", "t.xhtml")],
        "t663",
    )
    _ebook_convert(calibre, epub, kfx, True)
    below = _spacing(kfx)["K0"][1]
    assert _near(below, 1.927), f"calibre {version}: K0 $49 = {below}, want 1.927"


#: Spacers and an <hr> that draws nothing after the last block of a bordered
#: box, or of a file: Kindle Previewer 4.0.3 keeps their space below that
#: block, inside the box (#670; my probe, 2026-11-01). kfxgen dropped it, so
#: each block kept only its own 1em, 0.833 lh. The block's $49, in lh.
LAST_FILES = {
    "x.xhtml": _p("A0") + f"<div style='{BX}'>{_p('A1')}{SP}</div>" + _p("A2")
    + _p("B0") + f"<div style='{BX}'>{_p('B1')}{_sp('margin:0')}</div>" + _p("B2")
    + _p("C0") + f"<div style='{BX}'>{_p('C1')}{SP}{SP}</div>" + _p("C2")
    + _p("D0") + f"<div style='{BX}'>{_p('D1')}"
    + "<hr style='border:none;margin:1em 0'/></div>" + _p("D2")
    + _p("E0") + _p("E1") + SP,
    "y.xhtml": _p("F1"),
}  # fmt: skip
LAST = {"A1": 1.927, "B1": 1.427, "C1": 3.021, "D1": 0.833, "E1": 1.927}


def test_a_spacer_last_in_a_box_or_a_file_keeps_its_space(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "x670.epub", tmp / "x670.kfx"
    _build(epub, LAST_FILES, [("X", "x.xhtml"), ("Y", "y.xhtml")], "x670")
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    wrong = {k: got[k][1] for k, want in LAST.items() if not _near(got[k][1], want)}
    assert not wrong, f"calibre {version}: {wrong}, Previewer {LAST}"
