"""A picture inside a table cell's words stays in them (#565 item 5), as
Kindle Previewer 4.0.3 writes it (probed 2026-10-27): the cell's words and
picture are one entry, as in a paragraph (#529). kfxgen kept every cell
picture as an entry of its own (#262) and cut the words around it. A
picture alone in its cell, or alone on its line, is still an entry of its
own, in Previewer's file too.

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

from tests._helpers import colour_jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

ROWS = [
    "<tr><td>C1 cell <img src='s.jpg' alt='c1'/> words.</td><td>C1 right</td></tr>",
    "<tr><td><img src='s.jpg' alt='c2'/></td><td>C2 picture alone</td></tr>",
    "<tr><td><p>C3 para <img src='s.jpg' alt='c3'/> words.</p></td>"
    "<td>C3 right</td></tr>",
    "<tr><td>C4 <img src='s.jpg' alt='c4a'/> and <img src='s.jpg' alt='c4b'/> two."
    "</td><td>C4 right</td></tr>",
    "<tr><td><img src='s.jpg' alt='c5'/><br/>C5 caption</td><td>C5 right</td></tr>",
]

#: Previewer's mixed entries, in reading order: words, and each picture as
#: (alt, width, height) in em. A 48 px picture is 3em.
PREVIEWER = [
    ["C1 cell ", ("c1", 3.0, 3.0), " words."],
    ["C3 para ", ("c3", 3.0, 3.0), " words."],
    ["C4 ", ("c4a", 3.0, 3.0), " and ", ("c4b", 3.0, 3.0), " two."],
]


def _epub(path):
    body = f"<h1>Cells</h1><table>{''.join(ROWS)}</table><p>After.</p>"
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
            '<dc:identifier id="u">c565</dc:identifier><dc:title>C565</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="s" href="s.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>C</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>Cells</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            f"<body>{body}</body></html>",
        )
        z.writestr("s.jpg", colour_jpeg_of("RGB", (48, 48), (200, 40, 40)))


def test_a_picture_in_a_cells_words_stays_in_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "c565.epub", tmp / "c565.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    mixed, alone = [], []

    def part(k):
        k = getattr(k, "value", k)
        if isinstance(k, str):
            return k
        s = styles[str(k["$157"])]
        return (str(k["$584"]), float(s["$56"]["$307"]), float(s["$57"]["$307"]))

    def walk(nodes):
        for e in nodes or ():
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            kids = e.get("$146") or ()
            if str(e.get("$159")) == "$269" and any(isinstance(k, str) for k in kids):
                mixed.append([part(k) for k in kids])
                continue
            if str(e.get("$159")) == "$271":
                alone.append(str(e["$584"]))
            walk(kids)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"])
    assert mixed == PREVIEWER, f"calibre {version}: {mixed}"
    # Alone in its cell, or alone on its line: an entry of its own.
    assert alone == ["c2", "c5"], f"calibre {version}: {alone}"
