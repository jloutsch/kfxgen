"""A file nested past any real book keeps its text (#794).

The block walk recurses about four calls a level; 248 nested <div>s raised
RecursionError, and the spine loop left the whole file out. Elements past
100 levels are now unwrapped before the walk. Kindle Previewer 4.0.3 writes
nested plain <div>s as one paragraph at any depth (P794), as kfxgen does.
The calibre end-to-end case is in tests/integration/test_calibre_deep_nesting.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from tests._helpers import NullLog

pytestmark = pytest.mark.unit

XH = "http://www.w3.org/1999/xhtml"


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


def _text(blocks):
    return " ".join(b.get("text") or "" for b in blocks)


def _resolver(elem):
    tag = etree.QName(elem).localname
    if elem.get("class") == "b":
        return {"border-sides": [("solid", 1, "#000000")] * 4, "display": "block"}
    return {"display": "inline"} if tag in ("span", "em", "a") else {"display": "block"}


@pytest.mark.parametrize(
    "open_, close, levels",
    [
        ("<div>", "</div>", 1000),
        ('<div class="b">', "</div>", 300),
        ("<blockquote>", "</blockquote>", 300),
        ("<ul><li>", "</li></ul>", 200),
        ("<table><tr><td>", "</td></tr></table>", 150),
    ],
    ids=["div", "bordered-div", "blockquote", "list", "table"],
)
def test_a_page_nested_past_the_walks_depth_keeps_its_text(open_, close, levels):
    root = _doc(open_ * levels + "<p>DEEPWORD here.</p>" + close * levels)
    log = _Log()
    _conv._flatten_deep_nesting(root, log, "c.xhtml")
    assert _depth(root) <= _conv._NEST_LIMIT + 1
    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=_resolver, base_href="c.xhtml", native_tables=True
    )
    assert "DEEPWORD" in _text(blocks)
    assert len(log.warnings) == 1 and "c.xhtml" in log.warnings[0]


def test_text_keeps_its_order():
    levels = 300
    body = "".join(f"a{i}<div>" for i in range(levels)) + "<p>mid</p>"
    body += "".join(f"</div>z{i}" for i in reversed(range(levels)))
    root = _doc(body)
    before = " ".join("".join(root.itertext()).split())
    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml")
    assert " ".join("".join(root.itertext()).split()) == before


def test_ids_pictures_links_and_drawings_stay():
    deep = "<div>" * 150
    shut = "</div>" * 150
    root = _doc(
        deep
        + '<div id="target"><p>P <a href="#x">link</a></p><img src="p.png" alt=""/>'
        + '<svg xmlns="http://www.w3.org/2000/svg"><g><text>S</text></g></svg></div>'
        + shut
    )
    _conv._flatten_deep_nesting(root, NullLog(), "c.xhtml")
    tags = [etree.QName(e).localname for e in root.iter() if isinstance(e.tag, str)]
    assert root.xpath("//*[@id='target']")[0].tag == f"{{{XH}}}a"
    for tag in ("p", "img", "svg", "g", "text"):
        assert tag in tags
    assert root.xpath("//*[local-name()='a'][@href='#x']")


def test_a_page_within_the_limit_is_untouched():
    root = _doc("<div>" * 60 + '<p id="x">W.</p>' + "</div>" * 60)
    before = etree.tostring(root)
    log = _Log()
    _conv._flatten_deep_nesting(root, log, "c.xhtml")
    assert etree.tostring(root) == before
    assert log.warnings == []


@pytest.mark.parametrize("tag", ["math", "svg"])
def test_a_drawing_or_equation_starting_above_the_limit_is_untouched(tag):
    # The equation starts at depth 3; its rows run past 100. ziamath's own
    # budget makes it text (#406); unwrapping its rows ran them together
    # (the slow suite's deep equation, on #794).
    ns = {
        "math": "http://www.w3.org/1998/Math/MathML",
        "svg": "http://www.w3.org/2000/svg",
    }
    inner = "<g>" * 150 + "<text>S</text>" + "</g>" * 150
    if tag == "math":
        inner = "<mrow>" * 150 + "<mn>1</mn>" + "</mrow>" * 150
    root = _doc(f'<p><{tag} xmlns="{ns[tag]}">{inner}</{tag}></p>')
    before = etree.tostring(root)
    log = _Log()
    _conv._flatten_deep_nesting(root, log, "c.xhtml")
    assert etree.tostring(root) == before
    assert log.warnings == []
