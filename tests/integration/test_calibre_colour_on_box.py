"""A text colour against the box it sits on, through calibre's real
Stylizer (#383).

calibre must report a box's `background-color` and a paragraph's inherited
`color` as the unit tests' fake style assumes. This converts a book of the
#383 probe's shapes with calibre and checks each paragraph's colour against
Kindle Previewer's decoded values, within 1 per channel.

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

CSS = """div.dark { background-color: #333333; padding: 0.5em; }
div.black { background-color: #000000; padding: 0.5em; }
div.light { background-color: #dddddd; padding: 0.5em; }
p.pdark { background-color: #333333; }
.white { color: #ffffff; } .yellow { color: #ffff00; } .grey { color: #777777; }
div.wbox { color: #ffffff; }"""

#: Each case's text starts with its name.
CASES = [
    ("C1", '<div class="dark"><p class="white">C1 white on a dark box.</p></div>'),
    ("C2", '<div class="dark"><p class="yellow">C2 yellow on a dark box.</p></div>'),
    ("C3", '<div class="dark"><p class="grey">C3 grey on a dark box.</p></div>'),
    ("C4", '<div class="light"><p class="white">C4 white on a light box.</p></div>'),
    ("C6", '<p class="pdark white">C6 white on a paragraph fill.</p>'),
    ("C9", '<div class="black"><p class="white">C9 white on a black box.</p></div>'),
    ("C11", '<div class="dark"><p>C11 no colour on a dark box.</p></div>'),
    ("C14", '<p class="white">C14 white on the page.</p>'),
    # The colour inherited from the box itself.
    ("C15", '<div class="dark wbox"><p>C15 white inherited on a dark box.</p></div>'),
    # A box kfxgen doesn't draw: a box in a <span> in a <div> is walked as
    # one paragraph, so its text is on the page.
    ("C16", '<div><span><div class="dark"><p class="white">C16 white in a flattened sidebar.</p></div></span></div>'),
    # A box in an <aside> in a <div> is drawn (#387).
    ("C17", '<div><aside><div class="dark"><p class="white">C17 white in a nested aside.</p></div></aside></div>'),
]  # fmt: skip

#: Kindle Previewer 4.0.3's value for each (#383 probe); C14 is kfxgen's
#: page value. C6's <p> is a filled box, as Previewer draws it (#643).
EXPECTED = {
    "C1": 0xFFFFFFFF,
    "C2": 0xFFFFFF00,
    "C3": 0xFF9B9B9B,
    "C4": 0xFF616161,
    "C6": 0xFFFFFFFF,
    "C9": 0xFFFFFFFF,
    "C11": 0xFF9C9C9C,
    "C14": 0xFF767676,
    "C15": 0xFFFFFFFF,
    "C16": 0xFF767676,
    "C17": 0xFFFFFFFF,
}


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{case: its paragraph's colour, or its first run's when it has none},
    {case: the fill around it}, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "colour.epub", tmp / "colour.kfx"
    _build_floats(epub, CASES, CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    colours, fills = {}, {}

    def argb(v):
        return None if v is None else int(v) & 0xFFFFFFFF

    def walk(entries, fill):
        for e in entries:
            if isinstance(e, str):
                continue
            st = styles.get(str(e.get("$157")), {})
            here = argb(st.get("$70")) or fill
            if e.get("$145"):
                r = e["$145"]
                case = str(texts[str(r["name"])][r["$403"]]).split()[0]
                runs = [styles.get(str(r.get("$157")), {}) for r in e.get("$142") or []]
                colour = argb(st.get("$19"))
                if colour is None and runs:
                    colour = argb(runs[0].get("$19"))
                colours[case], fills[case] = colour, here
            walk(e.get("$146") or [], here)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return colours, fills, version


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_a_colour_matches_previewers(converted, case):
    colours, _fills, version = converted
    got, want = colours[case], EXPECTED[case]
    assert got is not None and got >> 24 == 0xFF, f"calibre {version}"
    assert all(
        abs(((got >> s) & 0xFF) - ((want >> s) & 0xFF)) <= 1 for s in (16, 8, 0)
    ), f"{got:08X} vs {want:08X}, calibre {version}"


def test_a_black_box_is_filled(converted):
    _colours, fills, version = converted
    assert fills["C9"] == 0xFF000000, f"calibre {version}"
