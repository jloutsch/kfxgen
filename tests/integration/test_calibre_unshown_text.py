"""Text a browser doesn't show isn't printed, through calibre (#477).

kfxgen walked a <template>'s content and an <iframe>'s fallback children as
text, and ran a <select>'s options together into one word (QA's #452
probe). This checks calibre hands them to kfxgen as written, and that they
now come out as a browser shows them.

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

FILES = {
    "c1.xhtml": "<h1>One</h1><p>M00 before.</p>"
    "<template><p>M01 template text</p></template>"
    '<p>M05 a <iframe src="c1.xhtml">M06 iframe fallback</iframe> b</p>'
    "<p>M08 <select><option>M09o option one</option>"
    "<option>M09p option two</option></select></p>",
}
TOC = [("One", "c1.xhtml")]


def test_unshown_text_is_left_out(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "unshown477.epub", tmp / "unshown477.kfx"
    _build(epub, FILES, TOC, "unshown477")
    _ebook_convert(calibre, epub, kfx, True)
    words = " ".join(
        str(t) for f in by_type(load_fragments(kfx), "$145") for t in val(f)["$146"]
    ).split()
    assert "M01" not in words and "M06" not in words, f"calibre {version}"
    assert {"one", "M09p"} <= set(words), f"calibre {version}: {words}"
