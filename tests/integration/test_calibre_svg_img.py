"""An <img> of an SVG file becomes a PNG picture drawn at twice the SVG's
own size and shown at its own size, through calibre (#438, #440).

kfxgen writes only JPEG and PNG resources, and calibre hands an SVG file
over parsed, not as bytes, so every such picture was dropped without a
word. Kindle Previewer has no KFX form to copy: it refuses Enhanced
Typesetting for any book with one, and its MOBI keeps the SVG file.

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

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

DRAWING = (
    '<rect width="100%" height="100%" fill="#c00"/>'
    '<circle cx="50%" cy="50%" r="25%" fill="#00c"/>'
)
SVGS = {
    "attrs.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
    f"{DRAWING}</svg>",
    "viewbox.svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 150">'
    f"{DRAWING}</svg>",
}
XHTML = (
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
    "<head><title>c</title></head><body><h1>Chapter One</h1><p>Before.</p>"
    '<p><img src="attrs.svg" alt="sized"/></p><p>Middle.</p>'
    '<p><img src="viewbox.svg" alt="viewbox"/></p><p>After.</p></body></html>'
)


def _build(path):
    svgs = "".join(
        f'<item id="s{i}" href="{n}" media-type="image/svg+xml"/>'
        for i, n in enumerate(SVGS)
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">svgimg'
        "</dc:identifier><dc:title>SVG pictures</dc:title><dc:language>en"
        '</dc:language></metadata><manifest><item id="c" href="c1.xhtml" '
        f'media-type="application/xhtml+xml"/>{svgs}'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>'
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>SVG pictures</text></docTitle>'
        '<navMap><navPoint id="n1" playOrder="1"><navLabel><text>Chapter One'
        '</text></navLabel><content src="c1.xhtml"/></navPoint></navMap></ncx>'
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
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        z.writestr("c1.xhtml", XHTML)
        for name, data in SVGS.items():
            z.writestr(name, data)


@pytest.fixture(scope="module")
def book(calibre):  # noqa: F811
    """Reading order (paragraph text, or the picture's pixel size), and
    calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "svg-img.epub", tmp / "svg-img.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    dims = {
        str(f.fid): (int(val(f)["$422"]), int(val(f)["$423"]))
        for f in by_type(frags, "$164")
    }
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}

    def width(e):
        w = {str(k): v for k, v in styles[str(e["$157"])].items()}["$56"]
        return str(w["$307"]) + ("%" if str(w["$306"]) == "$314" else str(w["$306"]))

    order = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if str(e.get("$159")) == "$271":
                order.append((*dims[str(e["$175"])], width(e)))
            elif e.get("$145"):
                order.append(str(content[str(e["$145"]["name"])][e["$145"]["$403"]]))
    return order, version


def test_each_svg_picture_is_written_where_it_was(book):
    order, version = book
    pictures = [x for x in order if isinstance(x, tuple)]
    assert len(pictures) == 2, f"calibre {version}: {order}"
    i = order.index("Before.")
    assert order[i : i + 5] == [
        "Before.",
        pictures[0],
        "Middle.",
        pictures[1],
        "After.",
    ]


def test_each_svg_picture_is_drawn_at_twice_its_size_and_shown_at_its_own(book):
    """Pixels: twice 200x100 and 300x150. Page width: the SVG's own width
    against the 496 px column, as a PNG of the SVG's size would get."""
    order, version = book
    pictures = [x for x in order if isinstance(x, tuple)]
    assert pictures == [(400, 200, "40.323%"), (600, 300, "60.484%")], (
        f"calibre {version}: {order}"
    )
