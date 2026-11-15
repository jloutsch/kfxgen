"""Blocks drawn inline (#780).

Project Gutenberg's ebookmaker wraps loose text in `div.pg_body_wrapper`,
set `display: inline`. kfxgen made each such <div> a paragraph: pg9939's
recipe yields printed as "(", "Sufficient to Serve Six", ")". Kindle Previewer
4.0.3 (probes P780 and P780J, decoded) runs a <div>, <p> or <h3> set
`inline` or `inline-block` into the words round it, at the top of <body>, in
a box, in a list item and in a cell; one holding a block breaks round it;
one holding only line breaks is a paragraph break with space. Through calibre
in tests/integration/test_calibre_inline_blocks.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from tests.unit.test_floats import _resolver

pytestmark = pytest.mark.unit

XHTML = "http://www.w3.org/1999/xhtml"
CSS = {"w": {"display": "inline"}, "ib": {"display": "inline-block"}}


def _root(body):
    return etree.fromstring(
        f'<html xmlns="{XHTML}"><body>{body}</body></html>'.encode()
    )


def _texts(body, **kw):
    blocks = _conv.extract_blocks_from_html(
        _root(body), style_resolver=_resolver(CSS), base_href="c.xhtml", **kw
    )
    return [b["text"] for b in blocks if b.get("text")]


def test_inline_divs_at_the_top_of_the_body_are_one_paragraph():
    # pg9939's recipe heading and yield, then its ingredients.
    body = (
        '<p>Before.</p><div class="w"><b>CONSERVE</b></div><div class="w"><br/>\n(</div>'
        '<div class="w"><i>Sufficient to Serve Six</i></div><div class="w">)\n\n</div>'
        "<p>After.</p>"
    )
    assert _texts(body) == ["Before.", "CONSERVE\n(Sufficient to Serve Six)", "After."]


def test_inline_divs_in_a_box_join_until_a_block():
    body = (
        '<div><div class="w">one</div><div class="w"> two</div><p>para</p>'
        '<div class="w">three</div></div>'
    )
    assert _texts(body) == ["one two", "para", "three"]


def test_inline_paragraphs_headings_and_inline_blocks_join():
    body = (
        '<p class="w">first p</p><p class="w">second p</p><h3 class="w">heading</h3>'
        '<p>plain</p><div class="ib">a</div><div class="ib">b</div>'
    )
    assert _texts(body) == ["first psecond pheading", "plain", "ab"]


def test_an_inline_div_in_a_list_item_or_a_cell_joins():
    assert _texts(
        '<ul><li><div class="w">a</div><div class="w"> b</div></li></ul>'
    ) == ["a b"]
    blocks = _conv.extract_blocks_from_html(
        _root(
            '<table><tr><td><div class="w">c</div><div class="w"> d</div></td>'
            "<td>other</td></tr></table>"
        ),
        style_resolver=_resolver(CSS),
        native_tables=True,
    )
    (table,) = blocks
    cells = [c["text"] for row in table["table"]["rows"] for c in row["cells"]]
    assert cells == ["c d", "other"]


def test_an_inline_box_with_side_padding_or_a_width_stays_a_block():
    # Hindawi verse: two half-lines, inline-block with `padding: 0 10px`.
    # Previewer pads each in a run of its own, which kfxgen doesn't write;
    # joined bare they ran together as one word.
    css = {
        **CSS,
        "half": {
            "display": "inline-block",
            "padding-left": "10px",
            "padding-right": "10px",
        },
        "wide": {"display": "inline-block", "width": "45%"},
    }
    blocks = _conv.extract_blocks_from_html(
        _root(
            '<div><div class="half">one</div><div class="half">two</div></div>'
            '<div><div class="wide">three</div><div class="wide">four</div></div>'
        ),
        style_resolver=_resolver(css),
    )
    assert [b["text"] for b in blocks] == ["one", "two", "three", "four"]


def test_nested_inline_divs_join():
    body = '<div class="w"><div class="w">a</div> b</div><div class="w"> c</div>'
    assert _texts(body) == ["a b c"]


def test_an_inline_div_holding_a_block_breaks_round_it():
    assert _texts('<div class="w">before <p>inner</p> after</div><p>next</p>') == [
        "before",
        "inner",
        "after",
        "next",
    ]
    # In a box too (P780K K01-K03).
    body = '<div><div class="w">before <p>inner</p> after</div></div>'
    assert _texts(body) == ["before", "inner", "after"]


def test_an_inline_div_of_line_breaks_alone_breaks_the_paragraph():
    # P780J J07-J08: Previewer writes "title" and "text" apart, with space.
    body = '<div class="w">title</div><div class="w"><br/>\n</div><div class="w">text</div>'
    assert _texts(body) == ["title", "text"]


def test_an_inline_div_of_a_non_breaking_space_stays_a_spacer():
    # pg23784 puts `<div class="pg_body_wrapper"><a id>&#160;</a></div>`
    # before each chapter heading; Previewer keeps space there (4.25lh
    # above "CHAPTER II"), as kfxgen's spacer does.
    body = '<p>end.</p><div class="w"><a id="x">&#160;</a></div><p>CHAPTER II</p>'
    blocks = _conv.extract_blocks_from_html(_root(body), style_resolver=_resolver(CSS))
    assert [b["text"] for b in blocks] == ["end.", "CHAPTER II"]
    # The spacer's line, above the heading; none without it.
    assert blocks[1]["margin_above"] > 0
    without = _conv.extract_blocks_from_html(
        _root("<p>end.</p><p>CHAPTER II</p>"), style_resolver=_resolver(CSS)
    )
    assert without[1]["margin_above"] == 0


def test_an_id_on_an_inline_div_lands_at_its_offset():
    body = (
        '<div class="w" id="t1">one</div><div class="w"> two <span id="t2">x</span></div>'
        '<p><a href="#t2">go</a></p>'
    )
    blocks = _conv.extract_blocks_from_html(
        _root(body),
        style_resolver=_resolver(CSS),
        base_href="c.xhtml",
        link_targets={"c.xhtml#t1", "c.xhtml#t2"},
    )
    joined = blocks[0]
    assert joined["text"] == "one two x"
    assert joined["anchor_offsets"]["c.xhtml#t1"] == 0
    assert joined["anchor_offsets"]["c.xhtml#t2"] == 8


def test_the_tree_keeps_its_tags():
    # The tree is calibre's: a Stylizer built on it later must still find
    # its <div>s and <p>s.
    root = _root('<div class="w">a</div><p class="w">b</p><h3 class="w">c</h3>')
    before = [e.tag for e in root.iter()]
    _conv.extract_blocks_from_html(root, style_resolver=_resolver(CSS))
    assert [e.tag for e in root.iter()] == before


def test_a_block_div_and_no_resolver_are_unchanged():
    assert _texts('<div>one</div><div class="x">two</div>') == ["one", "two"]
    blocks = _conv.extract_blocks_from_html(
        _root('<div class="w">one</div><div class="w">two</div>')
    )
    assert [b["text"] for b in blocks] == ["one", "two"]
