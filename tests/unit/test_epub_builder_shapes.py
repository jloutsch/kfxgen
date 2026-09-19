"""The page-image shapes `EpubBuilder` can construct.

These assert what the builder *emits*, not what the converter does with it —
so they hold on any branch, and a behaviour test elsewhere can rely on the
fixture being the shape it claims to be.

The shapes come from measuring a 226-EPUB library of illustrated children's
books, where 202 books have spine documents the navigation never lists, 104
wrap art in `<svg>`, and 94 state a size on the `<img>`.
"""

import os
import sys
import zipfile
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tests._helpers import jpeg_of  # noqa: E402
from tests.fixtures.epub_builder import (  # noqa: E402
    CALIBRE_INLINE_TOC_BODY_ID,
    EpubBuilder,
)

pytestmark = pytest.mark.unit

OPF = "{http://www.idpf.org/2007/opf}"
NCX = "{http://www.daisy.org/z3986/2005/ncx/}"
XHTML = "{http://www.w3.org/1999/xhtml}"
SVG = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}"


def _open(built):
    return zipfile.ZipFile(built)


def _opf(z):
    return ET.fromstring(z.read("OEBPS/content.opf"))


def _spine(z):
    """Spine hrefs, in reading order."""
    opf = _opf(z)
    href = {i.get("id"): i.get("href") for i in opf.iter(f"{OPF}item")}
    return [href[r.get("idref")] for r in opf.iter(f"{OPF}itemref")]


def _nav(z):
    """Hrefs the NCX points at."""
    ncx = ET.fromstring(z.read("OEBPS/toc.ncx"))
    return [c.get("src") for c in ncx.iter(f"{NCX}content")]


def _doc(z, href):
    return ET.fromstring(z.read(f"OEBPS/{href}"))


class TestUnlistedDocuments:
    """`listed=False` is the shape a chapter-level TOC gives a page-image
    book: every page between two entries is in the spine and in no nav."""

    def test_unlisted_pages_are_in_the_spine_and_not_the_nav(self, tmp_path):
        built = (
            EpubBuilder()
            .set_metadata(title="Unlisted", author="A")
            .add_chapter("Chapter One", "Body.")
            .add_image_page("Plate", image=jpeg_of(390, 625), listed=False)
            .add_image_page("Plate", image=jpeg_of(390, 625), listed=False)
            .build(tmp_path, "unlisted")
        )
        with _open(built) as z:
            spine, nav = _spine(z), _nav(z)
        assert len(spine) == 3
        assert nav == ["chapter_1.xhtml"]
        assert set(spine) - set(nav) == {"page_2.xhtml", "page_3.xhtml"}

    def test_everything_listed_by_default(self, tmp_path):
        built = (
            EpubBuilder()
            .add_chapter("One", "Body.")
            .add_image_page("Plate", image=jpeg_of(390, 625))
            .build(tmp_path, "listed")
        )
        with _open(built) as z:
            assert _nav(z) == _spine(z)


class TestImagePageMarkup:
    IMAGE = jpeg_of(390, 625)

    def test_plain_img_page_has_one_image_and_no_text(self, tmp_path):
        built = (
            EpubBuilder()
            .add_image_page("Plate", image=self.IMAGE, alt="Image")
            .build(tmp_path, "img")
        )
        with _open(built) as z:
            root = _doc(z, "page_1.xhtml")
        imgs = list(root.iter(f"{XHTML}img"))
        assert len(imgs) == 1
        assert imgs[0].get("alt") == "Image"
        # No text on the page — the <title> in the head is not page content.
        body = root.find(f"{XHTML}body")
        assert "".join(body.itertext()).strip() == ""

    def test_attrs_and_style_land_on_the_img(self, tmp_path):
        built = (
            EpubBuilder()
            .add_image_page(
                "Plate", image=self.IMAGE, attrs={"height": "98%"}, style="width:100%"
            )
            .build(tmp_path, "sized")
        )
        with _open(built) as z:
            img = next(_doc(z, "page_1.xhtml").iter(f"{XHTML}img"))
        assert img.get("height") == "98%"
        assert img.get("style") == "width:100%"

    def test_svg_wrapper_nests_an_image_inside_the_page(self, tmp_path):
        built = (
            EpubBuilder()
            .add_image_page("Cover art", image=self.IMAGE, wrapper="svg")
            .build(tmp_path, "svg")
        )
        with _open(built) as z:
            root = _doc(z, "page_1.xhtml")
        assert root.tag == f"{XHTML}html"
        image = next(root.iter(f"{SVG}image"))
        assert image.get(XLINK + "href").endswith(".jpg")
        # The viewBox carries the image's real dimensions.
        assert next(root.iter(f"{SVG}svg")).get("viewBox") == "0 0 390 625"

    def test_svg_root_page_is_an_svg_document(self, tmp_path):
        built = (
            EpubBuilder()
            .add_image_page("Page", image=self.IMAGE, wrapper="svg-root")
            .build(tmp_path, "svgroot")
        )
        with _open(built) as z:
            spine = _spine(z)
            root = _doc(z, spine[0])
            media = [
                i.get("media-type")
                for i in _opf(z).iter(f"{OPF}item")
                if i.get("href") == spine[0]
            ]
        assert spine == ["page_1.svg"]
        assert media == ["image/svg+xml"]
        assert root.tag == f"{SVG}svg"
        assert root.find(f"{XHTML}body") is None
        assert len(list(root.iter(f"{SVG}image"))) == 1

    def test_an_unknown_wrapper_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            EpubBuilder().add_image_page("x", image=self.IMAGE, wrapper="canvas")

    def test_a_page_can_show_the_cover_and_the_bytes_are_not_duplicated(self, tmp_path):
        cover = jpeg_of(1495, 2200)
        built = (
            EpubBuilder()
            .set_cover(cover, href="cover.jpg")
            .add_image_page("Cover", image=cover, href="cover.jpg")
            .add_chapter("One", "Body.")
            .build(tmp_path, "coverpage")
        )
        with _open(built) as z:
            img = next(_doc(z, "page_1.xhtml").iter(f"{XHTML}img"))
            names = z.namelist()
            hrefs = [i.get("href") for i in _opf(z).iter(f"{OPF}item")]
        assert img.get("src") == "cover.jpg"
        assert names.count("OEBPS/cover.jpg") == 1
        assert hrefs.count("cover.jpg") == 1


class TestCalibreInlineToc:
    def test_it_carries_calibres_body_id_and_links_every_document(self, tmp_path):
        built = (
            EpubBuilder()
            .add_chapter("One", "Body.")
            .add_chapter("Two", "Body.")
            .add_calibre_inline_toc()
            .build(tmp_path, "inlinetoc")
        )
        with _open(built) as z:
            spine, nav = _spine(z), _nav(z)
            root = _doc(z, "inline_toc_3.xhtml")
        assert spine[-1] == "inline_toc_3.xhtml"
        assert "inline_toc_3.xhtml" not in nav  # calibre keeps it out of the NCX
        assert root.find(f"{XHTML}body").get("id") == CALIBRE_INLINE_TOC_BODY_ID
        links = [a.get("href") for a in root.iter(f"{XHTML}a")]
        assert links == ["chapter_1.xhtml", "chapter_2.xhtml"]


class TestFixedLayoutMetadata:
    def test_declared_pre_paginated(self, tmp_path):
        built = (
            EpubBuilder()
            .set_fixed_layout(width=390, height=625)
            .add_image_page("Page", image=jpeg_of(390, 625))
            .build(tmp_path, "fxl")
        )
        with _open(built) as z:
            metas = list(_opf(z).iter(f"{OPF}meta"))
        assert any(
            m.get("property") == "rendition:layout" and m.text == "pre-paginated"
            for m in metas
        )
        assert any(
            m.get("name") == "fixed-layout" and m.get("content") == "true"
            for m in metas
        )
        assert any(m.get("content") == "390x625" for m in metas)

    def test_absent_by_default(self, tmp_path):
        built = EpubBuilder().add_chapter("One", "Body.").build(tmp_path, "reflow")
        with _open(built) as z:
            assert "rendition:layout" not in z.read("OEBPS/content.opf").decode()


class TestChapterOnlyBooksAreUnchanged:
    """The existing corpus is chapters only, and 13 goldens are compared
    byte-for-byte. Document numbering counts every spine document, so a book
    with no image pages is named exactly as it was."""

    def test_filenames_and_nav_are_the_historical_ones(self, tmp_path):
        built = (
            EpubBuilder()
            .set_metadata(title="Minimal Golden", author="Golden Author")
            .add_chapter("Chapter One", "First.\n\nSecond.")
            .add_chapter("Chapter Two", "Third.")
            .build(tmp_path, "minimal")
        )
        with _open(built) as z:
            assert _spine(z) == ["chapter_1.xhtml", "chapter_2.xhtml"]
            assert _nav(z) == ["chapter_1.xhtml", "chapter_2.xhtml"]
            ncx = z.read("OEBPS/toc.ncx").decode()
        assert 'id="np1" playOrder="1"' in ncx
        assert 'id="np2" playOrder="2"' in ncx
