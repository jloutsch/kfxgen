import os
import sys
from types import SimpleNamespace

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.converter import extract_chapters_from_oeb  # noqa: E402
from tests._helpers import NullLog  # noqa: E402

pytestmark = pytest.mark.unit

_XHTML_BODY = ".//{http://www.w3.org/1999/xhtml}body"


def _doc(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return etree.fromstring(xhtml.encode())


class _Node:
    """A TOC node as calibre exposes it: title, href, iterable children."""

    def __init__(self, title, href):
        self.title, self.href = title, href

    def __iter__(self):
        return iter(())


def _item(href, doc):
    return SimpleNamespace(href=href, data=doc, media_type="application/xhtml+xml")


def _listing():
    return _doc(
        '<h2>Table of Contents</h2><ul><li><a href="p1.xhtml">Page 1</a></li></ul>'
    )


class TestCalibreInlineToc:
    """calibre's MOBI/AZW3 output appends a generated contents page. Converted
    on, it was rebuilt as kfxgen's contents page — 93 "Page N" links at the
    end of a comic. KFX has a nav pane; the page is skipped."""

    def test_generated_inline_toc_is_skipped(self):
        listing = _listing()
        listing.find(_XHTML_BODY).set("id", "calibre_generated_inline_toc")
        spine = [
            _item("p1.xhtml", _doc("<p>Page one prose.</p>")),
            _item("toc.xhtml", listing),
        ]
        oeb = SimpleNamespace(
            spine=spine, toc=[_Node("Page 1", "p1.xhtml")], manifest=None
        )
        chapters = extract_chapters_from_oeb(oeb, NullLog())
        assert [c["title"] for c in chapters] == ["Page 1"]
        assert not any("Table of Contents" in c["text"] for c in chapters)

    def test_a_publisher_contents_page_is_not_mistaken_for_it(self):
        # Same markup without calibre's stamp: the existing contents-page
        # handling applies, and the listing still reaches the reader.
        spine = [
            _item("p1.xhtml", _doc("<p>Page one prose.</p>")),
            _item("toc.xhtml", _listing()),
        ]
        oeb = SimpleNamespace(
            spine=spine, toc=[_Node("Page 1", "p1.xhtml")], manifest=None
        )
        chapters = extract_chapters_from_oeb(oeb, NullLog())
        assert len(chapters) == 2
