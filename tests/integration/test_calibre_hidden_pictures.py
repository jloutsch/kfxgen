"""Pictures hidden by a class's `display: none` (#428).

Only calibre's Stylizer applies a class's `display: none`, so the unit tests,
which read an element's own `style`, can't see these. One picture is hidden
inside a paragraph, one under a hidden box in a contents listing whose text
is dropped (#132) and whose pictures are kept (#117).

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

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_picture_css import _build_epub  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = ".hide { display: none }"
PAGES = {
    "front": (
        "<h1>Front</h1><p>A first page with enough text of its own to stay a"
        " page, not a contents page to rebuild.</p>"
        '<div class="toc"><p><a href="inline.xhtml">Inline</a></p>'
        '<div class="hide"><img src="pic.jpg" alt="hidden-listing"/></div>'
        '<img src="pic.jpg" alt="listing-plate"/></div><p>After the listing.</p>'
    ),
    "contents": '<h1>Contents</h1><p><a href="inline.xhtml">Inline</a></p>',
    "inline": (
        "<h1>Inline</h1>"
        '<p><img class="hide" src="pic.jpg" alt="hidden-inline"/>Text one.</p>'
        '<p><img src="pic.jpg" alt="shown-inline"/>Text two.</p>'
    ),
}


@pytest.fixture(scope="module")
def alts(calibre):  # noqa: F811
    """The alt text of every picture written, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "hidden.epub", tmp / "hidden.kfx"
    _build_epub(epub, PAGES, CSS)
    _ebook_convert(calibre, epub, kfx, native=True)
    frags = load_fragments(kfx)
    found = [
        str(e.get("$584"))
        for story in by_type(frags, "$259")
        for e in iter_entries(val(story)["$146"])
        if str(e.get("$159")) == "$271"
    ]
    return found, version


def test_a_picture_hidden_by_class_inside_a_paragraph_is_not_written(alts):
    found, version = alts
    assert "shown-inline" in found, f"calibre {version}"
    assert "hidden-inline" not in found, f"calibre {version}"


def test_a_picture_under_a_box_hidden_by_class_in_a_listing_is_not_written(alts):
    found, version = alts
    # The listing was dropped, and its own picture kept (#117).
    assert "listing-plate" in found, f"calibre {version}"
    assert "hidden-listing" not in found, f"calibre {version}"
