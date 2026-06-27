"""
Tier-1 unit tests for cover-image magic-byte validation (#46).

Threat: Calibre identifies cover images by manifest media-type alone. An EPUB
declaring cover.jpg whose bytes are actually a zip file, an HTML page, or a
truncated/corrupt blob will pass that label-only check unchallenged. Before
this fix, those bytes flowed straight into the binary KFX serializer, which
emitted them as $285 (JPEG) regardless and let Kindle silently reject or
crash.

Two layers of defense:
  - converter.py:extract_cover_image rejects at source (returns None).
  - native_generator.py:generate_full_book raises if garbage somehow reaches
    the cover-emit branch (defense-in-depth).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.converter import extract_cover_image
from kfxgen.native_generator import NativeKFXGenerator


# Minimum size gate is 100 bytes (`len(data) > 100`); pad fixtures past that.
_PAD = b"\x00" * 200
_VALID_JPEG = b"\xff\xd8\xff\xe0" + _PAD
_VALID_PNG = b"\x89PNG\r\n\x1a\n" + _PAD


def _make_item(data, item_id="cover-img", href="cover.jpg", media_type="image/jpeg"):
    item = MagicMock()
    item.id = item_id
    item.href = href
    item.media_type = media_type
    item.data = data
    return item


def _book_via_metadata(
    data, item_id="cover-img", href="cover.jpg", media_type="image/jpeg"
):
    """Method 1: metadata.cover -> manifest item ID."""
    book = MagicMock()
    item = _make_item(data, item_id=item_id, href=href, media_type=media_type)
    book.metadata.cover = [item_id]
    book.manifest = MagicMock()
    book.manifest.__iter__ = lambda self: iter([item])
    book.manifest.hrefs = {href: item}
    book.guide = []
    return book


def _book_via_guide(data, item_id="some-id", href="cover.jpg", media_type="image/jpeg"):
    """Method 2: guide entry with type='cover' -> href -> manifest.hrefs."""
    book = MagicMock()
    item = _make_item(data, item_id=item_id, href=href, media_type=media_type)
    # Method 1 must miss: no metadata.cover.
    book.metadata.cover = None
    book.manifest = MagicMock()
    book.manifest.__iter__ = lambda self: iter([item])
    book.manifest.hrefs = {href: item}
    guide_ref = MagicMock()
    guide_ref.type = "cover"
    guide_ref.href = href
    book.guide = [guide_ref]
    return book


def _book_via_manifest_scan(
    data, item_id="cover-image", href="images/cover.jpg", media_type="image/jpeg"
):
    """Method 3: scan manifest for items with 'cover' in id/href + image type."""
    book = MagicMock()
    item = _make_item(data, item_id=item_id, href=href, media_type=media_type)
    # Method 1 must miss: no metadata.cover.
    book.metadata.cover = None
    book.manifest = MagicMock()
    book.manifest.__iter__ = lambda self: iter([item])
    book.manifest.hrefs = {href: item}
    # Method 2 must miss: no guide entry of type 'cover'.
    book.guide = []
    return book


# Each builder constructs a duck-typed OEB book whose cover is discoverable
# only via the specified path. Parametrizing tests over this list ensures
# every discovery path enforces the same magic-byte invariant.
_DISCOVERY_PATHS = [
    pytest.param(_book_via_metadata, id="method1-metadata"),
    pytest.param(_book_via_guide, id="method2-guide"),
    pytest.param(_book_via_manifest_scan, id="method3-manifest-scan"),
]


# Backwards-compat alias for tests that don't need to vary the discovery path.
_book_with_manifest_item = _book_via_metadata


@pytest.mark.tier1
@pytest.mark.unit
class TestExtractCoverAccepts:
    @pytest.mark.parametrize("builder", _DISCOVERY_PATHS)
    def test_valid_jpeg(self, builder):
        book = builder(_VALID_JPEG)
        log = MagicMock()
        data, href = extract_cover_image(book, log)
        assert data == _VALID_JPEG
        assert href  # discovery path may report any href, but must report one

    @pytest.mark.parametrize("builder", _DISCOVERY_PATHS)
    def test_valid_png(self, builder):
        book = builder(_VALID_PNG, href="cover.png", media_type="image/png")
        log = MagicMock()
        data, href = extract_cover_image(book, log)
        assert data == _VALID_PNG
        assert href


@pytest.mark.tier1
@pytest.mark.unit
class TestExtractCoverRejects:
    @pytest.mark.parametrize("builder", _DISCOVERY_PATHS)
    def test_zip_bytes_labeled_as_jpeg(self, builder):
        # Adversarial: manifest media-type says JPEG, bytes are PK\x03\x04 zip.
        zip_payload = b"PK\x03\x04" + _PAD
        book = builder(zip_payload)
        log = MagicMock()
        data, href = extract_cover_image(book, log)
        assert data is None and href is None
        # Warning logged on the converter's `log` argument (Calibre channel).
        assert any(
            "Skipping cover candidate" in str(c) for c in log.warn.call_args_list
        )

    @pytest.mark.parametrize("builder", _DISCOVERY_PATHS)
    def test_zero_byte_cover(self, builder):
        book = builder(b"")
        log = MagicMock()
        data, href = extract_cover_image(book, log)
        assert data is None and href is None

    @pytest.mark.parametrize("builder", _DISCOVERY_PATHS)
    def test_truncated_below_size_gate(self, builder):
        # 80 bytes — below the 100-byte size gate, rejected silently before
        # magic-byte check.
        book = builder(b"\xff\xd8\xff" + b"\x00" * 80)
        log = MagicMock()
        data, href = extract_cover_image(book, log)
        assert data is None and href is None

    @pytest.mark.parametrize("builder", _DISCOVERY_PATHS)
    def test_html_disguised_as_jpeg(self, builder):
        html = b"<!DOCTYPE html><html><body>not an image</body></html>" + _PAD
        book = builder(html)
        log = MagicMock()
        data, href = extract_cover_image(book, log)
        assert data is None and href is None


@pytest.mark.tier1
@pytest.mark.unit
class TestExtractCoverRejectsUnsupportedFormats:
    """Only JPEG and PNG are emitted as KFX cover resources. Every other
    image format the wider web supports must be explicitly rejected here so
    a future loosening of the magic-byte gate can't silently let WebP/GIF/
    HEIF/AVIF/BMP bytes through to the binary serializer."""

    @pytest.mark.parametrize(
        "fmt_name,header",
        [
            ("webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
            ("gif", b"GIF89a"),
            ("bmp", b"BM\x00\x00\x00\x00"),
            ("heif", b"\x00\x00\x00\x18ftypheic"),
            ("avif", b"\x00\x00\x00\x18ftypavif"),
        ],
    )
    @pytest.mark.parametrize("builder", _DISCOVERY_PATHS)
    def test_unsupported_format_rejected(self, builder, fmt_name, header):
        payload = header + _PAD
        book = builder(payload)
        log = MagicMock()
        data, href = extract_cover_image(book, log)
        assert data is None and href is None, (
            f"{fmt_name} payload should be rejected but was returned"
        )


@pytest.mark.tier1
@pytest.mark.unit
class TestGeneratorDefenseInDepth:
    """Even if the converter-side validator is bypassed, the generator must
    refuse rather than mislabel garbage as JPEG (#46 fallback hardening)."""

    def test_generator_raises_on_unrecognized_cover(self):
        gen = NativeKFXGenerator()
        garbage = b"PK\x03\x04" + b"\x00" * 200  # zip prefix
        with pytest.raises(ValueError, match="neither JPEG nor PNG"):
            gen.generate_full_book(
                title="t",
                author="a",
                chapters=[{"title": "c1", "text": "x" * 200}],
                cover_image=garbage,
            )

    def test_generator_accepts_valid_jpeg_cover(self):
        # Sanity: defense doesn't break the happy path.
        gen = NativeKFXGenerator()
        data = gen.generate_full_book(
            title="t",
            author="a",
            chapters=[{"title": "c1", "text": "x" * 200}],
            cover_image=_VALID_JPEG,
        )
        assert isinstance(data, bytes) and len(data) > 0
