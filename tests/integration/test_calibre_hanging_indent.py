"""A hanging indent through calibre (#465): `text-indent: -1em;
margin-left: 1em` keeps both, equal and opposite in one unit, so the first
line starts at the page's edge and the rest hang. Kindle Previewer 4.0.3
writes them as -3.125% and 3.125% (QA's measurement). An uncovered
negative indent is still dropped.

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

BODY = (
    "<h1>H</h1>"
    '<p style="text-indent: -1em; margin-left: 1em">H1 hanging entry, long enough '
    "to wrap onto a second line on any reader.</p>"
    '<p style="text-indent: -1em">H2 no margin under it.</p>'
)


def test_a_covered_hanging_indent_is_kept(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "hi465.epub", tmp / "hi465.kfx"
    _build(epub, {"c.xhtml": BODY}, [("H", "c.xhtml")], "hi465")
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
                if text[:1] == "H" and text[1:2].isdigit():
                    s = styles[str(e["$157"])]
                    got[text[:2]] = tuple(
                        (float(str(s[k]["$307"])), str(s[k]["$306"]))
                        if k in s
                        else None
                        for k in ("$36", "$48")
                    )
    (indent, margin) = got["H1"]
    assert indent[0] < 0 and indent[0] == -margin[0], f"calibre {version}: {got}"
    assert indent[1] == margin[1], f"calibre {version}: {got}"
    assert got["H2"][0] is None or got["H2"][0][0] >= 0, f"calibre {version}: {got}"
