"""Vertical writing through calibre (#247).

Probe P247 (Kindle Previewer 4.0.3, decoded): a Japanese book set
`writing-mode: vertical-rl` with an rtl spine is written with `$538`
`$560: $559` and `$192: $376`, whether the rule is on <html> (P247a), on
<body> (P247d) or written `-epub-writing-mode` (P247e, which calibre reads as
`writing-mode`). A horizontal file in it gets `$560: $557` on its section
(P247f), as does a file that sets no writing mode (QA's P746 m2, m4, m5).
The book is vertical when its spine is rtl and its `dc:language` Japanese,
whatever its CSS (m6: all horizontal-tb). With an ltr spine the book stays
horizontal and each vertical section gets `$560: $559` (P247c). `text-orientation` is a run's `$706`.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
"""

import re
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

VERT = "writing-mode: vertical-rl; -webkit-writing-mode: vertical-rl;"
HORIZ = "writing-mode: horizontal-tb; -webkit-writing-mode: horizontal-tb;"
REST = " .up { text-orientation: upright } .side { text-orientation: sideways }"
CH1 = (
    "<h1>第一章</h1>"
    '<p>V03 英字を立てて<span class="up">ABC</span>と書く。</p>'
    '<p>V05 横に寝かせて<span class="side">XYZ</span>と書く。</p>'
    '<p>V10 次へ<a href="c2.xhtml#t">行く</a>。</p>'
)
CH2 = '<h1>第二章</h1><p id="t">T1 ここが行き先です。</p>'

#: name: (rtl spine, CSS for c1.xhtml, CSS for c2.xhtml)
VARIANTS = {
    "a": (True, f"html {{ {VERT} }}" + REST, f"html {{ {VERT} }}" + REST),
    "c": (False, f"html {{ {VERT} }}" + REST, f"html {{ {VERT} }}" + REST),
    "d": (True, f"body {{ {VERT} }}" + REST, f"body {{ {VERT} }}" + REST),
    "e": (True, "html { -epub-writing-mode: vertical-rl }" + REST, "html { -epub-writing-mode: vertical-rl }" + REST),
    "f": (True, f"html {{ {VERT} }}" + REST, f"html {{ {HORIZ} }}" + REST),
    "m6": (True, f"html {{ {HORIZ} }}" + REST, f"html {{ {HORIZ} }}" + REST),
    "unset": (True, REST, f"html {{ {VERT} }}" + REST),
    "t1": (True, f"html {{ {VERT} }}" + REST, f"html {{ {VERT} }}" + REST,
           ("<p>B01 目次にない横書きの頁。</p>", f"html {{ {HORIZ} }}" + REST)),
}  # fmt: skip


def _write(path, rtl, css1, css2, middle=None):
    body = {"c1.xhtml": (CH1, css1)}
    if middle:
        # A file left out of the TOC, between the two (QA's P746 t1).
        body["m.xhtml"] = middle
    body["c2.xhtml"] = (CH2, css2)
    items = "".join(
        f'<item id="x{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in enumerate(body)
    )
    spine = "".join(f'<itemref idref="x{i}"/>' for i in range(len(body)))
    ppd = ' page-progression-direction="rtl"' if rtl else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="u">urn:kfxgen:{path.stem}</dc:identifier><dc:title>縦書き</dc:title>'
        "<dc:creator>A</dc:creator><dc:language>ja</dc:language></metadata>"
        f'<manifest>{items}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        f'<spine toc="ncx"{ppd}>{spine}</spine></package>'
    )
    toc = [("第一章", "c1.xhtml", 1), ("第二章", "c2.xhtml", 1)]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", _ncx(toc))
        for h, (b, css) in body.items():
            z.writestr(h, _page(b, ' xml:lang="ja"', css))


@pytest.fixture(scope="module")
def books(calibre):  # noqa: F811
    """{variant: its decoded fragments}."""
    tmp, _env, _version = calibre
    out = {}
    for name, (rtl, css1, css2, *middle) in VARIANTS.items():
        epub, kfx = tmp / f"v247{name}.epub", tmp / f"v247{name}.kfx"
        _write(epub, rtl, css1, css2, *middle)
        _ebook_convert(calibre, epub, kfx, True)
        out[name] = load_fragments(kfx)
    return out


def _doc(frags):
    (frag,) = by_type(frags, "$538")
    return val(frag)


def _sections(frags):
    order = [str(n) for n in _doc(frags)["$169"][0]["$170"]]
    by_name = {str(f.fid): val(f) for f in by_type(frags, "$260")}
    modes = []
    for name in order:
        (template,) = by_name[name]["$141"]
        mode = template.get("$560")
        modes.append(None if mode is None else str(mode))
    return modes


def _features(frags):
    (frag,) = by_type(frags, "$585")
    return {str(f["$492"]) for f in val(frag)["$590"]}


@pytest.mark.parametrize("name", ["a", "d", "e"])
def test_a_vertical_book_is_written_vertical(books, name):
    doc = _doc(books[name])
    assert (str(doc["$560"]), str(doc["$192"])) == ("$559", "$376")
    assert {"jp-reflow-language", "jpvertical-reflow-language"} <= _features(
        books[name]
    )
    assert _sections(books[name]) == [None, None]


def test_a_horizontal_file_in_it_is_set_on_its_section(books):
    assert str(_doc(books["f"])["$560"]) == "$559"
    assert _sections(books["f"]) == [None, "$557"]


def test_with_an_ltr_spine_each_vertical_section_is_set(books):
    doc = _doc(books["c"])
    assert (str(doc["$560"]), str(doc["$192"])) == ("$557", "$376")
    assert _sections(books["c"]) == ["$559", "$559"]
    assert "yj_mixed_writing_mode" in _features(books["c"])


def test_text_orientation_is_written_on_its_run(books):
    frags = books["a"]
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            ref = e.get("$145") if hasattr(e, "get") else None
            if not ref:
                continue
            text = str(texts[str(ref["name"])][ref["$403"]])
            for r in e.get("$142") or ():
                o = styles.get(str(r.get("$157")), {}).get("$706")
                if o is not None:
                    start = int(r["$143"])
                    found[text[start : start + int(r["$144"])]] = str(o)
    assert found == {"ABC": "$779", "XYZ": "$778"}


def test_a_japanese_rtl_book_is_vertical_whatever_its_css(books):
    # QA's P746 m6: every file horizontal-tb; m2/m4: a file that sets none.
    assert str(_doc(books["m6"])["$560"]) == "$559"
    assert _sections(books["m6"]) == ["$557", "$557"]
    assert str(_doc(books["unset"])["$560"]) == "$559"
    assert _sections(books["unset"]) == ["$557", None]


def test_a_horizontal_file_out_of_the_toc_gets_its_own_section(books):
    # QA's P746 t1: Previewer gives it a $557 section between the two.
    assert _sections(books["t1"]) == [None, "$557", None]
    # Still two TOC entries, the two chapters: the split part is none.
    (nav,) = by_type(books["t1"], "$389")
    toc = str(val(nav)).split("$212", 1)[1].split("$391::", 1)[0]
    assert re.findall(r"\$244: '([^']*)'", toc) == ["第一章", "第二章"]


#: A 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def test_a_fixed_layout_japanese_rtl_book_is_not_made_vertical(calibre):  # noqa: F811
    """QA of #746: the #711 manga test book (ja, rtl, pre-paginated) passed
    on the Paperwhite as written horizontal with $192 $375. Previewer makes
    a MOBI of such a book, so there is no evidence for vertical: it is left
    as main writes it."""
    tmp, _env, _version = calibre
    epub, kfx = tmp / "v247fxl.epub", tmp / "v247fxl.kfx"
    pages = [f"p{n}.xhtml" for n in range(1, 4)]
    items = "".join(
        f'<item id="x{n}" href="{h}" media-type="application/xhtml+xml"/>'
        f'<item id="i{n}" href="p{n + 1}.png" media-type="image/png"/>'
        for n, h in enumerate(pages)
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="u">urn:kfxgen:v247fxl</dc:identifier><dc:title>漫画</dc:title>'
        "<dc:creator>A</dc:creator><dc:language>ja</dc:language>"
        '<meta property="rendition:layout">pre-paginated</meta></metadata>'
        f'<manifest>{items}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx" page-progression-direction="rtl">'
        + "".join(f'<itemref idref="x{n}"/>' for n in range(len(pages)))
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
        z.writestr("toc.ncx", _ncx([("1", "p1.xhtml", 1)]))
        for n, h in enumerate(pages, 1):
            z.writestr(f"p{n}.png", PNG)
            z.writestr(
                h,
                '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title>'
                '<meta name="viewport" content="width=1200, height=1600"/></head>'
                f'<body><img src="p{n}.png" alt="page {n}"/></body></html>',
            )
    _ebook_convert(calibre, epub, kfx, True)
    doc = _doc(load_fragments(kfx))
    assert (str(doc["$560"]), str(doc["$192"])) == ("$557", "$375")
