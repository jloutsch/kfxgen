"""Landmarks and page-list targets spelled in other capitals than the
book's files still land (#610). In 2 library books the guide and the nav
document name every file in other capitals; Kindle Previewer 4.0.3 still
writes their contents landmark and page list. kfxgen wrote neither, since
its keys use calibre's spelling. A target now takes the spine's spelling
when one file fits, as #549 does for TOC hrefs.

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

from tests.integration.test_calibre_landmarks import _landmarks  # noqa: E402
from tests.integration.test_calibre_page_list import _page_list  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

NS = 'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'


def _page(body):
    return f'<?xml version="1.0"?><!DOCTYPE html><html {NS}><head><title>t</title></head><body>{body}</body></html>'


def _epub(path):
    files = {
        "contents.xhtml": _page('<h1>Contents</h1><p><a href="c1.xhtml">One</a></p>'),
        "c1.xhtml": _page(
            '<h1>One</h1><p>Before. <span epub:type="pagebreak" id="p1"/>After page one.</p>'
            + "<p>More words.</p>" * 10
            # A link in other capitals: calibre adds the file again under that
            # spelling, as in the library books (#549, #610).
            + '<p><a href="CONTENTS.xhtml">Back</a> <a href="C1.xhtml#p1">Here</a></p>'
        ),
        "nav.xhtml": _page(
            '<nav epub:type="toc"><ol><li><a href="C1.xhtml">One</a></li></ol></nav>'
            '<nav epub:type="landmarks"><ol><li><a epub:type="toc" href="CONTENTS.xhtml">'
            "Contents</a></li></ol></nav>"
            '<nav epub:type="page-list"><ol><li><a href="C1.xhtml#p1">1</a></li></ol></nav>'
        ),
    }
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>N610</dc:title><dc:identifier id="id">n610</dc:identifier>'
        '<dc:language>en</dc:language><meta property="dcterms:modified">2026-10-28T00:00:00Z'
        "</meta></metadata><manifest>"
        '<item id="a" href="contents.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="b" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx"><itemref idref="a"/><itemref idref="b"/></spine></package>'
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        '<head/><docTitle><text>N</text></docTitle><navMap><navPoint id="n1" playOrder="1">'
        '<navLabel><text>One</text></navLabel><content src="c1.xhtml"/></navPoint></navMap></ncx>'
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
        z.writestr("toc.ncx", ncx)
        for name, doc in files.items():
            z.writestr(name, doc)


def test_targets_in_other_capitals_still_land(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "n610.epub", tmp / "n610.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    marks = [(t, label, (words or "")[:8]) for t, label, words in _landmarks(kfx)]
    assert ("$212", "Contents", "Contents") in marks, f"calibre {version}: {marks}"
    pages = [(label, at[:15]) for label, at in _page_list(kfx)]
    assert pages == [("1", "After page one.")], f"calibre {version}: {pages}"
