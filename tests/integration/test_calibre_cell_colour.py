"""A text colour in a filled table cell, through calibre (#740).

Probe P740 (Kindle Previewer 4.0.3): a colour in a cell is measured against
the cell's fill only, as #383 measures a box's. With `p { margin: 0 }` the
cell and its one paragraph reach kfxgen as one block, with no open/close
pair, and kfxgen measured the colour lightened for the fill a second time
against the white page, darkening it: #701880 on #222222 came out 0xFFC129DD,
Previewer's is 0xFFDE30FE. The same for `<th>` and for a coloured span.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
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

CSS = """p { margin: 0 }
.c222 { background-color: #222222 } .c333 { background-color: #333333 }
.c000 { background-color: #000000 }
.pu { color: #701880 } .br { color: #8b4513 } .bl { color: #336699 }"""


def _table(tag, cls, inner):
    return f'<table><tr><{tag} class="{cls}">{inner}</{tag}><td>other</td></tr></table>'


#: Each case's text starts with its name; (case, markup, word, Previewer's
#: colour for that word).
CASES = [
    ("P01", _table("td", "c222", '<p class="pu">P01 td purple.</p>'), "purple", 0xFFDE30FE),
    ("P02", _table("th", "c222", '<p class="pu">P02 th purple.</p>'), "purple", 0xFFDE30FE),
    ("P03", _table("th", "c333", '<p class="br">P03 th brown.</p>'), "brown", 0xFFF27821),
    ("P07", _table("td", "c222", '<p>P07 plain <span class="pu">purplespan</span> end.</p>'), "purplespan", 0xFFDE30FE),
    ("P08", _table("th", "c222", '<p>P08 plain <span class="pu">purplespan</span> end.</p>'), "purplespan", 0xFFDE30FE),
    ("P11", _table("td", "c000", '<p class="bl">P11 td blue.</p>'), "blue", 0xFF3C78B4),
    ("P04", _table("td", "c222", '<p class="pu">P04 two.</p><p class="pu">P04b second.</p>'), "two", 0xFFDE30FE),
]  # fmt: skip


@pytest.fixture(scope="module")
def drawn(calibre):  # noqa: F811
    """{case: {word: the colour it is drawn in}} and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "cells740.epub", tmp / "cells740.kfx"
    _build_floats(epub, [(c, m) for c, m, _w, _p in CASES], CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}

    def argb(st):
        v = st.get("$19")
        return None if v is None else int(v) & 0xFFFFFFFF

    def walk(entries, inherited):
        for e in entries:
            if isinstance(e, str):
                continue
            st = styles.get(str(e.get("$157")), {})
            here = argb(st) or inherited
            if e.get("$145"):
                r = e["$145"]
                text = str(texts[str(r["name"])][r["$403"]])
                runs = [
                    (int(x["$143"]), int(x["$143"]) + int(x["$144"]), argb(styles.get(str(x.get("$157")), {})))
                    for x in e.get("$142") or []
                ]  # fmt: skip
                words, pos = {}, 0
                for w in text.split():
                    at = text.index(w, pos)
                    pos = at + len(w)
                    over = [c for a, b, c in runs if a <= at and pos <= b and c]
                    words[w.strip(".")] = over[-1] if over else here
                out[text.split()[0]] = words
            walk(e.get("$146") or [], here)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return out, version


@pytest.mark.parametrize(
    ("case", "word", "previewer"), [(c, w, p) for c, _m, w, p in CASES]
)
def test_a_colour_in_a_cell_matches_previewers(drawn, case, word, previewer):
    out, version = drawn
    got = out[case][word]
    assert got is not None and got >> 24 == 0xFF, f"calibre {version}"
    # Within 1 per channel: Previewer's own rounding (0xFFF27821 is kfxgen's
    # 0xFFF27922, as in #383's cases).
    assert all(
        abs(((got >> s) & 0xFF) - ((previewer >> s) & 0xFF)) <= 1 for s in (16, 8, 0)
    ), f"{got:08X} vs Previewer's {previewer:08X}, calibre {version}"
