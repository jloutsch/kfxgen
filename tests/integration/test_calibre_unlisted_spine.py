"""Chapters calibre appends to the spine, through real calibre (#253).

When an EPUB's spine leaves out chapter files that another page links to,
calibre appends those files to the spine itself, in a different order on
every conversion. kfxgen built chapters over that order, so chapters went
missing from the KFX, and which ones changed from run to run.

The book is the issue's reproduction: twelve chapters, a contents page
linking to all of them, a spine listing only chapters 1 to 4 and the
contents page, and an NCX listing "Contents" and then the twelve chapters.

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

from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    _page,
    _paragraphs,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CHAPTERS = range(1, 13)
RUNS = 3


def _marker(n):
    return f"MARKER{n:02d}WORD"


def _chapter(n):
    paras = "".join(
        f"<p>{_marker(n)} paragraph {i} of chapter {n}.</p>" for i in range(5)
    )
    return _page(f"Chapter {n}", f"<h1>Chapter {n}</h1>{paras}")


def _build_epub(path):
    files = [f"ch{n:02d}.xhtml" for n in CHAPTERS]
    contents = "".join(
        f'<p><a href="{f}">Chapter {n}</a></p>' for n, f in zip(CHAPTERS, files)
    )
    points = [("Contents", "contents.xhtml")] + [
        (f"Chapter {n}", f) for n, f in zip(CHAPTERS, files)
    ]
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head><meta name="dtb:uid" content="t"/></head>'
        "<docTitle><text>Unlisted</text></docTitle><navMap>"
        + "".join(
            f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{label}</text>'
            f'</navLabel><content src="{src}"/></navPoint>'
            for i, (label, src) in enumerate(points, 1)
        )
        + "</navMap></ncx>"
    )
    items = "".join(
        f'<item id="c{n}" href="{f}" media-type="application/xhtml+xml"/>'
        for n, f in zip(CHAPTERS, files)
    )
    spine = "".join(f'<itemref idref="c{n}"/>' for n in CHAPTERS[:4])
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">t'
            "</dc:identifier><dc:title>Unlisted spine files</dc:title><dc:language>en"
            "</dc:language></metadata><manifest>"
            + items
            + '<item id="toc" href="contents.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx">'
            + spine
            + '<itemref idref="toc"/></spine></package>',
        )
        for n, f in zip(CHAPTERS, files):
            z.writestr(f, _chapter(n))
        z.writestr("contents.xhtml", _page("Contents", f"<h1>Contents</h1>{contents}"))
        z.writestr("toc.ncx", ncx)


@pytest.fixture(scope="module")
def runs(calibre):  # noqa: F811
    tmp, _env, _version = calibre
    epub = tmp / "unlisted.epub"
    _build_epub(epub)
    out = []
    for i in range(RUNS):
        kfx = tmp / f"unlisted-{i}.kfx"
        _ebook_convert(calibre, epub, kfx, native=True)
        out.append([text for text, _ in _paragraphs(kfx)])
    return out


def test_every_chapter_is_in_every_conversion(runs):
    for texts in runs:
        missing = [n for n in CHAPTERS if sum(_marker(n) in t for t in texts) != 5]
        assert missing == []


def test_chapters_are_in_toc_order_every_time(runs):
    for texts in runs:
        order = [n for t in texts for n in CHAPTERS if _marker(n) in t]
        assert order == sorted(order)
