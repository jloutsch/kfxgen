import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen._img_tokens import IMG_TOKEN_RE  # noqa: E402
from kfxgen.converter import extract_blocks_from_html  # noqa: E402

pytestmark = pytest.mark.unit


def _doc(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return etree.fromstring(xhtml.encode())


def _tokens(blocks):
    """(href, alt, size) of every image token, in document order."""
    return [m.groups() for b in blocks for m in IMG_TOKEN_RE.finditer(b["text"])]


_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 390 625">'
    '<image width="390" height="625" xlink:href="../images/p49.jpg"/></svg>'
)


class TestSvgWrappedImage:
    def test_svg_page_is_an_image_block(self):
        # A publisher's cover.xhtml, and calibre's own titlepage.xhtml.
        blocks = extract_blocks_from_html(_doc(f"<div>{_SVG}</div>"))
        assert _tokens(blocks) == [("../images/p49.jpg", "", "h=100%")]

    def test_svg_inside_a_paragraph(self):
        blocks = extract_blocks_from_html(_doc(f"<p>{_SVG}</p>"))
        assert _tokens(blocks) == [("../images/p49.jpg", "", "h=100%")]

    def test_svg_directly_under_body(self):
        blocks = extract_blocks_from_html(_doc(_SVG))
        assert _tokens(blocks) == [("../images/p49.jpg", "", "h=100%")]

    def test_svg_beside_text_keeps_both(self):
        blocks = extract_blocks_from_html(_doc(f"<div><p>Caption</p>{_SVG}</div>"))
        assert [IMG_TOKEN_RE.sub("", b["text"]) for b in blocks] == ["Caption", ""]
        assert len(_tokens(blocks)) == 1

    def test_href_without_xlink_is_read_too(self):
        svg = _SVG.replace("xlink:href", "href")
        blocks = extract_blocks_from_html(_doc(f"<div>{svg}</div>"))
        assert _tokens(blocks) == [("../images/p49.jpg", "", "h=100%")]
