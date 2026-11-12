"""A vertical-rl block inside a horizontal-tb block of vertical text (#247).

Kindle Previewer 4.0.3 (probe PSB R01-R09, decoded; QA's P749 Q09c) writes
it in a container of its own: a `$269` whose style has `$560: $559`
(vertical-rl), `$57` 100% and the two fit flags `$478`, `$784`, inside the
horizontal block's container. A <div> of two paragraphs is one container
(R03); `vertical-lr` is not (R04). Its own margins are laid out in the
horizontal block: top and bottom meet that block's, left and right stay on
it in line heights (R06). Inside it, text is vertical again: its pictures
are sized by height (R08, R09).

`mark_vertical_margins` marks the first vertical-rl element inside each
horizontal one; the walker wraps its blocks in a box marked vertical; the
generator writes the box as above and turns margins as in vertical text
while it is open. Through calibre in
tests/integration/test_calibre_vertical_in_horizontal.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen import native_generator as ng
from kfxgen.page_scale import VERTICAL_PT, page_width
from tests.unit.test_vertical_horizontal_blocks import _Stylizer

pytestmark = pytest.mark.unit

XHTML = "http://www.w3.org/1999/xhtml"
BODY = (
    '<p id="top">V</p>'
    '<div id="h" class="hz">'
    '<p id="v" class="vt">R01</p>'
    '<p id="h1">Before</p>'
    '<div id="vd" class="vt"><p id="vp">R03</p></div>'
    '<p id="l" class="vlr">R04</p>'
    "</div>"
)
MODES = {
    "html": "vertical-rl",
    "hz": "horizontal-tb",
    "vt": "vertical-rl",
    "vlr": "vertical-lr",
}


def _marked():
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{BODY}</body></html>')
    _conv.mark_vertical_margins(root, _Stylizer(MODES), select=lambda t: [])
    return {
        e.get("id"): e for e in root.iter() if isinstance(e.tag, str) and e.get("id")
    }


def test_the_first_vertical_element_inside_a_horizontal_one_is_marked():
    marked = {k for k, e in _marked().items() if e.get(_conv._VERTICAL) is not None}
    # Not vertical text outside a horizontal block, not a paragraph inside a
    # marked block, not vertical-lr (R04, which Previewer writes plainly).
    assert marked == {"v", "vd"}


def test_a_marked_blocks_own_margins_are_not_turned():
    # Laid out in the horizontal block: its css as calibre gives it.
    e = _marked()["v"]
    css = {"margin-top": "1em", "margin-left": "2em"}
    assert _conv._vertical_box(dict(css), {}, e) == css
    # A paragraph inside it is vertical text: turned.
    p = _marked()["vp"]
    assert _conv._vertical_box({"margin-top": "1em"}, {}, p) != {"margin-top": "1em"}


def test_the_innermost_block_sets_the_rotation():
    h = {"type": "open", "node": "box", "box": {"horizontal": True}}
    v = {"type": "open", "node": "box", "box": {"vertical": True}}
    plain = {"type": "open", "node": "box", "box": {}}
    assert [ng._opens_mode(c) for c in (h, v, plain)] == [
        "horizontal",
        "vertical",
        None,
    ]
    assert ng._rotation_for(True, []) == ["vertical"]
    assert ng._rotation_for(True, ["horizontal", None]) == ["horizontal"]
    assert ng._rotation_for(True, ["horizontal", "vertical", None]) == ["vertical"]
    assert ng._rotation_for(False, ["horizontal", "vertical"]) == []


def _style(**kw):
    with page_width(VERTICAL_PT):
        frag = ng.NativeKFXGenerator().build_box_style_157("s", **kw)
    return {str(k): v for k, v in frag.value.items() if str(k) != "$173"}


def test_a_vertical_box_is_vertical_at_the_pages_height():
    st = _style(vertical=True)
    assert set(st) == {"$560", "$57", "$478", "$784"}
    assert str(st["$560"]) == "$559" and str(st["$57"]["$306"]) == "$314"
    assert float(st["$57"]["$307"]) == 100


def test_its_side_margins_are_in_line_heights():
    # 1em is 2.5% of the 480pt page: 0.833333lh, as Previewer writes R06.
    st = _style(vertical=True, margin_left=2.5, margin_right=2.5)
    for key in ("$48", "$50"):
        assert str(st[key]["$306"]) == "$310"
        assert abs(float(st[key]["$307"]) - 0.833333) < 1e-6
    # A horizontal box's stay a share of the page.
    st = _style(horizontal=True, margin_left=2.5)
    assert str(st["$48"]["$306"]) == "$314"


def test_a_fixed_layout_page_drops_the_mark():
    # #752: such a page is read as horizontal text, every mark taken off.
    root = _marked()["v"].getroottree().getroot()
    _conv._drop_vertical_marks(root)
    assert not any(
        e.get(_conv._VERTICAL) for e in root.iter() if isinstance(e.tag, str)
    )
