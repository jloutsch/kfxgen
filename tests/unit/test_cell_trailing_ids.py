"""An id after a rule at a table cell's end goes on the cell's last line,
and on nothing else (#795).

Every rule and space block shared one module-level list of ids; the cell
walk added the id to it in place, so it went on every rule and space
after it, in later books too, and the cell lost it. Kindle Previewer
4.0.3 keeps the rule in the cell and lands the id on it (P795); kfxgen
keeps only a cell's lines, so the id goes on the line just above."""

import pytest
from lxml import etree

from kfxgen import converter as _conv

pytestmark = pytest.mark.unit

XH = "http://www.w3.org/1999/xhtml"
CELL = (
    "<table><tr><td><p>B1 first.</p><p>B2 second.</p>"
    '<hr/><a id="leak"></a></td></tr></table>'
    "<p>After.</p><hr/><p>Last.</p>"
)


def _resolver(elem):
    """calibre's computed style, as far as these blocks need it: a drawn
    border on a rule, which keeps it a rule; `hr.s` draws nothing, a
    space."""
    if etree.QName(elem).localname == "hr" and elem.get("class") != "s":
        return {"border-sides": [("solid", 1, "#000000")] * 4}
    return {}


def _blocks(body, href="c.xhtml"):
    root = etree.fromstring(f'<html xmlns="{XH}"><body>{body}</body></html>')
    return _conv.extract_blocks_from_html(
        root, style_resolver=_resolver, base_href=href, native_tables=True
    )


def _holders(blocks):
    """(type or text, ids) of every block and cell paragraph holding an id."""
    out = []
    for b in blocks:
        if b["anchor_ids"]:
            out.append((b.get("type") or b["text"], b["anchor_ids"]))
        for row in (b.get("table") or {}).get("rows", ()):
            for cell in row["cells"]:
                for p in cell.get("paragraphs") or ():
                    if p["anchor_ids"]:
                        out.append((p["text"], p["anchor_ids"]))
    return out


def test_the_id_goes_on_the_cells_last_line_only():
    # The table lists its cells' ids too.
    assert _holders(_blocks(CELL)) == [("table", ["leak"]), ("B2 second.", ["leak"])]


def test_a_later_book_gets_no_id_from_an_earlier_one():
    # One calibre process converts book after book: nothing carries over.
    _blocks(CELL)
    later = _blocks("<p>Another book.</p><hr/><p>More.</p>", href="d.xhtml")
    assert [b.get("type") for b in later] == [None, "rule", None]
    assert _holders(later) == []


def test_rules_and_spaces_get_lists_of_their_own(monkeypatch):
    # Two rules and two spaces (an <hr> drawing nothing), as the walk makes
    # them, before spacing folds the spaces into margins (QA on #796).
    seen = []
    spacing = _conv._space_between

    def capture(blocks, *args, **kwargs):
        seen.extend(blocks)
        return spacing(blocks, *args, **kwargs)

    monkeypatch.setattr(_conv, "_space_between", capture)
    _blocks(
        '<p>A</p><hr/><p>B</p><hr class="s"/><p>C</p>'
        '<hr/><p>D</p><hr class="s"/><p>E</p>'
    )
    empty = [b for b in seen if b.get("type") in ("rule", "space")]
    assert sorted(b["type"] for b in empty) == ["rule", "rule", "space", "space"]
    assert len({id(b["anchor_ids"]) for b in empty}) == 4
    assert len({id(b["anchor_offsets"]) for b in empty}) == 4
