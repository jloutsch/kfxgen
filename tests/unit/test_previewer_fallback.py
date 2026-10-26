"""The Kindle Previewer fallback finds Previewer 4, calls it as Previewer 4
takes its arguments, and reads KPF with KFX Input's kfxlib, which kfxgen
doesn't ship (#498). Checked end to end once through calibre with a
debug build whose native generation fails (PR body)."""

import sys
import types

import pytest

from kfxgen import kp_calibre_converter as kp

KP4 = "/Applications/Kindle Previewer 4.app/Contents/MacOS/KindlePreviewer4CLI"
KP3 = "/Applications/Kindle Previewer 3.app/Contents/MacOS/Kindle Previewer 3"


@pytest.mark.unit
def test_previewer_4_is_found_first(monkeypatch):
    monkeypatch.setattr(kp.os.path, "isfile", lambda p: p in (KP4, KP3))
    assert kp.KPCalibreConverter().kindle_previewer_path == KP4


@pytest.mark.unit
def test_previewer_3_still_found_alone(monkeypatch):
    monkeypatch.setattr(kp.os.path, "isfile", lambda p: p == KP3)
    assert kp.KPCalibreConverter().kindle_previewer_path == KP3


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "args"),
    [
        # Previewer 4 refuses single dashes ("Insufficient arguments").
        (KP4, ["--convert", "--output"]),
        (KP3, ["-convert", "-output"]),
    ],
)
def test_each_previewer_gets_its_own_arguments(path, args):
    assert kp._convert_args(path) == args


@pytest.mark.unit
def test_kfx_inputs_kfxlib_comes_first(monkeypatch):
    mine = types.ModuleType("calibre_plugins.kfx_input.kfxlib")
    for name in ("calibre_plugins", "calibre_plugins.kfx_input"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "calibre_plugins.kfx_input.kfxlib", mine)
    # Even with another kfxlib importable, KFX Input's is the one used.
    monkeypatch.setitem(sys.modules, "kfxlib", types.ModuleType("kfxlib"))
    assert kp._kfxlib() is mine


@pytest.mark.unit
def test_without_kfx_input_it_says_so(monkeypatch):
    for name in ("calibre_plugins.kfx_input.kfxlib", "kfxlib"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(ImportError, match="KFX Input"):
        kp._kfxlib()
