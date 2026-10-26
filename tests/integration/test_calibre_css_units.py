"""Lengths in in, cm and ex, and text-align start/end, through calibre's
real Stylizer (#486).

kfxgen dropped lengths in in, cm and ex, and wrote start and end as
justify. They are now read as Kindle Previewer 4.0.3 reads them (QA's #452
probe): 1in = 72pt, 1cm = 10mm, 1ex = 0.44em; start is left and end right.
kfxgen writes a margin in the unit it reads, so 1in is 72pt where Previewer
writes 18.75% (6em at 3.125%), as for any pt or mm margin before this.

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
from tests.integration.test_calibre_floats import _build as _build_floats  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """p.in { margin-left: 1in; } p.cm { margin-left: 1cm; } p.ex { margin-left: 2ex; }
p.ti { text-indent: 2ex; } p.st { text-align: start; } p.en { text-align: end; }"""
CASES = [
    ("U1", '<p class="in">U1 one inch.</p>'),
    ("U2", '<p class="cm">U2 one centimetre.</p>'),
    ("U3", '<p class="ex">U3 two ex.</p>'),
    ("U4", '<p class="ti">U4 indent two ex.</p>'),
    ("U5", '<p class="st">U5 start.</p>'),
    ("U6", '<p class="en">U6 end.</p>'),
]


@pytest.fixture(scope="module")
def styles(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "units486.epub", tmp / "units486.kfx"
    _build_floats(epub, CASES, CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    st = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}
    for s in by_type(frags, "$259"):
        for e in iter_entries(val(s)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                r = e["$145"]
                case = str(texts[str(r["name"])][r["$403"]]).split()[0]
                out[case] = st.get(str(e.get("$157")), {})
    return out, version


def _len(style, key):
    v = style.get(key)
    return None if v is None else (round(float(v["$307"]), 3), str(v["$306"]))


def test_margins_in_in_cm_and_ex(styles):
    st, version = styles
    assert _len(st["U1"], "$48") == (72.0, "$318"), f"calibre {version}"  # pt
    assert _len(st["U2"], "$48") == (10.0, "$316"), f"calibre {version}"  # mm
    assert _len(st["U3"], "$48") == (0.88, "$308"), f"calibre {version}"  # em


def test_indent_in_ex(styles):
    st, version = styles
    assert _len(st["U4"], "$36") == (0.88, "$308"), f"calibre {version}"


def test_start_and_end(styles):
    st, version = styles
    assert str(st["U5"].get("$34")) == "$59", f"calibre {version}"
    assert str(st["U6"].get("$34")) == "$61", f"calibre {version}"
