"""A first page holding only a copy of the cover (another file name, the
same bytes) and a non-breaking space keeps the copy shown through calibre
(#524), as main and Kindle Previewer 4.0.3 do. Since #516 the copy was left
out, leaving a page of one NBSP (QA's c5 probe).

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
from tests.integration.test_calibre_cover_in_text import _page  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _epub(path):
    cover = decodable_jpeg_of(600, 800)
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
            '<dc:identifier id="u">cov524</dc:identifier><dc:title>C</dc:title>'
            '<dc:language>en</dc:language><meta name="cover" content="cov"/>'
            "</metadata><manifest>"
            '<item id="f" href="first.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="cov" href="cover.jpg" media-type="image/jpeg"/>'
            '<item id="cp" href="copy.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="f"/><itemref idref="c"/>'
            "</spine></package>",
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>C</text></docTitle><navMap>'
            '<navPoint id="n0" playOrder="1"><navLabel><text>Start</text></navLabel>'
            '<content src="first.xhtml"/></navPoint>'
            '<navPoint id="n1" playOrder="2"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr("first.xhtml", _page('<p><img src="copy.jpg" alt="c"/></p><p> </p>'))
        z.writestr("c.xhtml", _page("<h1>One</h1><p>Text.</p>"))
        z.writestr("cover.jpg", cover)
        z.writestr("copy.jpg", cover)


def test_a_first_page_of_only_a_cover_copy_keeps_it(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "cov524.epub", tmp / "cov524.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
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
    # kfxgen's cover page (#32), then the first page showing its copy.
    assert sections[0] == ["cover_img"], f"calibre {version}"
    assert "cover_img" in sections[1], f"calibre {version}"
    assert all(t.strip() for t in sections[1] if t != "cover_img"), f"calibre {version}"
