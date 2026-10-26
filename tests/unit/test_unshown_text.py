"""Text a browser doesn't show isn't printed (#477): a <template>'s content
and an <iframe>'s fallback children. A <select>'s options are separate
words, not run together. Their ids keep their place, as hidden text's do."""

import pytest
from lxml import etree

from kfxgen import converter as _conv


def _blocks(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )


def _texts(body):
    return [b["text"] for b in _blocks(body)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "texts"),
    [
        ("<p>A</p><template><p>M01 template text</p></template><p>B</p>", ["A", "B"]),
        ("<p>A <template>M02 inline</template>B</p>", ["A B"]),
        ('<p>A <iframe src="f.xhtml">M06 iframe fallback</iframe> B</p>', ["A B"]),
        ('<p>A</p><iframe src="f.xhtml"><p>M07 block fallback</p></iframe>', ["A"]),
        (
            "<p>Pick <select><option>M09o option one</option>"
            "<option>M09p option two</option></select></p>",
            ["Pick M09o option one M09p option two"],
        ),
    ],
    ids=[
        "template-block",
        "template-inline",
        "iframe-inline",
        "iframe-block",
        "options",
    ],
)
def test_unshown_text_is_left_out(body, texts):
    assert _texts(body) == texts


@pytest.mark.unit
def test_an_id_in_a_template_keeps_its_place():
    blocks = _blocks('<p>A <template><span id="t">x</span></template>B</p>')
    assert "c.xhtml#t" in blocks[0]["anchor_keys"]
