"""The book's own metadata through calibre (#454), as Kindle Previewer
4.0.3 writes it: one author entry per dc:creator, the dc:description, and
no issue date for a year alone (calibre fills it out to a day, so the
book's own OPF is read) or none at all; a full date is kept.

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


def _epub(path, date):
    date_el = f"<dc:date>{date}</dc:date>" if date else ""
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
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:opf="http://www.idpf.org/2007/opf">'
            f'<dc:identifier id="u">md454{path.stem}</dc:identifier>'
            "<dc:title>M</dc:title>"
            '<dc:creator opf:role="aut">Author One</dc:creator>'
            '<dc:creator opf:role="aut">Author Two</dc:creator>'
            f"{date_el}<dc:description>A short description.</dc:description>"
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>M</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            "<body><h1>One</h1><p>Body.</p></body></html>",
        )


@pytest.mark.parametrize(
    ("date", "issue"),
    [("1897", None), ("2021-03-04", "2021-03-04"), (None, None)],
    ids=["year", "full", "none"],
)
def test_the_books_own_metadata(calibre, date, issue):  # noqa: F811
    tmp, _env, version = calibre
    epub = tmp / f"md{date or 'none'}.epub"
    kfx = epub.with_suffix(".kfx")
    _epub(epub, date)
    _ebook_convert(calibre, epub, kfx, True)
    (meta,) = by_type(load_fragments(kfx), "$490")
    fields = [
        (str(f["$492"]), str(f["$307"]))
        for section in val(meta)["$491"]
        if str(section.get("$495")) == "kindle_title_metadata"
        for f in section["$258"]
    ]
    assert [v for k, v in fields if k == "author"] == ["Author One", "Author Two"], (
        f"calibre {version}"
    )
    assert [v for k, v in fields if k == "description"] == ["A short description."]
    assert [v for k, v in fields if k == "issue_date"] == ([issue] if issue else [])
