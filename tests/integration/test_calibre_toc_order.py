"""The contents pane keeps the book's TOC order when it differs from the
text's (#484), as Kindle Previewer 4.0.3 keeps NCX order 2, 1, 3: sections
still follow the document, and each entry lands on its own place. kfxgen
dropped an entry pointing before the one listed above it ("out of document
order") and listed 1, 2, 3, or lost the entry outright mid-book.

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

from tests._kfx_introspect import load_fragments  # noqa: E402
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_notes_aside import _contents_pane  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

FILES = {
    f"c{n}.xhtml": f"<h1>Chapter {n}</h1><p>Text of chapter {n}.</p>"
    for n in (1, 2, 3, 4)
}


@pytest.mark.parametrize(
    "order", [(2, 1, 3, 4), (1, 3, 2, 4)], ids=["first-moved", "middle-moved"]
)
def test_the_contents_pane_keeps_the_books_order(calibre, order):  # noqa: F811
    tmp, _env, version = calibre
    name = "toc484" + "".join(map(str, order))
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _build(epub, FILES, [(f"Chapter {n}", f"c{n}.xhtml") for n in order], name)
    _ebook_convert(calibre, epub, kfx, True)
    assert _contents_pane(load_fragments(kfx)) == [
        (f"Chapter {n}", f"Chapter {n}") for n in order
    ], f"calibre {version}"
