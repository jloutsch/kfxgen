"""Files nested past any real book keep their text through calibre, and a
link to an id deep inside lands on its paragraph (#794).

Before, 250 nested <div>s raised RecursionError in the block walk and the
spine loop left the file out; inside calibre, 230 converted and 250 did
not (P794). Kindle Previewer 4.0.3 kept every depth to 1,000, as one
paragraph. calibre's own parser stops at about 256 levels: past that it
drops the rest of the file, an EPUB-to-EPUB conversion too, so kfxgen can
keep only what comes before (the heading and text above the nesting).

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

from tests._kfx_introspect import (  # noqa: E402
    by_type,
    iter_entries,
    load_fragments,
    val,
)
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _nest(levels, inner, open_="<div>"):
    return open_ * levels + inner + "</div>" * levels


FILES = {
    "d250.xhtml": "<h1>Two fifty</h1>" + _nest(250, "<p>DEPTHTWOFIFTY here.</p>"),
    "d1000.xhtml": "<h1>Thousand</h1><p>ABOVETHOUSAND here.</p>"
    + _nest(1000, "<p>Lost by calibre.</p>"),
    # The id is on a <div> 151 levels down, past the limit: unwrapped.
    "dl.xhtml": "<h1>Linked</h1>"
    + _nest(150, '<div id="deep"><p>LINKTARGET here.</p></div>'),
    "end.xhtml": '<h1>End</h1><p><a href="dl.xhtml#deep">Back to the deep one</a>.</p>',
}


def test_deeply_nested_files_keep_their_text_and_links(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "dn794.epub", tmp / "dn794.kfx"
    toc = [(n.split(".")[0], n) for n in FILES]
    _build(epub, FILES, toc, "dn794")
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
        return str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])

    words = {w for e in entries for w in text(e).split()}
    for word in ("DEPTHTWOFIFTY", "ABOVETHOUSAND", "LINKTARGET"):
        assert word in words, f"{word} missing, calibre {version}"
    anchors = {str(f.fid): val(f)["$183"] for f in by_type(frags, "$266")}
    by_id = {e["$155"]: e for e in entries}
    link = next(e for e in entries if text(e) == "Back to the deep one.")
    (target,) = [sp["$179"] for sp in link.get("$142") or [] if "$179" in sp]
    assert text(by_id[anchors[str(target)]["$155"]]) == "LINKTARGET here."
