"""http, https and mailto links stay links through calibre (#474), as Kindle
Previewer 4.0.3 writes them: a $266 per address, holding it in $186, named
by the run's $179 (probed 2026-10-26).

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
    '<h1>X</h1><p>X1 see <a href="http://example.com/">the site</a> now.</p>'
    '<p>X2 mail <a href="mailto:x@example.com">us</a> today.</p>'
    '<p>X3 secure <a href="https://example.org/a?b=1#c">page</a> here.</p>'
    '<p>X4 twice <a href="http://example.com/">again</a> end.</p>'
)


def test_external_links_stay_links(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "xl474.epub", tmp / "xl474.kfx"
    _build(epub, {"c.xhtml": BODY}, [("X", "c.xhtml")], "xl474")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    anchors = {str(f.fid): val(f) for f in by_type(frags, "$266")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                for sp in e.get("$142") or []:
                    if "$179" in sp:
                        word = text[int(sp["$143"]) : int(sp["$143"]) + int(sp["$144"])]
                        got[word] = str(anchors[str(sp["$179"])].get("$186"))
    assert got == {
        "the site": "http://example.com/",
        "us": "mailto:x@example.com",
        "page": "https://example.org/a?b=1#c",
        "again": "http://example.com/",
    }, f"calibre {version}"
    assert sum(1 for a in anchors.values() if "$186" in a) == 3, f"calibre {version}"
