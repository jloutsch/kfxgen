"""A chapter's title cut from its first blocks and table rows (#282).

Six mutations the #282 recheck found every test still passes, in the code
that cuts a printed chapter title out of the body: the split opener over
up to three blocks (#64). The cut from a native table's first rows (#219,
#261) is gone: a table printing the title is kept whole (#470).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from tests.unit.test_book_headings import _printed  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.unit.test_link_landings import _landings, _link  # noqa: E402
from tests.unit.test_native_generator import _book, _para  # noqa: E402

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
