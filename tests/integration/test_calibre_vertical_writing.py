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
REST = (
    " .up { text-orientation: upright } .side { text-orientation: sideways }"
    " .tcy { text-combine-upright: all; -webkit-text-combine: horizontal }"
    " .em { text-emphasis-style: filled sesame; -webkit-text-emphasis-style: filled sesame }"
    " .hz { writing-mode: horizontal-tb; -webkit-writing-mode: horizontal-tb }"
    " .bxp { border: 1px solid black; padding: 10px }"
)
CH1 = (
    "<h1>第一章</h1>"
    '<p>V02 数字は<span class="tcy">12</span>月<span class="tcy">31</span>日。</p>'
    '<p>V03 英字を立てて<span class="up">ABC</span>と書く。</p>'
    '<p>V05 横に寝かせて<span class="side">XYZ</span>と書く。</p>'
    '<p>V07 <span class="em">ここに傍点</span>を打つ。</p>'
    '<p class="hz">V09 この段落だけ横書きにする。</p>'
    '<div class="hz"><p>V11 箱の一つ目。</p><p>V12 箱の二つ目。</p></div>'
    '<p>V10 次へ<a href="c2.xhtml#t">行く</a>。</p>'
)
CH2 = (
    '<h1>第二章</h1><p id="t">T1 ここが行き先です。</p>'
    '<p style="margin-left: 10%">P01 左に十パーセント。</p>'
    '<div class="bxp"><p>P02 枠の内側に十ピクセル。</p></div>'
)

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


def _write(path, rtl, css1, css2, middle=None, lang="ja"):
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
        f"<dc:creator>A</dc:creator><dc:language>{lang}</dc:language></metadata>"
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


def _fixed_layout_book(
    cal, name, css="", text="", first="", toc=(("1", "p1.xhtml", 1),)
):
    """A 3-page fixed-layout Japanese book with an rtl spine, the shape of
    the #711 manga test book: each page a viewport and a picture, with
    `css` in a <style>, `text` after the picture, and `first` after it on
    page 1 only; `toc` the NCX entries."""
    tmp, _env, _version = cal
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
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
        f'<dc:identifier id="u">urn:kfxgen:{name}</dc:identifier><dc:title>漫画</dc:title>'
        "<dc:creator>A</dc:creator><dc:language>ja</dc:language>"
        '<meta property="rendition:layout">pre-paginated</meta></metadata>'
        f'<manifest>{items}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx" page-progression-direction="rtl">'
        + "".join(f'<itemref idref="x{n}"/>' for n in range(len(pages)))
        + "</spine></package>"
    )
    style = f"<style>{css}</style>" if css else ""
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
        z.writestr("toc.ncx", _ncx(list(toc)))
        for n, h in enumerate(pages, 1):
            z.writestr(f"p{n}.png", PNG)
            z.writestr(
                h,
                '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title>'
                f'<meta name="viewport" content="width=1200, height=1600"/>{style}</head>'
                f'<body><img src="p{n}.png" alt="page {n}"/>{text}'
                f"{first if n == 1 else ''}</body></html>",
            )
    _ebook_convert(cal, epub, kfx, True)
    return load_fragments(kfx)


def test_a_fixed_layout_japanese_rtl_book_is_not_made_vertical(calibre):  # noqa: F811
    """QA of #746: the #711 manga test book (ja, rtl, pre-paginated) passed
    on the Paperwhite as written horizontal with $192 $375. Previewer makes
    a MOBI of such a book, so there is no evidence for vertical: it is left
    as main writes it."""
    doc = _doc(_fixed_layout_book(calibre, "v247fxl"))
    assert (str(doc["$560"]), str(doc["$192"])) == ("$557", "$375")


def test_a_fixed_layout_books_vertical_pages_stay_horizontal(calibre):  # noqa: F811
    """#752: pages set vertical-rl in a fixed-layout book (QA's C751, the
    #711 manga with `html { writing-mode: vertical-rl }`) were vertical
    sections with the Japanese features. They are written as the manga
    that passed on the Paperwhite: no vertical section, no vertical
    features, and a book's margin-top in its own place."""
    frags = _fixed_layout_book(
        calibre,
        "v752fxl",
        css=f"html {{ {VERT} }} .mt {{ margin-top: 2em }}",
        text='<p class="mt">F01 縦書きの頁。</p>',
    )
    doc = _doc(frags)
    assert (str(doc["$560"]), str(doc["$192"])) == ("$557", "$375")
    assert _sections(frags) == [None, None, None]
    assert not {
        "jp-reflow-language",
        "jpvertical-reflow-language",
        "yj_mixed_writing_mode",
    } & _features(frags)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    tops = set()
    for f in by_type(frags, "$259"):
        for e in val(f)["$146"]:
            r = e.get("$145")
            if r and str(texts[str(r["name"])][r["$403"]]).startswith("F01"):
                top = styles[str(e["$157"])].get("$47")
                tops.add(top and (str(top["$307"]), str(top["$306"])))
    # Read as horizontal text, margin-top 2em is the space above, $47
    # 1.66667lh; read as vertical, it would be the start of the line.
    assert tops == {("1.66667", "$310")}


def test_a_fixed_layout_book_keeping_its_listing_stays_horizontal(calibre):  # noqa: F811
    """QA's C753L on #753: a printed contents listing and no Contents page
    send the book through the listings-kept extraction again, which must
    keep the pages horizontal too."""
    listing = (
        '<ol class="toc"><li><a href="p1.xhtml">一</a></li>'
        '<li><a href="p2.xhtml">二</a></li><li><a href="p3.xhtml">三</a></li></ol>'
        # A second paragraph, so the page isn't rebuilt as the Contents page.
        "<p>L02 二つ目の段落。</p>"
    )
    frags = _fixed_layout_book(
        calibre,
        "v753l",
        css=f"html {{ {VERT} }}",
        text="<p>L01 縦書きの頁。</p>",
        first=listing,
        toc=[("一", "p1.xhtml", 1), ("二", "p2.xhtml", 1), ("三", "p3.xhtml", 1)],
    )
    assert set(_sections(frags)) == {None}


def test_a_fixed_layout_books_horizontal_block_is_no_container(calibre):  # noqa: F811
    """QA's C753 on #753: a horizontal-tb div on a vertical-rl page of a
    fixed-layout book is plain horizontal text, with no $560 container of
    its own (#749's), as the rest of the page is."""
    frags = _fixed_layout_book(
        calibre,
        "v753h",
        css=f"html {{ {VERT} }} .hz {{ writing-mode: horizontal-tb }}",
        text='<div class="hz"><p>H01 Horizontal line.</p></div>',
    )
    assert not [
        f.fid for f in by_type(frags, "$157") if "$560" in {str(k) for k in val(f)}
    ]


def _entry_and_parent(frags, prefix):
    """The style values of the storyline entry whose text starts `prefix`,
    and of the container around it ({} at the top)."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def lengths(e):
        style = styles.get(str(e.get("$157")), {}) if e is not None else {}
        return {
            str(k): str(v["$307"])
            for k, v in style.items()
            if str(k) in ("$47", "$48", "$49", "$50", "$52", "$53", "$54", "$55")
        }

    def walk(entries, parent):
        for e in entries:
            if not hasattr(e, "get"):
                continue
            r = e.get("$145")
            if r and str(texts[str(r["name"])][r["$403"]]).startswith(prefix):
                return lengths(e), lengths(parent)
            found = walk(e.get("$146") or [], e)
            if found:
                return found
        return None

    for f in by_type(frags, "$259"):
        found = walk(val(f)["$146"], None)
        if found:
            return found
    raise AssertionError(prefix)


@pytest.mark.parametrize(
    "name, lh, pad",
    [
        # P247j V04 and V09: a vertical book measures against 480pt.
        ("a", "3.33333", "0.938"),
        # An ltr spine isn't a vertical book (#746): 384pt, as main.
        ("c", "2.66667", "1.172"),
    ],
)
def test_percentages_are_measured_against_previewers_page(books, name, lh, pad):
    """margin-left 10% in vertical text is the space before the line, in lh
    from 10% of the page; 10px of box padding at the line's ends is a % of
    it (Previewer 4.0.3, P247j). The page is 480pt in a vertical book."""
    p01, _ = _entry_and_parent(books[name], "P01")
    assert p01["$48"] == lh
    _, box = _entry_and_parent(books[name], "P02")
    assert (box["$52"], box["$54"]) == (pad, pad)


def test_an_rtl_book_that_isnt_japanese_keeps_the_384pt_page(calibre):  # noqa: F811
    """QA on #754: an Arabic book turns its pages right to left but isn't
    vertical (#746), so its percentages stay on 384pt: 10px of box padding
    at the sides is 1.172%, not 0.938%."""
    tmp, _env, _version = calibre
    epub, kfx = tmp / "v754ar.epub", tmp / "v754ar.kfx"
    _write(epub, True, REST, REST, lang="ar")
    _ebook_convert(calibre, epub, kfx, True)
    _, box = _entry_and_parent(load_fragments(kfx), "P02")
    assert (box["$53"], box["$55"]) == ("1.172", "1.172")


def test_a_fixed_layout_japanese_book_keeps_the_384pt_page(calibre):  # noqa: F811
    """QA on #754: a fixed-layout book isn't vertical (#746, #753), so its
    percentages stay on 384pt too, its pages set vertical-rl or not."""
    frags = _fixed_layout_book(
        calibre,
        "v754fxl",
        css=f"html {{ {VERT} }} .bxp {{ border: 1px solid black; padding: 10px }}",
        text='<div class="bxp"><p>F01 枠の内側。</p></div>',
    )
    _, box = _entry_and_parent(frags, "F01")
    assert (box["$53"], box["$55"]) == ("1.172", "1.172")


def test_tcy_is_an_inline_container_in_the_words(books):
    # P247 V02: two containers, "12" and "31", in Previewer's style.
    frags = books["a"]
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    strings = {
        str(f.fid): [str(t) for t in val(f)["$146"]] for f in by_type(frags, "$145")
    }
    boxes = [
        w
        for st in by_type(frags, "$259")
        for e in val(st)["$146"]
        if hasattr(e, "get") and e.get("$146")
        for w in e["$146"]
        if not isinstance(w, str) and str(w.get("$601")) == "$283"
    ]
    assert [strings[str(b["$145"]["name"])][b["$145"]["$403"]] for b in boxes] == [
        "12",
        "31",
    ]
    assert {
        k: str(v) for k, v in styles[str(boxes[0]["$157"])].items() if k != "$173"
    } == {"$560": "$557", "$707": "$573", "$708": "$383"}


def test_emphasis_marks_are_a_runs_717(books):
    # P247 V07: filled sesame, $734, on ここに傍点.
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
                mark = styles.get(str(r.get("$157")), {}).get("$717")
                if mark is not None:
                    a = int(r["$143"])
                    found[text[a : a + int(r["$144"])]] = str(mark)
    assert found == {"ここに傍点": "$734"}


def _containers(frags):
    """{first words of each text entry: the style of the container it is in,
    or None at the top of its storyline}."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}

    def walk(entries, around):
        for e in entries:
            if not hasattr(e, "get"):
                continue
            ref = e.get("$145")
            if ref:
                text = str(texts[str(ref["name"])][ref["$403"]])
                out[text.split()[0]] = around
            kids = e.get("$146")
            if kids:
                own = styles.get(str(e.get("$157")), {})
                walk(kids, {k: str(v) for k, v in own.items() if k != "$173"})

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return out


def test_a_horizontal_block_in_vertical_text_is_a_horizontal_container(books):
    # P247h H01 and H03/H04: a horizontal <p>, and a horizontal <div> of two
    # paragraphs, each in one container, horizontal-tb at the page's width.
    where = _containers(books["a"])
    for words in ("V09", "V11"):
        st = where[words]
        assert st is not None, words
        assert (st["$560"], st["$478"], st["$784"]) == ("$557", "True", "True")
        assert "100" in st["$56"] and "$314" in st["$56"]
    assert where["V11"] is where["V12"] or where["V11"] == where["V12"]
    assert where["V07"] is None and where["V10"] is None


def test_a_horizontal_block_in_a_horizontal_file_is_no_container(books):
    where = _containers(books["m6"])
    assert where["V09"] is None and where["V11"] is None
