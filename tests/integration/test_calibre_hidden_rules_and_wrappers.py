"""The space an <hr> that draws nothing leaves, and padded wrappers inside
each other, as Kindle Previewer 4.0.3 writes them (#643; my probes,
2026-10-31, pages with a DOCTYPE).

An <hr> with no border: Previewer writes its space on the paragraph after,
reading its margins (and a height the book sets) in CSS units, 1em = 16px =
12pt and 100% = 74em, and keeping 0.6 of them. A 5% margin is 1.85 lh,
where kfxgen wrote 1.333, a paragraph's 5%.

Padded wrappers: padding keeps margins on its two sides apart. Previewer
nests a container per wrapper; kfxgen writes the same total on the
paragraph. Nested padded wrappers lost the inner one's margin (3.583 lh
against 4.417 in QA's probe), and a margin wrapper around a padded one added
its margin inside the padding.

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests.integration.test_calibre_contents_part_headings import (  # noqa: E402
    _book_epub,
)
from tests.integration.test_calibre_paragraph_margins import _spacing  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _p(label, style="margin:0"):
    return f"<p style='{style}'>{label} words.</p>"


def _hr(style):
    return f"<hr style='border:none;{style}'/>"


BODY = (
    "<h1>Rules</h1>" + _p("A0")
    + _hr("margin:5% 0") + _p("A1")
    + _hr("margin:1em 0") + _p("A2")
    + _hr("margin:10% 0") + _p("A3")
    + _hr("margin:2em 0 0") + _p("A4")
    + _hr("margin:24px 0") + _p("A6")
    + _hr("margin:0;height:1em") + _p("A7")
    + "<hr style='border:none'/>" + _p("A8")
    + _hr("margin:1em 0") + _p("A9", "margin:1em 0 0")
    + _hr("margin:12pt 0") + _p("B2")
    + _hr("margin:1em 0;font-size:200%") + _p("B3")
    + _hr("margin:1em 0;height:1em") + _p("B4")
    + _hr("margin:0") + _p("B5")
    + "<div style='padding:1em 0;margin:1em 0'><div style='padding:0.5em 0;margin:2em 0'>"
    + _p("W1") + "</div></div>" + _p("W2")
    + "<div style='padding:1em 0;margin:1em 0'><div style='margin:1em 0'>"
    + _p("X1") + "</div></div>" + _p("X2")
    + "<div style='margin:1em 0'><div style='padding:0.5em 0;margin:2em 0'>"
    + _p("Y1") + "</div></div>" + _p("Y2")
    + _p("Z0")
)  # fmt: skip

#: Previewer's $47 in lh on the paragraph after each <hr>, None for none.
RULES = {
    "A1": 1.85,
    "A2": 0.5,
    "A3": 3.7,
    "A4": 1.0,
    "A6": 0.75,
    "A7": 0.5,
    "A8": 0.25,
    "A9": 0.8333,
    "B2": 0.5,
    "B3": 1.0,
    "B4": 1.5,
    "B5": None,
}

#: The space Previewer puts above and below the paragraph, in lh: its
#: wrappers' margins and padding added up ($47 + $52 of each container).
WRAPPED = {"W1": 3.75, "X1": 2.5, "Y1": 2.083}


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "r643.epub", tmp / "r643.kfx"
    _book_epub(
        epub,
        "R643",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Rules", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    return _spacing(kfx), version


def _near(a, b):
    return (a is None) == (b is None) and (a is None or abs(a - b) < 0.002)


@pytest.mark.parametrize("case", sorted(RULES))
def test_a_rule_that_draws_nothing_leaves_previewers_space(converted, case):
    got, version = converted
    assert _near(got[case][0], RULES[case]), (
        f"calibre {version}: {case} $47 = {got[case][0]}, Previewer {RULES[case]}"
    )


@pytest.mark.parametrize("case", sorted(WRAPPED))
def test_padded_wrappers_keep_the_margins_inside_them(converted, case):
    got, version = converted
    want = WRAPPED[case]
    assert _near(got[case][0], want) and _near(got[case][1], want), (
        f"calibre {version}: {case} = {got[case]}, Previewer {want} above and below"
    )


def test_a_heading_first_in_a_padded_wrapper_keeps_the_padding(calibre):  # noqa: F811
    """`<div style="padding:1em 0"><h2>…`: Previewer draws the padding
    above the heading (QA on #642). kfxgen spaces its headings its own way,
    a line above, and dropped the padding: the heading had 1 lh. Now the
    line and the padding: (1.2em + 1em / 1.5) / 1.2em = 1.556 lh at the
    heading's 1.5em."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "h643.epub", tmp / "h643.kfx"
    _book_epub(
        epub,
        "H643",
        "<h1>Padded</h1>" + _p("C0")
        + "<div style='padding:1em 0'><h2>C1 heading</h2>" + _p("C2") + "</div>"
        + _p("C3"),
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Padded", []), ("c1", "c1.xhtml", "Next", [])],
    )  # fmt: skip
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    assert _near(got["C1"][0], 1.556), f"calibre {version}: {got}"
