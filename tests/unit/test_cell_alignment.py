"""Table cells have no alignment unless the book gives one, as Kindle
Previewer 4.0.3 writes them in a page with a DOCTYPE (#599, probed
2026-10-28); on a Paperwhite an unset cell shows justified, as Previewer's
does. Cells in a right-to-left table are right-aligned: Previewer marks
the table's direction instead, which kfxgen doesn't write."""

import pytest
from lxml import etree

from kfxgen import converter as _conv


def _cells(table_html):
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"{table_html}<p>After.</p></body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml", native_tables=True
    )
    (table,) = [b for b in blocks if b.get("table")]
    return [c for row in table["table"]["rows"] for c in row["cells"]]


def _align(cell):
    return (cell.get("block_style") or {}).get("align")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("html", "aligns"),
    [
        ("<table><tr><td>a</td><td>b</td></tr></table>", [None, None]),
        ('<table dir="rtl"><tr><td>a</td><td>b</td></tr></table>', ["right", "right"]),
        ('<div dir="rtl"><table><tr><td>a</td></tr></table></div>', ["right"]),
        # A cell's or a row's own dir doesn't count: Kindle Previewer 4.0.3
        # takes it from the table and what holds it (#570, probed).
        ('<table dir="rtl"><tr><td dir="ltr">a</td></tr></table>', ["right"]),
        ('<table><tr><td dir="rtl">a</td></tr></table>', [None]),
        ('<table><tr dir="rtl"><td>a</td></tr></table>', [None]),
    ],
    ids=["plain", "rtl-table", "rtl-ancestor", "ltr-cell", "rtl-cell", "rtl-row"],
)
def test_a_cell_with_no_alignment_of_its_own(html, aligns):
    assert [_align(c) for c in _cells(html)] == aligns


@pytest.mark.unit
def test_a_header_cell_is_left_for_the_generator_to_centre():
    (cell,) = _cells("<table><tr><th>h</th></tr></table>")
    assert _align(cell) is None


@pytest.mark.unit
def test_a_cells_one_paragraph_keeps_its_own_alignment():
    """`<td><p style="text-align: right">`: Previewer writes right (QA on
    #532). The cell is written from its own style, so the paragraph's
    alignment is read when the cell sets none."""
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><table><tr>'
        "<td><p>R</p></td><td>b</td></tr></table></body></html>"
    )
    root = etree.fromstring(xhtml.encode())
    p = root.find(".//{http://www.w3.org/1999/xhtml}p")

    def resolver(elem):
        return {"text-align": "right"} if elem is p else {}

    (table,) = [
        b
        for b in _conv.extract_blocks_from_html(
            root, style_resolver=resolver, base_href="c.xhtml", native_tables=True
        )
        if b.get("table")
    ]
    cells = table["table"]["rows"][0]["cells"]
    assert [_align(c) for c in cells] == ["right", None]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("own", "aligns"),
    [("right", "right"), (None, "center"), ("center", "center"), ("inherit", "right")],
    ids=["own-right", "none", "own-centre", "inherit"],
)
def test_a_header_cell_in_a_right_row_keeps_only_its_own_alignment(own, aligns):
    """`<tr style="text-align: right"><th style="text-align: right">`: right,
    as Kindle Previewer 4.0.3 writes it; the same cell with no alignment of
    its own is centred (#601). calibre's computed value is the row's either
    way, so the cell's own declared value decides."""
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><table><tr>'
        "<th>H</th><td>d</td></tr></table></body></html>"
    )
    root = etree.fromstring(xhtml.encode())
    th = root.find(".//{http://www.w3.org/1999/xhtml}th")
    tr = th.getparent()

    def resolver(elem):
        css = {"text-align": "right"}  # inherited from the row
        if elem is tr:
            css["own-text-align"] = "right"  # the row's own, in CSS
        if elem is th and own:
            css["own-text-align"] = own
        return css

    (table,) = [
        b
        for b in _conv.extract_blocks_from_html(
            root, style_resolver=resolver, base_href="c.xhtml", native_tables=True
        )
        if b.get("table")
    ]
    header, data = table["table"]["rows"][0]["cells"]
    assert _align(header) == aligns
    assert _align(data) == "right"


def _header_cells(xhtml, own_of):
    """The header cells' alignments, with `own_of(elem)` the element's own
    declared text-align, as calibre's resolver gives it."""
    root = etree.fromstring(xhtml.encode())
    _conv.mark_source_classes(root)

    def resolver(elem):
        own = own_of(elem)
        # Computed, as calibre gives it: the nearest declared value, an
        # `align=` included (calibre makes it CSS).
        computed = next(
            (
                v
                for e in (elem, *elem.iterancestors())
                if (v := own_of(e)) and v != "inherit"
            ),
            None,
        )
        css = {"own-text-align": own} if own else {}
        return {**css, "text-align": computed} if computed else css

    (table,) = [
        b
        for b in _conv.extract_blocks_from_html(
            root, style_resolver=resolver, base_href="c.xhtml", native_tables=True
        )
        if b.get("table")
    ]
    return [
        _align(c) for row in table["table"]["rows"] for c in row["cells"] if c["header"]
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("holder", "aligns"),
    [
        ('<tr align="right">', ["left"]),
        ('<tr style="text-align:right">', ["right"]),
    ],
    ids=["align-attribute", "css"],
)
def test_inherit_on_a_header_cell_skips_an_align_attribute(holder, aligns):
    """`<tr align="right"><th style="text-align: inherit">`: left in Kindle
    Previewer 4.0.3 (my probe, 2026-10-29), which doesn't inherit `align=`;
    the same row set in CSS gives right (#621). calibre has turned `align=`
    into a class's text-align by then, so kfxgen marks it before (#329)."""
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><table>'
        f'{holder}<th style="text-align:inherit">H</th><td>d</td></tr>'
        "</table></body></html>"
    )

    def own_of(elem):
        if elem.get("style") == "text-align:inherit":
            return "inherit"
        if elem.get("align") or elem.get("style") == "text-align:right":
            return "right"
        return None

    assert _header_cells(xhtml, own_of) == aligns


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dir_", "aligns"),
    [("rtl", ["right", "left", "center"]), ("ltr", ["left", "right", "center"])],
)
def test_header_cell_keywords_follow_the_tables_direction(dir_, aligns):
    """In a right-to-left table Kindle Previewer 4.0.3 reads `initial` as
    right and `end` as left; `start` stays centre (my probe, 2026-10-29,
    #621)."""
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f'<table dir="{dir_}"><tr><th class="initial">a</th><th class="end">b</th>'
        '<th class="start">c</th></tr></table></body></html>'
    )
    assert _header_cells(xhtml, lambda e: e.get("class")) == aligns
