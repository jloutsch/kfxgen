"""Notes in a book with no table of contents, through real calibre (#225).

With no NCX, calibre builds its own table of contents, and when it finds
fewer than six chapter headings it adds the book's links to it: every note
marker as an entry named "1", "2", ... and every back-link as "1.", "2.", ....
Each entry used to start a chapter, so each note after the first became a
chapter with its number as the heading, and its own number was cut from the
text as a repeat of that heading (". Note 2 text.").

Notes are recognised by their link pairs: a marker links to the note and a
link in the note's file links back to the marker. This holds for a notes
table (anchor before or after each row), and for plain `<p>` and `<div>`
notes, none of which carries note markup; `<aside>` notes carry it. Each
layout is built with and without an NCX; with one, calibre keeps the
book's own TOC and the notes were already right.

With every entry of calibre's TOC skipped, each file becomes a chapter,
titled by the heading it opens with (#304). Before, it was titled
"Section N", printed above that heading and listed in the Kindle TOC.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.15.0, where every case passes.
"""

import re
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _CHAPTER_HTML,
    _NOTES_HTML,
    _ebook_convert,
    _link_landings,
    _page,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

LAYOUTS = {
    # Anchors after rows 1-3 and before rows 4-6, as #225 and #223 found them.
    "table": _NOTES_HTML,
    "p": "<h1>Notes</h1>"
    + "".join(
        f'<p id="n{n}"><a href="ch.xhtml#r{n}">{n}</a>. Note {n} text.</p>'
        for n in range(1, 7)
    ),
    # Note markup: #203 already skipped these entries, so this book fell
    # back to "Section N" chapters before #225 (#304).
    "aside": "<h1>Notes</h1>"
    + "".join(
        f'<aside role="doc-endnote" id="n{n}"><p><a href="ch.xhtml#r{n}">{n}</a>. '
        f"Note {n} text.</p></aside>"
        for n in range(1, 7)
    ),
    "div": "<h1>Notes</h1>"
    + "".join(
        f'<div id="n{n}"><a href="ch.xhtml#r{n}">{n}</a>. Note {n} text.</div>'
        for n in range(1, 7)
    ),
}

_NCX = (
    '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
    'version="2005-1"><head><meta name="dtb:uid" content="t"/></head>'
    "<docTitle><text>Notes</text></docTitle><navMap>"
    '<navPoint id="p1" playOrder="1"><navLabel><text>Chapter</text></navLabel>'
    '<content src="ch.xhtml"/></navPoint>'
    '<navPoint id="p2" playOrder="2"><navLabel><text>Notes</text></navLabel>'
    '<content src="notes.xhtml"/></navPoint></navMap></ncx>'
)


def _build(path, notes_html, ncx):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        ncx_item = (
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            if ncx
            else ""
        )
        spine = '<spine toc="ncx">' if ncx else "<spine>"
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">t'
            "</dc:identifier><dc:title>Notes</dc:title><dc:language>en"
            "</dc:language></metadata><manifest>"
            '<item id="ch" href="ch.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>'
            f"{ncx_item}</manifest>{spine}"
            '<itemref idref="ch"/><itemref idref="notes"/></spine></package>',
        )
        z.writestr("ch.xhtml", _page("Chapter", _CHAPTER_HTML))
        z.writestr("notes.xhtml", _page("Notes", notes_html))
        if ncx:
            z.writestr("toc.ncx", _NCX)


def _texts(kfx):
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    return [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for story in by_type(frags, "$259")
        for e in iter_entries(val(story)["$146"])
        if e.get("$145") is not None
    ]


@pytest.fixture(
    scope="module",
    params=[(lay, ncx) for lay in LAYOUTS for ncx in (False, True)],
    ids=lambda p: f"{p[0]}-{'ncx' if p[1] else 'no-ncx'}",
)
def book(calibre, request):  # noqa: F811
    layout, ncx = request.param
    tmp, _env, version = calibre
    epub = tmp / f"notes-{layout}-{ncx}.epub"
    _build(epub, LAYOUTS[layout], ncx)
    kfx = tmp / f"notes-{layout}-{ncx}.kfx"
    _ebook_convert(calibre, epub, kfx, native=True)
    return kfx, f"calibre {version}, {layout}, {'NCX' if ncx else 'no NCX'}"


def test_no_note_number_becomes_a_heading(book):
    kfx, where = book
    texts = _texts(kfx)
    bare = [t for t in texts if re.fullmatch(r"\s*\d\s*", t)]
    cut = [t for t in texts if t.startswith(". Note")]
    assert not bare and not cut, f"{where}: headings {bare}, cut notes {cut}"


def test_every_note_keeps_its_number(book):
    kfx, where = book
    joined = " ".join(_texts(kfx))
    for n in range(1, 7):
        assert re.search(rf"\b{n}\. ?Note {n} text\.", joined), (
            f"{where}: note {n} lost its number"
        )


def test_every_marker_lands_on_its_own_note(book):
    kfx, where = book
    # Each marker is the only link labelled "n" whose landing is a note; in
    # the <p> and <div> books the back-links are labelled "n" too, and land
    # in the chapter.
    landings = _link_landings(kfx)
    for n in range(1, 7):
        on_note = [t for label, t in landings if label == str(n) and "Note" in t]
        assert on_note and all(
            re.match(rf"{n}\. ?Note {n} text\.", t) for t in on_note
        ), f"{where}: marker {n} lands on {[t[:40] for t in on_note]}"


def _toc_labels(kfx):
    """The labels in the KFX's navigation, as the Kindle's TOC lists them."""
    frags = load_fragments(kfx)
    return sorted(
        set(
            re.findall(
                r"\$244: '([^']*)'", repr([val(f) for f in by_type(frags, "$389")])
            )
        )
        - {"heading-nav-unit"}
    )


def test_the_book_prints_no_section_heading(book):
    """With every entry of calibre's TOC skipped as a note, each file is a
    chapter: titled by its own heading, and "Section N" never printed (#304)."""
    kfx, where = book
    section = [t for t in _texts(kfx) if re.fullmatch(r"Section \d+", t)]
    assert not section, f"{where}: printed {section}"


def test_the_toc_names_the_books_own_chapters(book):
    kfx, where = book
    labels = _toc_labels(kfx)
    assert {"Chapter", "Notes"} <= set(labels), f"{where}: TOC is {labels}"
