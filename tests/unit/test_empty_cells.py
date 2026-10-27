"""An empty table cell is written as a cell with nothing in it, as Kindle
Previewer 4.0.3 writes it (#480, probed 2026-10-26): kfxgen wrote a text
entry holding one space. A cell holding only an anchor keeps its entry, so
a link to it still lands."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val


def _row_cells(tmp_path, row_html):
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"<table><tr>{row_html}</tr></table><p>After.</p></body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml", native_tables=True
    )
    out = tmp_path / "e.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def find(es):
        for e in es:
            if hasattr(e, "get"):
                if str(e.get("$159")) == "$279":
                    return e
                hit = find(e.get("$146") or [])
                if hit is not None:
                    return hit
        return None

    row = find([e for st in by_type(frags, "$259") for e in val(st)["$146"]])

    def shape(cell):
        if cell.get("$145"):
            ref = cell["$145"]
            return ("text", str(content[str(ref["name"])][int(ref["$403"])]))
        return ("cell", len(cell.get("$146") or []))

    return [shape(c) for c in row["$146"]]


@pytest.mark.unit
def test_an_empty_cell_has_no_text_entry(tmp_path):
    cells = _row_cells(tmp_path, "<td>a</td><td></td><td> </td><td>&#160;</td>")
    # An NBSP-only cell is empty too, as Previewer writes it (QA on #533).
    assert cells == [("text", "a"), ("cell", 0), ("cell", 0), ("cell", 0)]


@pytest.mark.unit
def test_an_empty_cell_with_an_id_keeps_its_entry(tmp_path):
    cells = _row_cells(tmp_path, '<td>a</td><td id="x"></td>')
    assert cells == [("text", "a"), ("text", " ")]
