"""Pictures and a cover calibre can't decode are left out, through calibre
(#471).

kfxgen checked only magic bytes and size, so a file that won't decode was
written: a JPEG with no frame header had no size, and a PNG with a garbage
header claimed 4294967295 x 4294967295 pixels (QA's #452 probe; kfxlib
reported "bad image" or crashed). A JPEG cut short still decodes, its
missing part filled, as a browser shows it, and is kept.

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
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

GOOD = decodable_jpeg_of(400, 300)
PICTURES = {
    "good.jpg": GOOD,
    "cut.jpg": GOOD[:-3],  # cut inside its scan: decodes, kept
    "nosof.jpg": b"\xff\xd8\xff" + bytes(300),  # JPEG magic, no frame
    "junk.png": b"\x89PNG\r\n\x1a\n" + b"\xff" * 300,  # garbage header
}
COVER = b"\xff\xd8\xff" + bytes(500)


def _epub(path):
    imgs = "".join(f'<p><img src="{n}" alt="{n}"/></p>' for n in PICTURES)
    items = "".join(
        f'<item id="p{i}" href="{n}" media-type="image/{"png" if n.endswith("png") else "jpeg"}"/>'
        for i, n in enumerate(PICTURES)
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
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">bad471</dc:identifier><dc:title>B</dc:title>'
            '<dc:language>en</dc:language><meta name="cover" content="cov"/>'
            "</metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="cov" href="cover.jpg" media-type="image/jpeg"/>'
            f"{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>B</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
            f"<body><h1>One</h1><p>Text.</p>{imgs}</body></html>",
        )
        z.writestr("cover.jpg", COVER)
        for n, data in PICTURES.items():
            z.writestr(n, data)


def test_undecodable_pictures_and_cover_are_left_out(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "bad471.epub", tmp / "bad471.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    sizes = sorted(
        (int(val(f).get("$422", -1)), int(val(f).get("$423", -1)))
        for f in by_type(frags, "$164")
    )
    # The good picture and the cut one, both 400 x 300; nothing else, and
    # no cover.
    assert sizes == [(400, 300), (400, 300)], f"calibre {version}"
    meta = {
        str(m["$492"])
        for f in by_type(frags, "$490")
        for g in val(f)["$491"]
        for m in g["$258"]
    }
    assert "cover_image" not in meta, f"calibre {version}"
