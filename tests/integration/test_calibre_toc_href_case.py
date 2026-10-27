"""A TOC whose hrefs differ from the OPF's only in letter case keeps the
book's own contents through calibre (#549). Every entry was dropped as "not
in spine" and each spine file listed as "Section N"; Kindle Previewer 4.0.3
lists the book's labels (2 library books, 24 and 35 entries).

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
from tests.integration.test_calibre_cover_in_text import _page  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _epub(path):
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
            '<dc:identifier id="u">case549</dc:identifier><dc:title>C</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="a" href="one.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="b" href="two.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="a"/><itemref idref="b"/>'
            "</spine></package>",
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>C</text></docTitle><navMap>'
            '<navPoint id="n1" playOrder="1"><navLabel><text>The First</text>'
            '</navLabel><content src="One.xhtml"/></navPoint>'
            '<navPoint id="n2" playOrder="2"><navLabel><text>The Second</text>'
            '</navLabel><content src="Two.xhtml#s"/></navPoint></navMap></ncx>',
        )
        z.writestr("one.xhtml", _page("<h1>One</h1><p>First text.</p>"))
        z.writestr("two.xhtml", _page('<p>Before.</p><h1 id="s">Two</h1><p>B.</p>'))


def _labels(kfx):
    out = []

    def units(us):
        for u in us or []:
            u = getattr(u, "value", u)
            out.append(str(getattr(u["$241"], "value", u["$241"]).get("$244", "")))
            units(u.get("$247"))

    for f in by_type(load_fragments(kfx), "$389"):
        for c in val(f)[0]["$392"]:
            c = getattr(c, "value", c)
            if str(c["$235"]) == "$212":
                units(c["$247"])
    return out


def test_a_toc_spelled_in_another_case_is_kept(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "case549.epub", tmp / "case549.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    assert _labels(kfx) == ["The First", "The Second"], f"calibre {version}"
