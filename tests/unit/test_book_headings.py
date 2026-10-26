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
def test_a_different_label_gives_way_to_the_heading(tmp_path):
    """The book's heading prints alone, as on Kindle Previewer's page; the
    label stays in the TOC (#481)."""
    lines = _printed(tmp_path, "Another label", "<h1>Part<br/>One</h1><p>Body.</p>")
    assert [t for t, _ in lines] == ["Part\nOne", "Body."]


@pytest.mark.unit
def test_a_label_opening_a_plain_heading_prints_it_whole(tmp_path):
    """A label of a heading's first words doesn't split it (#481)."""
    lines = _printed(tmp_path, "Chapter 1", "<h1>Chapter 1 The Storm</h1><p>Body.</p>")
    assert [t for t, _ in lines] == ["Chapter 1 The Storm", "Body."]


@pytest.mark.unit
def test_an_empty_label_names_no_block(tmp_path):
    """A label with no letters or digits matches nothing: an ornament opening
    the chapter stays a paragraph, not its heading (#409 mutations), and an
    empty label prints no heading of its own (#473)."""
    lines = _printed(tmp_path, "", "<p>* * *</p><p>Body.</p>")
    assert [t for t, _ in lines] == ["* * *", "Body."]


def _part_page(tmp_path, label):
    """A part page as books write it: a heading and a picture, nothing else.
    [(text, number of runs)] for its text paragraphs, and its picture count."""
    from kfxgen.native_generator import NativeKFXGenerator
    from tests._helpers import MINIMAL_JPEG

    img = "\x00IMG\x01images/x.jpg\x01\x00"
    out = tmp_path / "p.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": label,
                "text": f"PART ONE\n\n{img}",
                "blocks": [
                    {"text": "PART ONE", "spans": [], "heading": True},
                    {"text": img, "spans": []},
                ],
            },
            {"title": "Two", "text": "Two\n\nBody.", "blocks": _blocks("<p>Body.</p>")},
        ],
        images={"images/x.jpg": MINIMAL_JPEG},
        output_path=str(out),
    )
    frags = load_fragments(out)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    lines, pictures = [], 0
    for st in by_type(frags, "$259"):
        if str(st.fid) != "l0":
            continue
        for e in iter_entries(val(st)["$146"]):
            if not hasattr(e, "get"):
                continue
            if e.get("$175") is not None:
                pictures += 1
            elif e.get("$145"):
                ref = e["$145"]
                lines.append(str(texts[str(ref["name"])][ref["$403"]]))
    return lines, pictures


@pytest.mark.unit
@pytest.mark.parametrize("label", ["Part One", "PART ONE"])
def test_a_part_page_keeps_its_heading_over_its_picture(tmp_path, label):
    """The page prints its picture with no heading of kfxgen's (#33); the
    book's own heading stays above it, under a label in other capitals as
    under one written the same (#409 QA: the heading was lost)."""
    lines, pictures = _part_page(tmp_path, label)
    assert lines == ["PART ONE"]
    assert pictures == 1
