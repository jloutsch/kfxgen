"""The container's entity list ($419) names each entity once through calibre
(#520), as Kindle Previewer 4.0.3 writes it: the cover's resource and
location, and a style the text shared, were listed again.

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
from tests.integration.test_calibre_cover_in_text import _epub  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def test_each_entity_is_listed_once(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "ent520.epub", tmp / "ent520.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    (entities,) = by_type(load_fragments(kfx), "$419")
    names = [str(n) for c in val(entities)["$252"] for n in c["$181"]]
    assert "cover_img" in names, f"calibre {version}"
    assert [n for n in names if names.count(n) > 1] == [], f"calibre {version}"
