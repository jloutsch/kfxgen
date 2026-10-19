"""A dedication or epigraph after a rebuilt contents listing is kept, through
calibre (#393).

calibre renames a book's classes before kfxgen sees it (#329): two classes
with the same style both take the name of the one it meets first. So
`class="dedication"` can only be read where kfxgen records it first. This
converts a book of pg64317's shape: a contents listing, then a dedication
and an epigraph with no TOC entry, then an unmarked line, which is replaced
with the listing. The dedication is styled like an earlier `.intro`, so
calibre renames it away; the unmarked line is styled like the epigraph, so
calibre renames it to `epigraph`. Reading calibre's classes, the dedication
is missed and the unmarked line kept.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
"""

import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

XHTML = (
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
    '<head><title>{0}</title><link rel="stylesheet" type="text/css" href="s.css"/>'
    "</head><body>{1}</body></html>"
)
CSS = """.toc { margin-left: 1em; }
.intro { text-align: center; font-style: italic; }
.dedication { text-align: center; font-style: italic; }
.epigraph { margin-left: 2em; font-size: 0.9em; }
.later { margin-left: 2em; font-size: 0.9em; }"""
FRONT = (
    "<h1>Contents</h1>"
    '<div class="toc"><p><a href="c1.xhtml">Chapter One</a></p>'
    '<p><a href="c2.xhtml">Chapter Two</a></p></div>'
    '<p class="dedication">For my family.</p>'
    '<p class="epigraph">Then wear the gold hat.</p>'
    '<p class="later">A leftover line.</p>'
)


def _build(path):
    files = {
        "preface.xhtml": XHTML.format(
            "P", '<h1>Preface</h1><p class="intro">An intro.</p>'
        ),
        "front.xhtml": XHTML.format("F", FRONT),
        "c1.xhtml": XHTML.format(
            "1", "<h1>Chapter One</h1><p>Prose of chapter one.</p>"
        ),
        "c2.xhtml": XHTML.format(
            "2", "<h1>Chapter Two</h1><p>Prose of chapter two.</p>"
        ),
    }
    toc = [
        ("Preface", "preface.xhtml"),
        ("Contents", "front.xhtml"),
        ("Chapter One", "c1.xhtml"),
        ("Chapter Two", "c2.xhtml"),
    ]
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, n in enumerate(files)
    )
    spine = "".join(f'<itemref idref="i{i}"/>' for i in range(len(files)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">front'
        "</dc:identifier><dc:title>Front matter</dc:title><dc:language>en"
        f"</dc:language></metadata><manifest>{items}"
        '<item id="s" href="s.css" media-type="text/css"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        f'</manifest><spine toc="ncx">{spine}</spine></package>'
    )
    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{t}</text></navLabel>'
        f'<content src="{h}"/></navPoint>'
        for i, (t, h) in enumerate(toc, 1)
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>Front matter</text></docTitle>'
        f"<navMap>{points}</navMap></ncx>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        z.writestr("s.css", CSS)
        for name, data in files.items():
            z.writestr(name, data)


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """The text of each paragraph, in reading order, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "front-matter.epub", tmp / "front-matter.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = [
        str(texts[str(e["$145"]["name"])][e["$145"]["$403"]])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if e.get("$145")
    ]
    return out, version


def test_the_dedication_and_epigraph_are_kept_before_chapter_one(converted):
    texts, version = converted
    assert "For my family." in texts and "Then wear the gold hat." in texts, (
        f"calibre {version}: {texts}"
    )
    first = texts.index("Prose of chapter one.")
    assert texts.index("Then wear the gold hat.") < first, f"calibre {version}: {texts}"


def test_an_unmarked_line_after_the_listing_is_still_replaced(converted):
    texts, version = converted
    assert "A leftover line." not in texts, f"calibre {version}: {texts}"
