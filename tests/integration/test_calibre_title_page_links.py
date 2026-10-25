"""Links into a title page kfxgen replaces land on it, through calibre.

kfxgen replaced a title page's text with the book's title and author, and a
half-title page's with the title (#33, #107), dropping the page's blocks and
their anchor keys with them. A link to the page's file or an id on it then
had nothing to land on. Since #448 the page is printed as written, its ids
with it.

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
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

# Links to the page's file and to an id on it.
BACK = '<p>Links: <a href="{0}">L1</a> <a href="{0}#t">L2</a></p>'
SHAPES = {
    "title-page": (
        "Title Page",
        '<h1 id="t">The Book</h1><p>by The Author</p>',
    ),
    "half-title": ("Half Title", '<h1 id="t">The Book</h1>'),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_links_into_a_replaced_title_page_land_on_it(calibre, shape):  # noqa: F811
    tmp, _env, version = calibre
    label, body = SHAPES[shape]
    files = {
        "title.xhtml": body,
        "c1.xhtml": "<h1>Chapter One</h1><p>One text.</p>" + BACK.format("title.xhtml"),
        "c2.xhtml": "<h1>Chapter Two</h1><p>Two text.</p>",
    }
    toc = [
        (label, "title.xhtml"),
        ("Chapter One", "c1.xhtml"),
        ("Chapter Two", "c2.xhtml"),
    ]
    epub, kfx = tmp / f"title-{shape}.epub", tmp / f"title-{shape}.kfx"
    _build(epub, files, toc, f"title{shape}")
    _ebook_convert(calibre, epub, kfx, True)

    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    by_id = {e["$155"]: e for e in entries}
    anchors = {str(f.fid): val(f)["$183"]["$155"] for f in by_type(frags, "$266")}

    def text(e):
        r = e["$145"]
        return str(content[str(r["name"])][int(r["$403"])])

    landed = {}
    for e in entries:
        t = text(e)
        for sp in e.get("$142") or []:
            word = t[int(sp["$143"]) : int(sp["$143"]) + int(sp["$144"])]
            if "$179" in sp and word in ("L1", "L2"):
                landed[word] = text(by_id[anchors[str(sp["$179"])]])
    # The page is printed as written since #448, its heading first.
    assert landed == {"L1": "The Book", "L2": "The Book"}, f"calibre {version}"
