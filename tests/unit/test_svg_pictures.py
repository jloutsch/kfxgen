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

from kfxgen.converter import _pictures_in_table_cells, extract_images_from_oeb
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


def _fake_rasterizer(monkeypatch, rasterize_svg, size=(200, 100)):
    """calibre's rasteriser, and Qt's renderer stating the SVG's own `size`."""
    fake = types.ModuleType("calibre.ebooks.oeb.transforms.rasterize")
    fake.rasterize_svg = rasterize_svg
    qt = types.ModuleType("qt.core")
    qt.QByteArray = bytes
    qt.QSvgRenderer = lambda data: mock.Mock(
        defaultSize=lambda: mock.Mock(width=lambda: size[0], height=lambda: size[1])
    )
    monkeypatch.setitem(sys.modules, "qt", types.ModuleType("qt"))
    monkeypatch.setitem(sys.modules, "qt.core", qt)
    for name in (
        "calibre",
        "calibre.ebooks",
        "calibre.ebooks.oeb",
        "calibre.ebooks.oeb.transforms",
    ):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, fake.__name__, fake)


def test_an_svg_picture_is_extracted_as_png_with_its_own_size(monkeypatch):
    seen = []

    def rasterize_svg(data=None, **kw):
        seen.append((data, kw))
        return PNG_SIG + b"x" * 200

    _fake_rasterizer(monkeypatch, rasterize_svg)
    own = {}
    images = extract_images_from_oeb(
        _Book([_Parsed("a.svg", SVG)]), mock.MagicMock(), display_dims=own
    )
    assert images["a.svg"][:8] == PNG_SIG
    # The file's own bytes, drawn at twice its 200x100 (#440); the page
    # width still comes from 200x100.
    assert seen == [(SVG, {"width": 400, "height": 400})]
    assert own == {"a.svg": (200, 100)}


@pytest.mark.parametrize(
    ("size", "box"),
    [
        ((200, 100), 400),
        ((1024, 100), 1920),
        ((1025, 10), 1920),
        ((3000, 1000), 1920),
        ((1000, 3000), 1920),
        ((20000, 20000), 1920),
    ],
)
def test_an_svg_is_drawn_at_twice_its_size_within_the_cap(monkeypatch, size, box):
    """Twice its own size, so small text is clear on a 300 ppi screen (#440,
    Paperwhite). Never past 1920 px, the optimiser's cap (#467): at its own size a 20000x20000 SVG took
    67 s and 1.2 GB, and Qt would not read the PNG back for `optimize_image`
    to shrink (#439 review). calibre's `size.scale` fits the SVG inside the
    box, keeping its shape."""
    seen = []

    def rasterize_svg(data=None, **kw):
        seen.append(kw)
        return PNG_SIG + b"x" * 200

    _fake_rasterizer(monkeypatch, rasterize_svg, size)
    assert svg_to_png(SVG) == (PNG_SIG + b"x" * 200, size)
    assert seen == [{"width": box, "height": box}]


def test_at_scale_1_an_svg_is_drawn_at_its_own_size(monkeypatch):
    seen = []

    def rasterize_svg(data=None, **kw):
        seen.append(kw)
        return PNG_SIG + b"x" * 200

    _fake_rasterizer(monkeypatch, rasterize_svg, (200, 100))
    assert svg_to_png(SVG, scale=1) == (PNG_SIG + b"x" * 200, (200, 100))
    assert seen == [{"width": 200, "height": 200}]


def test_an_svg_qt_cannot_size_is_drawn_as_qt_draws_it(monkeypatch):
    """No size to double: no box, and no own size to show it at."""
    seen = []

    def rasterize_svg(data=None, **kw):
        seen.append(kw)
        return PNG_SIG + b"x" * 200

    _fake_rasterizer(monkeypatch, rasterize_svg, (-1, -1))
    assert svg_to_png(SVG) == (PNG_SIG + b"x" * 200, None)
    assert seen == [{}]


@pytest.mark.parametrize(
    ("href", "cell_href"),
    [("a.svg", "a.svg"), ("my%20pic.svg", "my pic.svg")],  # (#195)
)
def test_an_svg_in_a_table_cell_is_drawn_at_its_own_size(monkeypatch, href, cell_href):
    """A picture in a cell has no style, as Previewer writes it (#262), so it
    shows at its pixel size: at 2x the CELL box was twice as big on the
    Paperwhite (#441 device check). It keeps #439's own size."""
    seen = []

    def rasterize_svg(data=None, **kw):
        seen.append(kw)
        return PNG_SIG + b"x" * 200

    _fake_rasterizer(monkeypatch, rasterize_svg)
    images = extract_images_from_oeb(
        _Book([_Parsed(href, SVG)]), mock.MagicMock(), own_size={cell_href}
    )
    assert images[href][:8] == PNG_SIG
    assert seen == [{"width": 200, "height": 200}]


def test_pictures_in_table_cells_are_found_by_their_manifest_href():
    from lxml import etree

    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<table><tr><td><p><img src="../images/a.svg"/></p></td>'
        '<th><img src="../images/b.svg"/></th><td><img src="../images/c.png"/></td>'
        '</tr></table><p><img src="../images/d.svg"/></p></body></html>'
    )
    item = mock.Mock(href="text/c.xhtml", data=etree.fromstring(xhtml))
    book = mock.Mock(spine=[item])
    assert _pictures_in_table_cells(book) == {
        "images/a.svg",
        "images/b.svg",
        "images/c.png",
    }


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
