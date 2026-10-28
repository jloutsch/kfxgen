"""The "Open new books at the cover" setting, through calibre: with the
per-conversion `--kfxgen-open-at-cover` option, or with the plugin's saved
setting (the Customize dialog's `plugins/kfxgen.json`), kfxgen writes no
start landmark ($396) and keeps the contents one. Without either, the start
landmark is written, as Kindle Previewer 4.0.3 writes it (#579).

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests.integration.test_calibre_landmarks import CASES, _epub, _landmarks  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _convert(calibre, name, *flags):  # noqa: F811
    tmp, env, version = calibre
    guide, nav, _want = CASES["nav"]
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _epub(epub, name, guide, nav)
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx), *flags],
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr[-2000:]
    return [kind for kind, _label, _words in _landmarks(kfx)], version


def test_by_default_a_book_opens_at_its_start(calibre):  # noqa: F811
    kinds, version = _convert(calibre, "cov-default")
    assert kinds == ["$212", "$396"], f"calibre {version}: {kinds}"


def test_the_option_opens_it_at_the_cover(calibre):  # noqa: F811
    kinds, version = _convert(calibre, "cov-flag", "--kfxgen-open-at-cover")
    assert kinds == ["$212"], f"calibre {version}: {kinds}"


def test_the_saved_setting_opens_it_at_the_cover(calibre):  # noqa: F811
    _tmp, env, _version = calibre
    path = Path(env["CALIBRE_CONFIG_DIRECTORY"]) / "plugins" / "kfxgen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_text() if path.exists() else None
    path.write_text(json.dumps({"open_at_cover": True}))
    try:
        kinds, version = _convert(calibre, "cov-pref")
    finally:
        if before is None:
            path.unlink()
        else:
            path.write_text(before)
    assert kinds == ["$212"], f"calibre {version}: {kinds}"
