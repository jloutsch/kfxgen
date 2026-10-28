"""The "Open new books at the cover" setting: kfxgen writes no start
landmark ($396), so a Kindle opens a new book at its cover, as books did
before #579. Off by default: a book opens where it marks its text's start,
as store books and Kindle Previewer 4.0.3's files do (Paperwhite, 2026-10-28).
"""

import sys
import types
from types import SimpleNamespace

import pytest

from kfxgen import converter as _conv
from tests.unit.test_book_landmarks import _book, _landmarks_nav
from tests.unit.test_plugin_recommendations import plugin_module  # noqa: F401

pytestmark = pytest.mark.unit


def _nav_book(tmp_path):
    return _book(
        tmp_path,
        nav=_landmarks_nav(
            [("toc", "toc.xhtml", "Contents"), ("bodymatter", "c1.xhtml", "Start")]
        ),
    )


def test_by_default_the_start_landmark_is_kept(tmp_path):
    marks = _conv._book_landmarks_for(_nav_book(tmp_path), SimpleNamespace())
    assert [kind for kind, _l, _k in marks] == ["toc", "start"]


def test_open_at_cover_drops_only_the_start_landmark(tmp_path):
    opts = SimpleNamespace(kfxgen_open_at_cover=True)
    marks = _conv._book_landmarks_for(_nav_book(tmp_path), opts)
    assert marks == [("toc", "Contents", "toc.xhtml")]


def test_a_mock_option_is_not_taken_for_on(tmp_path):
    """calibre's opts are real values; a test double's attribute is not."""
    from unittest.mock import MagicMock

    marks = _conv._book_landmarks_for(_nav_book(tmp_path), MagicMock())
    assert [kind for kind, _l, _k in marks] == ["toc", "start"]


def test_the_plugin_offers_the_option_off(plugin_module):  # noqa: F811
    recs = {r.name: r for r in plugin_module.KFXGenOutputPlugin.options}
    assert recs["kfxgen_open_at_cover"].recommended_value is False


def _prefs(monkeypatch, **values):
    mod = types.ModuleType("calibre_plugins.kfxgen.prefs")
    mod.prefs = {
        "disable_font_embedding": False,
        "disable_native_tables": False,
        "open_at_cover": False,
        **values,
    }
    for name in ("calibre_plugins", "calibre_plugins.kfxgen"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "calibre_plugins.kfxgen.prefs", mod)


class _Log:
    def info(self, msg):
        pass

    warn = info


def test_the_customize_setting_turns_it_on(plugin_module, monkeypatch):  # noqa: F811
    _prefs(monkeypatch, open_at_cover=True)
    opts = SimpleNamespace(kfxgen_open_at_cover=False)
    plugin_module.KFXGenOutputPlugin()._apply_prefs(opts, _Log())
    assert opts.kfxgen_open_at_cover is True


def test_the_setting_off_leaves_the_option(plugin_module, monkeypatch):  # noqa: F811
    _prefs(monkeypatch)
    opts = SimpleNamespace(kfxgen_open_at_cover=False)
    plugin_module.KFXGenOutputPlugin()._apply_prefs(opts, _Log())
    assert opts.kfxgen_open_at_cover is False
