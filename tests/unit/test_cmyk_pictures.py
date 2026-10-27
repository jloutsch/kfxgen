"""A CMYK JPEG is re-encoded as RGB (#466). Kindle Previewer 4.0.3 writes
it as RGB; kfxgen passed it through, and calibre's own scaling keeps CMYK
too. These cover the detection; the conversion needs calibre and is tested
through it (test_calibre_cmyk_pictures)."""

import io
import sys

import pytest
from PIL import Image

from kfxgen import image_optimize as io_
from tests._helpers import colour_jpeg_of


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "colour", "n"),
    [("CMYK", (0, 255, 255, 0), 4), ("RGB", (255, 0, 0), 3), ("L", 128, 1)],
)
def test_a_jpegs_colour_components_are_read(mode, colour, n):
    assert io_._jpeg_components(colour_jpeg_of(mode, (8, 8), colour)) == n


@pytest.mark.unit
def test_a_png_is_not_a_jpeg():
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, "PNG")
    assert io_._jpeg_components(out.getvalue()) is None


@pytest.mark.unit
def test_only_cmyk_is_converted_and_nothing_fails_without_calibre(monkeypatch):
    monkeypatch.setitem(sys.modules, "calibre.utils.img", None)
    rgb, cmyk = (
        colour_jpeg_of("RGB", (8, 8), (255, 0, 0)),
        colour_jpeg_of("CMYK", (8, 8), (0, 255, 255, 0)),
    )
    cover, images = io_.cmyk_to_rgb(cmyk, {"a.jpg": rgb, "b.jpg": cmyk}, _Log())
    assert cover is cmyk
    assert images["a.jpg"] is rgb
    assert images["b.jpg"] is cmyk


class _Log:
    def info(self, m):
        pass

    def warn(self, m):
        pass

    def debug(self, m):
        pass
