"""Paragraph spacing as Kindle Previewer 4.0.3 writes it (#553, #538; my
probe 2026-10-29, a page with a DOCTYPE): each gap is the larger of the
upper paragraph's margin-bottom and the lower one's margin-top, as margins
collapse in a browser. The lower paragraph takes it as its space above
($47) when its own margin-top is at least as large; otherwise the upper one
keeps it as its space below ($49). Each is in the paragraph's own line
heights. A file's last paragraph keeps its margin-bottom. Indented
paragraphs are spaced the same way. kfxgen drew 1 line above an unindented
paragraph and none above an indented one (#326).

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_part_headings import (  # noqa: E402
    _book_epub,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

W = " words words words words words words words words words words words words."


def _p(label, style=""):
    attr = f" style='{style}'" if style else ""
    return f"<p{attr}>{label}{W}</p>"


BODY = (
    "<h1>Spacing</h1>"
    + _p("A1")
    + _p("A2")
    + _p("A3")
    + _p("B1", "text-indent:1.5em")
    + _p("B2", "text-indent:1.5em")
    + _p("C1", "text-indent:1.5em;margin:0")
    + _p("C2", "text-indent:1.5em;margin:0")
    + _p("D1", "text-indent:1.5em;margin-top:0.5em;margin-bottom:0")
    + _p("D2", "text-indent:1.5em;margin-top:0.5em;margin-bottom:0")
    + _p("E1", "margin:2em 0")
    + _p("E2", "margin:2em 0")
    + _p("F1", "margin:0 0 3em 0")
    + _p("F2", "margin:0")
    + _p("G1", "font-size:2em")
    + _p("G2", "font-size:2em")
    + _p("H1", "margin:0;line-height:2")
    + _p("H2", "margin:0;line-height:2;margin-top:1em")
    + "<h2>Second heading</h2>"
    + _p("I1")
    + _p("I2", "margin-top:0")
    + "<blockquote><p>J1 quoted"
    + W
    + "</p></blockquote>"
    + _p("J2")
    + "<table><tr><td>T1 cell</td><td>cell</td></tr></table>"
    + _p("K1")
    + "<div style='line-height:24px'>"
    + _p("L1", "font-size:2em")
    + _p("L2")
    + "</div>"
)

#: Previewer's ($47, $49) in lh for each paragraph, None where it writes none.
PREVIEWER = {
    "A1": (1.117, None),
    "A2": (0.8333, None),
    "A3": (0.8333, None),
    "B1": (0.8333, None),
    "B2": (0.8333, 0.8333),
    "C1": (None, None),
    "C2": (None, None),
    "D1": (0.4167, None),
    "D2": (0.4167, None),
    "E1": (1.667, None),
    "E2": (1.667, 1.667),
    "F1": (None, 2.5),
    "F2": (None, None),
    "G1": (0.8333, None),
    "G2": (0.8333, 0.8333),
    "H1": (None, None),
    "H2": (0.5, None),
    "I1": (1.038, 0.8333),
    "I2": (None, None),
    "J1": (0.8333, None),
    "J2": (0.8333, 0.8333),
    "K1": (0.8333, None),
    "L1": (1.333, None),
    "L2": (1.333, 0.6667),
}


def _plain(x):
    return getattr(x, "value", x)


def _spacing(kfx):
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def lh(y, key):
        v = y.get(key)
        return round(float(_plain(v)["$307"]), 3) if v is not None else None

    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                y = styles.get(str(e.get("$157")), {})
                got[t[:2]] = (lh(y, "$47"), lh(y, "$49"))
    return got


def test_paragraphs_are_spaced_as_previewer_spaces_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "s553.epub", tmp / "s553.kfx"
    _book_epub(
        epub,
        "S553",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Spacing", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)

    def near(a, b):
        return (a is None) == (b is None) and (a is None or abs(a - b) < 0.002)

    wrong = {
        k: (got.get(k), want)
        for k, want in PREVIEWER.items()
        if not (k in got and near(got[k][0], want[0]) and near(got[k][1], want[1]))
    }
    assert not wrong, f"calibre {version}: (kfxgen, Previewer) {wrong}"


def test_a_cut_paragraphs_margins_go_on_its_first_and_last_parts(calibre):  # noqa: F811
    """A paragraph cut at an empty line (#602): its margin-top on the first
    part, its margin-bottom on the last, as Previewer writes them (my probe,
    2026-10-29). The parts after the cut keep kfxgen's line above (#602);
    Previewer writes 0.594 there."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "cut553.epub", tmp / "cut553.kfx"
    _book_epub(
        epub,
        "C553",
        "<h1>Cut</h1><p>P0 plain words before.</p>"
        "<p style='margin:0 0 2em 0'>M1 first part<br/><br/>M1 second part</p>"
        "<p style='margin:0'>M2 after with no margin.</p>"
        "<p style='margin:1em 0 0 0'>M3 top only<br/><br/>M3 later part</p>"
        "<p style='margin:0'>M4 end.</p>",
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Cut", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                y = styles.get(str(e.get("$157")), {})
                got[t.strip()] = tuple(
                    round(float(_plain(y[k])["$307"]), 3) if k in y else None
                    for k in ("$47", "$49")
                )
    assert got["M1 first part"] == (None, None), f"calibre {version}: {got}"
    assert got["M1 second part"][1] == 1.667, f"calibre {version}: {got}"
    assert got["M3 top only"] == (0.833, None), f"calibre {version}: {got}"
    assert got["M3 later part"][1] is None, f"calibre {version}: {got}"


def test_before_a_box_the_paragraph_keeps_no_space_below(calibre):  # noqa: F811
    """A bordered box spaces itself (#238): the paragraph before it writes
    no $49, or the gap was the paragraph's line and the box's (2 lines, where
    Previewer has 0.833; QA on #639). The same before a heading kfxgen
    spaces its own way."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "box553.epub", tmp / "box553.kfx"
    _book_epub(
        epub,
        "B553",
        "<h1>Box</h1><p>Q0 plain words before.</p>"
        "<p style='margin:0 0 1em 0'>Q1 before the box.</p>"
        "<div style='border:1px solid black;padding:0.5em'><p>Q2 in the box.</p></div>"
        "<p style='margin:0 0 2em 0'>Q3 before a heading.</p>"
        "<h2 style='margin-top:0.5em'>Q4 heading</h2><p>Q5 after.</p>",
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Box", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    assert got["Q1"][1] is None, f"calibre {version}: {got}"
    assert got["Q3"][1] is None, f"calibre {version}: {got}"


def test_a_percentage_margin_is_read_on_previewers_scale(calibre):  # noqa: F811
    """`margin-top: 5%`: Kindle Previewer 4.0.3 writes 1.333 lh, on its scale
    where 100% is 384pt (32em), so 5% is 1.6em (QA's probe on #639, #640).
    kfxgen read a percentage margin as none."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "pct640.epub", tmp / "pct640.kfx"
    _book_epub(
        epub,
        "P640",
        "<h1>Percent</h1><p style='margin:0'>R0 plain.</p>"
        "<p style='margin:5% 0 0 0'>R1 five percent above.</p>"
        "<p style='margin:0'>R2 end.</p>",
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Percent", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    assert got["R1"][0] == 1.333, f"calibre {version}: {got}"


def test_paragraphs_in_a_box_are_spaced_by_their_margins(calibre):  # noqa: F811
    """Inside a bordered box (#238) paragraphs follow their margins too, the
    first its own margin-top, as Kindle Previewer 4.0.3 writes them (QA's
    probe on #639: 0.667 for 0.8em; #640). kfxgen kept its 1-line default."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "inbox640.epub", tmp / "inbox640.kfx"
    _book_epub(
        epub,
        "I640",
        "<h1>In a box</h1><p>S0 plain words before.</p>"
        "<div style='border:1px solid black;padding:0.5em'>"
        "<p>S1 first in the box.</p><p>S2 second in the box.</p>"
        "<p style='margin:0'>S3 third, no margin.</p></div><p>S4 after.</p>",
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "In a box", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    assert got["S1"][0] == 0.833, f"calibre {version}: {got}"
    assert got["S2"][0] == 0.833, f"calibre {version}: {got}"
    assert got["S3"][0] is None, f"calibre {version}: {got}"


def test_a_wrappers_padding_spaces_its_first_and_last_paragraphs(calibre):  # noqa: F811
    """An unbordered <div> with padding: Kindle Previewer 4.0.3 writes it as
    a container with the padding ($52/$54, 0.833 lh for 1em), so the space
    shows; kfxgen draws no container there and lost it (my probe 2026-10-29,
    #640). kfxgen puts the same space on the first and last paragraph:
    padding doesn't collapse, so it adds to the margins."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "pad640.epub", tmp / "pad640.kfx"
    _book_epub(
        epub,
        "D640",
        "<h1>Padding</h1><p style='margin:0'>W0 plain.</p>"
        "<div style='padding-top:1em;padding-bottom:1em'>"
        "<p style='margin:0'>W4 first.</p><p style='margin:0'>W5 last.</p></div>"
        "<p style='margin:0'>W6 after.</p>",
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Padding", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    assert got["W4"] == (0.833, None), f"calibre {version}: {got}"
    assert got["W5"] == (None, 0.833), f"calibre {version}: {got}"
    assert got["W6"] == (None, None), f"calibre {version}: {got}"


def test_negative_margins_collapse_as_in_a_browser(calibre):  # noqa: F811
    """A negative margin collapses as in a browser: the largest positive plus
    the most negative, which may leave a negative gap, as Kindle Previewer
    4.0.3 writes it (my probe 2026-10-29, #640). kfxgen read a negative
    margin as none."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "neg640.epub", tmp / "neg640.kfx"

    def p(label, style):
        return f"<p style='{style}'>{label} words words words words words.</p>"

    _book_epub(
        epub,
        "NEG640",
        "<h1>Negative</h1>"
        + p("N0", "margin:0")
        + p("N1", "margin:0 0 1em 0")
        + p("N2", "margin:-0.5em 0 0 0")
        + p("N3", "margin:0 0 1em 0")
        + p("N4", "margin:-1em 0 0 0")
        + p("N5", "margin:0 0 1em 0")
        + p("N6", "margin:-2em 0 0 0")
        + p("N7", "margin:0")
        + p("N8", "margin:-1em 0 0 0")
        + p("N9", "margin:0 0 2em 0")
        + p("NA", "margin:-0.5em 0 0 0")
        + p("NB", "margin:0 0 -1em 0")
        + p("NC", "margin:1em 0 0 0")
        + p("ND", "margin:0 0 -0.5em 0")
        + p("NE", "margin:0"),
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Negative", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    # Previewer's; it writes a gap of 0 as $47 0, kfxgen writes none: the same.
    want = {
        "N2": (0.417, None),
        "N4": (None, None),
        "N6": (-0.833, None),
        "N8": (-0.833, None),
        "NA": (1.25, None),
        "NC": (None, None),
        "ND": (None, -0.417),
    }
    wrong = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
    assert not wrong, f"calibre {version}: (kfxgen, Previewer) {wrong}"
