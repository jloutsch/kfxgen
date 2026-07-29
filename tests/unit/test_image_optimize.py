import struct
import sys
import types
import pytest
from kfxgen import image_optimize as io


def _png(w, h):
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", w, h)
        + b"\x08\x02\x00\x00\x00"
    )


def _jpeg(w, h):
    # SOI, APP0 stub, SOF0 (len=17, precision=8, height, width), EOI
    app0 = (
        b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00"
        + b"\x01\x01\x00"
        + b"\x00\x01\x00\x01\x00\x00"
    )
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", h, w)
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


class _Log:
    def __init__(self):
        self.warns = []

    def warn(self, m):
        self.warns.append(m)

    def info(self, m):
        pass

    def debug(self, m):
        pass


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
        # calibre.utils.img.scale_image returns (width, height, data) (#11).
        return (2048, 1536, b"small-bytes")

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
        return (2048, 1536, b"x" * 10)

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
    fake.scale_image = lambda *a, **k: (2048, 1536, b"Z" * 100000)
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)

    big = _jpeg(4000, 3000)
    assert io.optimize_image(big, max_dim=2048, log=_Log()) == big


@pytest.mark.unit
def test_optimize_images_maps_all_and_handles_none_cover(monkeypatch):
    # Force the per-image optimizer to a deterministic stub.
    monkeypatch.setattr(io, "optimize_image", lambda data, **k: b"OPT" + data[:1])
    cover, imgs = io.optimize_images(None, {"a.jpg": b"AAAA", "b.png": b"BBBB"}, _Log())
    assert cover is None
    assert imgs == {"a.jpg": b"OPTA", "b.png": b"OPTB"}


@pytest.mark.unit
def test_optimize_images_optimizes_cover(monkeypatch):
    monkeypatch.setattr(io, "optimize_image", lambda data, **k: b"C")
    cover, imgs = io.optimize_images(b"COVERDATA", {}, _Log())
    assert cover == b"C"
    assert imgs == {}


@pytest.mark.unit
def test_optimize_images_reads_env_overrides(monkeypatch):
    seen = {}
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "1600")
    monkeypatch.setenv("KFXGEN_IMAGE_QUALITY", "70")
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_BYTES", "300000")

    def spy(data, *, max_dim, max_bytes, jpeg_quality, log):
        seen["max_dim"] = max_dim
        seen["max_bytes"] = max_bytes
        seen["q"] = jpeg_quality
        return data

    monkeypatch.setattr(io, "optimize_image", spy)
    io.optimize_images(b"COVER", {"a.jpg": b"AAAA"}, _Log())
    assert seen["max_dim"] == 1600
    assert seen["q"] == 70
    assert seen["max_bytes"] == 300000


# ── #55: byte-size gate ──────────────────────────────────────────────────────


def _fake_calibre(monkeypatch, calls):
    """Install a stub calibre.utils.img whose scale_image records its args."""
    fake = types.ModuleType("calibre.utils.img")

    def scale_image(data, width, height, as_png=False, compression_quality=90):
        calls["args"] = (width, height, as_png, compression_quality)
        return (width, height, b"x" * 1000)

    fake.scale_image = scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)
    return calls


@pytest.mark.unit
def test_heavy_image_under_max_dim_is_optimized(monkeypatch):
    """The #55 case: modest dimensions, near-lossless encoding. 1161x1800 is
    under the 2048 dimension gate, so before this it passed through untouched
    no matter how many bytes it was."""
    _fake_calibre(monkeypatch, {})
    heavy = _jpeg(1161, 1800) + b"\x00" * 2_000_000
    out = io.optimize_image(
        heavy, max_dim=2048, max_bytes=1_000_000, jpeg_quality=85, log=_Log()
    )
    assert out != heavy, "over-size-in-bytes image was not optimized"
    assert len(out) < len(heavy)


@pytest.mark.unit
def test_byte_trigger_reencodes_at_original_dimensions(monkeypatch):
    """When only the byte gate fires, dimensions must be preserved — the image
    is too heavy, not too big."""
    calls = _fake_calibre(monkeypatch, {})
    heavy = _jpeg(1161, 1800) + b"\x00" * 2_000_000
    io.optimize_image(heavy, max_dim=2048, max_bytes=1_000_000, log=_Log())
    assert calls["args"][:2] == (1161, 1800), (
        f"expected re-encode at original dims, got {calls['args'][:2]}"
    )


@pytest.mark.unit
def test_dimension_trigger_still_downscales_to_max_dim(monkeypatch):
    """The existing behaviour must not regress: too-wide images still shrink."""
    calls = _fake_calibre(monkeypatch, {})
    big = _jpeg(4000, 3000) + b"\x00" * 2_000_000
    io.optimize_image(big, max_dim=2048, max_bytes=1_000_000, log=_Log())
    assert calls["args"][:2] == (2048, 2048)


@pytest.mark.unit
def test_light_image_under_both_gates_untouched(monkeypatch):
    _fake_calibre(monkeypatch, {})
    light = _jpeg(800, 600)
    assert io.optimize_image(light, max_dim=2048, max_bytes=1_000_000) is light


@pytest.mark.unit
def test_byte_gate_respects_env_override(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_BYTES", "500000")
    log = _Log()
    assert (
        io._read_env_int(
            "KFXGEN_IMAGE_MAX_BYTES", io.DEFAULT_MAX_BYTES, 1, 1 << 30, log
        )
        == 500000
    )


@pytest.mark.unit
def test_reencode_larger_than_original_is_discarded(monkeypatch):
    """Existing guard must still hold on the byte path — never grow an image."""
    fake = types.ModuleType("calibre.utils.img")
    fake.scale_image = (
        lambda data, width, height, as_png=False, compression_quality=90: (
            width,
            height,
            b"y" * (len(data) + 10),
        )
    )
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)
    heavy = _jpeg(1161, 1800) + b"\x00" * 2_000_000
    assert io.optimize_image(heavy, max_dim=2048, max_bytes=1_000_000) == heavy
