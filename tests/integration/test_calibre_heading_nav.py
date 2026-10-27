"""The heading navigation ($798) through calibre (#461), as Kindle
Previewer 4.0.3 builds it for the same book (probed 2026-10-26): a unit per
heading level, $799 to $804, each holding its headings in reading order;
a TOC entry pointing at a paragraph isn't one of them.

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
    "c1.xhtml": '<h1 id="a">H1 one</h1><p>Body one.</p><h2 id="b">H2 sub</h2>'
    "<p>Body.</p><h3>H3 deeper</h3><p>Body.</p><h4>H4 deep</h4><p>B.</p>"
    '<h5>H5 x</h5><p>B.</p><h6>H6 y</h6><p>B.</p><p id="p">P target para.</p>',
    "c2.xhtml": "<h1>H1 two</h1><p>Body two.</p><h2>H2 again</h2><p>Body.</p>",
}
TOC = [
    ("One", "c1.xhtml#a"),
    ("Sub", "c1.xhtml#b"),
    ("Para", "c1.xhtml#p"),
    ("Two", "c2.xhtml"),
]


def test_heading_navigation_as_previewer_builds_it(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "hn461.epub", tmp / "hn461.kfx"
    _build(epub, FILES, TOC, "hn461")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    text = {
        e["$155"]: str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    }
    (nav,) = by_type(frags, "$389")
    (container,) = [
        getattr(c, "value", c)
        for c in val(nav)[0]["$392"]
        if str(getattr(c, "value", c)["$235"]) == "$798"
    ]
    got = [
        (
            str(getattr(u, "value", u)["$238"]),
            [
                text.get(getattr(k, "value", k)["$246"]["$155"])
                for k in getattr(u, "value", u)["$247"]
            ],
        )
        for u in container["$247"]
    ]
    assert got == [
        ("$799", ["H1 one", "H1 two"]),
        ("$800", ["H2 sub", "H2 again"]),
        ("$801", ["H3 deeper"]),
        ("$802", ["H4 deep"]),
        ("$803", ["H5 x"]),
        ("$804", ["H6 y"]),
    ], f"calibre {version}"
