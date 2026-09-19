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


class TestNonRenderedSvgContainers:
    """SVG paints nothing inside <defs>, <symbol>, <mask>, <clipPath>,
    <pattern> or <marker> — an <image> there is a definition, drawn only where
    a <use> references it. Walking into them painted a picture the publisher
    hid, and embedded the resource, which #102 then could not prune because an
    entry did display it."""

    def _page(self, inner):
        """An XHTML page wrapping an <svg> — the form this PR reads."""
        return _doc(
            '<div><svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink">{inner}</svg></div>'
        )

    @pytest.mark.parametrize(
        "container", ["defs", "symbol", "mask", "clipPath", "pattern", "marker"]
    )
    def test_an_image_inside_a_definition_is_not_painted(self, container):
        doc = self._page(
            f'<{container}><image xlink:href="hidden.jpg"/></{container}>'
            '<image xlink:href="drawn.jpg"/>'
        )
        assert [h for h, _a, _s in _tokens(extract_blocks_from_html(doc))] == [
            "drawn.jpg"
        ]

    def test_a_definition_nested_deeper_is_still_skipped(self):
        doc = self._page(
            '<g><defs><g><image xlink:href="hidden.jpg"/></g></defs></g>'
            '<image xlink:href="drawn.jpg"/>'
        )
        assert [h for h, _a, _s in _tokens(extract_blocks_from_html(doc))] == [
            "drawn.jpg"
        ]

    def test_a_painted_container_is_still_walked(self):
        # <g> and <a> paint their children; only the list above does not.
        doc = self._page('<g><a><image xlink:href="drawn.jpg"/></a></g>')
        assert [h for h, _a, _s in _tokens(extract_blocks_from_html(doc))] == [
            "drawn.jpg"
        ]

    def test_a_page_whose_only_image_is_a_definition_draws_nothing(self):
        doc = self._page('<defs><image xlink:href="hidden.jpg"/></defs>')
        assert extract_blocks_from_html(doc) == []


_SVG_DOC = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1200 1600">'
    '<image width="1200" height="1600" xlink:href="page01.jpg"/></svg>'
)


def _svg_doc(markup=_SVG_DOC):
    """A spine document that IS an SVG — no <body> anywhere in it."""
    return etree.fromstring(markup.encode())


class TestRootedSvgDocument:
    """#165: a spine document whose media type is image/svg+xml. The page is
    an SVG file, `extract_blocks_from_html` walks from <body>, and an SVG
    document has none — so every image it drew was lost."""

    def test_a_rooted_svg_page_yields_its_image(self):
        blocks = extract_blocks_from_html(_svg_doc())
        assert _tokens(blocks) == [("page01.jpg", "", "h=100%")]

    def test_several_images_in_one_rooted_document(self):
        markup = _SVG_DOC.replace(
            "</svg>",
            '<image width="10" height="10" xlink:href="stamp.png"/></svg>',
        )
        assert [
            h for h, _a, _s in _tokens(extract_blocks_from_html(_svg_doc(markup)))
        ] == [
            "page01.jpg",
            "stamp.png",
        ]

    def test_a_rooted_svg_drawing_nothing_yields_nothing(self):
        markup = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect width="10" height="10"/></svg>'
        )
        assert extract_blocks_from_html(_svg_doc(markup)) == []

    def test_an_xhtml_page_is_still_walked_as_before(self):
        # The control: the nested form must not change behaviour.
        blocks = extract_blocks_from_html(_doc(f"<p>Caption</p><div>{_SVG}</div>"))
        assert [IMG_TOKEN_RE.sub("", b["text"]) for b in blocks] == ["Caption", ""]


class TestRootedSvgEmitsOnlyPictures:
    """An SVG document's text nodes are the stylesheet, the RDF the drawing
    program left behind, the ids inside <defs> — never reading content. The
    flat-text fallback scooped all of it into the body: 1,894 characters of
    CSS from the cover of the IDPF/epub3-samples book, 220 of RDF from two of
    its pages."""

    def _rooted(self, inner):
        return etree.fromstring(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
            f"{inner}</svg>".encode()
        )

    def test_a_stylesheet_does_not_reach_the_reader(self):
        doc = self._rooted("<style>.st0{fill:#AAB2AB;}</style><path d='M0 0'/>")
        assert extract_blocks_from_html(doc) == []

    def test_metadata_does_not_reach_the_reader(self):
        doc = self._rooted("<metadata>Created with a drawing program</metadata>")
        assert extract_blocks_from_html(doc) == []

    def test_a_page_that_draws_a_bitmap_emits_only_the_bitmap(self):
        doc = self._rooted(
            "<style>.st0{fill:#000;}</style>"
            '<image width="10" height="10" xlink:href="p.jpg"/>'
            "<desc>a crane</desc>"
        )
        blocks = extract_blocks_from_html(doc)
        assert _tokens(blocks) == [("p.jpg", "", "h=100%")]
        assert IMG_TOKEN_RE.sub("", blocks[0]["text"]).strip() == ""

    def test_an_xhtml_page_still_falls_back_to_flat_text(self):
        # The control: the fallback is only suppressed for SVG documents.
        doc = etree.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "Loose text with no block element around it."
            "</body></html>".encode()
        )
        blocks = extract_blocks_from_html(doc)
        assert len(blocks) == 1
        assert "Loose text" in blocks[0]["text"]
