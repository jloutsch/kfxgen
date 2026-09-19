"""A page stored as GIF reaches the reader, re-encoded as PNG (#177).

kfxgen emits two image formats: `$285` JPEG and `$284` PNG. Anything else was
dropped at extraction with a warning, which is correct for a stray decorative
image and catastrophic for a book whose every page is one. Four books in a
226-EPUB library store their page scans as GIF and convert with the text intact
and no pictures at all.

GIF has a symbol — `$286`, named in the generator's own format comment and
listed among upstream kfxlib's fixed-layout formats — so passing one through is
conceivable. It is not what happens here, deliberately: nothing emits `$286`
today and no device has been asked whether it would render one. PNG is lossless
exactly as GIF is, so the re-encode discards nothing the original still had, and
it is a format this project has watched work on hardware.

The conversion runs through Calibre's `scale_image`, so outside Calibre — which
means here, and CI — it cannot happen. These tests inject a fake `calibre` the
way `test_image_optimize.py` does, and one of them asserts the real behaviour
when that module is genuinely absent: fall back to the skip that was there
before, rather than emit something broken.
"""

from __future__ import annotations

import sys
import types

import pytest

from kfxgen.converter import extract_images_from_oeb
from kfxgen.image_optimize import GIF_SIGNATURES, _read_image_size, gif_to_png
from tests._helpers import NullLog, jpeg_of

pytestmark = [pytest.mark.tier1, pytest.mark.unit]

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _gif(width: int, height: int) -> bytes:
    """A GIF87a header stating its size, padded past the converter's 100-byte
    floor. Only the header is read — nothing here decodes pixels."""
    return (
        b"GIF87a"
        + width.to_bytes(2, "little")
        + height.to_bytes(2, "little")
        + b"\x00\x00\x00"
        + b"\x00" * 120
    )


def _fake_calibre(monkeypatch, scale_image):
    fake = types.ModuleType("calibre.utils.img")
    fake.scale_image = scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)


class TestGifDimensions:
    def test_a_gif_states_its_size_in_its_header(self):
        """Little-endian at offset 6. Needed before the re-encode, so the
        converted page keeps the size it had."""
        assert _read_image_size(_gif(800, 1280)) == (800, 1280)

    @pytest.mark.parametrize("sig", GIF_SIGNATURES)
    def test_both_gif_versions_are_recognised(self, sig):
        data = (
            sig
            + (640).to_bytes(2, "little")
            + (480).to_bytes(2, "little")
            + b"\x00" * 100
        )
        assert _read_image_size(data) == (640, 480)

    def test_a_zero_dimension_gif_is_not_a_size(self):
        """Guards the re-encode: `scale_image(width=0)` is not a sensible call."""
        assert _read_image_size(_gif(0, 1280)) is None


class TestGifConversion:
    def test_a_gif_becomes_a_png_at_its_own_size(self, monkeypatch):
        seen = {}

        def scale_image(data, width, height, as_png=False, compression_quality=90):
            seen.update(width=width, height=height, as_png=as_png)
            return (width, height, PNG_SIG + b"converted")

        _fake_calibre(monkeypatch, scale_image)
        out = gif_to_png(_gif(800, 1280))
        assert out is not None and out[:8] == PNG_SIG
        assert seen == {"width": 800, "height": 1280, "as_png": True}, (
            "the re-encode must ask for the image's own dimensions — downscaling "
            "is `optimize_image`'s decision and stays there"
        )

    def test_without_calibre_it_declines_rather_than_guesses(self):
        """The real state of this test run, and of CI.

        Deliberately not monkeypatched: if this ever starts returning bytes,
        something is re-encoding images outside Calibre and the caller's
        fallback has stopped being exercised anywhere.
        """
        assert gif_to_png(_gif(800, 1280)) is None

    def test_a_failing_re_encode_declines(self, monkeypatch):
        def scale_image(*a, **k):
            raise RuntimeError("no decoder")

        _fake_calibre(monkeypatch, scale_image)
        assert gif_to_png(_gif(800, 1280)) is None

    def test_output_that_is_not_a_png_is_refused(self, monkeypatch):
        """A silent format surprise would reach the generator as `$284` PNG and
        be wrong in the container rather than here."""

        def scale_image(data, width, height, as_png=False, compression_quality=90):
            return (width, height, b"\xff\xd8\xff" + b"jpeg actually")

        _fake_calibre(monkeypatch, scale_image)
        assert gif_to_png(_gif(800, 1280)) is None

    def test_an_unreadable_gif_declines(self, monkeypatch):
        def scale_image(*a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("should not attempt to re-encode a sizeless GIF")

        _fake_calibre(monkeypatch, scale_image)
        assert gif_to_png(b"GIF89a" + b"\x00" * 4) is None


class _Item:
    def __init__(self, href, media_type, data):
        self.href, self.media_type, self.data = href, media_type, data


class _Book:
    def __init__(self, items):
        self.manifest = items


class TestTheExtractorUsesIt:
    """The helper working is not the same as the extraction path calling it."""

    def _book(self):
        return _Book(
            [
                _Item("images/p1.gif", "image/gif", _gif(800, 1280)),
                _Item("images/p2.jpg", "image/jpeg", jpeg_of(800, 1280)),
                _Item("images/bad.tif", "image/tiff", b"II*\x00" + b"\x00" * 200),
            ]
        )

    def test_a_gif_page_is_extracted_as_png(self, monkeypatch):
        def scale_image(data, width, height, as_png=False, compression_quality=90):
            return (width, height, PNG_SIG + b"converted")

        _fake_calibre(monkeypatch, scale_image)
        images = extract_images_from_oeb(self._book(), NullLog())
        assert set(images) == {"images/p1.gif", "images/p2.jpg"}, (
            "the GIF must survive extraction and the TIFF must not"
        )
        assert images["images/p1.gif"][:8] == PNG_SIG, (
            "it is carried as PNG bytes — the generator emits $284 for it and "
            "nothing downstream re-reads the manifest's media type"
        )

    def test_without_calibre_the_gif_is_skipped_as_before(self):
        """No conversion available means the old behaviour, not a broken image."""
        images = extract_images_from_oeb(self._book(), NullLog())
        assert set(images) == {"images/p2.jpg"}

    def test_a_format_that_is_not_gif_is_still_unsupported(self, monkeypatch):
        def scale_image(*a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("only GIF is converted")

        _fake_calibre(monkeypatch, scale_image)
        images = extract_images_from_oeb(
            _Book([_Item("x.tif", "image/tiff", b"II*\x00" + b"\x00" * 200)]),
            NullLog(),
        )
        assert images == {}
