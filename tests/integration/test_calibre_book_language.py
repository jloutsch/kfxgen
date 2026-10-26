"""The book's language reaches the KFX as a language code, through calibre
(#453).

kfxgen cut the language to its first two letters. calibre hands over its own
three-letter code (`por` for `pt_BR`), so the cut wrote codes that name no
language ("po", and by the same cut "sp" and "jp") or the wrong one ("ha",
Hausa, for Hawaiian), and "un" for a book with none. Kindle Previewer 4.0.3
writes the two-letter code, and "en" for a book with no language.

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


def _epub(path, lang):
    lang_el = f"<dc:language>{lang}</dc:language>" if lang else ""
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
            f'<dc:identifier id="u">lang-{lang}</dc:identifier><dc:title>L</dc:title>'
            f"{lang_el}</metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>L</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
            "<body><h1>One</h1><p>Text.</p></body></html>",
        )


@pytest.mark.parametrize(
    ("lang", "written"),
    [
        ("en", "en"),
        ("eng", "en"),
        ("pt_BR", "pt"),
        ("es", "es"),
        ("ja", "ja"),
        ("haw", "haw"),
        (None, "en"),
    ],
)
def test_the_book_language_is_a_language_code(calibre, lang, written):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"lang-{lang}.epub", tmp / f"lang-{lang}.kfx"
    _epub(epub, lang)
    _ebook_convert(calibre, epub, kfx, True)
    meta = {
        str(m["$492"]): str(m["$307"])
        for f in by_type(load_fragments(kfx), "$490")
        for g in val(f)["$491"]
        for m in g["$258"]
    }
    assert meta["language"] == written, f"calibre {version}"
