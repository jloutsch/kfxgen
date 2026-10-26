"""A link to an id inside a chapter's opening heading lands on the word
that holds it, as on main before #489 and in Kindle Previewer 4.0.3's file
(#491). The heading prints the block's text, so its ids keep their offsets
there, less any leading space; they went to the heading's start."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_link_landings import _link


def _blocks(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )


def _offset(tmp_path, label, body):
    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "Links", "text": "x", "blocks": [_link("L1", "c.xhtml#hx")]},
            {"title": label, "text": "x", "blocks": _blocks(body)},
        ],
        output_path=str(out),
    )
    offsets = [
        int(val(f)["$183"].get("$143", 0) or 0)
        for f in by_type(load_fragments(out), "$266")
    ]
    return offsets


@pytest.mark.unit
@pytest.mark.parametrize(
    "label", ["Chapter Nine", "Chapter Nine Begins"], ids=["differs", "equal"]
)
def test_an_id_in_the_heading_keeps_its_offset(tmp_path, label):
    body = '<h1>Chapter <span id="hx">Nine</span> Begins</h1><p>Body.</p>'
    assert 8 in _offset(tmp_path, label, body)
