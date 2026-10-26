"""The cover's picture, shown in the text too, is written there, through
calibre (#475).

The cover's file was kept out of the body's pictures, so an <img> of it in
a chapter was dropped. Kindle Previewer 4.0.3 shows it there (QA's #452
probe: the chapter's 2 pictures, the cover again and the other). kfxgen now
does, from the cover's own resource, so the bytes aren't stored twice. A
page showing nothing but the cover is still left out (#32).

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

from tests._helpers import decodable_jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _page(body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
        f"<body>{body}</body></html>"
    )


def _epub(path, opening=True, cover_first=False, label="One"):
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
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">cov475</dc:identifier><dc:title>C</dc:title>'
            '<dc:language>en</dc:language><meta name="cover" content="cov"/>'
            "</metadata><manifest>"
            '<item id="cp" href="cover.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="o0" href="open.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="cov" href="cover.jpg" media-type="image/jpeg"/>'
            '<item id="o" href="other.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="cp"/>'
            + ('<itemref idref="o0"/>' if opening else "")
            + '<itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>C</text></docTitle><navMap>'
            f'<navPoint id="n" playOrder="1"><navLabel><text>{label}</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr("cover.xhtml", _page('<p><img src="cover.jpg" alt="cover"/></p>'))
        z.writestr("open.xhtml", _page("<h1>Opening</h1><p>First page.</p>"))
        z.writestr(
            "c.xhtml",
            _page(
                (
                    '<p><img src="cover.jpg" alt="again"/></p><h1>One</h1><p>Text.</p>'
                    if cover_first
                    else '<h1>One</h1><p>Text.</p><p><img src="cover.jpg" alt="again"/></p>'
                )
                + '<p><img src="other.jpg" alt="other"/></p>'
            ),
        )
        z.writestr("cover.jpg", decodable_jpeg_of(600, 800))
        z.writestr("other.jpg", decodable_jpeg_of(300, 200))


def test_the_covers_picture_in_the_text_is_written(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "cov475.epub", tmp / "cov475.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    pictures = [
        str(e["$175"])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$175") is not None
    ]
    # kfxgen's cover page (#32), then the chapter's two: the cover again,
    # from the cover's resource, and the other picture. The book's own
    # cover page is still left out, or the cover would show three times.
    assert pictures[0] == "cover_img", f"calibre {version}"
    assert pictures[1:] == ["cover_img", "img_0"], f"calibre {version}"
    assert sorted(str(f.fid) for f in by_type(frags, "$164")) == [
        "cover_img",
        "img_0",
    ], f"calibre {version}"


def test_a_first_page_showing_the_cover_after_text_keeps_the_cover_page(
    calibre,  # noqa: F811
):
    """No opening page, and the book's first page shows the cover after its
    heading and a paragraph: kfxgen's cover page still goes first, as
    Kindle Previewer 4.0.3's does, or the book would open on that text
    (#475, QA's c7 and c8 probes)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "cov475b.epub", tmp / "cov475b.kfx"
    _epub(epub, opening=False)
    _ebook_convert(calibre, epub, kfx, True)
    assert _pictures(kfx) == ["cover_img", "cover_img", "img_0"], f"calibre {version}"


def test_a_first_page_opening_with_the_cover_shows_it_once(calibre):  # noqa: F811
    """No opening page, and the book's first page opens with the cover in a
    paragraph of its own: kfxgen's cover page shows it, a page of its own as
    on main, and the page's text starts the next section. Not printed again
    under it (#475), nor with the page's text run on under it (#516 review).
    """
    tmp, _env, version = calibre
    epub, kfx = tmp / "cov475c.epub", tmp / "cov475c.kfx"
    # Labelled "Cover", as in QA's 3 library books: not the page's words.
    _epub(epub, opening=False, cover_first=True, label="Cover")
    _ebook_convert(calibre, epub, kfx, True)
    assert _pictures(kfx) == ["cover_img", "img_0"], f"calibre {version}"
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    sections = [
        [
            str(e["$175"])
            if e.get("$175") is not None
            else str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            for e in iter_entries(val(st)["$146"])
            if hasattr(e, "get") and (e.get("$175") is not None or e.get("$145"))
        ]
        for st in by_type(frags, "$259")
    ]
    assert sections[0] == ["cover_img"], f"calibre {version}"
    assert sections[1][-3:] == ["One", "Text.", "img_0"], f"calibre {version}"


def _pictures(kfx):
    return [
        str(e["$175"])
        for st in by_type(load_fragments(kfx), "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$175") is not None
    ]
