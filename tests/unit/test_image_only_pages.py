import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen import converter as _conv  # noqa: E402
from kfxgen._img_tokens import IMG_TOKEN_RE  # noqa: E402
from kfxgen.converter import _assemble_chapters_by_coordinate  # noqa: E402
from tests._helpers import NullLog  # noqa: E402

pytestmark = pytest.mark.unit


def _page(href, img):
    """A spine item that is one image and nothing else."""
    token = _conv._make_img_token(img, "")
    return {
        "href": href,
        "text": token,
        "blocks": [{"text": token, "spans": [], "block_style": None, "anchor_ids": []}],
    }


def _prose(href, text):
    return {
        "href": href,
        "text": text,
        "blocks": [{"text": text, "spans": [], "block_style": None, "anchor_ids": []}],
    }


class TestImageOnlyPagesShip:
    """A fixed-layout comic with a chapter-level TOC lost every page after
    the last entry — four of seven in the oracle book — as 'image-only
    orphans'. The rule existed for one page: the EPUB's own cover.xhtml."""

    def test_tail_pages_after_the_last_toc_entry_ship(self):
        spine = [
            _page("p49.xhtml", "../images/p49.jpg"),
            _page("p50.xhtml", "../images/p50.jpg"),
            _page("back.xhtml", "../images/back.jpg"),
        ]
        toc = [{"title": "Chapter", "href": "p49.xhtml"}]
        chapters = _assemble_chapters_by_coordinate(
            spine, toc, NullLog(), cover_href="images/cover.jpg"
        )
        assert [c["title"] for c in chapters] == ["Chapter", "p50", "back"]
        # Unlisted in the nav, as the publisher had it (#143) — but shipped.
        assert all(c.get("_omit_from_toc") for c in chapters[1:])
        assert all(IMG_TOKEN_RE.search(c["text"]) for c in chapters)

    def test_a_page_showing_only_the_cover_is_still_dropped(self):
        spine = [
            _prose("ch.xhtml", "Prose."),
            _page("cover.xhtml", "../images/cover.jpg"),
        ]
        toc = [{"title": "Chapter", "href": "ch.xhtml"}]
        chapters = _assemble_chapters_by_coordinate(
            spine, toc, NullLog(), cover_href="OEBPS/images/cover.jpg"
        )
        assert [c["title"] for c in chapters] == ["Chapter"]

    def test_a_toc_listed_cover_page_is_not_a_blank_chapter(self):
        # Publishers list "Cover" in the nav. The page holds only the cover
        # image, which the generator can resolve only through the cover
        # chapter (#32), so as a chapter it would be a blank page with a nav
        # entry pointing at it.
        spine = [
            _page("cover.xhtml", "../images/cover.jpg"),
            _prose("ch.xhtml", "Prose."),
        ]
        toc = [
            {"title": "Cover", "href": "cover.xhtml"},
            {"title": "Chapter", "href": "ch.xhtml"},
        ]
        chapters = _assemble_chapters_by_coordinate(
            spine, toc, NullLog(), cover_href="images/cover.jpg"
        )
        assert [c["title"] for c in chapters] == ["Chapter"]

    def test_a_cover_page_with_more_art_folded_in_stays(self):
        # The title-page art between "Cover" and the first chapter folds
        # into the Cover slice; that slice now shows something.
        spine = [
            _page("cover.xhtml", "../images/cover.jpg"),
            _page("title.xhtml", "../images/title.jpg"),
            _prose("ch.xhtml", "Prose."),
        ]
        toc = [
            {"title": "Cover", "href": "cover.xhtml"},
            {"title": "Chapter", "href": "ch.xhtml"},
        ]
        chapters = _assemble_chapters_by_coordinate(
            spine, toc, NullLog(), cover_href="images/cover.jpg"
        )
        assert [c["title"] for c in chapters] == ["Cover", "Chapter"]

    def test_head_pages_before_the_first_toc_entry_ship(self):
        spine = [
            _page("title.xhtml", "../images/title.jpg"),
            _prose("ch.xhtml", "Prose."),
        ]
        toc = [{"title": "Chapter", "href": "ch.xhtml"}]
        chapters = _assemble_chapters_by_coordinate(
            spine, toc, NullLog(), cover_href="images/cover.jpg"
        )
        titles = [c["title"] for c in chapters]
        assert titles == [_conv.LEADING_TITLE_FALLBACK, "Chapter"]
        assert chapters[0].get("_omit_from_toc") is True
        assert chapters[0].get("_omit_title_heading") is True
        assert IMG_TOKEN_RE.search(chapters[0]["text"])

    def test_without_a_known_cover_every_image_counts(self):
        token = _conv._make_img_token("cover.jpg", "")
        assert _conv._page_shows_something(token)
        assert not _conv._page_shows_something(token, cover_href="x/cover.jpg")
        assert _conv._page_shows_something(token, cover_href="x/other.jpg")
