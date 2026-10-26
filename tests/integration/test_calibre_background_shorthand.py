"""A box's fill written with the `background:` shorthand is drawn, through
calibre's real Stylizer (#464).

calibre leaves the shorthand unexpanded, and kfxgen read only
`background-color`, so `background: #eeeeee` drew no fill, and an
unbordered box filled that way wasn't drawn as a box at all. Kindle
Previewer 4.0.3 writes the shorthand's colour as it writes
`background-color`'s (QA's #452 probe: `$70` 4293848814 for #eeeeee).

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
from tests.integration.test_calibre_floats import _build as _build_floats  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """div.b { border: 1px solid #000; padding: 0.5em; }
div.long { background-color: #eeeeee; }
div.short { background: #eeeeee; }
div.plain { background: #ffeecc; padding: 0.5em; }
div.mixed { background: url(none.png) no-repeat top left #ddeeff; padding: 0.5em; }
div.func { background: rgb(221, 238, 255); padding: 0.5em; }
div.nofill { background: none; }"""

CASES = [
    ("B1", '<div class="b long"><p>B1 bordered, background-color.</p></div>'),
    ("B2", '<div class="b short"><p>B2 bordered, background shorthand.</p></div>'),
    ("B3", '<div class="plain"><p>B3 unbordered, background shorthand.</p></div>'),
    ("B4", '<div class="mixed"><p>B4 shorthand with a picture first.</p></div>'),
    ("B5", '<div class="func"><p>B5 shorthand rgb().</p></div>'),
    ("B6", '<div class="b nofill"><p>B6 bordered, background none.</p></div>'),
]
EXPECTED = {
    "B1": 0xFFEEEEEE,
    "B2": 0xFFEEEEEE,
    "B3": 0xFFFFEECC,
    "B4": 0xFFDDEEFF,
    "B5": 0xFFDDEEFF,
    "B6": None,
}


@pytest.fixture(scope="module")
def fills(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "bg464.epub", tmp / "bg464.kfx"
    _build_floats(epub, CASES, CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}

    def walk(entries, fill):
        for e in entries:
            if isinstance(e, str):
                continue
            v = styles.get(str(e.get("$157")), {}).get("$70")
            here = int(v) & 0xFFFFFFFF if v is not None else fill
            if e.get("$145"):
                r = e["$145"]
                found[str(texts[str(r["name"])][r["$403"]]).split()[0]] = here
            walk(e.get("$146") or [], here)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return found, version


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_a_shorthand_fill_is_drawn(fills, case):
    found, version = fills
    assert found[case] == EXPECTED[case], f"calibre {version}"
