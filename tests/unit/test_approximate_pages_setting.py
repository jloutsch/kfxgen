"""The "Don't add estimated page numbers" setting (#586): off by default, so
a book with no page list gets approximate pages; the Customize setting or
`--kfxgen-disable-approximate-pages` turns them off."""

from types import SimpleNamespace

import pytest

from tests.unit.test_open_at_cover import _Log, _prefs
from tests.unit.test_plugin_recommendations import plugin_module  # noqa: F401

pytestmark = pytest.mark.unit


def test_the_plugin_offers_the_option_off(plugin_module):  # noqa: F811
    recs = {r.name: r for r in plugin_module.KFXGenOutputPlugin.options}
    assert recs["kfxgen_disable_approximate_pages"].recommended_value is False


def test_the_customize_setting_turns_them_off(plugin_module, monkeypatch):  # noqa: F811
    _prefs(monkeypatch, disable_approximate_pages=True)
    opts = SimpleNamespace(kfxgen_disable_approximate_pages=False)
    plugin_module.KFXGenOutputPlugin()._apply_prefs(opts, _Log())
    assert opts.kfxgen_disable_approximate_pages is True


def test_the_setting_off_leaves_the_option(plugin_module, monkeypatch):  # noqa: F811
    _prefs(monkeypatch)
    opts = SimpleNamespace(kfxgen_disable_approximate_pages=False)
    plugin_module.KFXGenOutputPlugin()._apply_prefs(opts, _Log())
    assert opts.kfxgen_disable_approximate_pages is False
