"""A first page outside the TOC, `<p><img/> words.</p>`, prints its words
once through calibre (#525), as Kindle Previewer 4.0.3 does: kfxgen titled
the page with them (#133) and printed them as a heading too.

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

WORDS = "Words beside the picture."


def _page(body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
        f"<body>{body}</body></html>"
    )


def _epub(path, first):
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
            '<dc:identifier id="u">w525</dc:identifier><dc:title>W</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="f" href="first.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="p" href="pic.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="f"/><itemref idref="c"/>'
            "</spine></package>",
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>W</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr("first.xhtml", _page(first))
        z.writestr("c.xhtml", _page("<h1>One</h1><p>Text.</p>"))
        z.writestr("pic.jpg", decodable_jpeg_of(300, 200))


@pytest.mark.parametrize(
    "more", ["", "<p>Second paragraph.</p>"], ids=["alone", "more"]
)
def test_the_words_beside_a_picture_print_once(calibre, more):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"w525{bool(more)}.epub", tmp / f"w525{bool(more)}.kfx"
    _epub(epub, f'<p><img src="pic.jpg" alt="x"/> {WORDS}</p>{more}')
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    texts = [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    assert [t for t in texts if WORDS in t] == [WORDS], f"calibre {version}"
