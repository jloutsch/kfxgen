"""A picture or cover calibre can't decode is skipped with a warning naming
it, not written into the book with no size or a garbage one (#471)."""

import sys
import types

import pytest

from kfxgen import converter as _conv
from kfxgen import image_optimize as _io
from tests._helpers import jpeg_of


class _Log:
    def __init__(self):
        self.warns = []

    def warn(self, m):
        self.warns.append(m)

    def info(self, m):
        pass

    def debug(self, m):
        pass


def _calibre_reading(monkeypatch, good):
    """A stub calibre.utils.img whose image_from_data reads only `good`."""
    fake = types.ModuleType("calibre.utils.img")

    def image_from_data(data):
        if data not in good:
            raise ValueError("Not a valid image")
        return object()

    fake.image_from_data = image_from_data
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)


@pytest.mark.unit
def test_decodes_asks_calibre(monkeypatch):
    _calibre_reading(monkeypatch, {b"good"})
    assert _io.decodes(b"good") is True
    assert _io.decodes(b"bad") is False


@pytest.mark.unit
def test_without_calibre_nothing_is_dropped(monkeypatch):
    monkeypatch.setitem(sys.modules, "calibre.utils.img", None)
    assert _io.decodes(b"anything") is True


@pytest.mark.unit
def test_bad_pictures_and_cover_are_dropped_and_named(monkeypatch):
    _calibre_reading(monkeypatch, {b"good"})
    log = _Log()
    cover, images = _conv._drop_undecodable(
        b"bad", "cover.jpg", {"a.jpg": b"good", "b.png": b"bad"}, log
    )
    assert cover is None
    assert images == {"a.jpg": b"good"}
    assert any("'cover.jpg'" in w for w in log.warns)
    assert any("'b.png'" in w for w in log.warns)


@pytest.mark.unit
def test_a_good_cover_is_kept(monkeypatch):
    _calibre_reading(monkeypatch, {b"good"})
    log = _Log()
    assert _conv._drop_undecodable(b"good", "c.jpg", {}, log) == (b"good", {})
    assert log.warns == []


def _calibre_counting(monkeypatch, bad):
    """A stub calibre.utils.img that refuses `bad`, counting every decode:
    image_from_data's, and scale_image's own."""
    count = {"decodes": 0}
    fake = types.ModuleType("calibre.utils.img")

    def image_from_data(data):
        count["decodes"] += 1
        if data in bad:
            raise ValueError("Not a valid image")
        return object()

    def scale_image(data, width, height, as_png, compression_quality):
        count["decodes"] += 1
        if data in bad:
            raise ValueError("Not a valid image")
        return width, height, b"small"

    fake.image_from_data, fake.scale_image = image_from_data, scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)
    return count


@pytest.mark.unit
@pytest.mark.parametrize("width", [4000, 400], ids=["scaled", "kept"])
def test_each_picture_is_decoded_once(monkeypatch, width):
    """Checked, then scaled: one decode, not two (#514). QA measured the
    check's decoding at 8% of a heavy picture book's conversion."""
    count = _calibre_counting(monkeypatch, bad=set())
    cover, images = _conv._check_and_optimize(
        None, None, {"a.jpg": jpeg_of(width, 300)}, object(), _Log()
    )
    assert list(images) == ["a.jpg"]
    assert count["decodes"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("width", [4000, 400], ids=["scaled", "kept"])
@pytest.mark.parametrize("embed", [False, True], ids=["optimised", "embed-originals"])
def test_an_undecodable_picture_is_still_dropped(monkeypatch, width, embed):
    bad = jpeg_of(width, 300) + b"bad"
    _calibre_counting(monkeypatch, bad={bad})
    log = _Log()
    opts = types.SimpleNamespace(kfxgen_embed_original_images=embed)
    cover, images = _conv._check_and_optimize(
        bad, "cover.jpg", {"a.jpg": bad, "b.jpg": jpeg_of(400, 300)}, opts, log
    )
    assert cover is None
    assert list(images) == ["b.jpg"]
    assert any("'cover.jpg'" in w for w in log.warns)
    assert any("'a.jpg'" in w for w in log.warns)
