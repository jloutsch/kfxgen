"""Colour names, hsl() and translucent colours, through calibre's real
Stylizer (#383).

calibre must hand kfxgen a name, an hsl() and an rgba() as written, on a
paragraph's colour, a box's fill and its border; the unit tests' fake style
can only assume it. This converts a book of the #383 probe's shapes with
calibre and checks each value: Previewer's for a name or hsl(), the colour
blended onto the page for a translucent one.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
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

CSS = """.T1 { color: darkred; } .T3 { color: hsl(0, 100%, 25%); }
.T6 { color: rgba(255, 0, 0, 0.25); } .T7 { color: rgba(0, 0, 0, 0.9); }
.T12 { color: lightgray; } .white { color: #ffffff; }
.T13 { color: rgb(100%, 0%, 0%); } .T14 { color: rgba(100%, 0%, 0%, 0.5); }
div.F1 { background-color: rgba(0, 0, 0, 0.1); padding: 0.5em; }
div.F3 { background-color: hsl(120, 50%, 80%); padding: 0.5em; }
div.F4 { background-color: lightgray; padding: 0.5em; }
div.F5 { background-color: rgba(0, 0, 0, 0.6); padding: 0.5em; }
div.B2 { border: 2px solid darkgray; padding: 0.5em; }
div.B3 { border: 2px solid rgba(255, 0, 0, 0.5); padding: 0.5em; }"""

#: Each case's text starts with its name.
CASES = [
    ("T1", '<p class="T1">T1 darkred text.</p>'),
    ("T3", '<p class="T3">T3 hsl text.</p>'),
    ("T6", '<p class="T6">T6 translucent red text.</p>'),
    ("T7", '<p class="T7">T7 nearly opaque black text.</p>'),
    ("T12", '<p class="T12">T12 lightgray text.</p>'),
    ("T13", '<p class="T13">T13 percentage red text.</p>'),
    ("T14", '<p class="T14">T14 translucent percentage red text.</p>'),
    ("F1", '<div class="F1"><p>F1 text on a translucent shade.</p></div>'),
    ("F3", '<div class="F3"><p>F3 text on an hsl fill.</p></div>'),
    ("F4", '<div class="F4"><p>F4 text on a lightgray fill.</p></div>'),
    ("F5", '<div class="F5"><p class="white">F5 white on translucent black.</p></div>'),
    ("B2", '<div class="B2"><p>B2 text in a darkgray border.</p></div>'),
    ("B3", '<div class="B3"><p>B3 text in a translucent red border.</p></div>'),
]  # fmt: skip


def _near(got, want):
    return got is not None and all(
        abs(((got >> s) & 0xFF) - ((want >> s) & 0xFF)) <= 1 for s in (24, 16, 8, 0)
    )


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{case: (its paragraph's colour, its box's fill, its box's border
    colour)}, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "colour-values.epub", tmp / "colour-values.kfx"
    _build_floats(epub, CASES, CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}

    def argb(v):
        return None if v is None else int(v) & 0xFFFFFFFF

    def walk(entries, box):
        for e in entries:
            if isinstance(e, str):
                continue
            st = styles.get(str(e.get("$157")), {})
            here = st if str(e.get("$159")) == "$270" else box
            if e.get("$145"):
                r = e["$145"]
                case = str(texts[str(r["name"])][r["$403"]]).split()[0]
                out[case] = (
                    argb(st.get("$19")),
                    argb((box or {}).get("$70")),
                    argb((box or {}).get("$83")),
                )
            walk(e.get("$146") or [], here)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return out, version


@pytest.mark.parametrize(
    ("case", "colour"),
    [
        # Previewer's values.
        ("T1", 0xFF8B0000),
        ("T3", 0xFF7F0000),
        ("T12", 0xFF767676),
        ("T13", 0xFFEE0000),
        # Blended onto the page: 90% black.
        ("T7", 0xFF1A1A1A),
    ],
)
def test_a_text_colour(converted, case, colour):
    by_case, version = converted
    assert _near(by_case[case][0], colour), f"calibre {version}: {by_case[case]}"


@pytest.mark.parametrize("case", ["T6", "T14"])
def test_translucent_text_is_opaque_and_pink(converted, case):
    by_case, version = converted
    got = by_case[case][0]
    r, g, b = (got >> 16) & 0xFF, (got >> 8) & 0xFF, got & 0xFF
    assert got >> 24 == 0xFF and r > g == b > 0, f"calibre {version}: {got:08X}"


@pytest.mark.parametrize(
    ("case", "fill"),
    [
        # Blended onto the page: 10% black, not a black box.
        ("F1", 0xFFE6E6E6),
        ("F3", 0xFFB3E6B3),
        ("F4", 0xFFD3D3D3),
        ("F5", 0xFF666666),
    ],
)
def test_a_box_fill(converted, case, fill):
    by_case, version = converted
    assert _near(by_case[case][1], fill), f"calibre {version}: {by_case[case]}"


def test_text_on_a_translucent_shade_stays_black(converted):
    by_case, version = converted
    assert by_case["F1"][0] is None, f"calibre {version}: {by_case['F1']}"


def test_white_on_translucent_black_stays_white(converted):
    by_case, version = converted
    assert by_case["F5"][0] == 0xFFFFFFFF, f"calibre {version}: {by_case['F5']}"


@pytest.mark.parametrize(("case", "colour"), [("B2", 0xFFA9A9A9), ("B3", 0xFFFF8080)])
def test_a_border_colour(converted, case, colour):
    by_case, version = converted
    assert _near(by_case[case][2], colour), f"calibre {version}: {by_case[case]}"
