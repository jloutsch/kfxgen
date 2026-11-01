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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
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


#: Between `p {margin: 1em 0}` paragraphs: an <hr> with a height the book
#: sets keeps each margin on its own side, 0.6 of its margins and height
#: (QA on #649; my probe 2026-10-31). Previewer's total space between the
#: paragraphs, in lh; with no top margin of its own it leaves the paragraph
#: above its `$49` (0.833) and writes the rest after, which kfxgen writes
#: as one `$47`. Exactly 2px is calibre's UA height, which calibre's CSS
#: rewrite can't tell from a book's 2px: taken as none.
HEIGHT_BODY = "<h1>Heights</h1><p style='margin:1em 0'>H0 before.</p>" + "".join(
    f"<hr style='border:none;{css}'/><p style='margin:1em 0'>{label} after.</p>"
    for label, css in (
        ("H1", "margin:1em 0;height:1px"),
        ("H2", "margin:1em 0;height:3px"),
        ("H3", "margin:0;height:3px"),
        ("H4", "margin:2em 0;height:3px"),
        ("H5", "margin:1em 0"),
        ("H6", "margin:0;height:10px"),
    )
)
HEIGHTS = {"H1": 1.698, "H2": 1.76, "H3": 1.76, "H4": 2.094, "H5": 0.833, "H6": 1.979}


def test_a_rule_with_a_height_keeps_its_margins_apart(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "h649.epub", tmp / "h649.kfx"
    _book_epub(
        epub,
        "H649",
        HEIGHT_BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Heights", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    wrong = {k: got[k] for k, want in HEIGHTS.items() if not _near(got[k][0], want)}
    assert not wrong, f"calibre {version}: {wrong}, Previewer {HEIGHTS}"


def test_a_rule_with_a_height_before_a_heading_adds_its_space(calibre):  # noqa: F811
    """`<p>` (1em), `<hr style="border:none;margin:1em 0;height:3px">`,
    `<h2>`: Kindle Previewer 4.0.3 spaces the heading as a paragraph: the
    paragraph's 16px (collapsed with the rule's 9.6px), the rule's 1.8px, and
    the h2's 24px (collapsed with the rule's 9.6px), 41.8px (my probe; QA on
    #649). kfxgen spaces its headings its own way, a line,
    and dropped the rule's space (28.8px). Now the line and the rule's
    1.1125em: (1.2 + 1.1125 / 1.5) / 1.2 = 1.618 lh at the h2's 1.5em, 46.6px
    (#655)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "x655.epub", tmp / "x655.kfx"
    _book_epub(
        epub,
        "X655",
        "<h1>Rule</h1><p style='margin:1em 0'>X0 before.</p>"
        "<hr style='border:none;margin:1em 0;height:3px'/>"
        "<h2 style='margin:1em 0'>X1 heading</h2><p style='margin:1em 0'>X2 after.</p>",
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Rule", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    assert _near(got["X1"][0], 1.618), f"calibre {version}: {got}"


#: An invisible <hr> first in a file: Previewer keeps its margin below and
#: collapses it with the next block's margin-top, as mid-file (my probe,
#: 2026-11-01, kpr4 4.0.3, DOCTYPE pages). kfxgen took the block's own
#: margin-top alone, so a box (no margin of its own) or a margin-less
#: paragraph lost the rule's space: 0 for 0.5 lh, and 0.594 for 1.094 lh
#: past a rule with a height (#659). Each file's first block's $47, on the
#: box's container for a box, in lh.
BX = "border:1px solid black;padding:0.5em"
FIRST_FILES = {
    "f1.xhtml": f"{_hr('margin:1em 0')}<div style='{BX}'><p>F1 boxed.</p></div>"
    + _p("F2"),
    "f2.xhtml": f"{_hr('margin:1em 0;height:3px')}<div style='{BX}'>"
    "<p style='margin:1em 0'>G1 boxed.</p></div>" + _p("G2"),
    "f3.xhtml": _hr("margin:1em 0") + _p("H1") + _p("H2"),
    "f4.xhtml": _hr("margin:1em 0;height:3px") + _p("J1", "margin:1em 0") + _p("J2"),
    "f5.xhtml": f"<div style='{BX}'><p>K1 boxed.</p></div>" + _p("K2"),
    # A padded wrapper: the rule's margin collapses with the wrapper's, and
    # the padding keeps the paragraph's apart (QA on #662).
    "f6.xhtml": _hr("margin:1em 0")
    + "<div style='padding:1em 0;margin:0'>"
    + _p("L1", "margin:0.5em 0")
    + "</div>"
    + _p("L2"),
    "f7.xhtml": _hr("margin:1em 0")
    + "<div style='padding:1em 0;margin:1em 0'>"
    + _p("M1", "margin:0.5em 0")
    + "</div>"
    + _p("M2"),
    "f8.xhtml": _hr("margin:1em 0")
    + "<div style='padding-top:1em;margin:0'>"
    + _p("N1", "margin:1.5em 0")
    + "</div>"
    + _p("N2"),
}
FIRST = {
    "F1": 0.5,
    "G1": 1.094,
    "H1": 0.5,
    "J1": 1.427,
    "K1": None,
    "L1": 1.75,
    "M1": 2.083,
    "N1": 2.583,
}


def _first_spaces(kfx):
    """{label: the $47 above each top-level block, in lh}: a box's on its
    container. kfxgen's own TOC label, above each file's first block, is
    not the book's."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = {}
    for st in by_type(frags, "$259"):
        for top in val(st)["$146"]:
            e = getattr(top, "value", top)
            space = styles.get(str(e.get("$157")), {}).get("$47")
            while not e.get("$145"):
                e = getattr(e["$146"][0], "value", e["$146"][0])
            t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            got[t[:2]] = None if space is None else round(float(space["$307"]), 3)
    return got


def test_a_rule_first_in_a_file_keeps_its_space_below(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "f659.epub", tmp / "f659.kfx"
    _build(epub, FIRST_FILES, [(f"T{i}", n) for i, n in enumerate(FIRST_FILES)], "f659")
    _ebook_convert(calibre, epub, kfx, True)
    got = _first_spaces(kfx)
    wrong = {k: got.get(k) for k, want in FIRST.items() if not _near(got.get(k), want)}
    assert not wrong, f"calibre {version}: {wrong}, Previewer {FIRST}"
