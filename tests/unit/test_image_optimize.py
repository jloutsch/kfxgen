import struct
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


def test_read_size_png():
    assert io._read_image_size(_png(3000, 2000)) == (3000, 2000)


def test_read_size_jpeg():
    assert io._read_image_size(_jpeg(2500, 1800)) == (2500, 1800)


def test_read_size_unknown_returns_none():
    assert io._read_image_size(b"not an image") is None
    assert io._read_image_size(b"\xff\xd8short") is None


def test_env_int_default_when_unset(monkeypatch):
    monkeypatch.delenv("KFXGEN_IMAGE_MAX_DIM", raising=False)
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, _Log()) == 2048


def test_env_int_valid(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "1600")
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, _Log()) == 1600


def test_env_int_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "huge")
    log = _Log()
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, log) == 2048
    assert log.warns


def test_env_int_out_of_range_falls_back(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_QUALITY", "999")
    assert io._read_env_int("KFXGEN_IMAGE_QUALITY", 85, 1, 100, _Log()) == 85
