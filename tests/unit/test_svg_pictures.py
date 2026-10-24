"""An SVG file used as a picture is rasterised to PNG at extraction (#438).

calibre hands an SVG manifest item over parsed (an lxml element), not as
bytes, so the extractor's bytes check dropped every one without a warning.
The rasterising is calibre's own `rasterize_svg` (Qt), so outside calibre
these tests inject a fake module, as `test_gif_pages.py` does; the real
conversion is `tests/integration/test_calibre_svg_img.py`.
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from kfxgen.converter import extract_images_from_oeb
from kfxgen.image_optimize import svg_to_png

pytestmark = [pytest.mark.unit]

PNG_SIG = b"\x89PNG\r\n\x1a\n"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100"/></svg>'


class _Parsed:
    """What calibre's manifest holds for an SVG: a parsed tree, with the
    file's bytes on `bytes_representation`."""

    def __init__(self, href, data):
        self.href, self.media_type = href, "image/svg+xml"
        self.data = object()  # an lxml element in calibre: not bytes
        self.bytes_representation = data


class _Book:
    def __init__(self, items):
        self.manifest = items


def _fake_rasterizer(monkeypatch, rasterize_svg):
    fake = types.ModuleType("calibre.ebooks.oeb.transforms.rasterize")
    fake.rasterize_svg = rasterize_svg
    for name in (
        "calibre",
        "calibre.ebooks",
        "calibre.ebooks.oeb",
        "calibre.ebooks.oeb.transforms",
    ):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, fake.__name__, fake)


def test_an_svg_picture_is_extracted_as_png(monkeypatch):
    seen = []

    def rasterize_svg(data=None, **kw):
        seen.append((data, kw))
        return PNG_SIG + b"x" * 200

    _fake_rasterizer(monkeypatch, rasterize_svg)
    images = extract_images_from_oeb(_Book([_Parsed("a.svg", SVG)]), mock.MagicMock())
    assert images["a.svg"][:8] == PNG_SIG
    # The file's own bytes, at its own size: no width or height asked for.
    assert seen == [(SVG, {})]


def test_an_excluded_svg_is_not_extracted(monkeypatch):
    """The cover's href is excluded by the caller, SVG or not."""
    _fake_rasterizer(monkeypatch, lambda data=None, **kw: PNG_SIG + b"x" * 200)
    book = _Book([_Parsed("cover.svg", SVG), _Parsed("a.svg", SVG)])
    images = extract_images_from_oeb(book, mock.MagicMock(), ["cover.svg"])
    assert set(images) == {"a.svg"}


def test_without_calibre_an_svg_picture_is_skipped_with_a_warning():
    log = mock.MagicMock()
    images = extract_images_from_oeb(_Book([_Parsed("a.svg", SVG)]), log)
    assert images == {}
    warned = " ".join(str(c.args[0]) for c in log.warn.call_args_list)
    assert "a.svg" in warned


def test_a_rasteriser_that_fails_or_returns_no_png_gives_none(monkeypatch):
    _fake_rasterizer(monkeypatch, mock.Mock(side_effect=ValueError("bad svg")))
    assert svg_to_png(SVG) is None
    _fake_rasterizer(monkeypatch, lambda data=None, **kw: b"not a png" * 20)
    assert svg_to_png(SVG) is None
