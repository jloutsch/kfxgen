"""A link to an id after a rule at a table cell's end lands in the cell,
through calibre (#795).

Before, the id went on a rule shared with every rule after it, and the
link landed on the paragraph after the next rule outside the table.
Kindle Previewer 4.0.3 keeps the rule in the cell and lands the link on
it (P795); kfxgen keeps only the cell's lines, so it lands on the last.

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
    "c1.xhtml": (
        "<h1>Cells</h1><p>Before the table.</p>"
        "<table><tr><td><p>B1 first cell line.</p><p>B2 second cell line.</p>"
        '<hr/><a id="leak"></a></td><td><p>B3 other cell.</p></td></tr></table>'
        "<p>After the table.</p><hr/><p>After the loose rule.</p>"
    ),
    "c2.xhtml": (
        "<h1>Later</h1><hr/><p>After the later rule.</p>"
        '<p><a href="c1.xhtml#leak">Back to the cell</a>.</p>'
    ),
}


def test_a_link_to_an_id_after_a_cells_rule_lands_on_its_last_line(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "ct795.epub", tmp / "ct795.kfx"
    _build(epub, FILES, [("Cells", "c1.xhtml"), ("Later", "c2.xhtml")], "ct795")
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
    link = next(e for e in entries if text(e) == "Back to the cell.")
    (target,) = [sp["$179"] for sp in link.get("$142") or [] if "$179" in sp]
    anchor = anchors[str(target)]
    assert text(by_id[anchor["$155"]]) == "B2 second cell line.", f"calibre {version}"
