"""A chapter's heading prints as the book writes it (#409).

When a chapter's TOC label repeats its opening heading, kfxgen prints one
heading, not two. It printed the label's text in place of the book's, which
lost the heading's line breaks, and a heading split over blocks became one
line. Kindle Previewer's page shows the book's heading as written; so does
kfxgen now, while the TOC keeps the label."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _blocks(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )


def _printed(tmp_path, label, body):
    """[(text, number of runs)] for each paragraph the chapter prints."""
    from kfxgen.native_generator import NativeKFXGenerator

    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": label, "text": "x", "blocks": _blocks(body)}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                ref = e["$145"]
                out.append(
                    (
                        str(texts[str(ref["name"])][ref["$403"]]),
                        len(e.get("$142") or []),
                    )
                )
    return out


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "heading", "printed"),
    [
        # Its line breaks (pg11: "CHAPTER VI.<br/>Pig and Pepper").
        ("Part One", "<h1>Part<br/>One</h1>", "Part\nOne"),
        # Its letter case and punctuation, which the label doesn't keep.
        ("Chapter I", "<h1>CHAPTER I.</h1>", "CHAPTER I."),
        # Its no-break spaces (pg345's title page).
        ("D R A C U L A", "<h1>D R A C U L A</h1>", "D R A C U L A"),
    ],
)
def test_a_heading_prints_as_the_book_writes_it(tmp_path, label, heading, printed):
    lines = _printed(tmp_path, label, heading + "<p>Body.</p>")
    assert [t for t, _ in lines] == [printed, "Body."]


@pytest.mark.unit
def test_a_heading_split_over_two_blocks_prints_on_two_lines(tmp_path):
    lines = _printed(
        tmp_path,
        "Chapter 1 The Storm",
        "<h1>Chapter 1</h1><h2>The <i>Storm</i></h2><p>Body.</p>",
    )
    assert lines == [("Chapter 1\nThe Storm", 1), ("Body.", 0)]


@pytest.mark.unit
def test_a_heading_keeps_its_own_runs(tmp_path):
    lines = _printed(tmp_path, "The Storm", "<h1>The <i>Storm</i></h1><p>Body.</p>")
    assert lines == [("The Storm", 1), ("Body.", 0)]


@pytest.mark.unit
def test_a_different_label_still_prints_above_the_heading(tmp_path):
    lines = _printed(tmp_path, "Another label", "<h1>Part<br/>One</h1><p>Body.</p>")
    assert [t for t, _ in lines] == ["Another label", "Part\nOne", "Body."]


@pytest.mark.unit
def test_a_label_opening_a_plain_heading_still_cuts_it(tmp_path):
    """Out of #409: a label of a plain heading's first words prints, and the
    rest of the heading follows, as before."""
    lines = _printed(tmp_path, "Chapter 1", "<h1>Chapter 1 The Storm</h1><p>Body.</p>")
    assert [t for t, _ in lines] == ["Chapter 1", "The Storm", "Body."]
