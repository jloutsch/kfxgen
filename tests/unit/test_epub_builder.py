"""
Unit tests for the EpubBuilder helper used by the #49 integration corpus.
"""

import zipfile
from pathlib import Path

import pytest

from tests.fixtures.epub_builder import EpubBuilder


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubBuilderOcfStructure:
    """Builder must produce a valid OCF (zip) container."""

    def test_build_returns_path_to_real_zip(self, tmp_path: Path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .build(tmp_path, "minimal")
        )
        assert path.exists()
        assert path.suffix == ".epub"
        assert zipfile.is_zipfile(path)

    def test_mimetype_is_first_entry_and_uncompressed(self, tmp_path: Path):
        # OCF requirement: 'mimetype' must be the first zip entry, stored
        # uncompressed, contents == 'application/epub+zip'.
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .build(tmp_path, "minimal")
        )
        with zipfile.ZipFile(path) as zf:
            first = zf.infolist()[0]
            assert first.filename == "mimetype"
            assert first.compress_type == zipfile.ZIP_STORED
            assert zf.read("mimetype") == b"application/epub+zip"

    def test_container_xml_points_at_opf(self, tmp_path: Path):
        path = (
            EpubBuilder().set_metadata(title="T", author="A").build(tmp_path, "minimal")
        )
        with zipfile.ZipFile(path) as zf:
            container = zf.read("META-INF/container.xml").decode("utf-8")
        assert "OEBPS/content.opf" in container
        assert "application/oebps-package+xml" in container

    def test_zero_chapters_produces_valid_zip_with_empty_spine(self, tmp_path: Path):
        # Zero chapters is a valid construction — it's the input the
        # zero_chapters fixture relies on. The OPF spine must be empty.
        path = EpubBuilder().set_metadata(title="Z", author="A").build(tmp_path, "zero")
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "<spine" in opf
        # No <itemref> entries.
        assert "itemref" not in opf

    def test_every_manifest_href_resolves_to_a_zip_entry(self, tmp_path: Path):
        # Catches the dangling-toc.ncx class of bug: any manifest item
        # whose href does not exist in the zip is invalid OPF 2.0.
        import re

        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .add_chapter("Ch2", "More.")
            .build(tmp_path, "resolves")
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            zip_entries = set(zf.namelist())
        # Find every manifest href.
        hrefs = re.findall(r'<item [^>]*href="([^"]+)"', opf)
        assert hrefs, "Test setup error: no manifest items in OPF"
        unresolved = []
        for href in hrefs:
            # OPF hrefs are relative to the OPF's directory (OEBPS/).
            arcname = f"OEBPS/{href}"
            if arcname not in zip_entries:
                unresolved.append((href, arcname))
        assert not unresolved, (
            f"Manifest hrefs that do not resolve to zip entries: {unresolved}"
        )


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubBuilderChapterEncoding:
    """Chapter body handling: str gets escaped + wrapped, bytes go verbatim."""

    def test_str_body_is_html_escaped(self, tmp_path: Path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "<script>alert(1)</script>")
            .build(tmp_path, "esc")
        )
        with zipfile.ZipFile(path) as zf:
            body = zf.read("OEBPS/chapter_1.xhtml").decode("utf-8")
        # Escaped, not raw — script tag must not be active markup.
        assert "&lt;script&gt;" in body
        assert "<script>" not in body

    def test_str_body_wrapped_in_minimal_xhtml(self, tmp_path: Path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Hello.")
            .build(tmp_path, "wrap")
        )
        with zipfile.ZipFile(path) as zf:
            body = zf.read("OEBPS/chapter_1.xhtml").decode("utf-8")
        assert "<html" in body
        assert "<body" in body
        assert "Hello." in body

    def test_bytes_body_written_verbatim(self, tmp_path: Path):
        # The non_utf8 fixture relies on this: invalid bytes must reach the
        # zip entry without being decoded/re-encoded.
        invalid = b"<html><body>\xff\xfe\x00not-utf8</body></html>"
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", invalid)
            .build(tmp_path, "verbatim")
        )
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("OEBPS/chapter_1.xhtml")
        assert raw == invalid

    def test_chapter_files_indexed_from_one(self, tmp_path: Path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "A.")
            .add_chapter("Ch2", "B.")
            .build(tmp_path, "two")
        )
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
        assert "OEBPS/chapter_1.xhtml" in names
        assert "OEBPS/chapter_2.xhtml" in names
        assert "OEBPS/chapter_0.xhtml" not in names


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubBuilderMetadata:
    """OPF metadata is required — title and creator must round-trip."""

    def test_metadata_appears_in_opf(self, tmp_path: Path):
        path = (
            EpubBuilder()
            .set_metadata(title="My Title", author="Jane Doe", language="en")
            .add_chapter("Ch1", "Body.")
            .build(tmp_path, "meta")
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "My Title" in opf
        assert "Jane Doe" in opf
        assert "<dc:language>en</dc:language>" in opf or 'xml:lang="en"' in opf


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubBuilderCover:
    """set_cover writes the cover bytes + OPF manifest entry + meta linkage.
    declare_only=True skips the zip write but keeps the OPF entries —
    this is the missing_cover.epub fixture's path."""

    def test_set_cover_writes_image_to_zip(self, tmp_path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .set_cover(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
            .build(tmp_path, "with_cover")
        )
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert "OEBPS/cover.jpg" in names
            assert zf.read("OEBPS/cover.jpg") == b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    def test_set_cover_writes_opf_manifest_entry(self, tmp_path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .set_cover(b"\xff\xd8\xff\xe0fake")
            .build(tmp_path, "with_cover")
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert 'id="cover-image"' in opf
        assert 'href="cover.jpg"' in opf
        assert 'media-type="image/jpeg"' in opf

    def test_set_cover_writes_meta_cover_linkage(self, tmp_path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .set_cover(b"\xff\xd8\xff\xe0fake")
            .build(tmp_path, "with_cover")
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert 'name="cover"' in opf
        assert 'content="cover-image"' in opf

    def test_set_cover_declare_only_skips_zip_write(self, tmp_path):
        # missing_cover.epub fixture: cover declared but not in zip.
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .set_cover(b"\xff\xd8\xff\xe0fake", declare_only=True)
            .build(tmp_path, "missing_cover")
        )
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "OEBPS/cover.jpg" not in names  # NOT in zip
        assert 'href="cover.jpg"' in opf  # but declared in OPF

    def test_set_cover_custom_href_and_media_type(self, tmp_path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .set_cover(
                b"\x89PNG\r\n\x1a\nfake",
                media_type="image/png",
                href="images/my_cover.png",
            )
            .build(tmp_path, "png_cover")
        )
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "OEBPS/images/my_cover.png" in names
        assert 'href="images/my_cover.png"' in opf
        assert 'media-type="image/png"' in opf


@pytest.mark.tier1
@pytest.mark.unit
class TestEpubBuilderAddManifestItem:
    """Lower-level escape hatch for fixtures that need raw control of
    the manifest entry shape. data=None skips zip write; in_spine=True
    appends a spine itemref."""

    def test_add_manifest_item_writes_data_to_zip(self, tmp_path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_manifest_item(
                item_id="evil",
                href="../../../etc/passwd",
                media_type="application/xhtml+xml",
                data=b"<html><body>evil</body></html>",
            )
            .build(tmp_path, "trav")
        )
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        # OPF references the malicious href verbatim:
        assert 'href="../../../etc/passwd"' in opf
        assert 'id="evil"' in opf
        # And the data was written somewhere in the zip:
        # zip path normalization may collapse the .. segments;
        # we don't depend on exact zip-internal path here, just that
        # SOMETHING got written. Calibre's safe-zipfile would strip
        # these segments at extraction time (per SECURITY.md).
        assert any("evil" in n or "passwd" in n for n in names)

    def test_add_manifest_item_data_none_skips_zip_write(self, tmp_path):
        # Declare two items, only zip-write one.
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .add_manifest_item(
                item_id="declared_only",
                href="OEBPS/missing.xhtml",
                media_type="application/xhtml+xml",
                data=None,
            )
            .build(tmp_path, "decl_only")
        )
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert 'id="declared_only"' in opf
        assert 'href="OEBPS/missing.xhtml"' in opf
        assert "OEBPS/missing.xhtml" not in names

    def test_add_manifest_item_in_spine_true_adds_spine_entry(self, tmp_path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_manifest_item(
                item_id="evil",
                href="../../../etc/passwd",
                media_type="application/xhtml+xml",
                data=b"<html/>",
                in_spine=True,
            )
            .build(tmp_path, "trav_spine")
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert '<itemref idref="evil"/>' in opf

    def test_add_manifest_item_in_spine_default_false(self, tmp_path):
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_chapter("Ch1", "Body.")
            .add_manifest_item(
                item_id="extra",
                href="extra.xhtml",
                media_type="application/xhtml+xml",
                data=b"<html/>",
            )
            .build(tmp_path, "extra_no_spine")
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        # extra is in manifest but NOT in spine
        assert 'id="extra"' in opf
        assert '<itemref idref="extra"/>' not in opf

    def test_two_manifest_items_with_same_basename(self, tmp_path):
        # duplicate_basename.epub fixture's shape.
        path = (
            EpubBuilder()
            .set_metadata(title="T", author="A")
            .add_manifest_item(
                item_id="ch1",
                href="chapter1/intro.xhtml",
                media_type="application/xhtml+xml",
                data=b"<html><body>chapter 1 intro</body></html>",
                in_spine=True,
            )
            .add_manifest_item(
                item_id="ch2",
                href="chapter2/intro.xhtml",
                media_type="application/xhtml+xml",
                data=b"<html><body>chapter 2 intro</body></html>",
                in_spine=True,
            )
            .build(tmp_path, "dup_base")
        )
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "OEBPS/chapter1/intro.xhtml" in names
        assert "OEBPS/chapter2/intro.xhtml" in names
        assert 'id="ch1"' in opf
        assert 'id="ch2"' in opf
        assert '<itemref idref="ch1"/>' in opf
        assert '<itemref idref="ch2"/>' in opf
