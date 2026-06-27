"""
Unit tests for EpubAsOeb — duck-typed Calibre-OEB wrapper feeding
plugin/kfxgen/converter.py from a built .epub.
"""

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from tests.fixtures.epub_builder import EpubBuilder
from tests.fixtures.oeb_shim import EpubAsOeb


@pytest.fixture
def simple_epub(tmp_path: Path) -> Path:
    return (
        EpubBuilder()
        .set_metadata(title="My Title", author="Jane Doe", language="en")
        .add_chapter("Ch1", "Hello.")
        .add_chapter("Ch2", "World.")
        .build(tmp_path, "simple")
    )


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubAsOebMetadata:
    def test_title_creator_language(self, simple_epub):
        oeb = EpubAsOeb(simple_epub)
        assert str(oeb.metadata.title[0]) == "My Title"
        assert str(oeb.metadata.creator[0]) == "Jane Doe"
        assert str(oeb.metadata.language[0]) == "en"

    def test_metadata_attrs_are_lists_for_calibre_compat(self, simple_epub):
        oeb = EpubAsOeb(simple_epub)
        # Calibre's `oeb_book.metadata.title[0]` access pattern requires
        # the attribute to be subscriptable.
        assert isinstance(oeb.metadata.title, list)
        assert isinstance(oeb.metadata.creator, list)

    def test_publisher_absent_returns_empty_list(self, simple_epub):
        # converter.py:177 reads metadata.publisher[0] hasattr-guarded.
        # When absent in the OPF, must be a (falsy) empty list, not None.
        oeb = EpubAsOeb(simple_epub)
        assert oeb.metadata.publisher == []


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubAsOebManifest:
    def test_manifest_iterable_yields_chapters(self, simple_epub):
        oeb = EpubAsOeb(simple_epub)
        items = list(oeb.manifest)
        hrefs = [it.href for it in items]
        assert "chapter_1.xhtml" in hrefs
        assert "chapter_2.xhtml" in hrefs

    def test_manifest_hrefs_dict_lookup(self, simple_epub):
        oeb = EpubAsOeb(simple_epub)
        item = oeb.manifest.hrefs.get("chapter_1.xhtml")
        assert item is not None
        assert item.href == "chapter_1.xhtml"

    def test_manifest_item_data_is_lazy_lxml_for_xhtml(self, simple_epub):
        # Calibre's OEB exposes `.data` as a parsed lxml Element for
        # XHTML/XML manifest items — converter.extract_text_from_html calls
        # `.find(".//{http://www.w3.org/1999/xhtml}body")` on it.
        oeb = EpubAsOeb(simple_epub)
        item = oeb.manifest.hrefs["chapter_1.xhtml"]
        data = item.data
        assert isinstance(data, etree._Element)
        serialized = etree.tostring(data)
        assert b"Hello." in serialized

    def test_manifest_item_data_bytes_returns_raw_bytes_for_xhtml(self, simple_epub):
        # data_bytes is the escape hatch for callers that want the unparsed
        # payload regardless of media type.
        oeb = EpubAsOeb(simple_epub)
        item = oeb.manifest.hrefs["chapter_1.xhtml"]
        raw = item.data_bytes
        assert isinstance(raw, bytes)
        assert b"Hello." in raw

    def test_non_xml_media_type_returns_raw_bytes(self, tmp_path: Path):
        # Hand-craft an EPUB with an image manifest entry to verify the
        # non-XML branch returns raw bytes (PR2 cover-image fixtures rely
        # on this).
        path = tmp_path / "img.epub"
        with zipfile.ZipFile(path, "w") as zf:
            zi = zipfile.ZipInfo("mimetype")
            zi.compress_type = zipfile.ZIP_STORED
            zf.writestr(zi, b"application/epub+zip")
            zf.writestr(
                "META-INF/container.xml",
                b'<?xml version="1.0"?>'
                b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                b'<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
                b"</container>",
            )
            zf.writestr(
                "OEBPS/content.opf",
                b'<?xml version="1.0"?>'
                b'<package version="2.0" xmlns="http://www.idpf.org/2007/opf">'
                b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>t</dc:title><dc:creator>a</dc:creator><dc:language>en</dc:language></metadata>'
                b'<manifest><item id="cover" href="cover.jpg" media-type="image/jpeg"/></manifest>'
                b"<spine/>"
                b"</package>",
            )
            zf.writestr("OEBPS/cover.jpg", b"\xff\xd8\xff\xe0NOT-A-REAL-JPEG")
        oeb = EpubAsOeb(path)
        item = oeb.manifest.hrefs["cover.jpg"]
        data = item.data
        assert isinstance(data, bytes)
        assert data.startswith(b"\xff\xd8\xff\xe0")
        # data_bytes returns the same raw payload for non-XML items too.
        assert item.data_bytes == data

    def test_manifest_item_media_type(self, simple_epub):
        oeb = EpubAsOeb(simple_epub)
        item = oeb.manifest.hrefs["chapter_1.xhtml"]
        assert "xhtml" in item.media_type


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubAsOebSpine:
    def test_spine_is_iterable_of_manifest_items_in_order(self, simple_epub):
        oeb = EpubAsOeb(simple_epub)
        spine_items = list(oeb.spine)
        assert len(spine_items) == 2
        assert spine_items[0].href == "chapter_1.xhtml"
        assert spine_items[1].href == "chapter_2.xhtml"

    def test_spine_supports_len(self, simple_epub):
        # converter.py:314 calls len(oeb_book.spine).
        oeb = EpubAsOeb(simple_epub)
        assert len(oeb.spine) == 2


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubAsOebPassesAttackerBytesThrough:
    """The shim deliberately does NOT clean up — its job is to feed
    converter.py the real attacker bytes. Verify three pass-throughs.
    """

    def test_invalid_utf8_xhtml_raises_on_data_access(self, tmp_path: Path):
        # Calibre's OEB also fails to parse malformed XHTML at load time —
        # the shim's `.data` property mirrors that contract by surfacing
        # lxml's parse error to the caller. Callers that want the raw
        # bytes anyway can use `.data_bytes`, which never parses.
        invalid = b"<html><body>\xff\xfe</body></html>"
        path = (
            EpubBuilder()
            .set_metadata(title="t", author="a")
            .add_chapter("Ch1", invalid)
            .build(tmp_path, "bad")
        )
        oeb = EpubAsOeb(path)
        item = oeb.manifest.hrefs["chapter_1.xhtml"]
        with pytest.raises((etree.XMLSyntaxError, ValueError, UnicodeDecodeError)):
            _ = item.data
        # data_bytes still surfaces the attacker bytes verbatim.
        assert item.data_bytes == invalid

    def test_missing_zip_entry_raises_on_data_access(self, tmp_path: Path, simple_epub):
        # Hand-craft an OPF that references a chapter file the zip doesn't have.
        # Easiest path: rebuild the simple_epub with the chapter entry deleted.
        broken = tmp_path / "broken.epub"
        with zipfile.ZipFile(simple_epub) as src, zipfile.ZipFile(broken, "w") as dst:
            for entry in src.infolist():
                if entry.filename == "OEBPS/chapter_1.xhtml":
                    continue
                dst.writestr(entry, src.read(entry.filename))
        oeb = EpubAsOeb(broken)
        item = oeb.manifest.hrefs["chapter_1.xhtml"]
        with pytest.raises(KeyError):
            _ = item.data


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubAsOebPathTraversalHrefPassthrough:
    """When PR2 ships path-traversal hrefs, the shim must surface them verbatim
    so `_normalize_href` is the layer that rejects. Verify by hand-crafting
    an OPF with `../` in a manifest href."""

    def test_traversal_href_returned_verbatim(self, tmp_path: Path):
        path = tmp_path / "trav.epub"
        with zipfile.ZipFile(path, "w") as zf:
            zi = zipfile.ZipInfo("mimetype")
            zi.compress_type = zipfile.ZIP_STORED
            zf.writestr(zi, b"application/epub+zip")
            zf.writestr(
                "META-INF/container.xml",
                b'<?xml version="1.0"?>'
                b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                b'<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
                b"</container>",
            )
            zf.writestr(
                "OEBPS/content.opf",
                b'<?xml version="1.0"?>'
                b'<package version="2.0" xmlns="http://www.idpf.org/2007/opf">'
                b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>t</dc:title><dc:creator>a</dc:creator><dc:language>en</dc:language></metadata>'
                b'<manifest><item id="evil" href="../../../etc/passwd" media-type="application/xhtml+xml"/></manifest>'
                b'<spine><itemref idref="evil"/></spine>'
                b"</package>",
            )
        oeb = EpubAsOeb(path)
        item = oeb.manifest.hrefs.get("../../../etc/passwd")
        assert item is not None
        assert item.href == "../../../etc/passwd"


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubAsOebMissingMetadata:
    """When the OPF tag is absent, _Metadata exposes an empty list
    (not None). Pins the empty-string-to-empty-list round-trip so a
    refactor of _dc_text doesn't accidentally surface None (#75 item 3)."""

    def test_missing_dc_date_returns_empty_list(self, tmp_path):
        # EpubBuilder doesn't emit <dc:date> by default — perfect.
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .build(tmp_path, "no_date")
        )
        oeb = EpubAsOeb(path)
        assert oeb.metadata.date == []
        assert oeb.metadata.publisher == []

    def test_missing_dc_date_metadata_attribute_is_subscriptable(self, tmp_path):
        # Calibre's access pattern is `if metadata.date: metadata.date[0]`
        # — the attribute must be a falsy iterable, not None.
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .build(tmp_path, "no_date2")
        )
        oeb = EpubAsOeb(path)
        if oeb.metadata.date:
            raise AssertionError("date should be falsy when absent")
        if oeb.metadata.publisher:
            raise AssertionError("publisher should be falsy when absent")
