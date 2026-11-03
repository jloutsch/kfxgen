"""Right-to-left books: page direction, writing direction and language (#246).

What Kindle Previewer 4.0.3 writes, read from its own output in the #246
probes (issue comments 6616979445 and 6617544327, and QA's Q246 probe):

- `$538` `$192`: `$375` when the spine says page-progression-direction="rtl",
  else `$376`. The book's language does not set it.
- `$682` writing direction (`$375` rtl, `$376` ltr) and `$674` unicode-bidi
  (`$676` isolate, the default for a `dir` attribute) from `dir`, inherited
  from any ancestor; `$10` from `lang` or `xml:lang`, lower-cased.
- The keys go on the outermost entry that holds them: a table carries them,
  and a cell repeats them only when its own differ.
- `<lang>-reflow-language` in `$585` for a book in ar, fa or he.

These run through the test shim, which reads no CSS: CSS `direction`,
`unicode-bidi`, boxes and `text-align: start` are checked through real calibre
in tests/integration/test_calibre_direction.py.
"""

import re
import zipfile
from unittest.mock import MagicMock

import pytest

from kfxgen import converter as _conv
from tests._kfx_introspect import by_type, load_fragments, val
from tests.fixtures.oeb_shim import EpubAsOeb

AR = "مرحبا بالعالم"
LAB = re.compile(r"\b[A-Z]\d\d\b")


class NullLog:
    def __getattr__(self, name):
        return lambda *a, **k: None


def _epub(path, body, html_attrs="", ppd=None, language="ar"):
    page = (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{html_attrs}>'
        f"<head><title>t</title></head><body>{body}</body></html>"
    )
    spine_attr = f' page-progression-direction="{ppd}"' if ppd else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="u">dir-test</dc:identifier><dc:title>Direction</dc:title>'
        f"<dc:creator>A</dc:creator><dc:language>{language}</dc:language></metadata>"
        '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        f'<spine toc="ncx"{spine_attr}><itemref idref="c1"/></spine></package>'
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        "<head/><docTitle><text>t</text></docTitle><navMap>"
        '<navPoint id="n1" playOrder="1"><navLabel><text>One</text></navLabel>'
        '<content src="c1.xhtml"/></navPoint></navMap></ncx>'
    )
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
        z.writestr("toc.ncx", ncx)
        z.writestr("c1.xhtml", page)
    return path


def _convert(tmp_path, body, **kw):
    epub = _epub(tmp_path / "dir.epub", body, **kw)
    out = tmp_path / "dir.kfx"
    _conv.convert_oeb_to_kfx(
        EpubAsOeb(str(epub)),
        str(out),
        opts=MagicMock(kfxgen_disable_native_tables=False),
        log=NullLog(),
    )
    return load_fragments(out)


def _keys(style):
    return {k: str(style[k]) for k in ("$10", "$682", "$674") if k in style}


def _entries(frags):
    """{label: (own keys, [(container type, its keys), ...] outermost first)}
    for each text entry whose text starts with a label like A01."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}

    def style_of(e):
        s = dict(styles.get(str(e.get("$157")), {}))
        s.update(e)
        return s

    def visit(nodes, above):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            s = style_of(e)
            ref = e.get("$145")
            if hasattr(ref, "get"):
                text = str(texts[str(ref["name"])][int(ref["$403"])])
                m = LAB.search(text)
                if m:
                    found[m.group(0)] = (_keys(s), above)
            visit(e.get("$146") or (), [*above, (str(s.get("$159")), _keys(s))])

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"], [])
    return found


def _page_direction(frags):
    return str(val(by_type(frags, "$538")[0])["$192"])


def _features(frags):
    return [
        str(getattr(x, "value", x)["$492"])
        for x in val(by_type(frags, "$585")[0])["$590"]
    ]


RTL = {"$10": "ar", "$682": "$375", "$674": "$676"}


@pytest.mark.unit
def test_an_rtl_spine_makes_the_book_right_to_left(tmp_path):
    frags = _convert(tmp_path, "<p>A01 words</p>", ppd="rtl")
    assert _page_direction(frags) == "$375"


@pytest.mark.unit
@pytest.mark.parametrize("ppd", [None, "ltr", "default"])
def test_any_other_spine_stays_left_to_right(tmp_path, ppd):
    frags = _convert(tmp_path, "<p>A01 words</p>", ppd=ppd)
    assert _page_direction(frags) == "$376"


@pytest.mark.unit
def test_the_language_alone_does_not_turn_the_pages(tmp_path):
    # Probe 1's "dir" variant: Arabic, <html dir="rtl">, no spine setting.
    frags = _convert(
        tmp_path, f"<p>A01 {AR}</p>", html_attrs=' dir="rtl" lang="ar"', language="ar"
    )
    assert _page_direction(frags) == "$376"


@pytest.mark.unit
def test_html_dir_and_lang_reach_every_paragraph(tmp_path):
    found = _entries(
        _convert(
            tmp_path,
            f"<h2>A00 {AR}</h2><p>A01 {AR}</p><p>A02 {AR}</p>",
            html_attrs=' dir="rtl" lang="ar"',
        )
    )
    for lab in ("A00", "A01", "A02"):
        assert found[lab][0] == RTL, lab


@pytest.mark.unit
@pytest.mark.parametrize("wrapper", ["div", "section", "blockquote"])
def test_dir_on_a_wrapper_reaches_the_paragraphs_inside(tmp_path, wrapper):
    # QA's Q01-Q13; <body dir> has its own test below.
    body = f'<{wrapper} dir="rtl" lang="ar"><p>A01 {AR}</p></{wrapper}>'
    found = _entries(_convert(tmp_path, body, html_attrs=""))
    assert found["A01"][0] == RTL


@pytest.mark.unit
def test_dir_on_body_reaches_the_paragraphs(tmp_path):
    epub = _epub(tmp_path / "b.epub", "", html_attrs="")
    # Rewrite the page with <body dir lang>.
    with zipfile.ZipFile(epub) as z:
        data = {n: z.read(n) for n in z.namelist()}
    data["c1.xhtml"] = data["c1.xhtml"].replace(
        b"<body></body>", f'<body dir="rtl" lang="ar"><p>A01 {AR}</p></body>'.encode()
    )
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("mimetype", data.pop("mimetype"), compress_type=zipfile.ZIP_STORED)
        for n, b in data.items():
            z.writestr(n, b)
    out = tmp_path / "b.kfx"
    _conv.convert_oeb_to_kfx(
        EpubAsOeb(str(epub)),
        str(out),
        opts=MagicMock(kfxgen_disable_native_tables=False),
        log=NullLog(),
    )
    assert _entries(load_fragments(out))["A01"][0] == RTL


@pytest.mark.unit
def test_a_paragraphs_own_dir_beats_its_pages(tmp_path):
    found = _entries(
        _convert(
            tmp_path,
            '<p dir="ltr">A02 English words</p>',
            html_attrs=' dir="rtl" lang="ar"',
        )
    )
    assert found["A02"][0] == {"$10": "ar", "$682": "$376", "$674": "$676"}


@pytest.mark.unit
def test_dir_auto_is_left_to_right(tmp_path):
    # Previewer 4.0.3 does not resolve auto from the text (probe 1, B05).
    found = _entries(
        _convert(tmp_path, f'<p dir="auto">A01 {AR}</p>', html_attrs=' lang="en"')
    )
    assert found["A01"][0] == {"$10": "en", "$682": "$376", "$674": "$676"}


@pytest.mark.unit
@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_book_with_no_dir_or_lang_gets_none_of_the_keys(tmp_path, language):
    # dc:language alone gives no $10: Previewer 4.0.3 writes none for a page
    # with no lang anywhere, in an ar book as in an en one (probe N01).
    found = _entries(_convert(tmp_path, "<p>A01 words</p>", language=language))
    assert found["A01"][0] == {}


@pytest.mark.unit
def test_lang_without_dir_gives_the_language_only(tmp_path):
    found = _entries(
        _convert(tmp_path, "<p>B01 words</p>", html_attrs=' lang="en"', language="en")
    )
    assert found["B01"][0] == {"$10": "en"}


@pytest.mark.unit
def test_lang_is_lower_cased_and_xml_lang_counts(tmp_path):
    found = _entries(
        _convert(
            tmp_path,
            f'<p lang="ar-EG">A01 {AR}</p><p xml:lang="fa">A02 {AR}</p>',
            html_attrs="",
        )
    )
    assert found["A01"][0] == {"$10": "ar-eg"}
    assert found["A02"][0] == {"$10": "fa"}


@pytest.mark.unit
def test_a_list_item_carries_the_keys(tmp_path):
    # kfxgen writes a list item as a paragraph, with no list container.
    found = _entries(
        _convert(
            tmp_path,
            f"<ul><li>A13 {AR}</li></ul><ol><li>A15 {AR}</li></ol>",
            html_attrs=' dir="rtl" lang="ar"',
        )
    )
    assert found["A13"][0] == RTL
    assert found["A15"][0] == RTL


@pytest.mark.unit
def test_a_table_carries_the_keys_and_its_cells_only_differences(tmp_path):
    body = (
        f"<table><tr><td>A17 {AR}</td>"
        '<td dir="ltr">A18 English</td></tr></table>'
        f"<p>A19 {AR}</p>"
    )
    found = _entries(_convert(tmp_path, body, html_attrs=' dir="rtl" lang="ar"'))
    own17, above17 = found["A17"]
    table = next(k for t, k in above17 if t == "$278")
    assert table == RTL
    # Q20: a cell without its own dir repeats nothing.
    assert own17 == {}
    assert all(k == {} for t, k in above17 if t not in ("$278",))
    # Q21: a cell whose dir differs writes it.
    own18, above18 = found["A18"]
    cell18 = {**own18, **next((k for t, k in above18 if t == "$270"), {})}
    assert cell18 == {"$682": "$376"}
    # After the table, a paragraph has them again.
    assert found["A19"][0] == RTL


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "feature"),
    [
        ("ar", "ar-reflow-language"),
        ("fa", "fa-reflow-language"),
        ("he", "he-reflow-language"),
        ("ar-EG", "ar-reflow-language"),
    ],
)
def test_an_arabic_persian_or_hebrew_book_declares_its_language(
    tmp_path, language, feature
):
    frags = _convert(tmp_path, "<p>A01 words</p>", language=language)
    assert feature in _features(frags)


@pytest.mark.unit
@pytest.mark.parametrize("language", ["en", "ur", "ja"])
def test_other_books_declare_no_reflow_language(tmp_path, language):
    # Previewer 4.0.3: none for en (even with Arabic text); ur gets no KFX.
    frags = _convert(tmp_path, f"<p>A01 {AR}</p>", language=language)
    assert not [f for f in _features(frags) if f.endswith("-reflow-language")]
