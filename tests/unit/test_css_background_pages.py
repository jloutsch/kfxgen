"""Pages drawn as a CSS background layer (#168).

Some print-to-EPUB chains put the scanned page in a `background-image` on an
empty div and position the text over it. There is no `<img>` and no `<svg>`,
so the page reached the reader with no picture at all — and a page-scan book
with no text overlay reached it as a failed conversion, every spine item
being empty.

The rule is deliberately the narrowest that covers that shape: the risk runs
the other way, since emitting for every `background-image` would add pictures
to books that convert correctly today. These tests pin both directions.
"""

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen._img_tokens import IMG_TOKEN_RE  # noqa: E402
from kfxgen.converter import (  # noqa: E402
    _css_background_image,
    extract_blocks_from_html,
)

pytestmark = pytest.mark.unit

PAGE_CSS = {
    "background-image": "url(images/00008.gif)",
    "background-repeat": "no-repeat",
}


def _doc(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return etree.fromstring(xhtml.encode())


def _tokens(blocks):
    return [m.groups() for b in blocks for m in IMG_TOKEN_RE.finditer(b["text"])]


def _resolver(css_by_class):
    """Stands in for Calibre's Stylizer, which exists only inside Calibre."""

    def resolve(elem):
        return dict(css_by_class.get(elem.get("class") or "", {}))

    return resolve


class TestThePageIsDrawn:
    """The shape from the issue: an empty div carrying the scan, text
    absolutely positioned over it in a sibling."""

    PAGE = '<div class="page"><div class="pimg"></div></div>'

    def test_the_background_becomes_an_image(self):
        blocks = extract_blocks_from_html(
            _doc(self.PAGE), style_resolver=_resolver({"pimg": PAGE_CSS})
        )
        assert _tokens(blocks) == [("images/00008.gif", "", None)]

    def test_the_box_size_is_carried_like_an_img(self):
        css = dict(PAGE_CSS, width="800px", height="1280px")
        blocks = extract_blocks_from_html(
            _doc(self.PAGE), style_resolver=_resolver({"pimg": css})
        )
        assert _tokens(blocks) == [("images/00008.gif", "", "w=800px")]

    def test_text_over_the_scan_survives_beside_it(self):
        body = (
            '<div class="page"><div class="pimg"></div>'
            '<p class="ptxt">Dear Curtis,</p></div>'
        )
        blocks = extract_blocks_from_html(
            _doc(body), style_resolver=_resolver({"pimg": PAGE_CSS})
        )
        assert [IMG_TOKEN_RE.sub("", b["text"]).strip() for b in blocks] == [
            "",
            "Dear Curtis,",
        ]

    def test_quoted_urls_are_read(self):
        for value in ("url('a b.jpg')", 'url("x/y.png")', "url( z.jpg )"):
            css = dict(PAGE_CSS, **{"background-image": value})
            blocks = extract_blocks_from_html(
                _doc(self.PAGE), style_resolver=_resolver({"pimg": css})
            )
            assert len(_tokens(blocks)) == 1, value


class TestNothingElseIsDrawn:
    """The risk side. Each of these is a background that is decoration, and
    emitting it would put a picture into a book that is correct today."""

    def _fires(self, css, body='<div class="x"></div>'):
        blocks = extract_blocks_from_html(
            _doc(body), style_resolver=_resolver({"x": css})
        )
        return bool(_tokens(blocks))

    def test_a_tiled_background_is_a_texture(self):
        assert not self._fires(dict(PAGE_CSS, **{"background-repeat": "repeat"}))

    def test_an_unstated_repeat_is_not_assumed(self):
        assert not self._fires({"background-image": "url(p.jpg)"})

    def test_a_box_with_text_of_its_own_is_decorated_not_drawn(self):
        assert not self._fires(PAGE_CSS, body='<div class="x">Chapter One</div>')

    def test_a_box_with_children_is_decorated_not_drawn(self):
        assert not self._fires(PAGE_CSS, body='<div class="x"><p>Body.</p></div>')

    def test_no_url_no_image(self):
        assert not self._fires(
            {"background-image": "none", "background-repeat": "no-repeat"}
        )

    def test_without_a_stylizer_nothing_changes(self):
        # Outside Calibre there is no resolver, so the feature is simply off.
        blocks = extract_blocks_from_html(_doc('<div class="x"></div>'))
        assert blocks == []


class TestTheRuleItself:
    def test_each_condition_is_required(self):
        elem = etree.fromstring(b"<div/>")
        assert _css_background_image(elem, PAGE_CSS) == "images/00008.gif"
        assert _css_background_image(elem, None) is None
        assert _css_background_image(elem, {}) is None
        assert (
            _css_background_image(
                elem, dict(PAGE_CSS, **{"background-repeat": "repeat-y"})
            )
            is None
        )

    def test_children_and_text_both_disqualify(self):
        assert (
            _css_background_image(etree.fromstring(b"<div>x</div>"), PAGE_CSS) is None
        )
        assert (
            _css_background_image(etree.fromstring(b"<div><i/></div>"), PAGE_CSS)
            is None
        )
        # A tail belongs to the parent, not to this element.
        parent = etree.fromstring(b"<div><span/>tail</div>")
        assert _css_background_image(parent[0], PAGE_CSS) == "images/00008.gif"
