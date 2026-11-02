"""A heading's space above and below, from its CSS margins as Kindle
Previewer 4.0.3 writes them (#658; my probe, 2026-11-01, a page with a
DOCTYPE, `p { margin: 1em 0 }`).

kfxgen spaced its headings its own way: a line of the heading's own above
it, whatever the book set. In a 26-book library sample every one of 590
headings past a chapter's start had a different space above it than in
Previewer's book, a third by a line or more. A heading's margins now
collapse with its neighbours' as a paragraph's do (#553): the larger of the
two, on the heading when its own margin-top is set, else kept below the
block before. Calibre's own sheet gives h1-h4 their usual margins.

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
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

P = "margin:1em 0"
BX = "border:1px solid black;padding:0.5em"


def _p(label, style=P):
    return f"<p style='{style}'>{label} words.</p>"


BODY = (
    "<h1>Headings</h1>" + _p("A1")
    + "<h3>A2 plain h3</h3>" + _p("A3")
    + "<h2>A4 plain h2</h2>" + _p("A5")
    + "<h1>A6 plain h1</h1>" + _p("A7")
    + "<h4>A8 plain h4</h4>" + _p("A9")
    + "<h2>B0 h2</h2><h3>B1 h3 under h2</h3>" + _p("B2")
    + "<h2 style='margin:3em 0 0.5em'>B3 big h2</h2>" + _p("B4")
    + "<h3 style='margin:0'>B5 zero h3</h3>" + _p("B6")
    + _p("B7", "margin:0 0 3em")
    + "<h3>B8 h3 after a tail</h3>" + _p("B9", "margin:0")
    + f"<div style='{BX}'><h3>C5 boxed h3</h3>" + _p("C6") + "</div>" + _p("C7")
    + f"<div style='{BX}'>" + _p("C8") + "<h3>C9 last in a box</h3></div>"
    + _p("D0")
)  # fmt: skip

#: Previewer's ($47, $49) on each heading, in the heading's own line heights.
PREVIEWER = {
    "A2": (0.833, None),
    "A4": (0.692, None),
    "A6": (0.558, None),
    "A8": (1.108, None),
    "B1": (0.887, None),
    "B3": (2.5, None),
    "B5": (None, None),
    "B8": (2.137, 0.833),
    "C5": (0.833, None),
    "C9": (0.833, 0.833),
}


def _near(a, b):
    return (a is None) == (b is None) and (a is None or abs(a - b) < 0.002)


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "h658.epub", tmp / "h658.kfx"
    _book_epub(
        epub,
        "H658",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Headings", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    return _spacing(kfx), version


@pytest.mark.parametrize("case", sorted(PREVIEWER))
def test_a_headings_space_is_its_css_margin(converted, case):
    got, version = converted
    want = PREVIEWER[case]
    assert _near(got[case][0], want[0]) and _near(got[case][1], want[1]), (
        f"calibre {version}: {case} ($47, $49) = {got[case]}, Previewer {want}"
    )


def test_the_paragraph_after_a_heading_is_spaced_as_before(converted):
    """The heading's margin-bottom met the next paragraph's margin-top
    before, and still does: A3 and B2 keep Previewer's space."""
    got, version = converted
    assert _near(got["A3"][0], 0.975) and _near(got["B2"][0], 0.975), (
        f"calibre {version}: {got}"
    )


#: A chapter that opens with the book's heading, which prints as kfxgen's
#: chapter title: its margin-bottom stays its own `$49` before a margin-less
#: paragraph, as Previewer writes it (QA on #665: the space was lost). Each
#: (heading's $49, first paragraph's $47) in their own line heights.
OPENERS = {
    "o1.xhtml": "<h1>One</h1><p style='margin:0'>O1 text.</p>",
    "o2.xhtml": "<h2 style='margin:0.83em 0 2em'>Two</h2><p style='margin:0'>O2 text.</p>",
    "o3.xhtml": f"<h1>Three</h1>{_p('O3')}",
    # A label spelled over two blocks: they print as written (#552), the
    # h2 with its own margins, 0.692 of its lines below, as Previewer's.
    "o4.xhtml": "<p style='margin:0'>IV</p><h2>Four</h2><p style='margin:0'>O4 text.</p>",
    # In a box, drawn round kfxgen's title (#646, QA on #665): the h1's 2em
    # of its 32px, 64px, at kfxgen's 1.2rem title, 2.778 of its lines, the
    # 3.333 main lines Previewer writes.
    "o5.xhtml": f"<div style='{BX}'><h1 style='margin:0.67em 0 2em'>Five</h1>"
    "<p style='margin:0'>O5 text.</p></div>",
}
OPENED = {
    "On": (0.558, None),
    "Tw": (1.667, None),
    "Th": (None, 1.117),
    "Fo": (0.692, None),
    "Fi": (2.778, None),
}


def test_a_chapter_title_keeps_its_space_below(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "o665.epub", tmp / "o665.kfx"
    _build(
        epub,
        OPENERS,
        [
            ("One", "o1.xhtml"),
            ("Two", "o2.xhtml"),
            ("Three", "o3.xhtml"),
            ("IV Four", "o4.xhtml"),
            ("Five", "o5.xhtml"),
        ],
        "o665",
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    first = {"On": "O1", "Tw": "O2", "Th": "O3", "Fo": "O4", "Fi": "O5"}
    pairs = {k: (got[k][1], got[first[k]][0]) for k in OPENED}
    wrong = {
        k: v
        for k, v in pairs.items()
        if not (_near(v[0], OPENED[k][0]) and _near(v[1], OPENED[k][1]))
    }
    assert not wrong, f"calibre {version}: {wrong}, Previewer {OPENED}"


#: A heading, then a spacer or an <hr> that draws nothing, then a paragraph
#: (`p { margin: 1em 0 }`): Kindle Previewer 4.0.3 collapses the heading's
#: margin-bottom with the spacer's or rule's margin-top as its em number in
#: the 16px root's, as it reads a rule's own lengths (#643): an h3's 1em is
#: 1em, not its 1.17. Before a `margin:0` spacer nothing collapses, and the
#: heading keeps its own (#669; my probe, 2026-11-01). The paragraph's $47,
#: in lh.
SPACED = {
    "s.xhtml": "<h1>Spaced</h1>"
    + _p("A0") + "<h3>A1 h3</h3><p style='margin:1em 0'>&#160;</p>" + _p("A2")
    + _p("B0") + "<h2>B1 h2</h2><p style='margin:1em 0'>&#160;</p>" + _p("B2")
    + _p("C0") + "<h3>C1 h3</h3><hr style='border:none;margin:1em 0'/>" + _p("C2")
    + _p("D0") + "<h3 style='margin:1em 0 2em'>D1 h3</h3>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("D2")
    + _p("F0") + "<h3>F1 h3</h3><hr style='border:none;margin:1em 0;height:3px'/>"
    + _p("F2")
    # In rem it is read in the heading's size too; in px as before, 1.25
    # root ems (QA on #673).
    + _p("H0") + "<h3 style='margin:1em 0 1rem'>H1 h3</h3>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("H2")
    # A rule, then a spacer: only the first reads the heading's (QA on #673).
    + _p("J0") + "<h3>J1 h3</h3><hr style='border:none;margin:1em 0'/>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("J2")
    # Any block with its own size, and a wrapper's em margin it wins with.
    + _p("K0") + _p("K1", "margin:1em 0;font-size:0.8em")
    + "<p style='margin:1em 0'>&#160;</p>" + _p("K2")
    + _p("L0") + _p("L1", "margin:1em 0;font-size:1.5em")
    + "<p style='margin:1em 0'>&#160;</p>" + _p("L2")
    + _p("M0") + "<div style='margin-bottom:2em'>"
    + _p("M1", "margin:1em 0;font-size:1.5em") + "</div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("M2")
    # Only when the paragraph's own margin is in em too (QA on #673).
    + _p("N0") + "<div style='margin-bottom:2em'>"
    + _p("N1", "margin:16px 0;font-size:0.8em") + "</div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("N2")
    + _p("O0") + "<div style='margin-bottom:40px'>"
    + _p("O1", "margin:1em 0;font-size:1.5em") + "</div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("O2")
    # Only the margin that wins counts: an em wrapper's that loses to a px
    # one inside it is not read in the paragraph's size (#676).
    + _p("P0") + "<div style='margin-bottom:0.5em'><div style='margin-bottom:40px'>"
    + _p("P1", "margin:1em 0;font-size:1.5em") + "</div></div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("P2")
    # A box with its own size, as a paragraph (#676).
    + _p("Q0") + "<div style='border:1px solid black;padding:0.5em;"
    + "font-size:1.5em;margin-bottom:1em'>"
    + _p("Q1", "margin:1em 0;font-size:0.6667em") + "</div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("Q2")
    # A block with no margin-bottom of its own takes the wrapper's in its
    # size; a box with a px one doesn't; a box with none does (QA on #677).
    + _p("R0") + "<div style='margin-bottom:2em'>"
    + _p("R1", "margin:0;font-size:0.8em") + "</div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("R2")
    + _p("S0") + "<div style='margin-bottom:2em'><div style='border:1px solid black;"
    + "padding:0.5em;font-size:1.5em;margin-bottom:24px'>"
    + _p("S1", "margin:0;font-size:0.6667em") + "</div></div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("S2")
    + _p("T0") + "<div style='margin-bottom:2em'><div style='border:1px solid black;"
    + "padding:0.5em;font-size:1.5em;margin-bottom:0'>"
    + _p("T1", "margin:0;font-size:0.6667em") + "</div></div>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("T2")
    + _p("G0") + "<h3 style='margin:1em 0 20px'>G1 h3</h3>"
    + "<p style='margin:1em 0'>&#160;</p>" + _p("G2")
    # Nothing collapses: the same total as Previewer's 0.833 on the h3 and
    # 0.594 on E2, written on E2.
    + _p("E0") + "<h3>E1 h3</h3><p style='margin:0'>&#160;</p>"
    + _p("E2", "margin:0"),
}  # fmt: skip
SPACED_PREVIEWER = {
    "A2": 2.26,
    "B2": 2.119,
    "C2": 0.833,
    "D2": 3.094,
    "F2": 1.76,
    "G2": 2.052,
    "H2": 2.139,
    "J2": 2.26,
    "K2": 2.26,
    "L2": 2.26,
    "M2": 2.538,
    "N2": 3.094,
    "O2": 2.677,
    "P2": 2.677,
    "Q2": 2.26,
    "R2": 3.51,
    "S2": 3.094,
    "T2": 2.538,
    "E2": 1.569,
}


def test_a_headings_margin_before_a_spacer_is_its_em_number(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "s669.epub", tmp / "s669.kfx"
    _build(epub, SPACED, [("Spaced", "s.xhtml")], "s669")
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    wrong = {
        k: got[k][0]
        for k, want in SPACED_PREVIEWER.items()
        if not _near(got[k][0], want)
    }
    assert not wrong, f"calibre {version}: {wrong}, Previewer {SPACED_PREVIEWER}"


#: A heading that opens a file, printing as kfxgen's chapter title: Kindle
#: Previewer 4.0.3 writes its margin-top as `$47`, with a spacer's or a
#: padded wrapper's space before it, where kfxgen wrote its own `$46` 2 lh
#: (#653, #671; my probe, 2026-11-02). Each title's $47, in its own lh;
#: Dfour's 1.247 is the wrapper's 1em padding and the h2's 1.245em, in its
#: 1.8em line, as Previewer's container and heading add up.
OPENING = {
    "p1.xhtml": ("Aone", "<h1>Aone</h1>", 0.558),
    "p2.xhtml": ("Btwo", "<h2>Btwo</h2>", 0.692),
    "p3.xhtml": ("Cthree", "<p style='margin:1em 0'>&#160;</p><h1>Cthree</h1>", 1.105),
    "p4.xhtml": ("Dfour", "<div style='padding:1em 0'><h2>Dfour</h2>", 1.247),
    # None set: none written, and not kfxgen's $46 either (QA on #686).
    "p5.xhtml": ("Efive", "<h1 style='margin-top:0'>Efive</h1>", None),
}


def _title_keys(kfx, titles):
    """{title: the set of style keys on its entry}."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                if t in titles:
                    out[t] = {str(k) for k in styles.get(str(e.get("$157")), {})}
    return out


def test_a_chapter_title_keeps_its_space_above(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "p671.epub", tmp / "p671.kfx"
    files = {
        name: head
        + "<p style='margin:1em 0'>Body text.</p>"
        + ("</div>" if head.startswith("<div") else "")
        for name, (_, head, _) in OPENING.items()
    }
    toc = [(title, name) for name, (title, _, _) in OPENING.items()]
    _build(epub, files, toc, "p671")
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    wrong = {
        title: got.get(title[:2])
        for title, _, want in OPENING.values()
        if not _near(got.get(title[:2], (None,))[0], want)
    }
    assert not wrong, f"calibre {version}: {wrong}"
    # The book's space in place of kfxgen's own: no $46 beside it.
    keys = _title_keys(kfx, {title for title, _, _ in OPENING.values()})
    doubled = {t: k for t, k in keys.items() if "$46" in k}
    assert len(keys) == len(OPENING) and not doubled, f"calibre {version}: {keys}"
