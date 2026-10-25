"""A title page's chapter that holds more than a title page keeps its text,
through calibre (#448).

kfxgen replaces a title page's text with the book's title and author (#33).
When the book's printed contents listing falls into the same chapter, it
went with it: in one shape the listing's file has no TOC entry of its own,
and in the other the listing shares the title page's file and has none.
The book then had no printed contents at all, and its "back to contents"
links went nowhere (#447 made them land on the title page).

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

TITLE = '<h1 id="t">The Book</h1><p>by The Author</p>'
CHAPTERS = [f"Chapter {n}" for n in range(1, 21)]
#: About 600 characters, as the listings were: 855 and 1,422 (#448).
LISTED = "{} listed, with its title"
LINKED = "".join(
    f'<p><a href="c{n}.xhtml">{LISTED.format(c)}</a></p>'
    for n, c in enumerate(CHAPTERS, 1)
)
PLAIN = "".join(f"<p>{LISTED.format(c)}</p>" for c in CHAPTERS)
BACK = '<p>Back: <a href="{0}">L1</a></p>'
SHAPES = {
    # The listing's file follows the title page's and has no TOC entry.
    "listing-file-without-an-entry": (
        {
            "title.xhtml": TITLE,
            "toc.xhtml": f'<h2>Contents</h2><div class="toc" id="box">{LINKED}</div>',
        },
        "toc.xhtml",
        "Contents",
    ),
    # The listing is on the title page itself, as plain lines.
    "listing-on-the-title-page": (
        {"title.xhtml": TITLE + f'<h2 id="lst">Contents</h2>{PLAIN}'},
        "title.xhtml#lst",
        "Contents",
    ),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_title_page_holding_a_listing_keeps_it(calibre, shape):  # noqa: F811
    tmp, _env, version = calibre
    front, back_to, lands_on = SHAPES[shape]
    files = dict(front)
    for n, c in enumerate(CHAPTERS, 1):
        files[f"c{n}.xhtml"] = f"<h1>{c}</h1><p>Text {n}.</p>" + (
            BACK.format(back_to) if n == 1 else ""
        )
    toc = [("Title Page", "title.xhtml")] + [
        (c, f"c{n}.xhtml") for n, c in enumerate(CHAPTERS, 1)
    ]
    epub, kfx = tmp / f"keep-{shape}.epub", tmp / f"keep-{shape}.kfx"
    _build(epub, files, toc, f"keep{shape}")
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

    texts = [text(e) for e in entries]
    # Every line of the listing is printed, and the title page's own lines.
    assert [c for c in CHAPTERS if LISTED.format(c) not in texts] == []
    assert "by The Author" in texts, f"calibre {version}"
    landed = [
        text(by_id[anchors[str(sp["$179"])]])
        for e in entries
        for sp in e.get("$142") or []
        if "$179" in sp
        and text(e)[int(sp["$143"]) : int(sp["$143"]) + int(sp["$144"])] == "L1"
    ]
    assert landed == [lands_on], f"calibre {version}"
