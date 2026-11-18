"""A file nested past any real book keeps its text (#794).

The block walk recurses about four calls a level; 248 nested <div>s raised
RecursionError, and the spine loop left the whole file out. In a file
nested more than 200 levels deep, plain wrappers holding one element are
now unwrapped past 100 levels; nothing else changes, so siblings, list
items, cells, ruby text, scripts and hidden elements read as they did
(QA on #798). Kindle Previewer 4.0.3 writes nested <div>s as one paragraph
at any depth (P794). The calibre case is in
tests/integration/test_calibre_deep_nesting.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from tests._helpers import NullLog

pytestmark = pytest.mark.unit

XH = "http://www.w3.org/1999/xhtml"
CHAIN = 300  # past _NEST_FLATTEN_PAST, under calibre's own cut (~256 + body)


class _Log(NullLog):
    def __init__(self):
        self.warnings = []

    def warn(self, msg, *a, **k):
        self.warnings.append(msg)


def _doc(body):
    return etree.fromstring(
        f'<html xmlns="{XH}"><body>{body}</body></html>',
        etree.XMLParser(huge_tree=True),
    )


def _depth(root):
    deepest, stack = 0, [(root, 0)]
    while stack:
        e, d = stack.pop()
        deepest = max(deepest, d)
        stack.extend((c, d + 1) for c in e if isinstance(c.tag, str))
    return deepest


def _resolver(elem):
    tag = etree.QName(elem).localname
    if elem.get("class") == "h" or tag in ("script", "style"):
        # As calibre's resolver gives them, from a browser's own rules.
        return {"display": "none"}
    if elem.get("class") == "pb":
        return {"display": "block", "page-break-before": "always"}
    if elem.get("class") == "m":
        return {"display": "block", "margin-left": "3em"}
    if elem.get("class") == "b":
        return {"border-sides": [("solid", 1, "#000000")] * 4, "display": "block"}
    inline = ("span", "em", "a", "ruby", "rt", "rp")
    return {"display": "inline"} if tag in inline else {"display": "block"}


def _walk(body, levels=CHAIN, open_="<div>"):
    """`body` under `levels` plain wrappers, flattened and walked: (each
    block's type, text, space above and page break, warnings)."""
    root = _doc(open_ * levels + body + "</div>" * levels)
    log = _Log()
    _conv._flatten_deep_nesting(root, log, "c.xhtml", _resolver)
    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=_resolver, base_href="c.xhtml", native_tables=True
    )
    return [
        (
            b.get("type"),
            b.get("text") or "",
            b.get("space_above"),
            b.get("break_before"),
        )
        for b in blocks
    ], log.warnings


@pytest.mark.parametrize(
    "open_, levels",
    [("<div>", 1000), ("<blockquote>", CHAIN)],
    ids=["div", "blockquote"],
)
def test_a_chain_of_wrappers_past_the_walks_depth_keeps_its_text(open_, levels):
    close = "</blockquote>" if open_ == "<blockquote>" else "</div>"
    root = _doc(open_ * levels + "<p>DEEPWORD here.</p>" + close * levels)
    log = _Log()
    _conv._flatten_deep_nesting(root, log, "c.xhtml", _resolver)
    assert _depth(root) <= _conv._NEST_LIMIT + 1
    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=_resolver, base_href="c.xhtml", native_tables=True
    )
    assert "DEEPWORD here." in [b.get("text") for b in blocks]
    assert len(log.warnings) == 1 and "c.xhtml" in log.warnings[0]


#: Shapes under the chain that the walk reads one way at 5 levels, untouched,
#: as on main; flattened at 300 levels they must read the same (QA on #798).
SHAPES = {
    "divs": "<div>Alpha</div><div>Beta</div>",
    "list": "<ul><li>One</li><li>Two</li></ul>",
    "definitions": "<dl><dt>Term</dt><dd>Meaning</dd></dl>",
    "cells": "<table><tr><td>CellA</td><td>CellB</td></tr></table>",
    "rule": "<p>Before</p><hr/><p>After</p><br/>",
    "inline-beside": "<div><span>InA</span></div><span>InC</span>",
    "inline-in-wrapper": "<div><div><span>InA</span></div><span>InC</span></div>",
    "words-beside": "<div><p>Inner</p></div>Loose words",
    "display-none": '<div class="h"><p>HIDDENONE</p></div><p>Shown.</p>',
    "hidden-chain": '<div class="h"><div><p>HIDDENTWO</p></div></div><p>Shown.</p>',
    "script": "<script>var SCRIPTWORD = 1;</script><p>Shown.</p>",
    "ruby": "<p><ruby>Kan<rt>kan</rt>ji<rt>ji</rt></ruby></p>",
    "empty": "<div></div><p>Shown.</p>",
    "inline-words-beside": "<div><span>InA</span></div>Loose words",
    "hidden-alone": '<div><div class="h"><p>HIDDENTHREE</p></div></div><p>Shown.</p>',
    # An empty element's page break marks the next block (#363).
    "break-alone": '<p>Top</p><div><div class="pb"></div></div><p>Bottom</p>',
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_shapes_past_the_limit_read_as_they_do_shallow(shape):
    shallow, warnings = _walk(SHAPES[shape], levels=5)
    assert warnings == []
    deep, warnings = _walk(SHAPES[shape])
    assert len(warnings) == 1
    assert deep == shallow


def test_text_keeps_its_order():
    # Every 50th wrapper holds words of its own, so it stays.
    body = (
        "".join(f"<div>a{i} " if i % 50 == 0 else "<div>" for i in range(CHAIN))
        + "<p>mid</p>"
    )
    body += "".join(
        f"</div>z{i} " if i % 50 == 0 else "</div>" for i in reversed(range(CHAIN))
    )
    root = _doc(body)
    before = " ".join("".join(root.itertext()).split())
    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml")
    assert " ".join("".join(root.itertext()).split()) == before
    assert _depth(root) < 120


def test_an_unwrapped_wrappers_id_stays():
    root = _doc(
        "<div>" * CHAIN
        + '<div id="target"><p>P <a href="#x">link</a></p></div>'
        + "</div>" * CHAIN
    )
    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml")
    (target,) = root.xpath("//*[@id='target']")
    assert target.tag == f"{{{XH}}}a"
    assert root.xpath("//*[local-name()='a'][@href='#x']")


@pytest.mark.parametrize("levels", [60, 150, 190])
def test_a_page_within_the_limit_is_untouched(levels):
    # Siblings under 150 wrappers ran together when everything past 100
    # was unwrapped (QA on #798); main reads them right to about 240.
    root = _doc(
        "<div>" * levels
        + '<div>Alpha</div><div class="h">Hidden</div><p id="x">W.</p>'
        + "</div>" * levels
    )
    before = etree.tostring(root)
    log = _Log()
    _conv._flatten_deep_nesting(root, log, "c.xhtml", _resolver)
    assert etree.tostring(root) == before
    assert log.warnings == []


@pytest.mark.parametrize("tag", ["math", "svg"])
def test_a_drawing_or_equation_is_untouched(tag):
    # ziamath's own budget makes a deep equation text (#406); unwrapping its
    # rows ran them together (the slow suite's deep equation, on #794).
    ns = {
        "math": "http://www.w3.org/1998/Math/MathML",
        "svg": "http://www.w3.org/2000/svg",
    }
    inner = "<g>" * 250 + "<text>S</text>" + "</g>" * 250
    if tag == "math":
        inner = "<mrow>" * 250 + "<mn>1</mn>" + "</mrow>" * 250
    root = _doc(f'<p><{tag} xmlns="{ns[tag]}">{inner}</{tag}></p>')
    before = etree.tostring(root)
    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml")
    assert etree.tostring(root) == before


def test_nested_lists_and_tables_are_left_as_they_are():
    # Not wrappers: unwrapping them would change what they are. Past about
    # 250 elements the walk still fails on them, as before.
    root = _doc("<ul><li>" * 120 + "W." + "</li></ul>" * 120)
    before = etree.tostring(root)
    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml")
    assert etree.tostring(root) == before


@pytest.mark.parametrize(
    "body, kept",
    [
        # QA's X01: 230 bordered containers on main and in Previewer.
        ('<div class="b">' * 230 + "<p>W.</p>" + "</div>" * 230, 230),
        # QA's X07: a margin on div 151 of 230.
        (
            "<div>" * 150
            + '<div class="m">'
            + "<div>" * 79
            + "<p>W.</p>"
            + "</div>" * 230,
            1,
        ),
    ],
    ids=["borders", "margin"],
)
def test_a_wrapper_with_a_box_of_its_own_is_kept(body, kept):
    root = _doc(body)
    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml", _resolver)
    assert len(root.xpath("//*[@class='b' or @class='m']")) == kept


@pytest.mark.parametrize(
    "css",
    [{"padding-top": "2px"}, {"margin-bottom": "auto"}, {"background-color": "red"}],
)
def test_padding_auto_margins_and_fills_are_boxes_too(css):
    root = _doc("<div>" * 230 + '<div class="x"><p>W.</p></div>' + "</div>" * 230)

    def resolver(elem):
        return dict(css) if elem.get("class") == "x" else {}

    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml", resolver)
    assert root.xpath("//*[@class='x']")


def test_zero_margins_are_no_box():
    root = _doc("<div>" * 230 + '<div class="x"><p>W.</p></div>' + "</div>" * 230)

    def resolver(elem):
        return {f"margin-{s}": "0px" for s in ("top", "right", "bottom", "left")}

    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml", resolver)
    assert not root.xpath("//*[@class='x']")
