"""Files calibre adds to the spine go in TOC order (#253).

When an EPUB's spine leaves out files its pages link to, calibre appends
them to the spine in a different order on every conversion, so the
chapters came out in a different order each time. kfxgen reads the book's
own OPF spine, which calibre leaves unpacked in its container folder, and
puts the files calibre added in TOC order, then by name."""

from types import SimpleNamespace

from kfxgen import converter as _conv


def _book(tmp_path, listed, spine, prefix=""):
    """`listed`: (href, linear) in the book's own OPF spine, each written
    with `prefix` in the OPF. `spine`: the hrefs in calibre's spine, in its
    order; calibre marks the files it adds non-linear."""
    linear = dict(listed)
    hrefs = sorted(set(linear) | set(spine))
    items = "".join(
        f'<item id="i{n}" href="{prefix}{h}" media-type="application/xhtml+xml"/>'
        for n, h in enumerate(hrefs)
    )
    refs = "".join(
        f'<itemref idref="i{hrefs.index(h)}"' + ("" if lin else ' linear="no"') + "/>"
        for h, lin in listed
    )
    (tmp_path / "OEBPS").mkdir()
    (tmp_path / "OEBPS" / "content.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        f'version="2.0"><manifest>{items}</manifest><spine>{refs}</spine></package>'
    )
    container = SimpleNamespace(rootdir=str(tmp_path), opfname="OEBPS/content.opf")
    return SimpleNamespace(
        container=container,
        spine=[SimpleNamespace(href=h, linear=linear.get(h, False)) for h in spine],
    )


def _toc(*hrefs):
    return [{"title": h, "href": h} for h in hrefs]


def _order(book, toc):
    return [i.href for i in _conv._spine_in_order(book, toc)]


def test_files_calibre_added_go_in_toc_order(tmp_path):
    book = _book(
        tmp_path,
        [("ch1.xhtml", True), ("contents.xhtml", True)],
        ["ch1.xhtml", "contents.xhtml", "ch4.xhtml", "ch2.xhtml", "ch3.xhtml"],
    )
    toc = _toc("contents.xhtml", "ch1.xhtml", "ch2.xhtml", "ch3.xhtml#x", "ch4.xhtml")
    assert _order(book, toc) == [
        "ch1.xhtml",
        "contents.xhtml",
        "ch2.xhtml",
        "ch3.xhtml",
        "ch4.xhtml",
    ]


def test_added_files_outside_the_toc_follow_by_name(tmp_path):
    book = _book(
        tmp_path,
        [("ch1.xhtml", True)],
        ["ch1.xhtml", "z.xhtml", "ch2.xhtml", "a.xhtml"],
    )
    assert _order(book, _toc("ch1.xhtml", "ch2.xhtml")) == [
        "ch1.xhtml",
        "ch2.xhtml",
        "a.xhtml",
        "z.xhtml",
    ]


def test_the_books_own_non_linear_files_keep_their_order(tmp_path):
    own = [("ch1.xhtml", True), ("notes.xhtml", False), ("about.xhtml", False)]
    book = _book(tmp_path, own, ["ch1.xhtml", "notes.xhtml", "about.xhtml"])
    toc = _toc("ch1.xhtml", "about.xhtml", "notes.xhtml")
    assert _order(book, toc) == ["ch1.xhtml", "notes.xhtml", "about.xhtml"]


def test_an_opf_writing_paths_from_its_folder_keeps_the_books_order(tmp_path):
    own = [("b.xhtml", False), ("a.xhtml", False)]
    book = _book(
        tmp_path, own, ["b.xhtml", "a.xhtml", "d.xhtml", "c.xhtml"], "../OEBPS/"
    )
    assert _order(book, _toc("a.xhtml", "b.xhtml")) == [
        "b.xhtml",
        "a.xhtml",
        "c.xhtml",
        "d.xhtml",
    ]


def test_percent_encoded_names_match_the_books_own(tmp_path):
    own = [("ch1.xhtml", True), ("my notes.xhtml", False), ("a.xhtml", False)]
    book = _book(tmp_path, own, ["ch1.xhtml", "my%20notes.xhtml", "a.xhtml"])
    assert _order(book, _toc("ch1.xhtml")) == [
        "ch1.xhtml",
        "my%20notes.xhtml",
        "a.xhtml",
    ]


def test_without_the_books_opf_the_spine_is_unchanged(tmp_path):
    spine = [SimpleNamespace(href=h) for h in ("b.xhtml", "a.xhtml")]
    for container in (None, SimpleNamespace(rootdir=str(tmp_path), opfname="none.opf")):
        book = SimpleNamespace(container=container, spine=spine)
        assert [i.href for i in _conv._spine_in_order(book, _toc("a.xhtml"))] == [
            "b.xhtml",
            "a.xhtml",
        ]


def test_a_linear_file_the_opf_names_differently_stays_in_place(tmp_path):
    # Only calibre's trailing non-linear files can be ones it added.
    book = _book(tmp_path, [("b.xhtml", True)], ["b.xhtml", "renamed.xhtml", "a.xhtml"])
    book.spine[1].linear = True
    assert _order(book, _toc("a.xhtml")) == ["b.xhtml", "renamed.xhtml", "a.xhtml"]


def test_a_file_calibre_added_again_under_other_capitals_is_left_out(tmp_path):
    """A link to "C2.XHTML" on a case-insensitive file system: calibre adds
    the file again under that name, and kfxgen printed its chapter twice;
    Kindle Previewer prints it once (#476)."""
    book = _book(
        tmp_path,
        [("text/c1.xhtml", True), ("text/c2.xhtml", True)],
        ["text/c1.xhtml", "text/c2.xhtml", "text/C2.XHTML", "text/c3.xhtml"],
    )
    assert _order(book, _toc("text/c1.xhtml", "text/c2.xhtml", "text/c3.xhtml")) == [
        "text/c1.xhtml",
        "text/c2.xhtml",
        "text/c3.xhtml",
    ]


def test_files_in_other_folders_with_one_name_are_both_kept(tmp_path):
    book = _book(
        tmp_path,
        [("a/notes.xhtml", True)],
        ["a/notes.xhtml", "b/Notes.xhtml"],
    )
    assert _order(book, _toc("a/notes.xhtml")) == ["a/notes.xhtml", "b/Notes.xhtml"]


def test_two_added_files_differing_in_case_are_one(tmp_path):
    """Neither in the book's spine, linked as "EXTRA.XHTML" and "extra.xhtml":
    calibre adds both, in either order, and only one is kept, the same one
    every time (#506 QA)."""
    orders = []
    for n, spine in enumerate(
        (
            ["c1.xhtml", "extra.xhtml", "EXTRA.XHTML"],
            ["c1.xhtml", "EXTRA.XHTML", "extra.xhtml"],
        )
    ):
        (tmp_path / str(n)).mkdir()
        book = _book(tmp_path / str(n), [("c1.xhtml", True)], spine)
        orders.append(_order(book, _toc("c1.xhtml")))
    assert len(orders[0]) == 2
    assert orders[0] == orders[1]
