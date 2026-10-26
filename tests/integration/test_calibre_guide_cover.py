"""A cover only the OPF guide names is found, through calibre (#495).

kfxgen looks for a cover in the metadata, then in the guide, then by
name. The guide lookup iterated calibre's Guide, which gives type
strings, so it never matched. Through calibre's EPUB input that didn't
lose a cover: calibre sets the metadata cover from the guide, and the
first lookup finds it. This passes on main too, and pins that; the guide
lookup's own fix is tested in tests/unit/test_cover_validation.py.

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
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


@pytest.mark.parametrize("kind", ["other.ms-coverimage-standard", "cover"])
def test_a_cover_only_the_guide_names_is_found(calibre, kind):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"guide495-{kind}.epub", tmp / f"guide495-{kind}.kfx"
    with zipfile.ZipFile(epub, "w") as z:
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
            f'<dc:identifier id="u">guide495{kind}</dc:identifier><dc:title>G</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="x1" href="images/plate1.jpg" media-type="image/jpeg"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine>'
            f'<guide><reference type="{kind}" title="Cover" href="images/plate1.jpg"/>'
            "</guide></package>",
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>G</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
            "<body><h1>One</h1><p>Text.</p></body></html>",
        )
        z.writestr("images/plate1.jpg", decodable_jpeg_of(600, 800))
    _ebook_convert(calibre, epub, kfx, True)
    meta = {
        str(m["$492"])
        for f in by_type(load_fragments(kfx), "$490")
        for g in val(f)["$491"]
        for m in g["$258"]
    }
    assert "cover_image" in meta, f"calibre {version}"
