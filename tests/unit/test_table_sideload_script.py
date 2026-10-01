"""research/make_table_sideload.py builds the device gate's pair (#219).

The pair is only fit for the gate when built by the real `ebook-convert` in
an isolated calibre config, so the script must never touch the user's own
config and must not write gate-named files any other way.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "research" / "make_table_sideload.py"


def _load():
    spec = importlib.util.spec_from_file_location("make_table_sideload", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_without_calibre_it_fails_and_writes_no_gate_files(tmp_path):
    # Round 1, M5: the shim fallback wrote gate-named files that are not fit
    # for the gate (no Stylizer), which a user could sideload by mistake.
    env = dict(os.environ, KFXGEN_EBOOK_CONVERT=str(tmp_path / "none/ebook-convert"))
    out = tmp_path / "out"
    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(out)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode != 0
    assert "ebook-convert" in run.stderr
    assert not out.exists() or not list(out.glob("table-gate-*"))


@pytest.mark.unit
def test_calibre_version_runs_in_the_isolated_config(monkeypatch):
    # Round 1, M4: `ebook-convert --version` ran with the user's own config.
    module = _load()
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="ebook-convert (x)\n")

    monkeypatch.setattr(module, "EBOOK_CONVERT", "ebook-convert")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    env = {"CALIBRE_CONFIG_DIRECTORY": "/isolated"}
    assert module.calibre_version(env) == "ebook-convert (x)"
    assert seen["env"] is env
