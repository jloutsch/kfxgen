"""A browser block nested in another element keeps its paragraphs, as
Kindle Previewer does (#387).

`aside`, `header`, `footer`, `nav`, `main` and the other browser blocks
`_BODY_BLOCK_TAGS` lists were missing from the converter's `block_tags`. One
directly in <body> was walked as a container, but one inside a <div> or a
<section> was walked as running text: its headings and paragraphs became one
paragraph, and a filled box in it was never drawn. Kindle Previewer 4
(kpr4, decoded probe on #387) writes each of them as its own paragraphs,
headings and boxes, nested or not.
"""

import pytest

from tests.unit.test_floats import _blocks

CSS = {
    "side": {"background-color": "#eeeeee", "border-top-style": "solid"},
    "filled": {"background-color": "#dddddd"},
    "gone": {"display": "none"},
}


def _texts(body):
    return [b["text"] for b in _blocks(body, CSS)]


@pytest.mark.unit
@pytest.mark.parametrize(
    "tag",
    [
        "aside",
        "header",
        "footer",
        "main",
        "nav",
        "address",
        "hgroup",
        "details",
        "fieldset",
        "figcaption",
        "center",
        "form",
        "menu",
    ],
)
def test_a_nested_browser_block_keeps_its_paragraphs(tag):
    body = f"<div><{tag}><p>One.</p><p>Two.</p></{tag}></div>"
    assert _texts(body) == ["One.", "Two."]


@pytest.mark.unit
def test_a_heading_in_a_nested_aside_stays_a_heading():
    blocks = _blocks("<section><div><aside><h3>Title</h3><p>Para.</p></aside></div></section>", CSS)  # fmt: skip
    assert [(b["text"], b["heading"]) for b in blocks] == [
        ("Title", True),
        ("Para.", False),
    ]


@pytest.mark.unit
def test_a_summary_and_its_details_are_paragraphs_of_their_own():
    body = "<div><details><summary>Summary.</summary><p>Details.</p></details></div>"
    assert _texts(body) == ["Summary.", "Details."]


@pytest.mark.unit
def test_a_caption_between_running_text_is_its_own_paragraph():
    """Previewer writes three paragraphs: the text before, the caption and
    the text after."""
    body = "<div>Lead <figcaption>Caption</figcaption> tail.</div>"
    assert _texts(body) == ["Lead", "Caption", "tail."]


@pytest.mark.unit
def test_a_filled_box_in_a_nested_aside_is_drawn():
    """The #383 A/B's sidebar shape: `section > div > aside > div.sidebar`."""
    body = (
        '<section><div><aside><div class="side"><h3>Sidebar</h3><p>Inside.</p>'
        "</div></aside></div></section>"
    )
    (box,) = _blocks(body, CSS)
    assert box["type"] == "box" and box["box"]["background"] is not None
    assert [k["text"] for k in box["blocks"]] == ["Sidebar", "Inside."]


@pytest.mark.unit
def test_a_filled_nested_aside_is_a_box():
    body = '<div><aside class="filled"><p>One.</p><p>Two.</p></aside></div>'
    (box,) = _blocks(body, CSS)
    assert box["type"] == "box"
    assert [k["text"] for k in box["blocks"]] == ["One.", "Two."]


@pytest.mark.unit
def test_a_hidden_nested_aside_is_still_dropped_and_its_id_moves_on():
    body = (
        '<div><p>Before.</p><aside class="gone" id="n1"><p>Hidden note.</p>'
        "</aside><p>After.</p></div>"
    )
    blocks = _blocks(body, CSS)
    assert [b["text"] for b in blocks] == ["Before.", "After."]
    assert "n1" in blocks[1]["anchor_ids"]


@pytest.mark.unit
def test_a_nested_contents_listing_is_discarded_as_at_the_body():
    body = (
        '<div><nav role="doc-toc"><ol><li><a href="c1.xhtml">Chapter I</a></li>'
        '<li><a href="c2.xhtml">Chapter II</a></li></ol></nav>'
        "<p>Real prose.</p></div>"
    )
    assert _texts(body) == ["Real prose."]


@pytest.mark.unit
def test_a_nested_nav_that_isnt_a_listing_keeps_its_paragraphs():
    """Only a listing marked as contents is discarded (#132); Previewer
    prints a plain <nav>'s paragraphs."""
    body = "<div><nav><p>One.</p><p>Two.</p></nav></div>"
    assert _texts(body) == ["One.", "Two."]


@pytest.mark.unit
def test_inline_text_in_a_nested_aside_is_one_paragraph():
    """Text with no block in it is one paragraph, as before."""
    body = "<div><aside>Text <em>only</em>.</aside><p>After.</p></div>"
    assert _texts(body) == ["Text only.", "After."]
