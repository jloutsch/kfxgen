"""A paragraph's line height through calibre (#463), as Kindle Previewer
4.0.3 writes it: against the book's own, the line height most of its text
has, so that is 1lh. Each expected value is Previewer's for the same book
(probed 2026-10-26).

Not covered: a line height below 1.2, which calibre raises to 1.2 (its
minimum line height) before kfxgen sees it; Previewer keeps it.

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


def _epub(path, body_css):
    paras = (
        '<p style="line-height: 1.5">L0 wide text here.</p>'
        "<p>L2 plain text here.</p><p>L3 plain text here too.</p>"
    )
    page = (
        '<?xml version="1.0" encoding="utf-8"?><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
        f'<body style="{body_css}"><h1>L</h1>{paras}</body></html>'
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
            f'<dc:identifier id="u">lh463{path.stem}</dc:identifier>'
            "<dc:title>L</dc:title><dc:language>en</dc:language></metadata>"
            '<manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>L</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>L</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr("c.xhtml", page)


@pytest.mark.parametrize(
    ("body_css", "wide"), [("", 1.25), ("line-height: 2", 0.75)], ids=["none", "body-2"]
)
def test_line_height_against_the_books_own(calibre, body_css, wide):  # noqa: F811
    tmp, _env, version = calibre
    epub = tmp / f"lh{int(wide * 100)}.epub"
    kfx = epub.with_suffix(".kfx")
    _epub(epub, body_css)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                if text[:1] == "L" and text[1:2].isdigit():
                    got[text.split()[0]] = float(
                        str(styles[str(e["$157"])]["$42"]["$307"])
                    )
    assert got == {"L0": wide, "L2": 1.0, "L3": 1.0}, f"calibre {version}"
