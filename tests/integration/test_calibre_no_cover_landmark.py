"""A book with no cover has no landmarks ($236) through calibre (#460), as
Kindle Previewer 4.0.3 writes it; a book with one keeps its cover landmark.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def test_a_coverless_book_has_no_landmarks(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "nc460.epub", tmp / "nc460.kfx"
    _build(
        epub,
        {"c1.xhtml": "<h1>Chapter 1</h1><p>Body.</p>"},
        [("Chapter 1", "c1.xhtml")],
        "nc460",
    )
    _ebook_convert(calibre, epub, kfx, True)
    (nav,) = by_type(load_fragments(kfx), "$389")
    kinds = [str(getattr(c, "value", c)["$235"]) for c in val(nav)[0]["$392"]]
    assert "$236" not in kinds, f"calibre {version}: {kinds}"
    assert "$212" in kinds, f"calibre {version}: {kinds}"
