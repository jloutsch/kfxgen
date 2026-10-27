"""A link to an id after a paragraph's last word lands on its last
character through calibre (#487), as Kindle Previewer 4.0.3 writes it:
offset len - 1, not len.

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
    "c1.xhtml": '<h1>One</h1><p>See <a href="c2.xhtml#end">L1</a>.</p>',
    "c2.xhtml": '<h1>Two</h1><p>Eighteen chars ok.<a id="end"/></p><p>Next.</p>',
}


def test_a_link_to_a_paragraphs_end_lands_on_its_last_character(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "ea487.epub", tmp / "ea487.kfx"
    _build(epub, FILES, [("One", "c1.xhtml"), ("Two", "c2.xhtml")], "ea487")
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

    anchors = {str(f.fid): val(f)["$183"] for f in by_type(frags, "$266")}
    by_id = {e["$155"]: e for e in entries}
    link = next(e for e in entries if text(e) == "See L1.")
    (target,) = [sp["$179"] for sp in link.get("$142") or [] if "$179" in sp]
    anchor = anchors[str(target)]
    assert text(by_id[anchor["$155"]]) == "Eighteen chars ok.", f"calibre {version}"
    assert int(anchor.get("$143", 0) or 0) == 17, f"calibre {version}"
