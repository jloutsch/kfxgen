"""When native generation fails, its error is logged with its type and
traceback where a user sees them, and kept when the Kindle Previewer
fallback fails too (#472). An exception with no message, StopIteration in
#434, logged "Native KFX generation failed: " and nothing more."""

import sys
import types

import pytest

from tests.unit.test_plugin_recommendations import plugin_module  # noqa: F401


class _Log:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg: self.lines.append((level, str(msg)))


def _convert(plugin_module, monkeypatch, fallback_error):  # noqa: F811
    import importlib

    converter = importlib.import_module("plugin.kfxgen.converter")

    def native(*a):
        raise StopIteration

    monkeypatch.setattr(converter, "convert_oeb_to_kfx", native)
    kp = types.ModuleType("plugin.kfxgen.kp_calibre_converter")

    def fallback(*a):
        if fallback_error:
            raise fallback_error

    kp.convert_oeb_to_kfx = fallback
    monkeypatch.setitem(sys.modules, "plugin.kfxgen.kp_calibre_converter", kp)
    plugin = plugin_module.KFXGenOutputPlugin()
    plugin.version = (0, 0, 0)
    monkeypatch.setattr(plugin, "_apply_prefs", lambda opts, log: None, raising=False)
    log = _Log()
    return plugin, log


@pytest.mark.unit
def test_the_native_error_is_named_with_its_traceback(plugin_module, monkeypatch):  # noqa: F811
    plugin, log = _convert(plugin_module, monkeypatch, None)
    plugin.convert(None, "out.kfx", None, None, log)
    warned = "\n".join(m for level, m in log.lines if level in ("warn", "error"))
    assert "Native KFX generation failed: StopIteration" in warned
    assert "Traceback (most recent call last)" in warned


@pytest.mark.unit
def test_a_failed_fallback_keeps_the_native_error(plugin_module, monkeypatch):  # noqa: F811
    plugin, log = _convert(plugin_module, monkeypatch, RuntimeError("kp broke"))
    with pytest.raises(RuntimeError, match="kp broke") as caught:
        plugin.convert(None, "out.kfx", None, None, log)
    assert isinstance(caught.value.__cause__, StopIteration)
