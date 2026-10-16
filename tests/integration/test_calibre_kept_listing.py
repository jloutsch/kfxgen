"""A book that keeps its own contents listing keeps the pages after it,
through calibre (#387).

A library book in #387's A/B lost its title, copyright and dedication pages.
Its EPUB 3 navigation document is in the spine, its listing's heading inside
the <nav>, and its first TOC entry comes after those pages. Once the nested
listing was recognised and dropped, no Contents page was left to rebuild, so
the listing was kept (#335); its heading then titled the leading chapter
"Table of Contents", which the title-keyed rebuild replaced, pages and all.
calibre reads the navigation document, which the unit tests' shim does not,
so this converts a book of that shape with calibre.

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
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{0}</title></head>'
    "<body>{1}</body></html>"
)
LISTING = (
    '<nav epub:type="toc" id="toc" role="doc-toc"><h1>Table of Contents</h1><ol>'
    '<li><a href="c1.xhtml">Chapter One</a></li>'
    '<li><a href="c2.xhtml">Chapter Two</a></li></ol></nav>'
)
COPYRIGHT = "Copyright 2020 by the publisher. All rights reserved, in every country."
DEDICATION = "For my family."


def _build(path, nested):
    listing = f"<section>{LISTING}</section>" if nested else LISTING
    files = {
        "nav.xhtml": XHTML.format("Contents", listing),
        "copyright.xhtml": XHTML.format("C", f"<section><p>{COPYRIGHT}</p></section>"),
        "dedication.xhtml": XHTML.format(
            "D", f"<section><p>{DEDICATION}</p></section>"
        ),
        "c1.xhtml": XHTML.format(
            "1", "<h1>Chapter One</h1><p>Prose of chapter one.</p>"
        ),
        "c2.xhtml": XHTML.format(
            "2", "<h1>Chapter Two</h1><p>Prose of chapter two.</p>"
        ),
    }
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"'
        + (' properties="nav"' if n == "nav.xhtml" else "")
        + "/>"
        for i, n in enumerate(files)
    )
    spine = "".join(f'<itemref idref="i{i}"/>' for i in range(len(files)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">kept'
        f"{int(nested)}</dc:identifier><dc:title>Kept listing</dc:title>"
        "<dc:language>en</dc:language>"
        '<meta property="dcterms:modified">2026-10-15T00:00:00Z</meta></metadata>'
        f"<manifest>{items}</manifest><spine>{spine}</spine><guide>"
        '<reference href="nav.xhtml" title="Table of Contents" type="toc"/>'
        "</guide></package>"
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
        for name, data in files.items():
            z.writestr(name, data)


@pytest.fixture(scope="module", params=[False, True], ids=["body-level", "nested"])
def converted(calibre, request):  # noqa: F811
    """The text of each paragraph, in reading order, and calibre's version."""
    tmp, _env, version = calibre
    epub = tmp / f"kept-{request.param}.epub"
    kfx = tmp / f"kept-{request.param}.kfx"
    _build(epub, request.param)
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


def test_the_pages_after_the_listing_are_kept(converted):
    texts, version = converted
    assert COPYRIGHT in texts and DEDICATION in texts, f"calibre {version}: {texts}"


def test_the_chapters_are_kept(converted):
    texts, version = converted
    assert "Prose of chapter one." in texts and "Prose of chapter two." in texts, (
        f"calibre {version}: {texts}"
    )
