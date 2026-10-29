"""Part headings on a rebuilt contents page (#595), as Kindle Previewer
4.0.3 prints them (probed 2026-10-28): a part's `<h2>` that is also a TOC
entry prints at the heading's size, bold, and is a level-2 heading in the
heading navigation (`$798`). kfxgen printed it as a plain contents line,
0.75em and underlined, with no level. kfxgen keeps the line's link (every
line on its page is one); Previewer links it only when the `<h2>` holds a
link, and underlines neither.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

PARTS = [
    ("p1.xhtml", "Part One", ["c1.xhtml", "c2.xhtml"]),
    ("p2.xhtml", "Part Two", ["c3.xhtml"]),
]
TITLES = {
    "c1.xhtml": "Chapter One",
    "c2.xhtml": "Chapter Two",
    "c3.xhtml": "Chapter Three",
}


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>'
        f'{title}</title><link rel="stylesheet" href="s.css" type="text/css"/></head>'
        f"<body>{body}</body></html>"
    )


def _epub(out, key, linked_part):
    contents = "<h1>Contents</h1>"
    for n, (pf, pt, chs) in enumerate(PARTS):
        if linked_part and n == 1:
            contents += f'<h2><a href="{pf}">{pt}</a></h2>'
        else:
            contents += f"<h2>{pt}</h2>"
        contents += "".join(
            f'<p class="toc"><a href="{c}">{TITLES[c]}</a></p>' for c in chs
        )
    files = {"contents.xhtml": _page("Contents", contents)}
    for pf, pt, chs in PARTS:
        files[pf] = _page(pt, f"<h1>{pt}</h1><p>{pt} opening words.</p>")
        for c in chs:
            files[c] = _page(
                TITLES[c], f"<h2>{TITLES[c]}</h2>" + f"<p>{TITLES[c]} words.</p>" * 15
            )
    spine = list(files)
    css = "h1{font-size:2em} h2{font-size:1.5em; font-weight:bold} p.toc{margin:0}"
    items = (
        "".join(
            f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
            for n, f in enumerate(spine)
        )
        + '<item id="css" href="s.css" media-type="text/css"/>'
    )
    points, order = "", 0
    for pf, pt, chs in PARTS:
        order += 1
        kids = ""
        for c in chs:
            order += 1
            kids += (
                f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{TITLES[c]}</text>'
                f'</navLabel><content src="{c}"/></navPoint>'
            )
        points += (
            f'<navPoint id="p{order}" playOrder="{order}"><navLabel><text>{pt}</text></navLabel>'
            f'<content src="{pf}"/>{kids}</navPoint>'
        )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        f'version="2005-1"><head/><docTitle><text>X</text></docTitle><navMap>'
        f'<navPoint id="c0" playOrder="0"><navLabel><text>Contents</text></navLabel>'
        f'<content src="contents.xhtml"/></navPoint>{points}</navMap></ncx>'
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>H595 {key}</dc:title><dc:identifier id="id">h595-{key}</dc:identifier>'
        f'<dc:language>en</dc:language></metadata><manifest>{items}<item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">'
        + "".join(f'<itemref idref="i{n}"/>' for n in range(len(spine)))
        + "</spine></package>"
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    for doc in [opf, ncx, container, *files.values()]:
        etree.fromstring(doc.encode())
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        z.writestr("s.css", css)
        for name, doc in files.items():
            z.writestr(name, doc)


def _plain(x):
    return getattr(x, "value", x)


def _contents(kfx):
    """The Contents page's lines: (text, size, weight, linked, underlined),
    and the heading navigation's level-2 headings."""
    frags = load_fragments(kfx)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    words, lines = {}, []
    for st in by_type(frags, "$259"):
        rows = []
        for e in iter_entries(val(st)["$146"]):
            if not (hasattr(e, "get") and e.get("$145")):
                continue
            t = str(text[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            words[int(e["$155"])] = t
            y = styles.get(str(e.get("$157")), {})
            runs = [_plain(r) for r in e.get("$142") or ()]
            size = _plain(y.get("$16"))
            rows.append(
                (
                    t,
                    float(size["$307"]) if size is not None else 1.0,
                    str(y.get("$13")),
                    any("$179" in [str(k) for k in r] for r in runs),
                    any(
                        str(styles.get(str(r.get("$157")), {}).get("$23")) == "$328"
                        for r in runs
                    ),
                )
            )
        if rows and rows[0][0] == "Contents":
            lines = rows
    level2 = []
    for f in by_type(frags, "$389"):
        for nav in val(f):
            for c in nav.get("$392") or ():
                c = _plain(c)
                if str(c.get("$235")) == "$798":
                    for unit in c.get("$247") or ():
                        unit = _plain(unit)
                        if str(unit.get("$238")) == "$800":
                            level2 = [
                                words.get(int(_plain(_plain(k)["$246"])["$155"]))
                                for k in unit.get("$247") or ()
                            ]
    return lines, level2


@pytest.mark.parametrize("linked", [False, True], ids=["plain", "linked"])
def test_a_part_heading_prints_as_a_heading(calibre, linked):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"h595-{linked}.epub", tmp / f"h595-{linked}.kfx"
    _epub(epub, f"t{linked}", linked)
    _ebook_convert(calibre, epub, kfx, True)
    lines, level2 = _contents(kfx)
    parts = {t: (size, weight, link, under) for t, size, weight, link, under in lines}
    for part in ("Part One", "Part Two"):
        size, weight, link, under = parts[part]
        # Previewer: 1.5em and bold, not underlined.
        assert (size, weight, under) == (1.5, "$361", False), (
            f"calibre {version}: {lines}"
        )
        assert link, f"calibre {version}: {part} lost its link"
    # A chapter line is still kfxgen's contents line.
    assert parts["Chapter One"][3], f"calibre {version}: {lines}"
    # Previewer's level 2: the page's part headings, then the chapters'.
    assert level2[:2] == ["Part One", "Part Two"], f"calibre {version}: {level2}"


def _notes_epub(out):
    """QA's N5 on #613: a Notes heading at the top of the page, and a Notes
    link last, the TOC listing Notes."""
    page = (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>{t}</title></head>'
        "<body>{b}</body></html>"
    )
    files = {
        "contents.xhtml": page.format(
            t="Contents",
            b='<h1>Contents</h1><h3>Notes</h3><p><a href="c1.xhtml">Chapter One</a></p>'
            '<p><a href="notes.xhtml">Notes</a></p>',
        ),
        "c1.xhtml": page.format(
            t="One", b="<h2>Chapter One</h2>" + "<p>Words.</p>" * 10
        ),
        "notes.xhtml": page.format(t="Notes", b="<h2>Notes</h2><p>1. A note.</p>"),
    }
    nav = [
        ("c0", "contents.xhtml", "Contents"),
        ("n1", "c1.xhtml", "Chapter One"),
        ("n2", "notes.xhtml", "Notes"),
    ]
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        "<head/><docTitle><text>N</text></docTitle><navMap>"
        + "".join(
            f'<navPoint id="{i}" playOrder="{n}"><navLabel><text>{t}</text></navLabel>'
            f'<content src="{f}"/></navPoint>'
            for n, (i, f, t) in enumerate(nav)
        )
        + "</navMap></ncx>"
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
        'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>N614</dc:title><dc:identifier id="id">n614</dc:identifier>'
        "<dc:language>en</dc:language></metadata><manifest>"
        + "".join(
            f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
            for n, f in enumerate(files)
        )
        + '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx">'
        + "".join(f'<itemref idref="i{n}"/>' for n in range(len(files)))
        + "</spine></package>"
    )
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        for name, doc in files.items():
            z.writestr(name, doc)


def test_a_heading_the_page_also_links_prints_where_it_is(calibre):  # noqa: F811
    """Previewer prints the Notes heading at the top and keeps a plain Notes
    link last (QA on #613, #614)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "n614.epub", tmp / "n614.kfx"
    _notes_epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    lines, _level2 = _contents(kfx)
    got = [(t, link, under) for t, _size, _weight, link, under in lines]
    assert got == [
        ("Contents", False, False),
        ("Notes", False, False),
        ("Chapter One", True, True),
        ("Notes", True, True),
    ], f"calibre {version}: {lines}"


def _book_epub(out, key, contents, chapters, nav):
    """A book with a contents page `contents`, chapters (file, title) and an
    NCX of (id, file, title, children)."""
    page = (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>{t}</title></head>'
        "<body>{b}</body></html>"
    )
    files = {"contents.xhtml": page.format(t="Contents", b=contents)}
    files.update(
        {
            f: page.format(t=t, b=f"<h2>{t}</h2>" + "<p>Words.</p>" * 10)
            for f, t in chapters
        }
    )

    def points(items):
        return "".join(
            f'<navPoint id="{i}"><navLabel><text>{t}</text></navLabel>'
            f'<content src="{f}"/>{points(kids)}</navPoint>'
            for i, f, t, kids in items
        )

    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f"<head/><docTitle><text>{key}</text></docTitle><navMap>{points(nav)}</navMap></ncx>"
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
        'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>{key}</dc:title><dc:identifier id="id">{key.lower()}</dc:identifier>'
        "<dc:language>en</dc:language></metadata><manifest>"
        + "".join(
            f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
            for n, f in enumerate(files)
        )
        + '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx">'
        + "".join(f'<itemref idref="i{n}"/>' for n in range(len(files)))
        + "</spine></package>"
    )
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        for name, doc in files.items():
            z.writestr(name, doc)


def _a(f, t):
    return f'<p><a href="{f}">{t}</a></p>'


def test_a_heading_merges_into_an_entry_the_page_does_not_link(calibre):  # noqa: F811
    """N6: Kindle Previewer 4.0.3 (my probe, 2026-10-29) prints Part Two's
    `<h3>Introduction</h3>` and no other line for that entry, though Part
    One's Introduction is linked on the page (#622). kfxgen keeps the
    heading's link, as for every merged heading (#595); Previewer's has none."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "n622a.epub", tmp / "n622a.kfx"
    _book_epub(
        epub,
        "N622a",
        "<h1>Contents</h1>"
        + _a("p1.xhtml", "Part One")
        + _a("i1.xhtml", "Introduction")
        + _a("c1.xhtml", "Chapter One")
        + _a("p2.xhtml", "Part Two")
        + "<h3>Introduction</h3>"
        + _a("c2.xhtml", "Chapter Two"),
        [
            ("p1.xhtml", "Part One"),
            ("i1.xhtml", "Introduction"),
            ("c1.xhtml", "Chapter One"),
            ("p2.xhtml", "Part Two"),
            ("i2.xhtml", "Introduction"),
            ("c2.xhtml", "Chapter Two"),
        ],
        [
            ("c0", "contents.xhtml", "Contents", []),
            (
                "p1",
                "p1.xhtml",
                "Part One",
                [
                    ("i1", "i1.xhtml", "Introduction", []),
                    ("c1", "c1.xhtml", "Chapter One", []),
                ],
            ),
            (
                "p2",
                "p2.xhtml",
                "Part Two",
                [
                    ("i2", "i2.xhtml", "Introduction", []),
                    ("c2", "c2.xhtml", "Chapter Two", []),
                ],
            ),
        ],
    )
    _ebook_convert(calibre, epub, kfx, True)
    lines, _level2 = _contents(kfx)
    got = [(t, link, under) for t, _size, _weight, link, under in lines]
    assert got == [
        ("Contents", False, False),
        ("Part One", True, True),
        ("Introduction", True, True),
        ("Chapter One", True, True),
        ("Part Two", True, True),
        ("Introduction", True, False),
        ("Chapter Two", True, True),
    ], f"calibre {version}: {lines}"


def test_a_heading_that_is_a_link_the_page_repeats_prints_where_it_is(calibre):  # noqa: F811
    """N9: `<h3><a>Notes</a></h3>` at the top and a Notes link last. Kindle
    Previewer 4.0.3 (my probe, 2026-10-29) prints the linked heading at the
    top and the plain link last (#622)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "n622b.epub", tmp / "n622b.kfx"
    _book_epub(
        epub,
        "N622b",
        '<h1>Contents</h1><h3><a href="notes.xhtml">Notes</a></h3>'
        + _a("c1.xhtml", "Chapter One")
        + _a("notes.xhtml", "Notes"),
        [("c1.xhtml", "Chapter One"), ("notes.xhtml", "Notes")],
        [
            ("c0", "contents.xhtml", "Contents", []),
            ("n1", "c1.xhtml", "Chapter One", []),
            ("n2", "notes.xhtml", "Notes", []),
        ],
    )
    _ebook_convert(calibre, epub, kfx, True)
    lines, _level2 = _contents(kfx)
    got = [(t, link, under) for t, _size, _weight, link, under in lines]
    assert got == [
        ("Contents", False, False),
        ("Notes", True, False),
        ("Chapter One", True, True),
        ("Notes", True, True),
    ], f"calibre {version}: {lines}"
