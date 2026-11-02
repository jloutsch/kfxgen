"""Paragraphs in a table cell of several blocks, spaced by their margins as
Kindle Previewer 4.0.3 writes them (#655 item 2, #675 item 1; my probe,
2026-11-02, a page with a DOCTYPE, `p { margin: 1em 0 }`).

Previewer writes each paragraph's margins in the cell, collapsed as outside
a table (#553): the first paragraph's margin-top, the last one's
margin-bottom, and a spacer's space below the paragraph before it. kfxgen
gave every paragraph its own line above, 1 lh, and none below, and dropped
a spacer at the cell's end, so the row came out shorter. Only a box in a
cell was spaced by its margins (#645). The cell's padding is the cell's,
written on it, not added to its paragraphs. A cell of one paragraph is
written flat, as before.

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_paragraph_margins import _spacing  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

P = "margin:1em 0"
BX = "border:1px solid black;padding:0.5em;margin:1em 0"


def _p(label, style=P):
    return f"<p style='{style}'>{label} words.</p>"


def _table(cell):
    return f"<table><tr><td>{cell}</td><td>Zz other</td></tr></table>"


BODY = (
    _p("A0")
    + _table(_p("B1") + "<p style='margin:1em 0'>&#160;</p>")
    + _p("A1")
    + _table(_p("C1") + f"<p style='{BX}'>Cb boxed.</p>" + _p("C2"))
    + _p("A2")
    + _table(_p("D1") + _p("D2"))
    + _p("A3")
    + "<table><tr><th>" + _p("E1") + _p("E2") + "</th><td>Zz other</td></tr></table>"
    + _p("A4")
    + _table("<h3>F1 cell head</h3>" + _p("F2"))
    + _p("A5")
    + _table(_p("G1", "margin:0") + _p("G2", "margin:0"))
    + _p("A6")
)  # fmt: skip

#: Previewer's ($47, $49) on each paragraph, in its own line heights.
PREVIEWER = {
    "B1": (0.833, 1.927),
    "C1": (0.833, None),
    "C2": (0.833, 0.833),
    "D1": (0.833, None),
    "D2": (0.833, 0.833),
    "E1": (0.833, None),
    "E2": (0.833, 0.833),
    "F1": (0.833, None),
    "F2": (0.975, 0.833),
    "G1": (None, None),
    "G2": (None, None),
}


def _near(a, b):
    return (a is None) == (b is None) and (a is None or abs(a - b) < 0.002)


def test_cell_paragraphs_are_spaced_by_their_margins(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "c655.epub", tmp / "c655.kfx"
    _build(epub, {"c.xhtml": BODY}, [("Cells", "c.xhtml")], "c655")
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    wrong = {
        k: got.get(k)
        for k, (above, below) in PREVIEWER.items()
        if not (k in got and _near(got[k][0], above) and _near(got[k][1], below))
    }
    assert not wrong, f"calibre {version}: {wrong}, Previewer {PREVIEWER}"
