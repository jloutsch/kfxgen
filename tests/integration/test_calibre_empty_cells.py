"""An empty table cell is a cell with nothing in it, through calibre (#480),
as Kindle Previewer 4.0.3 writes `<td></td>` and `<td> </td>`: no text entry
of a space.

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


def test_an_empty_cell_has_no_text_entry(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "ec480.epub", tmp / "ec480.kfx"
    _build(
        epub,
        {
            "c.xhtml": "<h1>T</h1><table><tr><td>E1 a</td><td></td><td> </td></tr>"
            "</table><p>After.</p>"
        },
        [("T", "c.xhtml")],
        "ec480",
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    (row,) = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and str(e.get("$159")) == "$279"
    ]

    def shape(cell):
        if cell.get("$145"):
            ref = cell["$145"]
            return str(content[str(ref["name"])][int(ref["$403"])])
        return len(cell.get("$146") or [])

    assert [shape(c) for c in row["$146"]] == ["E1 a", 0, 0], f"calibre {version}"
