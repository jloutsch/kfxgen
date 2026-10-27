"""Picture sizes as Kindle Previewer 4.0.3 writes them (#467), probed
2026-10-26: a picture alone in its paragraph is px / 512 of the column
(Previewer 3.106's 496 before); its longest side is capped at 1920 px
(kfxgen's optimiser capped at 2048); and a picture of 100 bytes or less,
a 1 x 1 spacer, is kept, as Previewer keeps it, not dropped."""

import sys
import types

import pytest

from kfxgen import image_optimize as io
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val
from tests.unit.test_native_generator import _png


def _pictures(tmp_path, images):
    out = tmp_path / "p.kfx"
    text = "\n\n".join(f"\x00IMG\x01{h}\x01\x00" for h in images)
    NativeKFXGenerator().generate_full_book(
        "T", "A", [{"title": "One", "text": text}], output_path=str(out), images=images
    )
    frags = load_fragments(out)
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    return [
        float(str(styles[str(e["$157"])]["$56"]["$307"]))
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and str(e.get("$159")) == "$271"
    ]


@pytest.mark.unit
def test_a_pictures_width_is_its_pixels_over_512(tmp_path):
    widths = _pictures(tmp_path, {"a.png": _png(256, 100), "b.png": _png(400, 300)})
    assert widths == [50.0, 78.125]


@pytest.mark.unit
def test_a_1x1_spacer_is_kept(tmp_path):
    spacer = _png(1, 1)
    assert len(spacer) <= 100
    assert _pictures(tmp_path, {"s.png": spacer}) == [0.195]


@pytest.mark.unit
def test_the_size_cap_is_1920(monkeypatch):
    calls = {}
    fake = types.ModuleType("calibre.utils.img")

    def scale_image(data, width, height, as_png=False, compression_quality=90):
        calls["box"] = (width, height)
        return (width, height, b"x" * 10)

    fake.scale_image = scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)
    monkeypatch.delenv("KFXGEN_IMAGE_MAX_DIM", raising=False)
    io.optimize_images(None, {"a.jpg": jpeg_of(4000, 3000)}, _Log())
    assert calls["box"] == (1920, 1920)


class _Log:
    def info(self, m):
        pass

    def warn(self, m):
        pass

    def debug(self, m):
        pass
