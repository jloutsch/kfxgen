"""The reading system's margins in vertical text, through calibre (#247).

Probe P247g (Kindle Previewer 4.0.3, decoded): in a vertical-rl book the
default margins of <p>, <h1>, <h2> and <hr> are written right (`$50`) and
left (`$48`); the book's own margin-top and margin-bottom stay top and
bottom, and a horizontal file keeps them top and bottom. calibre folds its
user agent's margins into its own classes when it flattens the CSS, so
kfxgen reads them first, in `specialize_css_for_output`, which only a real
conversion runs.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
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
from tests.unit.test_contents_pages import _ncx, _page  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

VERT = "html { writing-mode: vertical-rl; -webkit-writing-mode: vertical-rl }"
HORIZ = "html { writing-mode: horizontal-tb; -webkit-writing-mode: horizontal-tb }"
REST = " .ni { text-indent: 0; margin: 0 } .mt { margin: 0; margin-top: 2em } .mr { margin: 0; margin-right: 2em }"
BODY = (
    "<h1>第一章　余白</h1>"
    "<p>G01 既定の余白の段落です。</p>"
    '<p class="ni">G02 余白のない段落です。</p>'
    '<p class="mt">G03 上に二字の余白です。</p>'
    '<p class="mr">G04 右に二字の余白です。</p>'
    "<h2>G05 小見出し</h2>"
    '<p class="ni">G06 見出しの後です。</p>'
)
H_BODY = "<h1>Colophon</h1><p>H01 A default paragraph.</p>"


@pytest.fixture(scope="module")
def margins(calibre):  # noqa: F811
    """{first word: its $47-$50 keys}, both files."""
    tmp, _env, _version = calibre
    epub, kfx = tmp / "m247.epub", tmp / "m247.kfx"
    files = {"c1.xhtml": (BODY, VERT + REST), "c2.xhtml": (H_BODY, HORIZ + REST)}
    items = "".join(
        f'<item id="x{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in enumerate(files)
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="u">urn:kfxgen:m247</dc:identifier><dc:title>余白</dc:title>'
        "<dc:creator>A</dc:creator><dc:language>ja</dc:language></metadata>"
        f'<manifest>{items}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx" page-progression-direction="rtl">'
        + "".join(f'<itemref idref="x{i}"/>' for i in range(len(files)))
        + "</spine></package>"
    )
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("content.opf", opf)
        z.writestr(
            "toc.ncx", _ncx([("第一章", "c1.xhtml", 1), ("Colophon", "c2.xhtml", 1)])
        )
        for h, (b, css) in files.items():
            z.writestr(h, _page(b, ' xml:lang="ja"', css))
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            ref = e.get("$145") if hasattr(e, "get") else None
            if not ref:
                continue
            word = str(texts[str(ref["name"])][ref["$403"]]).split()[0]
            s = styles.get(str(e.get("$157")), {})
            out[word] = {
                k
                for k in ("$47", "$48", "$49", "$50")
                if k in s and not (k == "$48" and str(s[k]["$306"]) == "$314")
            }
    return out


def test_a_default_paragraphs_margins_go_right_and_left(margins):
    # G01: Previewer $50 1.11667lh and $48 0.833333lh.
    assert margins["G01"] == {"$50", "$48"}


def test_headings_default_margins_go_right_and_left(margins):
    assert margins["第一章"] == {"$50"}  # its left collapses into G01's right
    assert margins["G05"] == {"$50", "$48"}


def test_the_books_own_margins_stay_where_it_put_them(margins):
    assert margins["G03"] == {"$47"}  # margin-top 2em
    assert margins["G04"] == {"$50"}  # margin-right 2em
    assert margins["G02"] == set()


def test_a_horizontal_file_keeps_top_and_bottom(margins):
    assert "$50" not in margins["H01"] and "$47" in margins["H01"]
