"""The book's page list (#578), as Kindle Previewer 4.0.3 writes it: an
entry per print page break from an EPUB 3 nav `page-list`, else from the
NCX's `pageList`, each landing on its break. The end-to-end cases, checked
against Previewer's file, are in tests/integration/test_calibre_page_list.py."""

from types import SimpleNamespace

import pytest

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val

pytestmark = pytest.mark.unit


def _book(tmp_path, nav=None, ncx=None, ncx_href="toc.ncx"):
    (tmp_path / "OEBPS").mkdir(exist_ok=True)
    items = ""
    if nav is not None:
        items += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        (tmp_path / "OEBPS" / "nav.xhtml").write_text(
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:epub="http://www.idpf.org/2007/ops"><body>{nav}</body></html>'
        )
    if ncx is not None:
        items += (
            f'<item id="ncx" href="{ncx_href}" media-type="application/x-dtbncx+xml"/>'
        )
        path = tmp_path / "OEBPS" / ncx_href
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
            f"<navMap/><pageList>{ncx}</pageList></ncx>"
        )
    (tmp_path / "OEBPS" / "content.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        f'version="3.0"><manifest>{items}</manifest><spine/></package>'
    )
    return SimpleNamespace(
        container=SimpleNamespace(rootdir=str(tmp_path), opfname="OEBPS/content.opf")
    )


def _nav(pages):
    return (
        '<nav epub:type="page-list"><ol>'
        + "".join(f'<li><a href="{h}">{label}</a></li>' for label, h in pages)
        + "</ol></nav>"
    )


def _ncx(pages):
    return "".join(
        f'<pageTarget type="normal"><navLabel><text>{label}</text></navLabel>'
        f'<content src="{h}"/></pageTarget>'
        for label, h in pages
    )


def test_a_nav_page_list(tmp_path):
    book = _book(tmp_path, nav=_nav([("1", "c1.xhtml#p1"), ("ii", "c2.xhtml#p2")]))
    assert _conv._book_page_list(book) == [("1", "c1.xhtml#p1"), ("ii", "c2.xhtml#p2")]


def test_an_ncx_page_list_in_its_own_folder(tmp_path):
    book = _book(
        tmp_path, ncx=_ncx([("7", "../Text/c1.xhtml#p7")]), ncx_href="Nav/toc.ncx"
    )
    assert _conv._book_page_list(book) == [("7", "Text/c1.xhtml#p7")]


def test_the_nav_wins_over_the_ncx(tmp_path):
    book = _book(
        tmp_path, nav=_nav([("1", "c1.xhtml#a")]), ncx=_ncx([("9", "c1.xhtml#z")])
    )
    assert _conv._book_page_list(book) == [("1", "c1.xhtml#a")]


def test_a_nav_with_no_page_list_falls_back_to_the_ncx(tmp_path):
    book = _book(
        tmp_path,
        nav='<nav epub:type="toc"><ol><li><a href="c1.xhtml">One</a></li></ol></nav>',
        ncx=_ncx([("9", "c1.xhtml#z")]),
    )
    assert _conv._book_page_list(book) == [("9", "c1.xhtml#z")]


def test_a_page_with_no_label_or_out_of_the_book_is_dropped(tmp_path):
    book = _book(tmp_path, nav=_nav([("", "c1.xhtml#a"), ("2", "../../../x.xhtml#b")]))
    assert _conv._book_page_list(book) == []


def test_the_generator_writes_each_page_where_its_break_is(tmp_path):
    out = tmp_path / "p.kfx"
    block = {
        "text": "Before after.",
        "spans": [],
        "anchor_keys": ["c1.xhtml#p1"],
        "anchor_offsets": {"c1.xhtml#p1": 7},
    }
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": block["text"], "blocks": [block]}],
        output_path=str(out),
        page_list=[("1", "c1.xhtml#p1"), ("2", "gone.xhtml#x")],
    )
    (nav,) = by_type(load_fragments(out), "$389")
    (plist,) = [
        getattr(c, "value", c)
        for c in val(nav)[0]["$392"]
        if str(getattr(c, "value", c)["$235"]) == "$237"
    ]
    (unit,) = [getattr(u, "value", u) for u in plist["$247"]]
    assert str(unit["$241"]["$244"]) == "1"
    assert int(unit["$246"]["$143"]) == 7


def test_no_page_list_no_container(tmp_path):
    out = tmp_path / "n.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": "x", "blocks": [{"text": "x", "spans": []}]}],
        output_path=str(out),
    )
    (nav,) = by_type(load_fragments(out), "$389")
    assert "$237" not in [
        str(getattr(c, "value", c)["$235"]) for c in val(nav)[0]["$392"]
    ]


@pytest.mark.parametrize(
    ("labels", "kept"),
    [
        (["1", "ii", "IV", "Xii"], ["1", "ii", "IV", "Xii"]),
        (
            ["[Pg 1]", "{ii}", "[3]", "(iv)", "Page 5", "p. 6", "pg7"],
            ["1", "ii", "3", "iv", "5", "6", "7"],
        ),
        (["1", "A8"], []),
        (["1", "8-9"], []),
        (["1", "[Inhoud]"], []),
        (["Cover Page", "1"], []),
        (["1", "[]"], []),
    ],
    ids=[
        "digits-and-roman",
        "cleaned",
        "letter-digit",
        "range",
        "word-in-brackets",
        "words",
        "empty-brackets",
    ],
)
def test_labels_are_cleaned_then_previewers_rule_applies(tmp_path, labels, kept):
    """Kindle Previewer 4.0.3 writes no page list when any label is not all
    digits or all roman-numeral letters (QA on #612). kfxgen first takes off
    brackets and a "Page"/"Pg"/"p." before the number (#612 review)."""
    pages = [(label, f"c1.xhtml#p{n}") for n, label in enumerate(labels)]
    got = _conv._book_page_list(_book(tmp_path, nav=_nav(pages)))
    assert [label for label, _key in got] == kept
    if kept:
        assert [key for _label, key in got] == [key for _l, key in pages]


def test_spaces_round_a_label_are_trimmed(tmp_path):
    book = _book(tmp_path, ncx=_ncx([("\n   8\n  ", "c1.xhtml#p8")]))
    assert _conv._book_page_list(book) == [("8", "c1.xhtml#p8")]
