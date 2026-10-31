"""A bordered or filled <p>, <pre> or <div> in a table cell is a box in the
cell, as Kindle Previewer 4.0.3 draws it: the cell's container, then a
`$270` with the border, fill and padding, then the text (#645; QA's probe on
#644, mine 2026-10-31, a page with a DOCTYPE).

kfxgen wrote such a block flat. A cell of one paragraph was one text entry,
with no border; in a cell of several blocks the box's paragraphs were
joined into one entry. Links to a paragraph in the box land on it.

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_part_headings import (  # noqa: E402
    _book_epub,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BX = "border:1px solid black;padding:0.5em;margin:0"
FILL = "background-color:#eeeeee;margin:0"
BODY = (
    "<h1>Cells</h1>"
    "<p>L0 see <a href='#t1'>one</a>, <a href='#t2'>two</a> and <a href='#t3'>three</a>.</p>"
    f"<table><tr><td><p style='{BX}' id='t1'>L1 bordered</p></td><td>L2 plain</td></tr>"
    f"<tr><td><p style='margin:0'>L3 plain</p><div style='{BX}'><p style='margin:0'>L4 first</p>"
    f"<p style='margin:0' id='t2'>L5 second</p></div></td>"
    f"<td><pre style='{FILL}'><span id='t3'>L6</span> filled</pre></td></tr>"
    f"<tr><th><p style='{BX}'>L8 boxed header</p></th><th>L9 plain header</th></tr></table>"
    "<p>L7 after.</p>"
)
BORDER_KEYS = {"$88", "$89", "$90", "$91", "$92"}


def _plain(x):
    return getattr(x, "value", x)


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """({text: (its box's id, style) or None}, {anchor: text}, version)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "c645.epub", tmp / "c645.kfx"
    _book_epub(
        epub,
        "C645",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Cells", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    boxes, at = {}, {}

    def walk(e, box, in_cell):
        e = _plain(e)
        if not hasattr(e, "get"):
            return
        style = {str(k): v for k, v in styles.get(str(e.get("$157")), {}).items()}
        if e.get("$145"):
            t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            boxes[t[:2]] = (box, in_cell, style)
            at[int(e["$155"])] = t[:2]
            return
        kind = str(e.get("$159"))
        for k in e.get("$146") or ():
            walk(
                k, (id(e), style) if kind == "$270" else box, in_cell or kind == "$279"
            )

    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            walk(e, None, False)
    anchors = {
        str(f.fid): at.get(int(val(f)["$183"]["$155"]))
        for f in by_type(frags, "$266")
        if "$183" in val(f)
    }
    return boxes, anchors, version


@pytest.mark.parametrize("case", ["L1", "L4", "L5", "L6"])
def test_a_bordered_or_filled_block_in_a_cell_is_a_box(converted, case):
    boxes, _, version = converted
    box, in_cell, own = boxes[case]
    assert in_cell and box is not None, f"calibre {version}: {case} {boxes[case]}"
    # Its text takes no space above: the cell and the box space it.
    assert "$47" not in own, f"calibre {version}: {case} {own}"


def test_its_border_and_fill_are_the_books(converted):
    boxes, _, version = converted
    assert BORDER_KEYS & set(boxes["L1"][0][1]), f"calibre {version}: {boxes['L1']}"
    assert int(boxes["L6"][0][1]["$70"]) == 0xFFEEEEEE, f"calibre {version}"
    # The box's two paragraphs are two entries in one box.
    assert boxes["L4"][0][0] == boxes["L5"][0][0], f"calibre {version}: {boxes}"


def test_plain_cells_are_unchanged(converted):
    boxes, _, version = converted
    for case in ("L2", "L3"):
        assert boxes[case][0] is None, f"calibre {version}: {case} {boxes[case]}"


def test_links_land_on_the_paragraph_in_the_box(converted):
    _, anchors, version = converted
    assert sorted(v for v in anchors.values() if v) == ["L1", "L5", "L6"], (
        f"calibre {version}: {anchors}"
    )


def test_a_box_in_a_header_cell_is_centred_as_the_cell(converted):
    """`<th><p style="border:…">`: the text centred, as Previewer writes it
    (`$34` `$320`), and as the plain header cell beside it. The box's
    paragraphs first came out justified."""
    boxes, _, version = converted
    for case in ("L8", "L9"):
        assert str(boxes[case][2].get("$34")) == "$320", (
            f"calibre {version}: {case} {boxes[case]}"
        )


#: A box in a cell is spaced by its margins against its neighbours in the
#: cell, as Kindle Previewer 4.0.3 spaces it (QA on #650; my probe
#: 2026-10-31): ($47, $49) on the box, in lh. The box had none, and touched
#: the paragraph above it.
SPACED = {"U1": (0.833, 0.833), "U4": (0.833, None), "U6": (0.417, 1.667)}


def test_a_box_in_a_cell_is_spaced_by_its_margins(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "u650.epub", tmp / "u650.kfx"
    bx = "border:1px solid black;padding:0.5em"
    _book_epub(
        epub,
        "U650",
        "<h1>Spaced</h1><p>U0 before.</p>"
        f"<table><tr><td><p style='{bx}'>U1 boxed</p></td><td>U2 plain</td></tr>"
        f"<tr><td><p>U3 plain</p><p style='{bx}'>U4 boxed</p><p>U5 plain</p></td>"
        "<td><p style='border:1px solid black;margin:0.5em 0 2em'>U6 boxed</p></td>"
        "</tr></table><p>U7 after.</p>",
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Spaced", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = {}

    def lh(style, key):
        v = style.get(key)
        return round(float(_plain(v)["$307"]), 3) if v is not None else None

    def walk(e, box):
        e = _plain(e)
        if not hasattr(e, "get"):
            return
        style = {str(k): v for k, v in styles.get(str(e.get("$157")), {}).items()}
        if e.get("$145"):
            t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            got[t[:2]] = box
            return
        for k in e.get("$146") or ():
            walk(k, style if str(e.get("$159")) == "$270" else box)

    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            walk(e, None)
    wrong = {
        k: (lh(got[k], "$47"), lh(got[k], "$49"))
        for k, want in SPACED.items()
        if got.get(k) is None or (lh(got[k], "$47"), lh(got[k], "$49")) != want
    }
    assert not wrong, f"calibre {version}: {wrong}, Previewer {SPACED}"
