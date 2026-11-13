"""Which lists are written as Kindle Previewer's `$276`/`$277` entries (#768).

A `<ul>`/`<ol>` of `<li>` items with a marker style Previewer writes
(`$100`) becomes a list box; its items lose kfxgen's typed markers. The rest
keep #201's typed markers: a `reversed` list (Previewer counts it up, S768
L8 on the Paperwhite), items that type their own number or bullet, mixed
marker styles, a style with no `$100` (a quoted string), an item displayed
as a block, a list with a child other than `<li>`, and lists in table cells
or vertical text, which were not probed. Through calibre in
tests/integration/test_calibre_list_entries.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.converter import extract_blocks_from_html

pytestmark = pytest.mark.unit

XHTML = "http://www.w3.org/1999/xhtml"


def _blocks(body, css=None):
    """Blocks of `body`, every element styled by the `class` rules in
    `css` ({class: {property: value}}), items as list items."""
    css = css or {}

    def resolver(e):
        out = {"display": "list-item"} if e.tag.endswith("}li") else {}
        for c in (e.get("class") or "").split():
            out.update(css.get(c, {}))
        return out

    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    return extract_blocks_from_html(root, style_resolver=resolver, native_tables=True)


def _lists(blocks):
    return [
        b["box"]["list"]
        for b in blocks
        if b.get("type") == "box" and b["box"].get("list")
    ]


def _texts(blocks):
    return [b["text"] for b in blocks]


def test_a_plain_list_is_a_list_box_without_typed_markers():
    blocks = _blocks("<ul><li>a</li><li>b</li></ul>")
    assert _lists(blocks) == [{"type": "$340", "start": None, "inside": False}]
    assert [b["text"] for b in blocks[0]["blocks"]] == ["a", "b"]


@pytest.mark.parametrize(
    "body, css, texts",
    [
        # Previewer counts it up: the typed 3, 2, 1 stay.
        ('<ol reversed=""><li>a</li><li>b</li></ol>', None, ["2. a", "1. b"]),
        # The item typed its number; Kindle would draw a second.
        ("<ol><li>1. a</li><li>2. b</li></ol>", None, ["1. a", "2. b"]),
        ("<ul><li>• a</li><li>• b</li></ul>", None, ["• a", "• b"]),
        # A quoted-string marker has no `$100`.
        ('<ul><li class="q">a</li></ul>', {"q": {"list-style-type": '"– "'}}, ["– a"]),
        # Two marker styles in one list.
        (
            '<ol><li>a</li><li class="r">b</li></ol>',
            {"r": {"list-style-type": "lower-roman"}},
            ["1. a", "ii. b"],
        ),
        # A child other than <li>.
        ("<ul><li>a</li><p>p</p></ul>", None, ["• a", "p"]),
    ],
    ids=["reversed", "typed-number", "typed-bullet", "quoted", "mixed", "not-li"],
)
def test_these_lists_keep_typed_markers(body, css, texts):
    blocks = _blocks(body, css)
    assert not _lists(blocks)
    assert _texts(blocks) == texts


def test_an_item_shown_as_a_block_keeps_the_list_as_paragraphs():
    blocks = _blocks(
        '<ul><li class="b">a</li><li>b</li></ul>', {"b": {"display": "block"}}
    )
    assert not _lists(blocks)
    assert _texts(blocks) == ["a", "• b"]


def test_a_list_in_a_table_cell_keeps_typed_markers():
    blocks = _blocks(
        "<table><tr><td><ol><li>a</li><li>b</li></ol></td><td>x</td></tr></table>"
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    cell = table["table"]["rows"][0]["cells"][0]
    assert "1. a" in str(cell) and not _lists(cell.get("blocks") or [])


def test_a_list_in_vertical_text_keeps_its_paragraphs():
    root = etree.fromstring(
        f'<html xmlns="{XHTML}"><body><ul><li>a</li></ul></body></html>'
    )
    for e in root.iter():
        e.set(_conv._V_AUTHOR, "")
        e.set(_conv._V_UA, "")
    blocks = extract_blocks_from_html(
        root,
        style_resolver=lambda e: (
            {"display": "list-item"} if e.tag.endswith("}li") else {}
        ),
    )
    assert not _lists(blocks)
    assert _texts(blocks) == ["• a"]


def test_an_item_of_one_paragraph_is_marked_and_one_of_two_is_a_box():
    (lst,) = _blocks("<ol><li>a</li><li><p>b</p><p>c</p></li></ol>")
    first, second = lst["blocks"]
    assert first["list_item"] == {"value": None, "n": 1}
    assert second["type"] == "box" and second["box"]["item"] == {"value": None, "n": 2}
    assert _texts(second["blocks"]) == ["b", "c"]


def test_a_start_and_a_value_are_kept():
    (lst,) = _blocks('<ol start="3"><li>a</li><li value="9">b</li></ol>')
    assert lst["box"]["list"]["start"] == 3
    assert [b["list_item"]["value"] for b in lst["blocks"]] == [None, 9]


def test_a_list_in_a_horizontal_block_of_a_vertical_file_keeps_its_paragraphs():
    # The block is marked, the list inside it is not (`mark_vertical_margins`).
    root = etree.fromstring(
        f'<html xmlns="{XHTML}"><body><div><ul><li>a</li></ul></div></body></html>'
    )
    body = root[0]
    body.set(_conv._V_AUTHOR, "")
    body.set(_conv._V_UA, "")
    body[0].set(_conv._HORIZONTAL, "1")
    blocks = extract_blocks_from_html(
        root,
        style_resolver=lambda e: (
            {"display": "list-item"} if e.tag.endswith("}li") else {}
        ),
    )
    assert not _lists(blocks)
    assert "• a" in str(blocks)


#: Items with no marker, as calibre's computed style gives them from the list.
_NONE = '<li class="i">a</li><li class="i">b</li>'


def _plain(blocks):
    return [
        b["box"] for b in blocks if b.get("type") == "box" and b["box"].get("plain")
    ]


@pytest.mark.parametrize(
    "css, left, right",
    [
        ({}, 4.6875, None),  # 40px
        ({"margin-left": "-2em"}, -1.5625, None),  # QA's Q771D D5
        ({"padding-sides": ("0", "0", "0", "1em"), "font-size": 13.28}, 2.59375, None),
        ({"margin-left": "1.3em", "margin-right": "1em"}, 8.75, 3.125),  # D2
    ],
    ids=["default", "negative-margin", "own-size", "both-sides"],
)
def test_an_unmarked_lists_indent_is_its_margin_and_padding(css, left, right):
    blocks = _blocks(
        f'<ul class="n">{_NONE}</ul>',
        {"n": css, "i": {"list-style-type": "none"}},
    )
    (box,) = _plain(blocks)
    assert box["margin_left"] == pytest.approx(left)
    assert box["margin_right"] == (None if right is None else pytest.approx(right))


def test_an_unmarked_list_in_rtl_text_is_plain_paragraphs():
    # QA's Q771 B15: no container, so no indent on the end side.
    blocks = _blocks(
        f'<div dir="rtl"><ul>{_NONE}</ul></div>',
        {"i": {"list-style-type": "none"}},
    )
    assert not _plain(blocks) and not _lists(blocks)
    assert _texts(blocks) == ["a", "b"]


def test_an_empty_item_is_an_empty_item_box():
    # QA's Q771 X26: Previewer writes an empty `$277`, which shows "2.".
    (lst,) = _blocks("<ol><li>a</li><li></li><li>c</li></ol>")
    first, empty, third = lst["blocks"]
    assert empty["type"] == "box" and empty["blocks"] == []
    assert empty["box"]["item"] == {"value": None, "n": 2, "empty": True}
    assert third["list_item"] == {"value": None, "n": 3}
