"""Pictures whose size or presence comes from calibre's Stylizer (#417).

calibre folds an `<img>`'s `width` and `height` attributes into its CSS and
strips them, so kfxgen sees an attribute size only through the Stylizer
(#153). A page drawn as a `background-image` on an empty div has no `<img>`
at all; kfxgen writes it as a picture when the div's computed style holds a
`url()` and `background-repeat: no-repeat` (#168). Every other test of both
runs without a Stylizer.

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

from kfxgen.native_generator import _IMAGE_COLUMN_PX  # noqa: E402
from tests._helpers import jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = (
    ".scan { background-image: url(page.jpg); background-repeat: no-repeat;"
    " width: 600px; height: 400px }"
    ".tiled { background-image: url(page.jpg); background-repeat: repeat }"
)
PAGES = {
    "sized": (
        "<h1>Sized</h1><p>Before.</p>"
        '<p><img src="pic.jpg" alt="attr" width="150" height="100"/></p>'
        '<p><img src="pic.jpg" alt="tall" height="50%"/></p>'
        '<p><img src="pic.jpg" alt="plain"/></p><p>After.</p>'
    ),
    "scan": '<div class="scan"></div>',
    "tiled": "<h1>Tiled</h1><div class='tiled'></div><p>Over a texture.</p>",
}


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>'
        f'{title}</title><link rel="stylesheet" type="text/css" href="s.css"/>'
        f"</head><body>{body}</body></html>"
    )


def _build_epub(path):
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head><meta name="dtb:uid" content="t"/></head>'
        "<docTitle><text>Pictures</text></docTitle><navMap>"
        + "".join(
            f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{n}</text>'
            f'</navLabel><content src="{n}.xhtml"/></navPoint>'
            for i, n in enumerate(PAGES, 1)
        )
        + "</navMap></ncx>"
    )
    items = "".join(
        f'<item id="{n}" href="{n}.xhtml" media-type="application/xhtml+xml"/>'
        for n in PAGES
    )
    spine = "".join(f'<itemref idref="{n}"/>' for n in PAGES)
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
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">t'
            "</dc:identifier><dc:title>Pictures</dc:title><dc:language>en"
            "</dc:language></metadata><manifest>"
            + items
            + '<item id="css" href="s.css" media-type="text/css"/>'
            '<item id="pic" href="pic.jpg" media-type="image/jpeg"/>'
            '<item id="pg" href="page.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx">' + spine + "</spine></package>",
        )
        for n, body in PAGES.items():
            z.writestr(f"{n}.xhtml", _page(n, body))
        z.writestr("s.css", CSS)
        z.writestr("pic.jpg", jpeg_of(600, 400))
        z.writestr("page.jpg", jpeg_of(800, 1200))
        z.writestr("toc.ncx", ncx)


@pytest.fixture(scope="module")
def book(calibre):  # noqa: F811
    """Per section in order, its pictures as (alt, style), and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "pictures.epub", tmp / "pictures.kfx"
    _build_epub(epub)
    _ebook_convert(calibre, epub, kfx, native=True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    pictures = []
    for story in by_type(frags, "$259"):
        pictures.append(
            [
                (str(e.get("$584")), styles.get(str(e.get("$157")), {}))
                for e in iter_entries(val(story)["$146"])
                if str(e.get("$159")) == "$271"
            ]
        )
    return pictures, version


def _pct(style, key):
    return float(style[key]["$307"]) if key in style else None


def test_attribute_sizes_reach_the_picture(book):
    # Previewer's rule (#153): a width is divided by the text column, and a
    # height counts only as a percentage, and only without a width.
    (sized, _scan, _tiled), version = book
    styles = dict(sized)
    width = round(150 / _IMAGE_COLUMN_PX * 100, 3)
    assert _pct(styles["attr"], "$56") == width, f"calibre {version}"
    assert _pct(styles["tall"], "$57") == 50.0, f"calibre {version}"
    assert _pct(styles["tall"], "$56") is None, f"calibre {version}"
    assert _pct(styles["plain"], "$56") == 100.0, f"calibre {version}"


def test_a_page_drawn_as_a_background_is_a_picture(book):
    (_sized, scan, tiled), version = book
    assert len(scan) == 1, f"calibre {version}"
    assert tiled == [], f"calibre {version}: a tiled background is a texture"
