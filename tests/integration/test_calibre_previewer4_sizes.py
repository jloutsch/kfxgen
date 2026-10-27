"""Picture sizes as Kindle Previewer 4.0.3 writes them, through calibre
(#467): a 1 x 1 spacer is kept at px / 512 of the column, as Previewer keeps
it, and a picture's longest side is capped at 1920 px, as Previewer caps
it (4000 x 3000 became 1920 x 1440 in Previewer's own file).

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._helpers import colour_jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _spacer_png():
    """A real 1 x 1 PNG, 69 bytes."""

    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


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
            '<dc:identifier id="u">p467</dc:identifier><dc:title>P</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="s" href="s.png" media-type="image/png"/>'
            '<item id="b" href="b.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>P</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            '<body><h1>One</h1><p><img src="s.png" alt="s"/></p><p>Text.</p>'
            '<p><img src="b.jpg" alt="b"/></p><p>More.</p></body></html>',
        )
        z.writestr("s.png", _spacer_png())
        z.writestr("b.jpg", colour_jpeg_of("RGB", (4000, 3000), (200, 30, 30)))


def test_a_spacer_is_kept_and_the_cap_is_1920(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "p467.epub", tmp / "p467.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    res = {str(f.fid): val(f) for f in by_type(frags, "$164")}
    shown = [
        (
            int(res[str(e["$175"])]["$422"]),
            int(res[str(e["$175"])]["$423"]),
            str(styles[str(e["$157"])]["$56"]["$307"]),
        )
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and str(e.get("$159")) == "$271"
    ]
    assert shown == [(1, 1, "0.195"), (1920, 1440, "100.0")], f"calibre {version}"
