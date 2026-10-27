"""A small picture is sized by its pixel width and keeps its shape, through
calibre (#462): not a 3em square shifted down 9 pt. Kindle Previewer 4.0.3
writes a picture alone in its paragraph as px / 512 of the column (#467):
these are its own values for the two sizes.

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
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

SIZES = [(100, 100), (300, 220)]


def _epub(path):
    body = "".join(
        f'<p><img src="p{w}.jpg" alt="p"/></p><p>After {w}.</p>' for w, _ in SIZES
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
        items = "".join(
            f'<item id="p{w}" href="p{w}.jpg" media-type="image/jpeg"/>'
            for w, _ in SIZES
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">sp462</dc:identifier><dc:title>S</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            f"{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>S</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            f"<body><h1>One</h1>{body}</body></html>",
        )
        for w, h in SIZES:
            z.writestr(f"p{w}.jpg", decodable_jpeg_of(w, h))


def test_a_small_picture_is_sized_by_its_width(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "sp462.epub", tmp / "sp462.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    shown = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and str(e.get("$159")) == "$271":
                style = styles[str(e["$157"])]
                shown.append(
                    (
                        str(style["$56"]["$307"]),
                        str(style["$56"]["$306"]),
                        "$57" in style,
                        "$31" in style,
                    )
                )
    assert shown == [
        ("19.531", "$314", False, False),
        ("58.594", "$314", False, False),
    ], f"calibre {version}"
