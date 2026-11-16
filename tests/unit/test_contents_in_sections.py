"""A contents heading whose listing sits under its own TOC entries (#782).

pg26209 prints `<h2>CONTENTS.</h2>` and then 19 tables, each under an `<h3>`
the navigation lists as a child of "CONTENTS.". The chapter titled
"CONTENTS." holds only its heading. kfxgen replaced it with its own Contents
page (#716), and the 19 tables, in chapters of their own, followed it: the
contents twice. Kindle Previewer 4.0.3 prints the heading and the tables
only, and so does kfxgen now.
"""

import pytest

from tests.unit.test_converter import (
    _conv,
    _OEBBook,
    _RawSpineItem,
    _silent_log,
    _TOCNode,
)

pytestmark = pytest.mark.unit

RECIPES = "<h1>C0 Breads</h1>" + "".join(
    f"<p id='{i}'>{i} recipe text.</p>" for i in ("r1", "r2", "r3")
)


def _table(ids):
    rows = "".join(
        f"<tr><td>{i} recipe</td><td><a href='c0.xhtml#{i}'>13</a></td></tr>"
        for i in ids
    )
    return f"<table>{rows}</table>"


def _list(ids):
    items = "".join(f"<li><a href='c0.xhtml#{i}'>{i} recipe</a></li>" for i in ids)
    return f"<ul class='toc'>{items}</ul>"


def _chapters(listing, contents_body="", nested=True):
    c1 = (
        f"<h2 id='toc'>CONTENTS.</h2>{contents_body}<h3 id='s1'>BREADS.</h3>"
        f"{listing(['r1', 'r2'])}<h3 id='s2'>EGGS.</h3>{listing(['r3'])}"
    )
    spine = [
        _RawSpineItem("c0.xhtml", RECIPES),
        _RawSpineItem("c1.xhtml", c1),
        _RawSpineItem("c2.xhtml", "<h2>C2 License</h2><p>C2 words.</p>"),
    ]
    sections = [_TOCNode("BREADS.", "c1.xhtml#s1"), _TOCNode("EGGS.", "c1.xhtml#s2")]
    if nested:
        middle = [_TOCNode("CONTENTS.", "c1.xhtml#toc", sections)]
    else:
        middle = [_TOCNode("CONTENTS.", "c1.xhtml#toc"), *sections]
    toc = [
        _TOCNode("C0 Breads", "c0.xhtml"),
        *middle,
        _TOCNode("C2 License", "c2.xhtml"),
    ]
    return _conv.extract_chapters_from_oeb(
        _OEBBook(spine, toc),
        _silent_log(),
        metadata={"title": "T"},
        native_tables=True,
    )


def _by_title(chapters):
    return {c["title"]: c for c in chapters}


def test_a_heading_whose_tables_sit_under_its_entries_is_kept():
    chapters = _by_title(_chapters(_table))
    contents = chapters["CONTENTS."]
    # The book's heading, not kfxgen's listing of every entry.
    assert [b["text"] for b in contents["blocks"]] == ["CONTENTS."]
    assert "C0 Breads" not in contents["text"]
    # The tables print once, under their own headings.
    assert [b.get("type") for b in chapters["BREADS."]["blocks"]] == [None, "table"]
    assert [b.get("type") for b in chapters["EGGS."]["blocks"]] == [None, "table"]


def test_listings_dropped_from_its_entries_are_rebuilt():
    # Listings the book marks as its contents (class="toc") under the
    # entries are dropped, as before (#132): the rebuilt page is then the
    # book's only contents.
    chapters = _by_title(_chapters(_list))
    assert chapters["CONTENTS."]["text"].split("\n\n")[:2] == ["Contents", "C0 Breads"]
    assert chapters["BREADS."].get("_had_nav_listing")


def test_a_heading_with_no_entries_under_it_is_rebuilt():
    # The sections beside it in the TOC, not under it, are the book's own
    # chapters, whatever links they hold.
    chapters = _by_title(_chapters(_table, nested=False))
    assert chapters["BREADS."]["toc_level"] == chapters["CONTENTS."]["toc_level"]
    assert "C0 Breads" in chapters["CONTENTS."]["text"]


def test_a_contents_page_with_more_than_its_heading_is_rebuilt():
    # Text of its own beside the heading: not a heading over its entries.
    chapters = _by_title(_chapters(_table, "<p>An index of recipes by kind.</p>"))
    assert "C0 Breads" in chapters["CONTENTS."]["text"]


def _plain_table(ids):
    rows = "".join(f"<tr><td>{i} recipe</td><td>13</td></tr>" for i in ids)
    return f"<table>{rows}</table>"


@pytest.mark.parametrize(
    "listing,body",
    [
        # Its own listing, dropped (#132): the heading alone is left.
        (_table, _list(["r1", "r2", "r3"])),
        # A dropped listing under its entries, beside a table of links.
        (lambda ids: _list(ids) + _table(ids), ""),
        # Tables under its entries with no links: not a listing.
        (_plain_table, ""),
    ],
    ids=["own-listing-dropped", "listing-dropped-under-it", "no-links"],
)
def test_other_headings_over_entries_are_rebuilt(listing, body):
    chapters = _by_title(_chapters(listing, body))
    assert "C0 Breads" in chapters["CONTENTS."]["text"]
