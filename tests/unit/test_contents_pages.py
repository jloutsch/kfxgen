"""A book with more than one page titled "Contents" (#714).

pg100 (Gutenberg's Complete Works of Shakespeare) has the book's contents
page, then a "Contents" page in each play listing its acts and scenes: 39
TOC entries titled "Contents". kfxgen rebuilt every one of them as the
book's contents page, so each play's own listing was replaced by a list of
the whole book (1,131 lines, 39 times), and those pages, rebuilt with no
language, had no `$10`. Kindle Previewer 4.0.3 prints each play's page as
the book has it.

The first page titled "Contents" is the book's and is rebuilt; the others
are kept as the book has them. kfxgen's own lines (a rebuilt page's lines,
a heading it prints from a chapter's title) take the direction and language
of the page they stand for.
"""

import zipfile
from unittest.mock import MagicMock

import pytest

from kfxgen import converter as _conv
from tests._kfx_introspect import by_type, load_fragments, val
from tests.fixtures.oeb_shim import EpubAsOeb
from tests.unit.test_direction import NullLog


def _page(body, html_attrs, css=""):
    style = f"<style>{css}</style>" if css else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{html_attrs}>'
        f"<head><title>t</title>{style}</head><body>{body}</body></html>"
    )


def _play(n, name):
    return {
        f"p{n}.xhtml": (
            f"<div><h2 id='play{n}'>{name}</h2><hr/></div>"
            f"<div id='pc{n}'><h2>Contents</h2><table><tr><td>ACT I</td></tr>"
            f"<tr><td><a href='p{n}s.xhtml#s{n}'>Scene I. Room {n}</a></td></tr>"
            "</table></div>"
        ),
        f"p{n}s.xhtml": (
            f"<h3 id='a{n}'>ACT I</h3><h3 id='s{n}'>SCENE I. Room {n}.</h3>"
            f"<p>Words of play {n}.</p>"
        ),
    }


PAGES = {
    "c0.xhtml": "<h1 id='w'>The Works</h1>",
    "c1.xhtml": (
        "<div id='bc'><h3>Contents</h3><table>"
        "<tr><td><a href='p1.xhtml#play1'>PLAY ONE</a></td></tr>"
        "<tr><td><a href='p2.xhtml#play2'>PLAY TWO</a></td></tr></table></div>"
    ),
    **_play(1, "PLAY ONE"),
    **_play(2, "PLAY TWO"),
}


def _toc():
    out = [("The Works", "c0.xhtml#w", 0), ("Contents", "c1.xhtml#bc", 0)]
    for n, name in ((1, "PLAY ONE"), (2, "PLAY TWO")):
        out += [
            (name, f"p{n}.xhtml#play{n}", 0),
            ("Contents", f"p{n}.xhtml#pc{n}", 1),
            ("ACT I", f"p{n}s.xhtml#a{n}", 1),
            (f"SCENE I. Room {n}.", f"p{n}s.xhtml#s{n}", 1),
        ]
    return out


def _ncx(toc):
    points, depth = "", 0
    for i, (label, src, level) in enumerate(toc):
        while depth > level:
            points += "</navPoint>"
            depth -= 1
        if i and depth == level:
            points += "</navPoint>"
        points += (
            f'<navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{label}</text>'
            f'</navLabel><content src="{src}"/>'
        )
        depth = level
    points += "</navPoint>" * (depth + 1)
    return (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        f'version="2005-1"><head/><docTitle><text>t</text></docTitle><navMap>{points}'
        "</navMap></ncx>"
    )


def write_epub(
    path, html_attrs=' xml:lang="en"', pages=PAGES, toc=None, css="", ident="works"
):
    """An EPUB of `pages` with an NCX of `toc` [(label, src, level)], each
    page styled by `css`."""
    items = "".join(
        f'<item id="x{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in enumerate(pages)
    )
    spine = "".join(f'<itemref idref="x{i}"/>' for i in range(len(pages)))
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="u">{ident}</dc:identifier><dc:title>The Works</dc:title>'
        "<dc:creator>A</dc:creator><dc:language>en</dc:language></metadata>"
        f'<manifest>{items}<item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest>'
        f'<spine toc="ncx">{spine}</spine></package>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", _ncx(toc or _toc()))
        for h, body in pages.items():
            z.writestr(h, _page(body, html_attrs, css))
    return path


def _convert(tmp_path, html_attrs=' xml:lang="en"', pages=PAGES, toc=None):
    path = write_epub(tmp_path / "works.epub", html_attrs, pages, toc)
    out = tmp_path / "works.kfx"
    _conv.convert_oeb_to_kfx(
        EpubAsOeb(str(path)),
        str(out),
        opts=MagicMock(kfxgen_disable_native_tables=False),
        log=NullLog(),
    )
    return load_fragments(out)


def _texts(frags):
    """[(text, {$10, $682 from the entry or a container above it})] in
    reading order, for every text entry."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []

    def visit(nodes, above):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            s = dict(styles.get(str(e.get("$157")), {}))
            s.update(e)
            keys = {**above, **{k: str(s[k]) for k in ("$10", "$682") if k in s}}
            ref = e.get("$145")
            if hasattr(ref, "get"):
                out.append((str(texts[str(ref["name"])][int(ref["$403"])]), keys))
            visit(e.get("$146") or (), keys)

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"], {})
    return out


@pytest.mark.unit
def test_only_the_books_contents_page_is_rebuilt(tmp_path):
    lines = [t.strip() for t, _ in _texts(_convert(tmp_path))]
    # Each play keeps its own listing, as the book has it.
    for n in (1, 2):
        assert lines.count(f"Scene I. Room {n}") == 1, lines
    # The book's list of plays is printed once: the rebuilt page lists the
    # chapters, so a play's name is there and on the play's own heading.
    assert lines.count("PLAY TWO") == 2, lines


@pytest.mark.unit
@pytest.mark.parametrize(
    "html_attrs, expected",
    [
        (' xml:lang="en"', {"$10": "en"}),
        (' lang="fr"', {"$10": "fr"}),
        (' dir="rtl" lang="ar"', {"$10": "ar", "$682": "$375"}),
    ],
)
def test_every_line_has_the_pages_language(tmp_path, html_attrs, expected):
    # Every page sets it, so every text entry carries it, on itself or a
    # container above it, as in Previewer's pg100: the rebuilt contents
    # page's lines and a play's title heading included.
    missing = [
        t for t, keys in _texts(_convert(tmp_path, html_attrs)) if keys != expected
    ]
    assert not missing, missing


# A listing recognised by its markup, on a page not titled "Contents"
# (#132's path): rebuilt as the contents page, in the page's language too.
NAV_PAGES = {
    "c0.xhtml": "<h1 id='w'>The Works</h1>",
    "c1.xhtml": (
        "<h2 id='n'>Chapters</h2><div class='toc'>"
        "<p><a href='c2.xhtml#one'>Chapter One</a></p>"
        "<p><a href='c3.xhtml#two'>Chapter Two</a></p></div>"
    ),
    "c2.xhtml": "<h2 id='one'>Chapter One</h2><p>Text one.</p>",
    "c3.xhtml": "<h2 id='two'>Chapter Two</h2><p>Text two.</p>",
}
NAV_TOC = [
    ("The Works", "c0.xhtml#w", 0),
    ("Chapters", "c1.xhtml#n", 0),
    ("Chapter One", "c2.xhtml#one", 0),
    ("Chapter Two", "c3.xhtml#two", 0),
]


@pytest.mark.unit
def test_a_listing_rebuilt_from_its_markup_has_the_pages_language(tmp_path):
    found = _texts(_convert(tmp_path, ' lang="fr"', pages=NAV_PAGES, toc=NAV_TOC))
    # The listing's heading, kept as a chapter of its own (#369), then the
    # rebuilt page: its heading and a line per chapter.
    assert [t for t, _ in found][1:7] == [
        "Chapters",
        "Contents",
        "The Works",
        "Chapters",
        "Chapter One",
        "Chapter Two",
    ]
    missing = [t for t, keys in found if keys != {"$10": "fr"}]
    assert not missing, missing


def _sections(frags):
    """[[text, ...] per $259 section]."""
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []

    def visit(nodes, into):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            ref = e.get("$145")
            if hasattr(ref, "get"):
                into.append(str(texts[str(ref["name"])][int(ref["$403"])]).strip())
            visit(e.get("$146") or (), into)

    for sl in by_type(frags, "$259"):
        out.append([])
        visit(val(sl)["$146"], out[-1])
    return out


@pytest.mark.unit
def test_a_kept_contents_page_keeps_the_pages_after_it(tmp_path):
    # A file the TOC doesn't list after a play's Contents page is part of
    # that page's section, as after any chapter kfxgen keeps. Only the
    # rebuilt page has its following files split off (#551 review).
    pages = dict(PAGES)
    pages = {
        **{k: v for k, v in pages.items() if k < "p1s"},
        "p1n.xhtml": "<p>A note on the text of play 1.</p>",
        **{k: v for k, v in pages.items() if k >= "p1s"},
    }
    sections = _sections(_convert(tmp_path, pages=pages))
    (play_contents,) = [s for s in sections if "Scene I. Room 1" in s]
    assert "A note on the text of play 1." in play_contents, sections


@pytest.mark.unit
def test_a_contents_page_that_is_only_a_table_gives_its_language(tmp_path):
    pages = {
        **PAGES,
        "c1.xhtml": (
            "<table id='bc'><tr><td><a href='p1.xhtml#play1'>PLAY ONE</a></td></tr>"
            "<tr><td><a href='p2.xhtml#play2'>PLAY TWO</a></td></tr></table>"
        ),
    }
    missing = [
        t
        for t, keys in _texts(_convert(tmp_path, ' lang="fr"', pages=pages))
        if keys != {"$10": "fr"}
    ]
    assert not missing, missing


@pytest.mark.unit
def test_a_page_named_after_its_file_is_not_the_contents_page(tmp_path):
    # A TOC label that is only the file's name takes the page's first
    # heading as its title (#285), but front-matter rules keyed on titles
    # don't apply to it: a "Contents" heading there doesn't make it the book's
    # contents page, and the Contents entry after it is still rebuilt.
    pages = {
        "c0.xhtml": PAGES["c0.xhtml"],
        "split_001.xhtml": "<h1 id='s'>Contents</h1><p>A note before the list.</p>",
        **{k: v for k, v in PAGES.items() if k != "c0.xhtml"},
    }
    toc = _toc()
    toc.insert(1, ("split_001", "split_001.xhtml#s", 0))
    lines = [t.strip() for t, _ in _texts(_convert(tmp_path, pages=pages, toc=toc))]
    # The rebuilt page lists the book's title page; the book's own list of
    # plays doesn't.
    assert lines.count("The Works") == 2, lines
    assert "A note before the list." in lines, lines
