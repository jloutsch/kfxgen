"""A line of only a picture ends the entry after it (#565 item 4), as
Kindle Previewer 4.0.3 writes it (probed 2026-10-27): the picture stays in
the words before it, and the next line starts an entry. kfxgen cut such a
paragraph at every picture.

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

PARAS = [
    "<p>M1 <img src='s.jpg' alt='m1a'/><br/>caption <img src='s.jpg' alt='m1b'/>"
    " inline.</p>",
    "<p>M3 start <img src='s.jpg' alt='m3a'/> mid<br/>"
    "<img src='s.jpg' alt='m3b'/><br/>end.</p>",
]

#: Previewer's entries after the heading, in reading order: a mixed entry as
#: [words, and each picture as (alt, width, height) in em], a text entry as
#: its words. A 48 px picture is 3em.
PREVIEWER = [
    ["M1 ", ("m1a", 3.0, 3.0), "\ncaption ", ("m1b", 3.0, 3.0), " inline."],
    ["M3 start ", ("m3a", 3.0, 3.0), " mid\n", ("m3b", 3.0, 3.0)],
    "end.",
    "After.",
]


def _epub(path):
    body = f"<h1>Lines</h1>{''.join(PARAS)}<p>After.</p>"
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
            '<dc:identifier id="u">l565</dc:identifier><dc:title>L565</dc:title>'
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
            '<navPoint id="n" playOrder="1"><navLabel><text>Lines</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            f"<body>{body}</body></html>",
        )
        z.writestr("s.jpg", colour_jpeg_of("RGB", (48, 48), (200, 40, 40)))


def test_a_line_of_only_a_picture_ends_the_entry(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "l565.epub", tmp / "l565.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def part(k):
        k = getattr(k, "value", k)
        if isinstance(k, str):
            return k
        s = styles[str(k["$157"])]
        return (str(k["$584"]), float(s["$56"]["$307"]), float(s["$57"]["$307"]))

    got = []
    for st in by_type(frags, "$259"):
        for e in (getattr(x, "value", x) for x in val(st)["$146"]):
            if e.get("$145"):
                got.append(str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])]))
            elif e.get("$146"):
                got.append([part(k) for k in e["$146"]])
    assert got[1:] == PREVIEWER, f"calibre {version}: {got}"
