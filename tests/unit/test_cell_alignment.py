"""Table cells are left-aligned unless the book says otherwise, and
right-aligned in a right-to-left table, as Kindle Previewer 4.0.3 writes
them (#479, probed 2026-10-26): it writes an explicit left on every cell
and on each paragraph in a cell, where kfxgen left cells unset and set a
cell's paragraphs justified, the body's default."""

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
        ("<table><tr><td>a</td><td>b</td></tr></table>", ["left", "left"]),
        ('<table dir="rtl"><tr><td>a</td><td>b</td></tr></table>', ["right", "right"]),
        ('<div dir="rtl"><table><tr><td>a</td></tr></table></div>', ["right"]),
        ('<table dir="rtl"><tr><td dir="ltr">a</td></tr></table>', ["left"]),
    ],
    ids=["plain", "rtl-table", "rtl-ancestor", "ltr-cell"],
)
def test_a_cell_with_no_alignment_of_its_own(html, aligns):
    assert [_align(c) for c in _cells(html)] == aligns


@pytest.mark.unit
def test_a_header_cell_is_left_for_the_generator_to_centre():
    (cell,) = _cells("<table><tr><th>h</th></tr></table>")
    assert _align(cell) is None
