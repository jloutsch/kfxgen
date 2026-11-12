"""A horizontal-tb item in a list of vertical-rl text, through calibre
(#247, QA's P749 Q04).

Kindle Previewer 4.0.3 (probe PQ4, decoded; a vertical Japanese book and the
same with an ltr spine, alike) writes the list as a `$276`: its space before
and after (in vertical text its right and left) `$50`/`$48` 0.833333lh, a
`margin: 2em 0` list's top and bottom `$47`/`$49` 2em. kfxgen writes a list
as paragraphs with the list's margins on the first and last; a horizontal
item there is a horizontal container, which took them on its top and bottom.
It now takes them on the sides Previewer's list has them.

It needs calibre installed, so it is `slow` and skips when `ebook-convert`
is not found.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import load_fragments  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.integration.test_calibre_vertical_in_horizontal import (  # noqa: E402
    _is_horizontal,
    _lh,
    _tree,
)
from tests.integration.test_calibre_vertical_writing import (  # noqa: E402
    REST,
    VERT,
    _write,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = f"html {{ {VERT} }} p {{ margin: 0; text-indent: 1em }}" + REST
BODY = (
    "<h1>第一章</h1><p>V00 縦の段落。</p>"
    '<ul><li class="hz">L01 Item one.</li></ul>'
    "<p>V01 縦の段落。</p>"
    '<ul><li>L02a 縦の一。</li><li class="hz">L02 Two.</li><li>L02c 縦の三。</li></ul>'
    "<p>V02 縦の段落。</p>"
    '<ul><li class="hz">L03a First.</li><li class="hz">L03b Second.</li></ul>'
    "<p>V03 縦の段落。</p>"
    '<ol><li class="hz">L04 Numbered.</li><li>L04b 縦の二。</li></ol>'
    "<p>V04 縦の段落。</p>"
    '<ul style="margin: 2em 0"><li class="hz">L05 Spaced.</li></ul>'
    "<p>V05 縦の段落。</p>"
    '<ul style="margin: 1em 0 3em 0"><li class="hz">L07 Uneven.</li></ul>'
    "<p>V07 縦の段落。</p>"
    '<ul><li class="hz" style="margin-left: 2em; margin-right: 0.5em">L08 Own.</li></ul>'
    "<p>V08 縦の段落。</p>"
    '<div class="hz" style="margin-left: 2em"><p>L09 Left margin.</p></div>'
    "<p>V09 縦の段落。</p>"
    '<p class="hz" style="margin-top: 2em">L10 Top margin.</p>'
    "<p>V10 縦の段落。</p>"
    # QA's Q764: W06 a padded wrapper, W05 padded on all sides, W11 nested
    # lists, W12 two margin-tops.
    '<div style="padding-right: 1em; margin-right: 2em"><div class="hz">'
    "<p>W06 First, padded.</p></div><p>W06b 縦。</p></div>"
    '<div style="padding: 1em; margin: 0 2em"><div class="hz"><p>W05 Only.</p></div></div>'
    "<p>V11 縦の段落。</p>"
    '<ul style="margin-right: 1em"><li><ul style="margin-right: 2em">'
    '<li class="hz">W11 Nested.</li></ul></li></ul>'
    "<p>V12 縦の段落。</p>"
    '<div style="margin-top: 1em"><ul style="margin-top: 1em">'
    '<li class="hz">W12 Twice.</li></ul></div>'
    "<p>V13 縦の段落。</p>"
    '<div style="padding-right: 1em"><ul style="margin-right: 2em">'
    '<li class="hz">L11 Inside padding.</li></ul></div>'
    "<p>V14 縦の段落。</p>"
    # QA's W21, W30: margins outside a padding meet only each other.
    '<div style="margin-right: 2em"><div style="margin-right: 1em">'
    '<div style="padding-right: 1em"><div class="hz"><p>W21 Two outside.</p></div>'
    "</div></div></div>"
    "<p>V15 縦の段落。</p>"
    '<div style="margin-right: 2em"><div style="padding-right: 1em; margin-right: 1em">'
    '<div class="hz"><p>W30 Both sides.</p></div></div></div>'
    "<p>V99 縦の段落。</p>"
)


def _book(calibre, name, rtl):  # noqa: F811
    tmp, _env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _write(epub, rtl, CSS, CSS, ch1=BODY)
    _ebook_convert(calibre, epub, kfx, True)
    return _tree(load_fragments(kfx))


@pytest.fixture(scope="module", params=[True, False], ids=["rtl", "ltr"])
def tree(calibre, request):  # noqa: F811
    return _book(calibre, f"pq4-{request.param}", request.param)


def _sides(tree, words):
    _st, chain = tree[words]
    box = chain[0]
    assert _is_horizontal(box), box
    return box


def test_a_single_horizontal_item_has_the_lists_space_on_its_sides(tree):
    # L01: before and after on its right and left, none on its top and bottom.
    box = _sides(tree, "L01")
    for key in ("$50", "$48"):
        assert abs(_lh(box[key]) - 0.833333) < 1e-6
    assert "$47" not in box and "$49" not in box


def test_the_first_and_last_horizontal_items_take_before_and_after(tree):
    # L03: the first its right, the last its left; L04: a numbered list.
    first, last = _sides(tree, "L03a"), _sides(tree, "L03b")
    assert "$50" in first and "$48" not in first
    assert "$48" in last and "$50" not in last
    assert "$50" in _sides(tree, "L04")


def test_an_item_in_the_middle_takes_none(tree):
    # L02: the vertical items around it keep the list's space.
    box = _sides(tree, "L02")
    assert not {"$47", "$48", "$49", "$50"} & set(box)
    assert "$50" in tree["L02a"][0] and "$48" in tree["L02c"][0]


def test_the_lists_top_and_bottom_go_on_its_top_and_bottom(tree):
    # L05, margin: 2em 0: `$47`/`$49` 2em, as Previewer's list has them.
    box = _sides(tree, "L05")
    for key in ("$47", "$49"):
        assert "$308" in box[key] and "'2'" in box[key]
    assert "$48" not in box and "$50" not in box


def test_an_uneven_lists_top_and_bottom_keep_their_sides(tree):
    # PQ4B L07, margin: 1em 0 3em 0: `$47` 1em, `$49` 3em.
    box = _sides(tree, "L07")
    assert "'1'" in box["$47"] and "'3'" in box["$49"]


def test_the_items_own_side_margins_collapse_with_the_lists(tree):
    # PQ4B L08: its own 2em left and 0.5em right against the list's 1em
    # after and before: the larger, `$48` 1.66667lh and `$50` 0.833333lh.
    box = _sides(tree, "L08")
    assert abs(_lh(box["$48"]) - 1.66667) < 1e-5
    assert abs(_lh(box["$50"]) - 0.833333) < 1e-6


def test_a_horizontal_blocks_own_margins_are_unchanged(tree, request):
    # PQ4B L09: a horizontal <div>'s margin-left 2em stays `$48` 5% of the
    # 480pt page (#749; 6.25% of 384pt with an ltr spine); P247j H05: a
    # horizontal <p>'s margin-top stays on its top.
    box = _sides(tree, "L09")
    pct = "'5'" if "rtl" in request.node.callspec.id else "'6.25'"
    assert "$314" in box["$48"] and pct in box["$48"]
    box = _sides(tree, "L10")
    assert abs(_lh(box["$47"]) - 1.66667) < 1e-5 and "$50" not in box


def test_a_padded_wrappers_padding_and_margin_add_up_before_it(tree):
    # QA's W06, padding-right 1em and margin-right 2em: Previewer's outer
    # container `$50` 1.66667lh and `$55` 0.833333lh; on the block, 3em,
    # 2.5lh. W05, padding 1em and margin 0 2em: 2.5lh on both sides.
    box = _sides(tree, "W06")
    assert abs(_lh(box["$50"]) - 2.5) < 1e-6 and "$47" not in box
    box = _sides(tree, "W05")
    for key in ("$50", "$48"):
        assert abs(_lh(box[key]) - 2.5) < 1e-6
    assert "$47" not in box and "$49" not in box


def test_nested_lists_space_before_collapses(tree):
    # QA's W11: 1em and 2em before, collapsed: 2em, 1.66667lh.
    box = _sides(tree, "W11")
    assert abs(_lh(box["$50"]) - 1.66667) < 1e-5


def test_two_margin_tops_add_up(tree):
    # QA's W12: a <div>'s and a list's margin-top 1em each, in vertical
    # text on the line's axis, don't collapse: `$47` 2em.
    box = _sides(tree, "W12")
    assert "$308" in box["$47"] and "'2'" in box["$47"]


def test_a_margin_inside_a_padding_adds_to_it(tree):
    # PQ4C L11: a list's margin-right 2em inside a div's padding-right 1em:
    # Previewer's div `$55` 0.833333lh and list `$50` 1.66667lh; on the
    # block, 2.5lh.
    box = _sides(tree, "L11")
    assert abs(_lh(box["$50"]) - 2.5) < 1e-6


@pytest.mark.parametrize("words", ["W21", "W30"])
def test_margins_outside_a_padding_meet_only_each_other(tree, words):
    # QA's W21 (2em › 1em › padding 1em) and W30 (2em › padding 1em and
    # margin 1em): 1em padding + the larger of 1em and 2em = 3em, 2.5lh,
    # as Previewer totals them.
    box = _sides(tree, words)
    assert abs(_lh(box["$50"]) - 2.5) < 1e-6
