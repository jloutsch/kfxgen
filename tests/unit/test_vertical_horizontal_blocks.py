"""A horizontal-tb block inside vertical-rl text (#247).

Kindle Previewer 4.0.3 writes it in a container of its own (probe P247h,
decoded): a `$269` whose style has `$560: $557` (horizontal-tb), `$56`
100%, `$478` and `$784` (fit flags kfxlib reads as fit_width and
fit_tight), around the block's paragraphs, with the block's margins on it
and kept where the book puts them, not moved as vertical text's are. A
<div> holding two paragraphs is one container around both (H03); a
bordered one is a `$270` (H11); kfxgen writes a filled one the same way. kfxgen's own 0.5% left margin is not
written in it (none of H01-H11 has one).

`mark_vertical_margins` marks the first horizontal-tb element of each
branch of a vertical-rl file and leaves it and all inside it without the
vertical marks, so their margins are physical; the walker wraps its blocks
in a box marked horizontal; the generator writes the box as above and
stops moving margins while it is open.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

XHTML = "http://www.w3.org/1999/xhtml"
HZ = "horizontal-tb"


class _Stylizer:
    """calibre's pre-flatten Stylizer as far as `mark_vertical_margins` reads
    it: no rules, and an element's writing mode by its tag or class."""

    rules = ()

    def __init__(self, modes):
        self._modes = modes

    def style(self, elem):
        keys = [elem.tag.split("}")[-1], *(elem.get("class") or "").split()]
        mode = next((self._modes[k] for k in keys if k in self._modes), None)
        return {"writing-mode": mode} if mode else {}


BODY = (
    '<p id="v">V</p><p id="h" class="hz">H01 <span id="s">s</span></p>'
    '<div id="d" class="hz"><p id="d1">H03</p><p id="d2" class="hz">H04</p></div>'
    '<p id="i">H10 <span id="is" class="hz">ABC</span></p>'
)


def _marks(modes):
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{BODY}</body></html>')
    _conv.mark_vertical_margins(root, _Stylizer(modes), select=lambda t: [])
    return {
        e.get("id"): (e.get(_conv._HORIZONTAL), e.get(_conv._V_AUTHOR) is not None)
        for e in root.iter()
        if isinstance(e.tag, str) and e.get("id")
    }


@pytest.mark.unit
def test_the_first_horizontal_element_of_a_branch_is_marked_and_nothing_in_it_is_vertical():
    assert _marks({"html": "vertical-rl", "hz": HZ}) == {
        "v": (None, True),
        "h": ("1", False),
        "s": (None, False),
        "d": ("1", False),
        "d1": (None, False),
        # Horizontal inside horizontal: the <div> is the block.
        "d2": (None, False),
        "i": (None, True),
        "is": ("1", False),
    }


@pytest.mark.unit
def test_a_horizontal_file_marks_nothing():
    assert set(_marks({"hz": HZ}).values()) == {(None, False)}


@pytest.mark.unit
def test_a_vertical_element_inside_a_horizontal_one_stays_horizontal_free():
    # vertical-rl again inside the horizontal <div>: vertical text, marked so.
    marks = _marks({"html": "vertical-rl", "hz": HZ, "p": "vertical-rl"})
    assert marks["d1"] == (None, True)


# --- The walker -----------------------------------------------------------------

CSS = {
    "hz": {"writing-mode": HZ},
    "bx": {"writing-mode": HZ, "background-color": "#eeeeee"},
    "mt": {"margin-top": "2em"},
}


def _blocks(body):
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    for e in root.iter():
        if isinstance(e.tag, str):
            e.set(_conv._V_AUTHOR, "")
            e.set(_conv._V_UA, "margin-top:1em;margin-bottom:1em")
    for e in root.iter():
        if isinstance(e.tag, str) and (e.get("data-h") is not None):
            for x in e.iter():
                x.attrib.pop(_conv._V_AUTHOR, None)
                x.attrib.pop(_conv._V_UA, None)
            e.set(_conv._HORIZONTAL, "1")
    return _conv.extract_blocks_from_html(
        root, style_resolver=_resolver(CSS), base_href="c.xhtml"
    )


def _shape(blocks):
    return [
        ("box", bool(b["box"].get("horizontal")), _shape(b["blocks"]))
        if b.get("type") == "box"
        else b["text"]
        for b in blocks
        if b.get("type") != "space"
    ]


@pytest.mark.unit
def test_a_horizontal_paragraph_is_a_horizontal_box_around_it():
    assert _shape(_blocks('<p>V</p><p data-h="">H01</p><p>V2</p>')) == [
        "V",
        ("box", True, ["H01"]),
        "V2",
    ]


@pytest.mark.unit
def test_a_horizontal_div_is_one_box_around_its_paragraphs():
    assert _shape(_blocks('<div data-h=""><p>H03</p><p>H04</p></div>')) == [
        ("box", True, ["H03", "H04"])
    ]


@pytest.mark.unit
def test_a_horizontal_heading_is_a_box_too():
    # H08: a heading is no box tag, but Previewer wraps it.
    assert _shape(_blocks('<h2 data-h="">H08</h2>')) == [("box", True, ["H08"])]


@pytest.mark.unit
def test_an_unmarked_paragraph_is_no_box():
    assert _shape(_blocks('<p class="hz">H01</p>')) == ["H01"]


# --- The generator ----------------------------------------------------------------

_SIDES = ("$47", "$48", "$49", "$50")


def _storyline(tmp_path, body):
    blocks = _blocks(body)
    for b in blocks:
        b["writing_mode"] = "vertical-rl"
    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "",
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": blocks,
            }
        ],
        output_path=str(out),
        rtl=True,
        language="ja",
        writing_mode="vertical-rl",
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (story,) = by_type(frags, "$259")

    def entry(e):
        st = styles.get(str(e.get("$157")), {})
        return {
            "type": str(e["$159"]),
            "style": {str(k): v for k, v in st.items() if str(k) != "$173"},
            "kids": [entry(k) for k in e.get("$146") or () if hasattr(k, "get")],
        }

    return [entry(e) for e in val(story)["$146"]]


def _num(v):
    return float(v["$307"])


@pytest.mark.unit
def test_the_box_is_a_horizontal_container_at_the_pages_width(tmp_path):
    (box,) = _storyline(tmp_path, '<p data-h="">H01</p>')
    assert box["type"] == "$269"
    st = box["style"]
    assert str(st["$560"]) == "$557"
    assert _num(st["$56"]) == 100.0 and str(st["$56"]["$306"]) == "$314"
    assert st["$478"] is True and st["$784"] is True
    assert [k["type"] for k in box["kids"]] == ["$269"]


@pytest.mark.unit
def test_margins_in_it_stay_where_the_book_puts_them(tmp_path):
    # H05: margin-top 2em is the box's top, in line heights; a paragraph
    # beside it in vertical text has its space moved right and left.
    entries = _storyline(
        tmp_path,
        '<p class="mt">V</p><div data-h="" class="mt"><p class="mt">H</p></div>',
    )
    vertical, box = entries[-2], entries[-1]
    assert "$47" not in vertical["style"] and "$50" in vertical["style"]
    assert _num(box["style"]["$47"]) == pytest.approx(2 / 1.2, abs=1e-5)
    (inner,) = box["kids"]
    sides = {k for k in _SIDES if k in inner["style"]}
    assert "$50" not in sides and "$48" not in sides


@pytest.mark.unit
def test_kfxgens_own_left_margin_is_not_written_in_it(tmp_path):
    (box,) = _storyline(tmp_path, '<p data-h="">H01</p>')
    assert "$48" not in box["kids"][0]["style"]


@pytest.mark.unit
def test_a_filled_one_is_a_box_container(tmp_path):
    (box,) = _storyline(tmp_path, '<p data-h="" class="bx">H11</p>')
    assert box["type"] == "$270"
    assert str(box["style"]["$560"]) == "$557"


@pytest.mark.unit
def test_vertical_text_after_it_is_moved_again(tmp_path):
    entries = _storyline(tmp_path, '<p data-h="">H</p><p class="mt">V</p>')
    assert "$50" in entries[-1]["style"] and "$47" not in entries[-1]["style"]
