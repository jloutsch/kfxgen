"""A picture inside a paragraph's words keeps the size and vertical-align
the markup gives it, as Kindle Previewer 4.0.3 writes them (#565, probed
2026-10-27 with a 300 x 220 picture): a width and a height both given are
both kept, `vertical-align` is `$44`, and a picture the markup sizes is
marked `$546: $377`.

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

#: (alt, markup, Previewer's style: width em, height em, $546, $44)
SHAPES = [
    ("P1", "", (18.75, 13.75, None, None)),
    ("P2", "style='width:2em;height:1em'", (2, 1, "$377", None)),
    ("P3", "width='40' height='40'", (1.5, 1.5, "$377", None)),
    ("P4", "class='sq'", (1, 1, "$377", None)),
    ("P5", "style='vertical-align:middle'", (18.75, 13.75, None, "$320")),
    ("P6", "style='vertical-align:top'", (18.75, 13.75, None, "$58")),
    ("P7", "style='vertical-align:bottom'", (18.75, 13.75, None, "$60")),
    ("P8", "style='vertical-align:text-top'", (18.75, 13.75, None, "$447")),
    ("P9", "style='vertical-align:text-bottom'", (18.75, 13.75, None, "$449")),
    ("P10", "style='vertical-align:baseline'", (18.75, 13.75, None, "$350")),
    ("P11", "style='vertical-align:super'", (18.75, 13.75, None, "$370")),
    ("P12", "style='vertical-align:sub'", (18.75, 13.75, None, "$371")),
    ("P13", "style='height:1em'", (1.36364, 1, "$377", None)),
    ("P14", "style='width:2em'", (2, 1.46667, "$377", None)),
    ("P15", "style='height:1em;vertical-align:middle'", (1.36364, 1, "$377", "$320")),
    ("P16", "style='width:20%'", (6.4, 4.69333, "$377", None)),
    ("P18", "style='width:12pt'", (1, 0.733333, "$377", None)),
    ("P19", "style='width:20%;height:1em'", (6.4, 1, "$377", None)),
]


def _epub(path):
    body = "<h1>Inline</h1>" + "".join(
        f"<p>{k} before <img src='p.jpg' alt='{k}' {a}/> after words.</p>"
        for k, a, _ in SHAPES
    )
    page = (
        '<?xml version="1.0" encoding="utf-8"?><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title>'
        f"<style>.sq{{width:1em;height:1em}}</style></head><body>{body}</body></html>"
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
            '<dc:identifier id="u">s565</dc:identifier><dc:title>S565</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="p" href="p.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>S</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>Inline</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr("c.xhtml", page)
        z.writestr("p.jpg", colour_jpeg_of("RGB", (300, 220), (40, 120, 200)))


def test_inline_pictures_are_styled_as_previewer_styles_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "s565.epub", tmp / "s565.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            for part in getattr(e, "value", e).get("$146") or ():
                if hasattr(part, "get") and str(part.get("$159")) == "$271":
                    s = styles[str(part["$157"])]
                    got[str(part["$584"])] = (
                        float(s["$56"]["$307"]),
                        float(s["$57"]["$307"]),
                        str(s["$546"]) if s.get("$546") else None,
                        str(s["$44"]) if s.get("$44") else None,
                    )
    assert sorted(got) == sorted(k for k, _, _ in SHAPES), f"calibre {version}"
    for alt, _markup, (w, h, marked, valign) in SHAPES:
        gw, gh, gmarked, gvalign = got[alt]
        assert abs(gw - w) < 0.001 and abs(gh - h) < 0.001, (
            f"calibre {version}: {alt} {got[alt]}"
        )
        assert (gmarked, gvalign) == (marked, valign), f"calibre {version}: {alt}"
