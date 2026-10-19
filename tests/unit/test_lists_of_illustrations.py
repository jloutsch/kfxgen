"""A book's list of illustrations or tables is printed (#392).

kfxgen dropped every `<nav>` whose `epub:type` is `lot`, `loi` or `lov`
(#60), with page lists and landmarks. Kindle Previewer 4 prints a visible
one: a heading and a linked paragraph per entry (decoded probe on #392). A
list of illustrations is the book's own content, a page of links to its
figures that no other part of a KFX gives; 43 of 1,373 library EPUBs have
a visible list of tables or illustrations. Landmarks (about 3 entries
naming places the reader's own menu offers) and page lists (hundreds of
page numbers) are still dropped, and a hidden list is too.

A list after a contents listing that is rebuilt as kfxgen's Contents page
is kept, as a dedication is (#393).
"""

import pytest

from kfxgen.converter import extract_chapters_from_oeb
from tests.unit.test_converter import _OEBBook, _RawSpineItem, _silent_log, _TOCNode
from tests.unit.test_floats import XHTML, _resolver

META = {"title": "The Real Title", "author": "Jane Author"}


def _list(kind, tag, attrs=""):
    items = "".join(
        f'<li><a href="c.xhtml#{kind}{i}">{tag} item {i}</a></li>' for i in (1, 2)
    )
    return (
        f'<nav epub:type="{kind}"{attrs}><h2>{tag} heading</h2><ol>{items}</ol></nav>'
    )


def _linked(block):
    return any(
        isinstance(f, tuple) and f[0] == "link"
        for _s, _l, flags in block.get("spans") or ()
        for f in flags
    )


def _printed(body):
    """(text, linked) of each paragraph."""
    from lxml import etree

    from kfxgen import converter

    xhtml = (
        f'<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<body>{body}</body></html>"
    )
    blocks = converter.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=_resolver({}),
        base_href="c.xhtml",
    )
    return [(b["text"], _linked(b)) for b in blocks]


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["loi", "lot", "lov"])
def test_a_list_of_illustrations_or_tables_is_printed_with_its_links(kind):
    assert _printed("<p>Prose.</p>" + _list(kind, "L")) == [
        ("Prose.", False),
        ("L heading", False),
        ("1. L item 1", True),
        ("2. L item 2", True),
    ]


@pytest.mark.unit
def test_a_nested_list_is_printed():
    texts = [t for t, _ in _printed("<section>" + _list("lot", "S") + "</section>")]
    assert texts == ["S heading", "1. S item 1", "2. S item 2"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "markup",
    [
        _list("loi", "H", ' hidden=""'),
        _list("landmarks", "M"),
        _list("page-list", "G"),
    ],
    ids=["hidden", "landmarks", "page-list"],
)
def test_a_hidden_list_landmarks_and_a_page_list_are_still_dropped(markup):
    assert _printed("<p>Prose.</p>" + markup) == [("Prose.", False)]


def _chapters(spine, toc):
    oeb = _OEBBook(
        [_RawSpineItem(h, m) for h, m in spine], [_TOCNode(t, h) for t, h in toc]
    )
    return extract_chapters_from_oeb(oeb, _silent_log(), metadata=META)


BODY = [("c1.xhtml", "<p>First chapter prose.</p>")]


@pytest.mark.unit
def test_a_list_on_a_page_of_its_own_is_one_paragraph_per_entry():
    """On main the page printed "PI headingPI item 1PI item 2", unlinked."""
    chapters = _chapters(
        [("loi.xhtml", _list("loi", "P"))] + BODY,
        [("Illustrations", "loi.xhtml"), ("Chapter I", "c1.xhtml")],
    )
    loi = chapters[0]
    assert [(b["text"], _linked(b)) for b in loi["blocks"]] == [
        ("P heading", False),
        ("1. P item 1", True),
        ("2. P item 2", True),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["loi", "lot", "lov"])
def test_a_list_after_a_rebuilt_contents_listing_is_kept(kind):
    front = (
        '<nav epub:type="toc"><h1>Contents</h1><ol>'
        '<li><a href="c1.xhtml">Chapter I</a></li></ol></nav>' + _list(kind, "N")
    )
    chapters = _chapters(
        [("front.xhtml", front)] + BODY,
        [("Contents", "front.xhtml"), ("Chapter I", "c1.xhtml")],
    )
    (contents,) = [ch for ch in chapters if ch.get("toc_links")]
    after = chapters[chapters.index(contents) + 1]
    assert after.get("_omit_from_toc")
    assert [b["text"] for b in after["blocks"]] == [
        "N heading",
        "1. N item 1",
        "2. N item 2",
    ]
