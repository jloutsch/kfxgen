"""A picture or cover calibre can't decode is skipped with a warning naming
it, not written into the book with no size or a garbage one (#471)."""

import sys
import types

import pytest

from kfxgen import converter as _conv
from kfxgen import image_optimize as _io


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
