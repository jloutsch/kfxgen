"""A CMYK JPEG reaches the book as an RGB JPEG with its colours, through
calibre (#466), as Kindle Previewer 4.0.3 writes it: with optimisation on,
and with original pictures embedded. calibre's Qt reads it as CMYK and
writes it back as CMYK unless it is converted first.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._helpers import colour_jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _epub(path):
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
            '<dc:identifier id="u">cmyk466</dc:identifier><dc:title>C</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="p" href="p.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>C</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            '<body><h1>One</h1><p><img src="p.jpg" alt="p"/></p><p>After.</p>'
            "</body></html>",
        )
        # Red on the left, cyan on the right.
        z.writestr(
            "p.jpg", colour_jpeg_of("CMYK", (40, 30), (0, 255, 255, 0), (255, 0, 0, 0))
        )


@pytest.mark.parametrize("embed", [False, True], ids=["optimised", "embed-originals"])
def test_a_cmyk_picture_is_written_as_rgb(calibre, embed):  # noqa: F811
    tmp, env, version = calibre
    epub, kfx = tmp / f"cmyk{embed}.epub", tmp / f"cmyk{embed}.kfx"
    _epub(epub)
    cmd = [EBOOK_CONVERT, str(epub), str(kfx)]
    if embed:
        cmd.append("--kfxgen-embed-original-images")
    run = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-2000:]
    (raw,) = [bytes(val(f)) for f in by_type(load_fragments(kfx), "$417")]
    picture = Image.open(io.BytesIO(raw))
    assert picture.mode == "RGB", f"calibre {version}"
    red, cyan = picture.getpixel((5, 15)), picture.getpixel((35, 15))
    assert red[0] > 200 and red[1] < 60 and red[2] < 60, f"calibre {version}: {red}"
    assert cyan[0] < 60 and cyan[1] > 200 and cyan[2] > 200, (
        f"calibre {version}: {cyan}"
    )
