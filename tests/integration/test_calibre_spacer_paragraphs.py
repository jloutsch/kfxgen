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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
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


def test_a_spacer_after_a_table_last_in_a_box_keeps_its_space(calibre):  # noqa: F811
    """A box ending in a table, then a spacer: Kindle Previewer 4.0.3 writes
    the spacer's space, 0.6em + its 0.7125em line + 0.6em, 1.594 lh, below
    the table inside the box (#675). kfxgen dropped it. It now goes in the
    box's padding below, the same place on the page: 0.417 + 1.594."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "t675.epub", tmp / "t675.kfx"
    _build(
        epub,
        {
            "t.xhtml": _p("B6")
            + f"<div style='{BX}'>{_p('B7')}<table><tr><td>B8 cell</td></tr></table>"
            + f"{SP}</div>"
            + _p("B9")
        },
        [("T", "t.xhtml")],
        "t675",
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    below = None
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            e = getattr(e, "value", e)
            if str(e.get("$159")) == "$270":
                pad = styles.get(str(e.get("$157")), {}).get("$54")
                below = None if pad is None else round(float(pad["$307"]), 3)
    assert _near(below, 2.01), f"calibre {version}: box $54 = {below}, want 2.01"


def _box_paddings_below(kfx):
    """Each top-level box's $54, in order."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            e = getattr(e, "value", e)
            if str(e.get("$159")) == "$270":
                pad = styles.get(str(e.get("$157")), {}).get("$54")
                out.append(None if pad is None else round(float(pad["$307"]), 3))
    return out


def test_a_trailing_spacer_in_a_box_is_spaced_by_its_edge(calibre):  # noqa: F811
    """A box ending table + spacer (#680):
    - filled, no border or padding below: nothing holds the space in, so it
      collapses with the next paragraph's margin, 1.594 lh above it, as
      Kindle Previewer 4.0.3 writes it (QA on #678: #678 added the two);
    - bordered, at 1.5em: the space goes in its padding in its own ems.
      The spacer is at the box's 1.5em too: 0.9 + 0.7125 + 0.9 root ems,
      1.675 of the box's, and its 0.5em padding, $54 2.719; with the
      table's cell padding and the next paragraph's 0.833, Previewer's 3.583
      (QA on #678). Dropping the division gave 3.766;
    - bordered, a paragraph + spacer last: the paragraph keeps the space
      (#670), and the box's padding stays 0.417 (marking the paragraph too
      doubled it, QA on #678)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "b680.epub", tmp / "b680.kfx"
    table = "<table><tr><td>Tc cell</td></tr></table>"
    _build(
        epub,
        {
            "b.xhtml": _p("G0")
            + f"<div style='background-color:#cccccc'>{_p('G1')}{table}{SP}</div>"
            + _p("G2")
            + f"<div style='{BX};font-size:1.5em'>"
            + _p("H1", "margin:1em 0;font-size:0.6667em")
            + f"{table}{SP}</div>"
            + _p("H2")
            + f"<div style='{BX}'>{_p('J1')}{SP}</div>"
            + _p("J2")
        },
        [("B", "b.xhtml")],
        "b680",
    )
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    pads = _box_paddings_below(kfx)
    assert _near(got["G2"][0], 1.594), f"calibre {version}: G2 $47 = {got['G2']}"
    assert len(pads) == 3, f"calibre {version}: boxes {pads}"
    assert pads[0] is None, f"calibre {version}: filled box $54 = {pads[0]}"
    assert _near(pads[1], 2.719), f"calibre {version}: 1.5em box $54 = {pads[1]}"
    assert _near(pads[2], 0.417), f"calibre {version}: J box $54 = {pads[2]}"


def _box_spaces_below(kfx):
    """Each box's $49, outer before inner, in reading order."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []

    def walk(e):
        e = getattr(e, "value", e)
        if not hasattr(e, "get"):
            return
        if str(e.get("$159")) == "$270":
            below = styles.get(str(e.get("$157")), {}).get("$49")
            out.append(None if below is None else round(float(below["$307"]), 3))
        for k in e.get("$146") or ():
            walk(k)

    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            walk(e)
    return out


def test_an_open_boxs_trailing_space_leaves_it(calibre):  # noqa: F811
    """Boxes with nothing at their bottom edge (a fill, no border or padding)
    let the space after their last block out, as Kindle Previewer 4.0.3
    writes it (QA on #682; my probe):
    - a bordered box ending an open box (p, table, spacer): the open box
      keeps 1.594 below it, inside the bordered one (#682 lost it: 0);
    - an open box ending an open box (p, table, spacer): 1.594 above the
      next paragraph;
    - an open box ending p + spacer, or an open box + spacer: the p's 1em
      meets the spacer's 0.6em first, 1.927 above the next paragraph, none
      below the p (kfxgen kept it inside and added the next margin: 2.427);
    - an open box ending a picture's paragraph + spacer: the spacer is
      dropped, 0.833."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "o682.epub", tmp / "o682.kfx"
    table = "<table><tr><td>Tc cell</td></tr></table>"
    fill = "background-color:#cccccc"
    body = (
        _p("A0")
        + f"<div style='{BX}'><div style='{fill}'>{_p('A1')}{table}{SP}</div></div>"
        + _p("A2")
        + f"<div style='{fill}'><div style='{fill}'>{_p('B1')}{table}{SP}</div></div>"
        + _p("B2")
        + f"<div style='{fill}'>{_p('C1')}{SP}</div>"
        + _p("C2")
        + f"<div style='{fill}'><div style='{fill}'>{_p('D1')}</div>{SP}</div>"
        + _p("D2")
        # A picture's paragraph, then a spacer: Previewer drops the spacer
        # (QA on #682: 1.927 when it left the box).
        + f"<div style='{fill}'>{_p('E1')}<p><img src='x.png'/></p>{SP}</div>"
        + _p("E2")
    )
    _build(epub, {"o.xhtml": body}, [("O", "o.xhtml")], "o682")
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    wanted = {"A2": 0.833, "B2": 1.594, "C2": 1.927, "D2": 1.927, "E2": 0.833}
    wrong = {k: got[k][0] for k, w in wanted.items() if not _near(got[k][0], w)}
    assert not wrong, f"calibre {version}: {wrong}, Previewer {wanted}"
    assert got["C1"][1] is None, f"calibre {version}: C1 $49 = {got['C1'][1]}"
    boxes = _box_spaces_below(kfx)
    assert _near(boxes[1], 1.594), f"calibre {version}: A's inner box $49, {boxes}"


def test_an_open_box_edge_is_read_from_its_border_and_padding(calibre):  # noqa: F811
    """Three rows no test held (QA on #682, its mutations):
    - a filled box with padding below, ending table + spacer: padding holds
      the edge, so the space goes in it, $54 0.417 + 1.594 (from the bottom
      border alone, it collapsed out: 2.042 in all, Previewer 2.875);
    - an open box ending an h3 + spacer: the h3's space below leaves the
      box in the h3's own size, 1.927 above the next paragraph (unscaled,
      1.647);
    - an open box ending a padded paragraph + spacer: the paragraph keeps
      its 0.5em padding below it, 0.417, inside the fill (#685 has the
      rest of that gap)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "e682.epub", tmp / "e682.kfx"
    table = "<table><tr><td>Tc cell</td></tr></table>"
    fill = "background-color:#cccccc"
    body = (
        _p("K0")
        + f"<div style='{fill};padding-bottom:0.5em'>{_p('K1')}{table}{SP}</div>"
        + _p("K2")
        + f"<div style='{fill}'><h3>L1 head</h3>{SP}</div>"
        + _p("L2")
        + f"<div style='{fill}'>{_p('M1', 'margin:1em 0;padding-bottom:0.5em')}{SP}</div>"
        + _p("M2")
    )
    _build(epub, {"e.xhtml": body}, [("E", "e.xhtml")], "e682")
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    pads = _box_paddings_below(kfx)
    assert _near(pads[0], 2.01), f"calibre {version}: K box $54 = {pads}"
    assert _near(got["L2"][0], 1.927), f"calibre {version}: L2 = {got['L2']}"
    assert _near(got["M1"][1], 0.417), f"calibre {version}: M1 = {got['M1']}"


def test_a_spacer_after_a_padded_paragraph_meets_its_outer_margin(calibre):  # noqa: F811
    """A paragraph with padding below, then a spacer (#685; my probe,
    2026-11-02): Kindle Previewer 4.0.3 keeps the padding with the
    paragraph, its margin outside the padding meets the spacer's, and the
    spacer's margin-bottom the next paragraph's. kfxgen added the margin
    after the spacer's space instead. Each next paragraph's $47 here is
    Previewer's padding (0.417) and its gap, written on the one entry:
    - padded p, spacer, p: 2.677 (kfxgen 2.844);
    - padded p, `margin:0` spacer, p: 2.677 (1.844);
    - padded p, spacer, `margin:0` p: 2.344 (2.844 below the padded p);
    - in a grey box with nothing below: the p keeps its 0.417, and 1.927
      leaves the box (#682);
    - a padded wrapper: unchanged, 3.594."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "p685.epub", tmp / "p685.kfx"
    pad = "margin:1em 0;padding-bottom:0.5em"
    body = (
        _p("A0") + _p("A1", pad) + SP + _p("A2")
        + _p("C0") + _p("C1", pad) + _sp("margin:0") + _p("C2")
        + _p("E0") + _p("E1", pad) + SP + _p("E2", "margin:0")
        + _p("B0")
        + f"<div style='background-color:#cccccc'>{_p('B1', pad)}{SP}</div>"
        + _p("B2")
        + _p("D0") + f"<div style='padding:1em 0'>{_p('D1')}</div>" + SP + _p("D2")
        # At 1.5em: its em margin read in its own size, 1em, as #669 reads
        # one with no padding; a px one isn't (QA on #688).
        + _p("F0") + _p("F1", pad + ";font-size:1.5em") + SP + _p("F2")
        + _p("G0") + _p("G1", pad + ";font-size:1.5em;margin-bottom:24px") + SP
        + _p("G2")
        # Two spacers: only the first meets the outer margin (QA on #688).
        + _p("T0") + _p("T1", pad) + SP + SP + _p("T2")
        # A padded wrapper's own em margin, in the wrapper's size, not its
        # paragraph's (QA on #688: 4.622).
        + _p("U0")
        + "<div style='padding-bottom:1em;margin-bottom:2em'>"
        + _p("U1", "margin:1em 0;font-size:1.5em") + "</div>" + SP + _p("U2")
        + _p("V0")
        + "<div style='font-size:1.5em;padding-bottom:1em;margin-bottom:2em'>"
        + _p("V1", "margin:1em 0;font-size:0.6667em") + "</div>" + SP + _p("V2")
    )  # fmt: skip
    _build(epub, {"p.xhtml": body}, [("P", "p.xhtml")], "p685")
    _ebook_convert(calibre, epub, kfx, True)
    got = _spacing(kfx)
    wanted = {
        "A2": 2.677,
        "C2": 2.677,
        "E2": 2.344,
        "B2": 1.927,
        "D2": 3.594,
        "F2": 2.885,
        "G2": 2.802,
        "T2": 3.771,
        "U2": 5.177,
        "V2": 5.177,
    }
    wrong = {k: got[k][0] for k, w in wanted.items() if not _near(got[k][0], w)}
    assert not wrong, f"calibre {version}: {wrong}, Previewer {wanted}"
    assert _near(got["B1"][1], 0.417), f"calibre {version}: B1 = {got['B1']}"
