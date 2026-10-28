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
import zipfile
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


@pytest.mark.parametrize(
    ("heading", "listed"),
    [
        ("<h1>Contents</h1>", ["Contents", "Chapter One", "Chapter Two"]),
        ("<p style='font-size:2em'>Contents</p>", ["Chapter One", "Chapter Two"]),
    ],
    ids=["h1", "paragraph"],
)
def test_a_rebuilt_contents_page_keeps_its_headings_level(calibre, heading, listed):  # noqa: F811
    """kfxgen rebuilds a contents page and prints its title as its own
    label; the book's <h1> it replaces is listed, as Previewer lists it, and
    a styled paragraph is not (#580: #577 dropped the <h1>)."""
    files = {
        "toc.xhtml": heading
        + "<p><a href='c1.xhtml'>Chapter One</a></p>"
        + "<p><a href='c2.xhtml'>Chapter Two</a></p>",
        "c1.xhtml": "<h1>Chapter One</h1><p>One text.</p>",
        "c2.xhtml": "<h1>Chapter Two</h1><p>Two text.</p>",
    }
    toc = [
        ("Contents", "toc.xhtml"),
        ("Chapter One", "c1.xhtml"),
        ("Chapter Two", "c2.xhtml"),
    ]
    got = _units(calibre, f"hn580{heading[1]}", files, toc)
    assert got == [("$799", listed)], f"calibre {calibre[2]}: {got}"


def test_a_contents_pages_other_headings_are_kept(calibre):  # noqa: F811
    """A contents page with a styled title and an <h2> under it: Kindle
    Previewer 4.0.3 prints the <h2> and lists it at level 2 (#585, probed
    2026-10-27). kfxgen's rebuilt page dropped it, and its "Contents" took
    the <h2>'s level."""
    tmp, version = calibre[0], calibre[2]
    files = {
        "toc.xhtml": "<p style='font-size:2em;font-weight:bold'>Contents</p>"
        "<h2>Part One</h2>"
        "<p><a href='c1.xhtml'>Chapter One</a></p>"
        "<p><a href='c2.xhtml'>Chapter Two</a></p>",
        "c1.xhtml": "<h1>Chapter One</h1><p>One text.</p>",
        "c2.xhtml": "<h1>Chapter Two</h1><p>Two text.</p>",
    }
    toc = [
        ("Contents", "toc.xhtml"),
        ("Chapter One", "c1.xhtml"),
        ("Chapter Two", "c2.xhtml"),
    ]
    got = _units(calibre, "hn585", files, toc)
    assert got == [
        ("$800", ["Part One"]),
        ("$799", ["Chapter One", "Chapter Two"]),
    ], f"calibre {version}: {got}"
    frags = load_fragments(tmp / "hn585.kfx")
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    first_page = [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for e in iter_entries(val(by_type(frags, "$259")[0])["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    assert first_page == ["Contents", "Part One", "Chapter One", "Chapter Two"], (
        f"calibre {version}: {first_page}"
    )
    # At its own size, as Previewer prints it, not the rebuilt page's.
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (size,) = [
        float(styles[str(e["$157"])]["$16"]["$307"])
        for e in iter_entries(val(by_type(frags, "$259")[0])["$146"])
        if hasattr(e, "get")
        and e.get("$145")
        and str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])]) == "Part One"
    ]
    assert size == 1.5, f"calibre {version}: {size}"


def test_units_follow_each_levels_first_heading(calibre):  # noqa: F811
    """QA's probe on #584: an <h2> before the first <h1>; Previewer writes the
    `$800` unit first."""
    files = {
        "c1.xhtml": "<h2>Preface</h2><p>Preface text.</p>",
        "c2.xhtml": "<h1>One</h1><p>One text.</p>",
        "c3.xhtml": "<h1>Two</h1><p>Two text.</p><h2>Two sub</h2><p>Sub text.</p>",
    }
    toc = [("Preface", "c1.xhtml"), ("One", "c2.xhtml"), ("Two", "c3.xhtml")]
    got = _units(calibre, "hn584", files, toc)
    assert got == [
        ("$800", ["Preface", "Two sub"]),
        ("$799", ["One", "Two"]),
    ], f"calibre {calibre[2]}: {got}"


def test_a_linked_heading_on_a_contents_page_prints_once(calibre):  # noqa: F811
    """A part title that is itself a link is one of the page's entries: it
    prints once, as its linked line, not again as a kept heading (#585)."""
    tmp, version = calibre[0], calibre[2]
    files = {
        "toc.xhtml": "<h1>Contents</h1>"
        "<h2><a href='p2.xhtml'>Part Two</a></h2>"
        "<p><a href='c1.xhtml'>Chapter One</a></p>",
        "p2.xhtml": "<h1>Part Two</h1><p>Part text.</p>",
        "c1.xhtml": "<h1>Chapter One</h1><p>One text.</p>",
    }
    toc = [
        ("Contents", "toc.xhtml"),
        ("Part Two", "p2.xhtml"),
        ("Chapter One", "c1.xhtml"),
    ]
    _units(calibre, "hn585l", files, toc)
    frags = load_fragments(tmp / "hn585l.kfx")
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    first_page = [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for e in iter_entries(val(by_type(frags, "$259")[0])["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    assert first_page == ["Contents", "Part Two", "Chapter One"], (
        f"calibre {version}: {first_page}"
    )


def _first_page(kfx):
    """[(text, font size)] of the book's first storyline's text entries."""
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    return [
        (
            str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])]),
            float((styles[str(e["$157"])].get("$16") or {"$307": 1})["$307"]),
        )
        for e in iter_entries(val(by_type(frags, "$259")[0])["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]


def test_parts_listed_in_the_toc_print_once_in_order(calibre):  # noqa: F811
    """Parts that are TOC entries, shown on the contents page as unlinked
    <h2>s: each prints once, where Previewer prints it (QA on #594: twice, and
    "Part Two" a line early)."""
    files = {
        "toc.xhtml": "<h1>Contents</h1><h2>Part One</h2>"
        "<p><a href='c1.xhtml'>Chapter One</a></p>"
        "<p><a href='c2.xhtml'>Chapter Two</a></p><h2>Part Two</h2>"
        "<p><a href='c3.xhtml'>Chapter Three</a></p>",
        "p1.xhtml": "<h1>Part One</h1><p>Part text.</p>",
        "c1.xhtml": "<h1>Chapter One</h1><p>One text.</p>",
        "c2.xhtml": "<h1>Chapter Two</h1><p>Two text.</p>",
        "p2.xhtml": "<h1>Part Two</h1><p>Part text.</p>",
        "c3.xhtml": "<h1>Chapter Three</h1><p>Three text.</p>",
    }
    toc = [
        ("Contents", "toc.xhtml"),
        ("Part One", "p1.xhtml"),
        ("Chapter One", "c1.xhtml"),
        ("Chapter Two", "c2.xhtml"),
        ("Part Two", "p2.xhtml"),
        ("Chapter Three", "c3.xhtml"),
    ]
    _units(calibre, "hn594", files, toc)
    lines = [t for t, _ in _first_page(calibre[0] / "hn594.kfx")]
    assert lines == [
        "Contents",
        "Part One",
        "Chapter One",
        "Chapter Two",
        "Part Two",
        "Chapter Three",
    ], f"calibre {calibre[2]}: {lines}"


def test_a_kept_heading_keeps_its_size_in_a_book_with_a_cover(calibre):  # noqa: F811
    """The cover chapter moves the chapters along; the kept heading still
    takes its own size (QA on #594: 1em with a cover)."""
    from tests._helpers import colour_jpeg_of

    tmp = calibre[0]
    files = {
        "toc.xhtml": "<p style='font-size:2em;font-weight:bold'>Contents</p>"
        "<h2>Part One</h2>"
        "<p><a href='c1.xhtml'>Chapter One</a></p>",
        "c1.xhtml": "<h1>Chapter One</h1><p>One text.</p>",
    }
    toc = [("Contents", "toc.xhtml"), ("Chapter One", "c1.xhtml")]
    plain = tmp / "hn594c-plain.epub"
    _build(plain, files, toc, "hn594c")
    epub, kfx = tmp / "hn594c.epub", tmp / "hn594c.kfx"
    with zipfile.ZipFile(plain) as src, zipfile.ZipFile(epub, "w") as z:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith(".opf"):
                data = (
                    data.decode()
                    .replace(
                        "</metadata>", '<meta name="cover" content="cov"/></metadata>'
                    )
                    .replace(
                        "<manifest>",
                        '<manifest><item id="cov" href="cover.jpg" '
                        'media-type="image/jpeg"/>',
                    )
                    .encode()
                )
            z.writestr(info.filename, data, compress_type=info.compress_type)
        z.writestr("cover.jpg", colour_jpeg_of("RGB", (600, 800), (30, 60, 120)))
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    # The book has kfxgen's cover section: a cover landmark.
    (nav,) = by_type(frags, "$389")
    kinds = [str(getattr(c, "value", c)["$235"]) for c in val(nav)[0]["$392"]]
    assert "$236" in kinds, f"calibre {calibre[2]}: no cover, {kinds}"
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    sizes = [
        float((styles[str(e["$157"])].get("$16") or {"$307": 1})["$307"])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get")
        and e.get("$145")
        and str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])]) == "Part One"
    ]
    assert sizes == [1.5], f"calibre {calibre[2]}: {sizes}"


def test_kept_headings_dont_set_the_books_main_size(calibre):  # noqa: F811
    """QA's K5 on #594: two kept part headings outweigh the chapters' short
    body text and became the book's main size, so they printed at 1em. They
    are left out of the count, as on main, where they weren't printed."""
    files = {
        "toc.xhtml": "<h1>Contents</h1><h2>Part One</h2>"
        "<p><a href='c1.xhtml'>Chapter One</a></p><h2>Part Two</h2>"
        "<p><a href='c2.xhtml'>Chapter Two</a></p>",
        "c1.xhtml": "<h1>Chapter One</h1><p>Body 1.</p>",
        "c2.xhtml": "<h1>Chapter Two</h1><p>Body 2.</p>",
    }
    toc = [
        ("Contents", "toc.xhtml"),
        ("Chapter One", "c1.xhtml"),
        ("Chapter Two", "c2.xhtml"),
    ]
    _units(calibre, "hn594k5", files, toc)
    sizes = dict(_first_page(calibre[0] / "hn594k5.kfx"))
    assert (sizes["Part One"], sizes["Part Two"]) == (1.5, 1.5), (
        f"calibre {calibre[2]}: {sizes}"
    )
