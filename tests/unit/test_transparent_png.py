"""`flatten_transparent` (#783): a PNG with transparent pixels drawn onto
white, still a PNG. A palette one is done in its palette, without calibre;
the rest need calibre, and `tests/integration/test_calibre_transparent_png.py`
checks them through a real conversion."""

import io
import sys

import pytest
from PIL import Image

from kfxgen.image_optimize import _png_chunks, flatten_transparent


class _Log:
    def __init__(self):
        self.warns = []

    def warn(self, m):
        self.warns.append(m)


def _png(img, **kw):
    out = io.BytesIO()
    img.save(out, "PNG", **kw)
    return out.getvalue()


def _palette(alphas):
    """A 3 x 1 palette picture of green, blue and red, with `alphas`."""
    img = Image.new("P", (3, 1))
    img.putdata([0, 1, 2])
    img.putpalette([0, 255, 0, 0, 0, 200, 200, 30, 30] + [0] * 759)
    return _png(img, transparency=bytes(alphas))


def _flat(data):
    cover, images = flatten_transparent(data, {"x.png": data}, _Log())
    assert cover == images["x.png"]
    return cover


@pytest.mark.unit
def test_a_palette_png_is_drawn_onto_white_in_its_palette():
    """Green fully transparent, blue half, red opaque: white, blue mixed
    with white, red. No transparency left, and no pixel touched."""
    src = _palette([0, 128, 255])
    out = _flat(src)
    img = Image.open(io.BytesIO(out))
    assert img.mode == "P" and "transparency" not in img.info
    assert [img.convert("RGB").getpixel((x, 0)) for x in range(3)] == [
        (255, 255, 255),
        (127, 127, 227),
        (200, 30, 30),
    ]
    idat = [b for k, b in _png_chunks(src) if k == b"IDAT"]
    assert [b for k, b in _png_chunks(out) if k == b"IDAT"] == idat


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    [
        _palette([255, 255]),
        _png(Image.new("RGB", (3, 1), (0, 0, 200))),
        b"\xff\xd8\xff\xe0" + b"J" * 40,
        _palette([0, 128])[:40],
        b"",
        None,
    ],
    ids=["opaque-palette", "rgb", "jpeg", "cut-short", "empty", "none"],
)
def test_a_picture_with_no_transparency_is_left_as_it_is(monkeypatch, data):
    """Without asking calibre, which would raise here."""
    monkeypatch.setitem(sys.modules, "calibre.utils.img", None)
    assert flatten_transparent(data, {"x": data}, _Log()) == (data, {"x": data})


@pytest.mark.unit
def test_with_no_calibre_an_alpha_png_is_left_as_it_is(monkeypatch):
    monkeypatch.setitem(sys.modules, "calibre.utils.img", None)
    data = _png(Image.new("RGBA", (3, 1), (0, 0, 200, 0)))
    assert _flat(data) == data
