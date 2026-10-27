"""A palette PNG over the 1920 px cap stays a palette PNG when scaled (#557):
calibre gave it back as RGB, several times its bytes. One with a
transparent colour stays RGBA, its transparency kept.

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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _palette_png(w, h, transparent):
    """64 colours in 40 px squares; colour 0 transparent when asked."""

    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    palette = b"".join(
        bytes((i * 37 % 256, i * 91 % 256, i * 151 % 256)) for i in range(64)
    )
    rows = b"".join(
        b"\x00" + bytes((x // 40 + y // 40) % 64 for x in range(w)) for y in range(h)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
        + chunk(b"PLTE", palette)
        + (chunk(b"tRNS", b"\x00") if transparent else b"")
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


PICTURES = {"p.png": False, "t.png": True}


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
        pictures = "".join(
            f'<item id="{n[0]}" href="{n}" media-type="image/png"/>' for n in PICTURES
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">p557</dc:identifier><dc:title>P</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            f"{pictures}"
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
            '<body><h1>One</h1><p><img src="p.png" alt="p"/></p><p>Text.</p>'
            '<p><img src="t.png" alt="t"/></p><p>More.</p></body></html>',
        )
        for name, transparent in PICTURES.items():
            z.writestr(name, _palette_png(2000, 1466, transparent))


def test_a_scaled_palette_png_stays_a_palette(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "p557.epub", tmp / "p557.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    raw = {str(f.fid): bytes(val(f)) for f in by_type(frags, "$417")}
    pictures = [raw[str(val(f)["$165"])] for f in by_type(frags, "$164")]
    # (width, height, PNG colour type): 3 a palette, 6 RGBA.
    shown = sorted((*struct.unpack(">II", p[16:24]), p[25]) for p in pictures)
    assert shown == [(1919, 1407, 3), (1919, 1407, 6)], f"calibre {version}"
    source = len(_palette_png(2000, 1466, False))
    # The palette one stays near its own size; calibre's RGB copy was 2.5x.
    assert min(len(p) for p in pictures) < 2 * source, f"calibre {version}"
