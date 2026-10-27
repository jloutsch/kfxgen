"""An id after a paragraph's last word lands on its last character, as
Kindle Previewer 4.0.3 writes it: offset len - 1, where kfxgen wrote len,
one past the text (#487). Amazon's offsets fall inside the paragraph
(#79)."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_link_landings import _link


@pytest.mark.unit
def test_an_id_after_the_last_word_lands_on_the_last_character(tmp_path):
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<p>Eighteen chars ok.<a id="end"/></p><p>Next.</p></body></html>'
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )
    out = tmp_path / "e.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [
            {"title": "One", "text": "x", "blocks": blocks},
            {"title": "Two", "text": "x", "blocks": [_link("L1", "c.xhtml#end")]},
        ],
        output_path=str(out),
    )
    offsets = [
        int(val(f)["$183"].get("$143", 0) or 0)
        for f in by_type(load_fragments(out), "$266")
    ]
    assert len("Eighteen chars ok.") == 18
    assert 17 in offsets
    assert 18 not in offsets
