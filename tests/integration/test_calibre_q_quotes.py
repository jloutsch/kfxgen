"""<q> is printed with its language's quotation marks through calibre (#469).

kfxgen walked <q> as an ordinary inline element, so its quotation marks,
which a browser's stylesheet adds, were lost. This checks that calibre
keeps the lang attributes the marks depend on, on <html> and on the <q>.

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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BODY = (
    "<h1>One</h1>"
    "<p>Q1 a <q>quoted <q>inner</q></q> run.</p>"
    '<p>Q2 <q lang="en-US">us</q> and <q lang="de">de</q>.</p>'
)


def _epub(path, html_lang):
    attrs = f' xml:lang="{html_lang}" lang="{html_lang}"' if html_lang else ""
    page = (
        '<?xml version="1.0" encoding="utf-8"?><html '
        f'xmlns="http://www.w3.org/1999/xhtml"{attrs}><head><title>c</title></head>'
        f"<body>{BODY}</body></html>"
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
            f'<dc:identifier id="u">q-{html_lang}</dc:identifier><dc:title>Q</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>Q</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr("c.xhtml", page)


@pytest.mark.parametrize(
    ("html_lang", "q1", "q2"),
    [
        # No lang: plain marks, as Kindle Previewer 4.0.3 prints them.
        (None, "Q1 a \"quoted 'inner'\" run.", 'Q2 "us" and „de“.'),
        # The document's lang reaches each <q>.
        ("en", "Q1 a “quoted ‘inner’” run.", None),
        ("fr", "Q1 a «quoted «inner»» run.", None),
    ],
)
def test_q_is_printed_with_its_marks(calibre, html_lang, q1, q2):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"q-{html_lang}.epub", tmp / f"q-{html_lang}.kfx"
    _epub(epub, html_lang)
    _ebook_convert(calibre, epub, kfx, True)
    texts = [
        str(t) for f in by_type(load_fragments(kfx), "$145") for t in val(f)["$146"]
    ]
    assert q1 in texts, f"calibre {version}: {texts}"
    if q2:
        assert q2 in texts, f"calibre {version}: {texts}"
