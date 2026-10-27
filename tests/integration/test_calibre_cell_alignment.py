"""Table cells aligned as Kindle Previewer 4.0.3 writes them, through
calibre (#479): left unless the book says otherwise, right in a
right-to-left table, and a cell's paragraphs left too, not justified. Each
expected value is Previewer's own for the same markup (probed 2026-10-26).

Not covered: `<td style="text-align: left" align="right">`, which Previewer
writes left. calibre's CSS flattening has already made it right, with the
same class as `align="right"` alone, before kfxgen sees it.

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
    "c.xhtml": "<h1>Tables</h1>"
    '<table><tr><td align="right">T2 attr</td><td>T2b</td></tr></table>'
    "<table><tr><td>T3 plain</td><td>T3b</td></tr></table>"
    '<table dir="rtl"><tr><td>T4 rtl</td><td>T4b</td></tr></table>'
    "<table><tr><td><p>T5 one</p><p>T5 two</p></td><td>T5b</td></tr></table>"
    '<table><tr><td><p style="text-align: right">T6 p right</p></td>'
    "<td>T6b</td></tr></table>"
    # A cell's or a row's own dir doesn't count (#570).
    '<table dir="rtl"><tr><td dir="ltr">T7 ltr cell</td><td>T7b</td></tr></table>'
    '<table><tr><td dir="rtl">T8 rtl cell</td><td>T8b</td></tr></table>'
    '<table><tr dir="rtl"><td>T9 rtl row</td><td>T9b</td></tr></table>'
    "<p>After.</p>"
}
ALIGN = {"$59": "left", "$61": "right", "$320": "center", "$321": "justify"}


def test_cells_are_aligned_as_previewer_writes_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "ca479.epub", tmp / "ca479.kfx"
    _build(epub, FILES, [("Tables", "c.xhtml")], "ca479")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                if text[:1] == "T" and text[1:2].isdigit():
                    a = styles.get(str(e.get("$157")), {}).get("$34")
                    got[text] = ALIGN.get(str(a)) if a is not None else None
    assert got == {
        "T2 attr": "right",
        "T2b": "left",
        "T3 plain": "left",
        "T3b": "left",
        "T4 rtl": "right",
        "T4b": "right",
        "T5 one": "left",
        "T5 two": "left",
        "T5b": "left",
        "T6 p right": "right",
        "T7 ltr cell": "right",
        "T7b": "right",
        "T8 rtl cell": "left",
        "T8b": "left",
        "T9 rtl row": "left",
        "T9b": "left",
        "T6b": "left",
    }, f"calibre {version}"
