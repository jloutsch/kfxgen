"""Read-aloud narration, audio and video, through calibre (#248).

calibre's EPUB input drops the pages' `media-overlay` links and the SMIL
files from the manifest kfxgen is given; the book's own OPF stays in the
folder calibre unpacked it into. This converts a narrated book, a book
with audio and video clips, and a book that only declares the overlay
highlight class, and reads the warning from `ebook-convert`'s log.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_media_warning import STYLE_ONLY, _epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _convert(calibre, name, **kw):  # noqa: F811
    """The #248 lines of the log, and the book's text."""
    tmp, env, _ = calibre
    epub, kfx = _epub(tmp / f"{name}.epub", **kw), tmp / f"{name}.kfx"
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx)], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr
    said = [line for line in (run.stdout + run.stderr).splitlines() if "#248" in line]
    texts = [
        str(t) for f in by_type(load_fragments(kfx), "$145") for t in val(f)["$146"]
    ]
    return said, texts


def test_a_narrated_book_says_its_narration_is_left_out(calibre):  # noqa: F811
    said, texts = _convert(calibre, "narrated", overlays=True)
    assert len(said) == 1 and "this book's read-aloud narration." in said[0]
    assert "The cat sat." in texts and "The dog ran." in texts


def test_clips_are_counted_and_their_fallback_written(calibre):  # noqa: F811
    said, texts = _convert(calibre, "clips", audio=True)
    assert len(said) == 1 and "3 audio or video clips in 1 file." in said[0]
    assert "No video." in texts


def test_an_overlay_style_without_overlays_says_nothing(calibre):  # noqa: F811
    said, _ = _convert(calibre, "style", meta=STYLE_ONLY)
    assert said == []
