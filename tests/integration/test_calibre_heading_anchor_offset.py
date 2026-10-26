"""A link to an id inside a chapter's opening heading lands on its word,
through calibre (#491).

Since #481 kfxgen's heading prints the book's heading in place of a
differing label, and the heading's ids went to its start (offset 0).
Kindle Previewer 4.0.3, and kfxgen before #489, land on the word (QA's
probe: offset 8 for "Nine" in "Chapter Nine Begins").

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

FILES = {
    "c1.xhtml": '<h1>One</h1><p>See <a href="c2.xhtml#hx">L1</a>.</p>',
    "c2.xhtml": '<h1>Chapter <span id="hx">Nine</span> Begins</h1><p>Body.</p>',
}
TOC = [("One", "c1.xhtml"), ("Chapter Nine", "c2.xhtml")]


def test_a_link_into_the_heading_lands_on_its_word(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "hoff491.epub", tmp / "hoff491.kfx"
    _build(epub, FILES, TOC, "hoff491")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]

    def text(e):
        r = e["$145"]
        return str(content[str(r["name"])][int(r["$403"])])

    anchors = {str(f.fid): val(f)["$183"] for f in by_type(frags, "$266")}
    by_id = {e["$155"]: e for e in entries}
    link = next(e for e in entries if text(e) == "See L1.")
    (target,) = [sp["$179"] for sp in link.get("$142") or [] if "$179" in sp]
    anchor = anchors[str(target)]
    assert text(by_id[anchor["$155"]]) == "Chapter Nine Begins", f"calibre {version}"
    assert int(anchor.get("$143", 0) or 0) == 8, f"calibre {version}"
