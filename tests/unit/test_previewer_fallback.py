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


def _fallback(monkeypatch, tmp_path, kpf_data=b"KFX"):
    """A converter whose Previewer run and kfxlib are stand-ins: Previewer
    writes a KPF, kfxlib turns it into `kpf_data`. Returns the converter and
    the list of Previewer calls."""
    calls = []

    def run(cmd, **kw):
        calls.append(kw)
        (kp.Path(cmd[-1]) / "KPF").mkdir(parents=True, exist_ok=True)
        (kp.Path(cmd[-1]) / "KPF" / "book.kpf").write_bytes(b"kpf")
        return types.SimpleNamespace(
            returncode=0, stdout="Converted successfully", stderr=""
        )

    class Book:
        def __init__(self, path):
            pass

        def decode_book(self):
            pass

        def convert_to_single_kfx(self):
            return kpf_data

    lib = types.ModuleType("kfxlib")
    lib.YJ_Book = Book
    monkeypatch.setitem(sys.modules, "calibre_plugins.kfx_input.kfxlib", None)
    monkeypatch.setitem(sys.modules, "kfxlib", lib)
    monkeypatch.setattr(kp.subprocess, "run", run)
    conv = kp.KPCalibreConverter(kindle_previewer_path=KP4, log=None)
    monkeypatch.setattr(
        conv, "_save_oeb_as_epub", lambda oeb, p: open(p, "wb").write(b"e")
    )
    monkeypatch.setattr(conv, "_fix_cover_metadata", lambda book: None)
    monkeypatch.setattr(conv, "_fix_title_metadata", lambda book: None)
    return conv, calls


@pytest.mark.unit
def test_without_kfx_input_previewer_never_runs(monkeypatch, tmp_path):
    """A missing KFX Input fails before Previewer's conversion, which took
    379 s for an 81 MB book in QA's run, not after it (#513)."""
    conv, calls = _fallback(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "kfxlib", None)
    assert conv.convert_oeb(object(), str(tmp_path / "o.kfx")) is False
    assert calls == []


@pytest.mark.unit
def test_the_output_is_written_safely(monkeypatch, tmp_path):
    """Through _safe_write_bytes (#45): a symlink at the output path is
    refused, and its target is left alone (#513)."""
    conv, _ = _fallback(monkeypatch, tmp_path)
    target = tmp_path / "target"
    target.write_bytes(b"keep")
    link = tmp_path / "o.kfx"
    link.symlink_to(target)
    assert conv.convert_oeb(object(), str(link)) is False
    assert target.read_bytes() == b"keep"

    out = tmp_path / "p.kfx"
    assert conv.convert_oeb(object(), str(out)) is True
    assert out.read_bytes() == b"KFX"


@pytest.mark.unit
@pytest.mark.parametrize(("mb", "timeout"), [(1, 600), (81, 1215), (200, 3000)])
def test_previewers_timeout_grows_with_the_book(mb, timeout):
    """15 s per MB of EPUB, at least the old 10 minutes: QA's 81 MB book took
    379 s, so if the time grows with size, a fixed 600 s fails books past
    about 130 MB (#513)."""
    assert kp._previewer_timeout(mb * 1024 * 1024) == timeout
