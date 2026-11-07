"""A colour on a link, or set inside one, as the link's colour (#728).

Kindle Previewer 4.0.3 (probes PUNF, P728b, P728c, decoded) writes a link's
own colour as `$576` and `$577`, the colour of a link and of a visited link,
each `{$19: ARGB}`, and no `$19`. kfxgen dropped it:

- `<a class="grey">` and a grey span inside a link: `$576` 0xFF999999. The
  colour is the book's, not measured against the page or a box behind it:
  a yellow link on a dark box stays 0xFFFFFF00.
- A link inside coloured text, with no colour of its own: `$19`, the text's
  colour, as before (#377).
- Black is no link colour: a black link in a grey paragraph gets the grey,
  as `$19`.
- A translucent colour: Previewer writes its alpha (0x7FFF0000). kfxgen
  blends it onto the page, as it does a text colour (#383).

Through calibre: tests/integration/test_calibre_link_colour.py.
"""

import pytest

from kfxgen.inline_style import COLOUR_FLAG, LINK_COLOUR_FLAG, css_link_colour
from tests.unit.test_typography import FILLER, _book, _found, _one, _run

GREY = 0xFF999999


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, argb",
    [
        ("#999999", GREY),
        ("#ff0", 0xFFFFFF00),
        ("black", 0xFF000000),
        # Blended onto the white page, as css_rgb does.
        ("rgba(255, 0, 0, 0.5)", 0xFFFF8080),
        ("transparent", None),
        ("rgba(0, 0, 0, 0)", None),
        ("nonsense", None),
    ],
)
def test_a_link_colour_is_the_books_colour(value, argb):
    assert css_link_colour(value) == argb


@pytest.mark.unit
def test_a_links_own_colour_is_its_link_colour():
    css = {"l": {"color": "#999999"}}
    b = _one('<p id="x">a <a class="l" href="#x">link <i>here</i></a></p>', css)
    assert _run(b, "link", LINK_COLOUR_FLAG) == GREY
    assert _run(b, "here", LINK_COLOUR_FLAG) == GREY
    assert _run(b, "link", COLOUR_FLAG) is None


@pytest.mark.unit
def test_a_coloured_span_inside_a_link_is_a_link_colour():
    css = {"g": {"color": "#999999"}}
    b = _one('<p id="x">a <a href="#x">plain <span class="g">grey</span></a></p>', css)
    assert _run(b, "grey", LINK_COLOUR_FLAG) == GREY
    assert _run(b, "plain", LINK_COLOUR_FLAG) is None
    assert _run(b, "grey", COLOUR_FLAG) is None


@pytest.mark.unit
def test_a_span_inside_a_coloured_link_takes_its_own():
    css = {"g": {"color": "#999999"}, "r": {"color": "#ff0000"}}
    b = _one(
        '<p id="x"><a class="g" href="#x">grey <span class="r">red</span> end</a></p>',
        css,
    )
    assert [_run(b, w, LINK_COLOUR_FLAG) for w in ("grey", "red", "end")] == [
        GREY,
        0xFFFF0000,
        GREY,
    ]


@pytest.mark.unit
def test_a_coloured_link_in_a_coloured_span_has_only_its_link_colour():
    css = {"r": {"color": "#e00"}, "g": {"color": "#999999"}}
    b = _one(
        '<p id="x"><span class="r">red <a class="g" href="#x">link</a></span></p>', css
    )
    assert _run(b, "link", LINK_COLOUR_FLAG) == GREY
    assert _run(b, "link", COLOUR_FLAG) is None
    assert _run(b, "red", COLOUR_FLAG) == 0xFFEE0000


@pytest.mark.unit
def test_a_link_in_coloured_text_has_no_link_colour():
    css = {"r": {"color": "#e00"}}
    b = _one('<p id="x"><span class="r">red <a href="#x">link</a></span></p>', css)
    assert _run(b, "link", LINK_COLOUR_FLAG) is None
    assert _run(b, "link", COLOUR_FLAG) == 0xFFEE0000


@pytest.mark.unit
def test_a_black_link_in_a_grey_paragraph_is_grey():
    """P728c Q12: Previewer writes the paragraph's grey on the link, and no
    link colour."""
    css = {"gp": {"color": "#333333"}, "k": {"color": "black"}}
    b = _one('<p class="gp" id="x">grey <a class="k" href="#x">link</a></p>', css)
    assert _run(b, "link", LINK_COLOUR_FLAG) is None
    assert _run(b, "link", COLOUR_FLAG) == 0xFF333333


@pytest.mark.unit
def test_a_transparent_link_has_no_link_colour():
    css = {"t": {"color": "transparent"}}
    b = _one('<p id="x">a <a class="t" href="#x">link</a></p>', css)
    assert _run(b, "link", LINK_COLOUR_FLAG) is None


@pytest.mark.unit
def test_a_coloured_name_anchor_is_text_colour():
    """An `<a>` with no href is no link: its colour is its text's."""
    css = {"g": {"color": "#333333"}}
    b = _one('<p>a <a class="g" id="n">name</a> b</p>', css)
    assert _run(b, "name", LINK_COLOUR_FLAG) is None
    assert _run(b, "name", COLOUR_FLAG) == 0xFF333333


@pytest.mark.unit
def test_the_book_writes_the_link_colour_as_previewer_does(tmp_path):
    css = {"g": {"color": "#999999"}}
    body = FILLER + '<p id="t">See <a class="g" href="#t">Here</a> now.</p>'
    _style, runs = _found(_book(tmp_path, body, css))["See Here now."]
    style = runs["Here"]
    assert style["$576"] == {"$19": GREY}
    assert style["$577"] == {"$19": GREY}
    assert "$19" not in style


@pytest.mark.unit
def test_a_plain_link_writes_no_link_colour(tmp_path):
    body = FILLER + '<p id="t">See <a href="#t">Here</a> now.</p>'
    found = _found(_book(tmp_path, body))["See Here now."]
    assert not any("$576" in s for s in found[1].values())
