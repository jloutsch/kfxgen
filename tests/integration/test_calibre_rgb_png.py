"""An RGB PNG over the 1920 px cap is written as a JPEG when that is smaller
(#557): calibre scaled it as a PNG, which grew one picture from 89 KB to
270 KB. Kindle Previewer 4.0.3 writes such a picture as a JPEG. One with an
alpha channel stays a PNG: a JPEG has no transparency.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import random
import struct
import sys
import zipfile
import zlib
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

W, H = 2000, 1500


def _png(alpha):
    """A noisy gradient, as a photo is to PNG's compression: RGB, or RGBA
    with its top half transparent."""

    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rnd = random.Random(557)
    rows = []
    for y in range(H):
        row = bytearray(b"\x00")
        for x in range(W):
            n = rnd.randrange(24)
            row += bytes((x * 255 // W ^ n, y * 255 // H ^ n, 128 ^ n))
            if alpha:
                row.append(0 if y < H // 2 else 255)
        rows.append(bytes(row))
    kind = 6 if alpha else 2
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, kind, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
        + chunk(b"IEND", b"")
    )


def _epub(path, pictures):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        items = "".join(
            f'<item id="{n[0]}" href="{n}" media-type="image/png"/>' for n in pictures
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">r557</dc:identifier><dc:title>R</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            f"{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>R</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        body = "".join(
            f"<p>{n[0]} text.</p><p><img src='{n}' alt='{n[0]}'/></p>" for n in pictures
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            f"<body><h1>One</h1>{body}</body></html>",
        )
        for name, data in pictures.items():
            z.writestr(name, data)


def test_an_opaque_png_over_the_cap_becomes_a_smaller_jpeg(calibre):  # noqa: F811
    tmp, _env, version = calibre
    rgb, rgba = _png(False), _png(True)
    epub, kfx = tmp / "r557.epub", tmp / "r557.kfx"
    _epub(epub, {"r.png": rgb, "t.png": rgba})
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    raw = {str(f.fid): bytes(val(f)) for f in by_type(frags, "$417")}
    res = {str(f.fid): val(f) for f in by_type(frags, "$164")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if e.get("$175") is not None:
                r = res[str(e["$175"])]
                data = raw[str(r["$165"])]
                got[str(e["$584"])] = (str(r["$161"]), data[:3], len(data))
    # The RGB one: a JPEG, labelled one ($285), smaller than the source.
    fmt, magic, size = got["r"]
    assert (fmt, magic) == ("$285", b"\xff\xd8\xff"), f"calibre {version}: {got}"
    assert size < len(rgb), f"calibre {version}: {size} >= {len(rgb)}"
    # The RGBA one: still a PNG with its alpha channel (colour type 6).
    fmt, magic, _size = got["t"]
    assert (fmt, magic) == ("$284", b"\x89PN"), f"calibre {version}: {got}"
