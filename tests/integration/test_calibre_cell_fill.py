"""A table cell's fill reaches the book, through calibre (#502), as Kindle
Previewer 4.0.3 writes it: $70 on the cell, from background-color or the
background shorthand. White text on a black cell stays white: its colour
is measured against the cell's fill, not the page (#383).

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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

TABLE = (
    "<h1>T</h1><table>"
    '<tr><td style="background-color: #eeeeee">F1 grey</td><td>F1b</td></tr>'
    '<tr><td style="background: #336699">F2 blue</td>'
    '<td style="background-color: #000000; color: #ffffff">F2 black</td></tr>'
    "<tr><td><p>F3 one</p><p>F3 two</p></td>"
    '<td style="background-color: #ffeecc"><p>F4 one</p><p>F4 two</p></td></tr>'
    "</table><p>After.</p>"
)


def test_a_cells_fill_is_written(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "cf502.epub", tmp / "cf502.kfx"
    _build(epub, {"c.xhtml": TABLE}, [("T", "c.xhtml")], "cf502")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    fills, colours = {}, {}

    def walk(e, fill):
        if not hasattr(e, "get"):
            return
        s = styles.get(str(e.get("$157")), {})
        own = s.get("$70")
        here = int(own) if own is not None else fill
        if e.get("$145"):
            text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            if text[:1] == "F" and text[1:2].isdigit():
                fills[text] = here
                if s.get("$19") is not None:
                    colours[text] = int(s["$19"])
        for k in e.get("$146") or []:
            walk(k, here)

    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            walk(e, None)
    assert fills == {
        "F1 grey": 0xFFEEEEEE,
        "F1b": None,
        "F2 blue": 0xFF336699,
        "F2 black": 0xFF000000,
        "F3 one": None,
        "F3 two": None,
        "F4 one": 0xFFFFEECC,
        "F4 two": 0xFFFFEECC,
    }, f"calibre {version}"
    assert colours.get("F2 black", 0xFFFFFFFF) == 0xFFFFFFFF, f"calibre {version}"
