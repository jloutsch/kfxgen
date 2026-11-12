"""A horizontal-tb item in a list of vertical-rl text (#247, QA's P749 Q04).

Kindle Previewer 4.0.3 (probe PQ4, decoded, both spines alike) writes the
list as a `$276` whose space before and after, in vertical text its right
and left, are `$50`/`$48` 0.833333lh, and a `margin: 2em 0` list's top and
bottom `$47`/`$49` 2em; a horizontal item is a `$277` with the horizontal
keys. kfxgen writes a list as paragraphs, the list's margins on its first
and last: when that is a horizontal item, its container, which took them on
its top and bottom. They now go on its right and left (`before`, `after`)
and its top and bottom (`over`, `under`) as Previewer's list has them.
Through calibre in tests/integration/test_calibre_vertical_lists.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen import native_generator as ng
from kfxgen.page_scale import VERTICAL_PT, page_width
from tests.unit.test_floats import _resolver

pytestmark = pytest.mark.unit

XHTML = "http://www.w3.org/1999/xhtml"
HZ = "horizontal-tb"
CSS = {"hz": {"writing-mode": HZ}, "tb": {"margin-top": "2em", "margin-bottom": "2em"}}


def _boxes(body):
    """The horizontal boxes `body` gives in vertical text, in order. Every
    element is vertical text with the user agent's 1em before and after
    a list (`_V_UA`), except a `class="hz"` one; `class="tb"` sets the
    book's own margin-top and -bottom."""
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    for e in root.iter():
        if not isinstance(e.tag, str):
            continue
        if "hz" in (e.get("class") or "").split():
            e.set(_conv._HORIZONTAL, "1")
            continue
        own = "margin-top margin-bottom" if "tb" in (e.get("class") or "") else ""
        e.set(_conv._V_AUTHOR, own)
        ua = "margin-top:1em;margin-bottom:1em" if e.tag.endswith("}ul") else ""
        e.set(_conv._V_UA, ua)
    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=_resolver(CSS), native_tables=True, base_href="c.xhtml"
    )
    return [b["box"] for b in blocks if b.get("type") == "box"]


def test_a_single_horizontal_item_takes_the_lists_space_on_its_sides():
    # PQ4 L01: the list's 1em before and after, on its right and left.
    (box,) = _boxes('<ul><li class="hz">One.</li></ul>')
    assert box.get("before") == 1.0 and box.get("after") == 1.0


def test_only_the_first_and_last_take_them():
    # PQ4 L03: two horizontal items, the first before, the last after.
    first, last = _boxes('<ul><li class="hz">A.</li><li class="hz">B.</li></ul>')
    assert first.get("before") == 1.0 and not first.get("after")
    assert last.get("after") == 1.0 and not last.get("before")
    # PQ4 L02: one in the middle takes none.
    (middle,) = _boxes('<ul><li>一。</li><li class="hz">Two.</li><li>三。</li></ul>')
    assert not middle.get("before") and not middle.get("after")


def test_the_lists_top_and_bottom_go_on_its_top_and_bottom():
    # PQ4 L05, margin: 2em 0: in vertical text its left and right slots;
    # on the horizontal item, its top and bottom.
    (box,) = _boxes('<ul class="tb"><li class="hz">Spaced.</li></ul>')
    assert box.get("over") == 2.0 and box.get("under") == 2.0
    assert not box.get("margin_left") and not box.get("margin_right")


def test_a_horizontal_block_in_horizontal_text_is_unchanged():
    # Not in vertical text (no marks): nothing moves.
    root = etree.fromstring(
        f'<html xmlns="{XHTML}"><body><ul><li class="hz">One.</li></ul></body></html>'
    )
    blocks = _conv.extract_blocks_from_html(root, style_resolver=_resolver(CSS))
    assert not any(b.get("type") == "box" for b in blocks)


def _style(**kw):
    with page_width(VERTICAL_PT):
        frag = ng.NativeKFXGenerator().build_box_style_157("s", horizontal=True, **kw)
    return {str(k): v for k, v in frag.value.items() if str(k) != "$173"}


def test_the_box_writes_them_on_their_sides():
    st = _style(before=1.0, after=1.0, over=2.0, under=2.0)
    for key in ("$50", "$48"):
        assert str(st[key]["$306"]) == "$310"
        assert abs(float(st[key]["$307"]) - 0.833333) < 1e-6
    for key in ("$47", "$49"):
        assert str(st[key]["$306"]) == "$308" and float(st[key]["$307"]) == 2


def test_its_own_side_margins_stay_when_none_are_given():
    st = _style(margin_left=2.5)
    assert str(st["$48"]["$306"]) == "$314"
