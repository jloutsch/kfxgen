"""An NCX entry with an empty or blank label is kept (#459), as Kindle
Previewer 4.0.3 keeps it, its label as written. calibre's TOC leaves it out,
so it is read back from the book's own NCX and put where the NCX has it.
Previewer drops one with no label at all at the top level, and so does
kfxgen."""

from types import SimpleNamespace

import pytest

from kfxgen import converter as _conv
from tests._helpers import NullLog

pytestmark = pytest.mark.unit


class _Node(list):
    def __init__(self, title, href, kids=()):
        super().__init__(kids)
        self.title, self.href = title, href


def _book(tmp_path, points, calibre_toc, ncx_dir="OEBPS"):
    """An unpacked book: an OPF in OEBPS/ naming its NCX, and calibre's TOC."""
    (tmp_path / "OEBPS").mkdir(exist_ok=True)
    ncx_href = f"{ncx_dir}/toc.ncx" if ncx_dir != "OEBPS" else "toc.ncx"
    if ncx_dir != "OEBPS":
        (tmp_path / "OEBPS" / ncx_dir).mkdir(exist_ok=True)
    (tmp_path / "OEBPS" / "content.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0"><manifest>'
        f'<item id="ncx" href="{ncx_href}" media-type="application/x-dtbncx+xml"/>'
        '</manifest><spine toc="ncx"/></package>'
    )

    def nav(ps):
        return "".join(
            f"<navPoint><navLabel><text>{t}</text></navLabel>"
            f'<content src="{h}"/>{nav(kids)}</navPoint>'
            for t, h, kids in ps
        )

    (tmp_path / "OEBPS" / ncx_href).write_text(
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
        f"<navMap>{nav(points)}</navMap></ncx>"
    )
    return SimpleNamespace(
        toc=calibre_toc,
        container=SimpleNamespace(rootdir=str(tmp_path), opfname="OEBPS/content.opf"),
    )


def _entries(book):
    return [
        (e["title"], e["href"], e["level"])
        for e in _conv._extract_toc_with_hrefs(book, NullLog())
    ]


def test_an_empty_label_is_put_back_where_the_ncx_has_it(tmp_path):
    book = _book(
        tmp_path,
        [("One", "c1.xhtml", [("", "c1.xhtml#mid", [])]), ("Two", "c2.xhtml", [])],
        [_Node("One", "c1.xhtml"), _Node("Two", "c2.xhtml")],
    )
    assert _entries(book) == [
        ("One", "c1.xhtml", 0),
        ("", "c1.xhtml#mid", 1),
        ("Two", "c2.xhtml", 0),
    ]


def test_an_ncx_in_another_folder_gives_hrefs_from_the_opfs(tmp_path):
    book = _book(
        tmp_path,
        [("One", "../c1.xhtml", [("", "../c1.xhtml#mid", [])])],
        [_Node("One", "c1.xhtml")],
        ncx_dir="nav",
    )
    assert _entries(book) == [("One", "c1.xhtml", 0), ("", "c1.xhtml#mid", 1)]


def test_a_toc_that_doesnt_match_the_ncx_is_left_as_calibre_gives_it(tmp_path):
    """calibre read another TOC (an EPUB 3 nav document): nothing is put in."""
    book = _book(
        tmp_path,
        [("Other", "c1.xhtml", [("", "c1.xhtml#mid", [])])],
        [_Node("One", "c1.xhtml")],
    )
    assert _entries(book) == [("One", "c1.xhtml", 0)]


def test_with_no_empty_label_the_toc_is_calibres(tmp_path):
    book = _book(
        tmp_path,
        [("One", "c1.xhtml", [])],
        [_Node("One", "c1.xhtml")],
    )
    assert _entries(book) == [("One", "c1.xhtml", 0)]


def test_an_opf_at_the_books_root(tmp_path):
    (tmp_path / "content.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0"><manifest><item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest></package>'
    )
    (tmp_path / "toc.ncx").write_text(
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
        "<navMap><navPoint><navLabel><text>One</text></navLabel>"
        '<content src="c1.xhtml"/><navPoint><navLabel><text></text></navLabel>'
        '<content src="c1.xhtml#mid"/></navPoint></navPoint></navMap></ncx>'
    )
    book = SimpleNamespace(
        toc=[_Node("One", "c1.xhtml")],
        container=SimpleNamespace(rootdir=str(tmp_path), opfname="content.opf"),
    )
    assert _entries(book) == [("One", "c1.xhtml", 0), ("", "c1.xhtml#mid", 1)]


@pytest.mark.parametrize(
    ("label", "kept"),
    [
        ("", []),
        (" ", [(" ", "c1.xhtml#mid", 0)]),
        ("\u00a0", [("\u00a0", "c1.xhtml#mid", 0)]),
    ],
    ids=["empty", "space", "nbsp"],
)
def test_at_the_top_level_only_a_label_with_no_text_at_all_is_dropped(
    tmp_path, label, kept
):
    """As Kindle Previewer 4.0.3 has it: a top-level entry labelled '' is
    left out (2 probes), one labelled ' ' or an NBSP is kept as written."""
    book = _book(
        tmp_path,
        [("One", "c1.xhtml", []), (label, "c1.xhtml#mid", [])],
        [_Node("One", "c1.xhtml")],
    )
    assert _entries(book) == [("One", "c1.xhtml", 0), *kept]


def test_a_pretty_printed_ncxs_empty_label_is_empty(tmp_path):
    """The spaces and line breaks around `<text>` are no part of the label
    (QA on #459: 2 of the library's 4 such entries are written so)."""
    (tmp_path / "content.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0"><manifest><item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest></package>'
    )
    (tmp_path / "toc.ncx").write_text(
        '<?xml version="1.0"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">\n'
        "  <navMap>\n    <navPoint>\n      <navLabel>\n        <text>One</text>\n"
        '      </navLabel>\n      <content src="c1.xhtml"/>\n      <navPoint>\n'
        "        <navLabel>\n          <text></text>\n        </navLabel>\n"
        '        <content src="c1.xhtml#mid"/>\n      </navPoint>\n    </navPoint>\n'
        "    <navPoint>\n      <navLabel>\n        <text></text>\n      </navLabel>\n"
        '      <content src="c2.xhtml"/>\n    </navPoint>\n  </navMap>\n</ncx>\n'
    )
    book = SimpleNamespace(
        toc=[_Node("One", "c1.xhtml")],
        container=SimpleNamespace(rootdir=str(tmp_path), opfname="content.opf"),
    )
    assert _entries(book) == [("One", "c1.xhtml", 0), ("", "c1.xhtml#mid", 1)]
