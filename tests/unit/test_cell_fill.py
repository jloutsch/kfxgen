"""A table cell's fill is written on the cell, as Kindle Previewer 4.0.3
writes it ($70 on the cell, probed 2026-10-26), and the cell's text colour
is measured against that fill, as #383 measures a box's (#502). kfxgen
wrote no fill, and white text on a black cell came out grey."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from tests.unit.test_native_generator import _storyline, _table_block


def _cell_styles(tmp_path, fill, paragraphs=False):
    block = _table_block([["a", "b"]])
    cell = block["table"]["rows"][0]["cells"][0]
    cell["background"] = fill
    if paragraphs:
        cell["paragraphs"] = [
            {
                "text": t,
                "spans": [],
                "anchor_ids": [],
                "anchor_keys": [],
                "anchor_offsets": {},
                "block_style": None,
            }
            for t in ("p1", "p2")
        ]
    top, styles = _storyline(tmp_path, [block])
    table = next(e for e in top if str(e["$159"]) == "$278")
    row = table["$146"][0]["$146"][0]
    return [styles[str(c["$157"])] for c in row["$146"]]


@pytest.mark.unit
@pytest.mark.parametrize(
    "paragraphs", [False, True], ids=["text-cell", "paragraph-cell"]
)
def test_a_cell_fill_is_written_on_the_cell(tmp_path, paragraphs):
    first, second = _cell_styles(tmp_path, 0xFFEEEEEE, paragraphs)
    assert int(first["$70"]) == 0xFFEEEEEE
    assert "$70" not in second


@pytest.mark.unit
def test_the_converter_reads_a_cells_fill():
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><table><tr>'
        "<td>a</td><td>b</td></tr></table></body></html>"
    )
    root = etree.fromstring(xhtml.encode())
    first = root.find(".//{http://www.w3.org/1999/xhtml}td")

    def resolver(elem):
        return {"background-color": "#eeeeee"} if elem is first else {}

    (table,) = [
        b
        for b in _conv.extract_blocks_from_html(
            root, style_resolver=resolver, base_href="c.xhtml", native_tables=True
        )
        if b.get("table")
    ]
    cells = table["table"]["rows"][0]["cells"]
    assert [c.get("background") for c in cells] == [0xFFEEEEEE, None]
