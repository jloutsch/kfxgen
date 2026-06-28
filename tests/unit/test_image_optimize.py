import struct
import sys
import types
import pytest
from kfxgen import image_optimize as io


def _png(w, h):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00")


def _jpeg(w, h):
    # SOI, APP0 stub, SOF0 (len=17, precision=8, height, width), EOI
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00" + b"\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


class _Log:
    def __init__(self): self.warns = []
    def warn(self, m): self.warns.append(m)
    def info(self, m): pass
    def debug(self, m): pass


@pytest.mark.unit
def test_read_size_png():
    assert io._read_image_size(_png(3000, 2000)) == (3000, 2000)


@pytest.mark.unit
def test_read_size_jpeg():
    assert io._read_image_size(_jpeg(2500, 1800)) == (2500, 1800)


@pytest.mark.unit
def test_read_size_unknown_returns_none():
    assert io._read_image_size(b"not an image") is None
    assert io._read_image_size(b"\xff\xd8short") is None


@pytest.mark.unit
def test_env_int_default_when_unset(monkeypatch):
    monkeypatch.delenv("KFXGEN_IMAGE_MAX_DIM", raising=False)
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, _Log()) == 2048


@pytest.mark.unit
def test_env_int_valid(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "1600")
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, _Log()) == 1600


@pytest.mark.unit
def test_env_int_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "huge")
    log = _Log()
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, log) == 2048
    assert log.warns


@pytest.mark.unit
def test_env_int_out_of_range_falls_back(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_QUALITY", "999")
    log = _Log()
    assert io._read_env_int("KFXGEN_IMAGE_QUALITY", 85, 1, 100, log) == 85
    assert log.warns


@pytest.mark.unit
def test_optimize_image_small_is_identity():
    data = _jpeg(800, 600)
    assert io.optimize_image(data, max_dim=2048, log=_Log()) is data


@pytest.mark.unit
def test_optimize_image_no_calibre_is_noop():
    # calibre.utils.img is absent in CI -> over-size image returns unchanged
    big = _jpeg(4000, 3000)
    assert io.optimize_image(big, max_dim=2048, log=_Log()) == big


@pytest.mark.unit
def test_optimize_image_downscales_via_calibre(monkeypatch):
    calls = {}
    fake = types.ModuleType("calibre.utils.img")

    def scale_image(data, width, height, as_png=False, compression_quality=90):
        calls["args"] = (width, height, as_png, compression_quality)
        return ("JPEG", b"small-bytes")

    fake.scale_image = scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)

    big = _jpeg(4000, 3000)
    out = io.optimize_image(big, max_dim=2048, jpeg_quality=85, log=_Log())
    assert out == b"small-bytes"
    assert calls["args"] == (2048, 2048, False, 85)


@pytest.mark.unit
def test_optimize_image_keeps_png_format(monkeypatch):
    seen = {}
    fake = types.ModuleType("calibre.utils.img")

    def scale_image(data, width, height, as_png=False, compression_quality=90):
        seen["as_png"] = as_png
        return ("PNG", b"x" * 10)

    fake.scale_image = scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)

    out = io.optimize_image(_png(4000, 3000), max_dim=2048, log=_Log())
    assert seen["as_png"] is True
    assert out == b"x" * 10


@pytest.mark.unit
def test_optimize_image_keeps_original_if_result_larger(monkeypatch):
    fake = types.ModuleType("calibre.utils.img")
    fake.scale_image = lambda *a, **k: ("JPEG", b"Z" * 100000)
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)

    big = _jpeg(4000, 3000)
    assert io.optimize_image(big, max_dim=2048, log=_Log()) == big
