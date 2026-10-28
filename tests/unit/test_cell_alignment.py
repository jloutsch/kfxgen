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
