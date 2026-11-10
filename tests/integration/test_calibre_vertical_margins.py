"""Margins in vertical text, through calibre (#247).

Kindle Previewer 4.0.3 (probes P247g and QA's P747, decoded) lays a
vertical-rl book's margins out as a browser does: the user agent's default
margins of <p>, <h1>, <h2> and <hr> are the space before and after, written
right (`$50`) and left (`$48`); the book's own margin-top and margin-bottom
are the top and bottom (`$47`, `$49`, in ems, not collapsed), added to the
default space; its margin-right and margin-left are the space before and
after. A horizontal file keeps top and bottom. calibre folds its user
agent's margins into its own classes when it flattens the CSS, so kfxgen
reads them first, in `specialize_css_for_output`, which only a real
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
T = "朝の光が窓から入る。"
#: QA's P747: V01 `p { margin: 1em 0 }`; V03 `.mt { margin-top: 2em }` on
#: default <p>s; V12 `.ml { margin-left: 2em; margin-right: 1em }`.
V01 = (f"<p>V01a {T}</p><p>V01b {T}</p>", VERT + " p { margin: 1em 0 }")
V03 = (
    f'<p>V03a {T}</p><p class="mt2">V03t {T}</p><p>V03z {T}</p>',
    VERT + " .mt2 { margin-top: 2em }",
)
V12 = (
    f'<p>V12a {T}</p><p class="ml">V12l {T}</p><p>V12z {T}</p>',
    VERT + " .ml { margin-left: 2em; margin-right: 1em }",
)


@pytest.fixture(scope="module")
def margins(calibre):  # noqa: F811
    """{first word: its $47-$50 keys}, both files."""
    tmp, _env, _version = calibre
    epub, kfx = tmp / "m247.epub", tmp / "m247.kfx"
    files = {
        "c1.xhtml": (BODY, VERT + REST),
        "c2.xhtml": (H_BODY, HORIZ + REST),
        "v01.xhtml": V01,
        "v03.xhtml": V03,
        "v12.xhtml": V12,
    }
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
        z.writestr("toc.ncx", _ncx([(h, h, 1) for h in files]))
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
                k: "%g%s"
                % (
                    float(s[k]["$307"]),
                    {"$310": "lh", "$308": "em", "$314": "%"}[str(s[k]["$306"])],
                )
                for k in ("$47", "$48", "$49", "$50")
                if k in s
            }
    return out


def test_a_default_paragraphs_margins_go_right_and_left(margins):
    # G01: Previewer $50 1.11667lh and $48 0.833333lh.
    assert margins["G01"] == {"$50": "1.11667lh", "$48": "0.833333lh"}


def test_headings_default_margins_go_right_and_left(margins):
    assert margins["第一章"] == {
        "$50": "0.558333lh"
    }  # its left collapses into G01's right
    assert margins["G05"] == {"$50": "0.691667lh", "$48": "0.691667lh"}


def test_the_books_own_margins_are_physical(margins):
    assert margins["G03"] == {"$47": "2em"}  # margin: 0; margin-top 2em
    assert margins["G04"] == {"$50": "1.66667lh"}  # margin: 0; margin-right 2em
    assert margins["G02"] == {}


def test_a_books_margin_top_is_added_to_the_default_space(margins):
    # P747 V03t: $47 2em and $50 0.833333lh.
    assert margins["V03t"] == {"$47": "2em", "$50": "0.833333lh"}


def test_a_books_top_and_bottom_margins_dont_collapse(margins):
    # P747 V01: every <p> $47 1em and $49 1em.
    assert margins["V01a"] == {"$47": "1em", "$49": "1em"}
    assert margins["V01b"] == {"$47": "1em", "$49": "1em"}


def test_a_books_right_and_left_margins_collapse_as_the_space_between(margins):
    # P747 V12: V12l $50 0.833333lh; its 2em left becomes V12z's $50.
    assert margins["V12l"] == {"$50": "0.833333lh"}
    assert margins["V12z"] == {"$50": "1.66667lh", "$48": "0.833333lh"}


def test_a_horizontal_file_keeps_top_and_bottom(margins):
    assert "$50" not in margins["H01"] and "$47" in margins["H01"]
