"""A chapter's title cut from its first blocks and table rows (#282).

Six mutations the #282 recheck found every test still passes, in the code
that cuts a printed chapter title out of the body: the split opener over
up to three blocks (#64) and the cut from a native table's first rows
(#219, #261).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.inline_style import FLAG_ITALIC  # noqa: E402
from tests.unit.test_book_headings import _printed  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.unit.test_link_landings import _landings, _link  # noqa: E402
from tests.unit.test_native_generator import _book, _para, _tiny_table  # noqa: E402

pytestmark = pytest.mark.unit


def _texts(tmp_path, title, blocks, more=()):
    entries, _, text, _ = _book(
        tmp_path, [{"title": title, "text": "x", "blocks": blocks}, *more]
    )
    return entries, [text(e) for e in entries if text(e)], text


def _landing_text(kfx, label):
    """All the text inside the entry the link `label` lands on: a row or a
    cell is a container whose text is in the entries it holds."""
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    anchors = {str(f.fid): val(f)["$183"] for f in by_type(frags, "$266")}

    def text(e):
        ref = e.get("$145")
        own = str(texts[str(ref["name"])][ref["$403"]]) if ref else ""
        return own + "".join(text(c) for c in e.get("$146") or () if hasattr(c, "get"))

    entries, target = {}, None
    for story in by_type(frags, "$259"):
        for e in iter_entries(val(story)["$146"]):
            entries[int(e["$155"])] = e
            ref = e.get("$145")
            for span in e.get("$142") or ():
                own = str(texts[str(ref["name"])][ref["$403"]]) if ref else ""
                start = int(span["$143"])
                if "$179" in span and own[start : start + int(span["$144"])] == label:
                    target = str(span["$179"])
    assert target is not None, f"the link {label!r} was written as plain text"
    return text(entries[int(anchors[target]["$155"])])


def test_a_three_block_opener_prints_once(tmp_path):
    lines = _printed(
        tmp_path,
        "IV Dawn Breaks",
        "<h1>IV</h1><h2>Dawn</h2><h3>Breaks</h3><p>Body.</p>",
    )
    assert [t for t, _ in lines] == ["IV\nDawn\nBreaks", "Body."]


def test_the_first_eaten_blocks_id_still_lands(tmp_path):
    # <h2 id="c3">III</h2><h2>Title</h2>: the id is on the block before the
    # last one eaten, and goes on the heading.
    blocks = [_para("III", ["c.xhtml#c3"]), _para("Title"), _para("Body.")]
    other = {"title": "Two", "text": "x", "blocks": [_link("To three", "c.xhtml#c3")]}
    _texts(tmp_path, "III Title", blocks, [other])
    assert _landings(tmp_path / "b.kfx")["To three"] == "III\nTitle"


def test_an_empty_cell_before_the_title_leaves_no_letter_of_it(tmp_path):
    # Cut and empty cells stay as blanks, so later columns keep their places.
    _, texts, _ = _texts(tmp_path, "One", [_tiny_table([["", "", "One", "x"]])])
    assert texts[0] == "One"
    assert [t.strip() for t in texts[1:]] == ["", "", "", "x"]


def test_a_run_crossing_the_cut_starts_where_the_cell_now_does(tmp_path):
    table = _tiny_table([["One more"]])
    cell = table["table"]["rows"][0]["cells"][0]
    cell["spans"] = [(2, 6, frozenset({FLAG_ITALIC}))]
    entries, texts, _ = _texts(tmp_path, "One", [table])
    assert "more" in texts
    starts = [int(r["$143"]) for e in entries for r in e.get("$142") or ()]
    assert starts and min(starts) >= 0


def test_a_table_left_with_only_empty_rows_goes(tmp_path):
    entries, texts, _ = _texts(
        tmp_path, "One", [_tiny_table([["One"], [""]]), _para("Text.")]
    )
    assert texts == ["One", "Text."]
    assert not [e for e in entries if str(e.get("$159")) == "$278"]


def test_a_cut_cell_paragraphs_id_lands_on_the_next_row(tmp_path):
    # A title split over two rows drops both; an id on a paragraph in a
    # dropped cell goes on the first row kept. It used to go to the table's
    # last row, rows away from the title.
    table = _tiny_table([["One"], ["Two"], ["Row three"], ["Row four"]])
    cell = table["table"]["rows"][0]["cells"][0]
    cell["paragraphs"] = [
        {
            "text": "One",
            "spans": [],
            "anchor_keys": ["c.xhtml#p1"],
            "anchor_offsets": {"c.xhtml#p1": 0},
        }
    ]
    other = {"title": "Next", "text": "x", "blocks": [_link("To one", "c.xhtml#p1")]}
    _texts(tmp_path, "One Two", [table], [other])
    assert _landing_text(tmp_path / "b.kfx", "To one") == "Row three"
