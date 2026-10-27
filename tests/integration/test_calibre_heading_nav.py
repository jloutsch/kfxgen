"""The heading navigation ($798) through calibre (#461), as Kindle
Previewer 4.0.3 builds it for the same book (probed 2026-10-26): a unit per
heading level, $799 to $804, each holding its headings in reading order;
a TOC entry pointing at a paragraph isn't one of them. Only <h1>-<h6> are,
and a book with none has no $798 (#575).

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


def _units(calibre, name, files, toc):  # noqa: F811
    """[(unit, [heading texts])] of the converted book's $798, or None when
    it has none."""
    tmp = calibre[0]
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _build(epub, files, toc, name)
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
    containers = [
        getattr(c, "value", c)
        for c in val(nav)[0]["$392"]
        if str(getattr(c, "value", c)["$235"]) == "$798"
    ]
    if not containers:
        return None
    (container,) = containers
    return [
        (
            str(getattr(u, "value", u)["$238"]),
            [
                text.get(getattr(k, "value", k)["$246"]["$155"])
                for k in getattr(u, "value", u)["$247"]
            ],
        )
        for u in container["$247"]
    ]


def test_heading_navigation_as_previewer_builds_it(calibre):  # noqa: F811
    version = calibre[2]
    got = _units(calibre, "hn461", FILES, TOC)
    assert got == [
        ("$799", ["H1 one", "H1 two"]),
        ("$800", ["H2 sub", "H2 again"]),
        ("$801", ["H3 deeper"]),
        ("$802", ["H4 deep"]),
        ("$803", ["H5 x"]),
        ("$804", ["H6 y"]),
    ], f"calibre {version}"


STYLED = (
    "<p style='font-size:2em;font-weight:bold'>Chapter {n}</p>"
    "<p style='font-size:1.5em;font-weight:bold'>Part {n}</p><p>Body {n}.</p>"
)
TAGGED = "<h1>Chapter {n}</h1><h2>Part {n}</h2><p>Body {n}.</p>"
CHAPTERS = [(f"Chapter {n}", f"c{n}.xhtml") for n in range(1, 5)]


def test_only_h1_to_h6_are_listed(calibre):  # noqa: F811
    """Chapters 1 and 3 with heading tags, 2 and 4 with styled paragraphs:
    Previewer lists 1 and 3 only (#575, probed 2026-10-27)."""
    files = {
        f"c{n}.xhtml": (TAGGED if n % 2 else STYLED).format(n=n) for n in range(1, 5)
    }
    got = _units(calibre, "hn575m", files, CHAPTERS)
    assert got == [
        ("$799", ["Chapter 1", "Chapter 3"]),
        ("$800", ["Part 1", "Part 3"]),
    ], f"calibre {calibre[2]}"


def test_a_book_without_headings_has_no_heading_navigation(calibre):  # noqa: F811
    """Previewer writes no $798 for it (#575, probed 2026-10-27)."""
    files = {f"c{n}.xhtml": STYLED.format(n=n) for n in range(1, 5)}
    assert _units(calibre, "hn575n", files, CHAPTERS) is None, f"calibre {calibre[2]}"
