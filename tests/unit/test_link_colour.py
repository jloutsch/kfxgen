"""A colour on a link, or set inside one, as the link's colour (#728).

Kindle Previewer 4.0.3 (probes PUNF, P728b, P728c, decoded) writes a link's
own colour as `$576` and `$577`, the colour of a link and of a visited link,
each `{$19: ARGB}`, and no `$19`. kfxgen dropped it:

- `<a class="grey">` and a grey span inside a link: `$576` 0xFF999999. The
  colour is the book's, not measured against the page or a box behind it:
  a yellow link on a dark box stays 0xFFFFFF00.
- A link inside coloured text, with no colour of its own: `$19`, the text's
  colour, as before (#377).
- On the page, black and near-black (every channel up to 0x1A) are no link
  colour: the link is drawn in its paragraph's colour, a span's around it
  dropped (QA on #732, P732 B05, P732d). In a box, every colour is written,
  black included.
- A link whose target isn't found is written as text, its colour as `$19`
  (P732b).
- A translucent colour: Previewer writes its alpha (0x7FFF0000). kfxgen
  blends it onto the page, as it does a text colour (#383).

Through calibre: tests/integration/test_calibre_link_colour.py.
"""

import pytest

from kfxgen.inline_style import COLOUR_FLAG, LINK_COLOUR_FLAG, css_link_colour
from tests.unit.test_typography import (
    FILLER,
    _blocks,
    _book,
    _found,
    _one,
    _resolver,
    _run,
)

GREY = 0xFF999999


def _link(block, word):
    """The link colour over `word`, as given, or None."""
    got = _run(block, word, LINK_COLOUR_FLAG)
    return None if got is None else got[0]


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
    assert _link(b, "link") == GREY
    assert _link(b, "here") == GREY
    assert _run(b, "link", COLOUR_FLAG) is None


@pytest.mark.unit
def test_a_coloured_span_inside_a_link_is_a_link_colour():
    css = {"g": {"color": "#999999"}}
    b = _one('<p id="x">a <a href="#x">plain <span class="g">grey</span></a></p>', css)
    assert _link(b, "grey") == GREY
    assert _link(b, "plain") is None
    assert _run(b, "grey", COLOUR_FLAG) is None


@pytest.mark.unit
def test_a_span_inside_a_coloured_link_takes_its_own():
    css = {"g": {"color": "#999999"}, "r": {"color": "#ff0000"}}
    b = _one(
        '<p id="x"><a class="g" href="#x">grey <span class="r">red</span> end</a></p>',
        css,
    )
    assert [_link(b, w) for w in ("grey", "red", "end")] == [
        GREY,
        0xFFFF0000,
        GREY,
    ]
    # One link colour per run: two would leave the colour to set order.
    for _s, _n, flags in b["spans"]:
        assert sum(1 for f in flags if f[:1] == (LINK_COLOUR_FLAG,)) <= 1


@pytest.mark.unit
def test_a_coloured_link_in_a_coloured_span_has_only_its_link_colour():
    css = {"r": {"color": "#e00"}, "g": {"color": "#999999"}}
    b = _one(
        '<p id="x"><span class="r">red <a class="g" href="#x">link</a></span></p>', css
    )
    assert _link(b, "link") == GREY
    assert _run(b, "link", COLOUR_FLAG) is None
    assert _run(b, "red", COLOUR_FLAG) == 0xFFEE0000


@pytest.mark.unit
def test_a_link_in_coloured_text_has_no_link_colour():
    css = {"r": {"color": "#e00"}}
    b = _one('<p id="x"><span class="r">red <a href="#x">link</a></span></p>', css)
    assert _link(b, "link") is None
    assert _run(b, "link", COLOUR_FLAG) == 0xFFEE0000


@pytest.mark.unit
def test_a_black_link_in_a_grey_paragraph_is_the_paragraphs():
    """P728c Q12: drawn in the paragraph's grey, which its entry carries."""
    css = {"gp": {"color": "#333333"}, "k": {"color": "black"}}
    b = _one('<p class="gp" id="x">grey <a class="k" href="#x">link</a></p>', css)
    assert _link(b, "link") is None
    assert _run(b, "link", COLOUR_FLAG) is None
    assert b["block_style"]["colour"] == 0xFF333333


@pytest.mark.unit
def test_a_black_link_in_a_red_span_drops_the_red():
    """QA on #732, P732 B05: Previewer draws it black, as main did."""
    css = {"r": {"color": "#cc0000"}, "k": {"color": "black"}}
    b = _one(
        '<p id="x">a <span class="r">red <a class="k" href="#x">link</a></span></p>',
        css,
    )
    assert _link(b, "link") is None
    assert _run(b, "link", COLOUR_FLAG) is None
    assert _run(b, "red", COLOUR_FLAG) == 0xFFCC0000


@pytest.mark.unit
@pytest.mark.parametrize(
    "colour, argb",
    [
        # P732d: none up to 0x1A in every channel, written from 0x20.
        ("#1a1a1a", None),
        ("#100000", None),
        ("#00001a", None),
        ("#202020", 0xFF202020),
        ("#200000", 0xFF200000),
        ("#003000", 0xFF003000),
    ],
)
def test_a_near_black_link_on_the_page_has_no_link_colour(colour, argb):
    css = {"gp": {"color": "#666666"}, "k": {"color": colour}}
    b = _one('<p class="gp" id="x">grey <a class="k" href="#x">link</a></p>', css)
    assert _link(b, "link") == argb
    assert _run(b, "link", COLOUR_FLAG) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "colour, argb", [("black", 0xFF000000), ("#1a1a1a", 0xFF1A1A1A)]
)
def test_in_a_box_a_black_link_has_its_link_colour(colour, argb):
    """QA on #732, P732 B01-B04: Previewer writes every link colour in a box,
    black too, where the box's light text would draw it otherwise."""
    css = {
        "box": {"background-color": "#333333", "color": "#eeeeee"},
        "k": {"color": colour},
    }
    box = _one(
        '<div class="box"><p id="x">box <a class="k" href="#x">link</a></p></div>', css
    )
    (b,) = box["blocks"]
    assert _link(b, "link") == argb
    assert _run(b, "link", COLOUR_FLAG) is None


@pytest.mark.unit
def test_a_transparent_link_has_no_link_colour():
    css = {"t": {"color": "transparent"}}
    b = _one('<p id="x">a <a class="t" href="#x">link</a></p>', css)
    assert _link(b, "link") is None


@pytest.mark.unit
def test_a_coloured_name_anchor_is_text_colour():
    """An `<a>` with no href is no link: its colour is its text's."""
    css = {"g": {"color": "#333333"}}
    b = _one('<p>a <a class="g" id="n">name</a> b</p>', css)
    assert _link(b, "name") is None
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


@pytest.mark.unit
def test_a_link_whose_target_isnt_found_has_its_colour_as_text(tmp_path):
    """QA on #732, P732b: the link is dropped, and Previewer writes its colour
    as the text's."""
    css = {"g": {"color": "#333333"}}
    body = FILLER + '<p>See <a class="g" href="#nowhere">Here</a> now.</p>'
    style = _found(_book(tmp_path, body, css))["See Here now."][1]["Here"]
    assert "$576" not in style
    assert style["$19"] == 0xFF333333


@pytest.mark.unit
def test_a_coloured_link_in_a_table_cell_has_its_link_colour(tmp_path):
    """QA on #732, P732 T01: the cell's runs take the link colour too."""
    css = {"g": {"color": "#999999"}}
    body = FILLER + (
        '<p id="t">Target.</p><table><tr><td>A <a class="g" href="#t">Here</a> b</td>'
        "<td>Other</td></tr></table>"
    )
    found = _found(_book(tmp_path, body, css))
    (runs,) = [r for t, (_s, r) in found.items() if t.startswith("A Here")]
    assert runs["Here"]["$576"] == {"$19": GREY}


@pytest.mark.unit
def test_words_inside_a_link_whose_target_isnt_found_have_no_link_colour(tmp_path):
    """Runs inside a link are layered on its run (#715) and carry no target
    of their own: whether the link was found is read from the run they're
    layered on. A word coloured inside it is such a run."""
    css = {"g": {"color": "#333333"}, "r": {"color": "#cc0000"}}
    body = FILLER + (
        '<p>See <a class="g" href="#nowhere">Here <span class="r">red</span> x</a> now.</p>'
    )
    runs = _found(_book(tmp_path, body, css))["See Here red x now."][1]
    assert not any("$576" in s for s in runs.values())
    assert runs["red"]["$19"] == 0xFFCC0000


@pytest.mark.unit
@pytest.mark.parametrize(
    "box_text, colour, argb",
    [
        # QA on #732, P732 B03, B04, B14: darkened for contrast these are
        # all the box text's own colour; Previewer writes them as given.
        ("#333333", "black", 0xFF000000),
        ("#000000", "#333333", 0xFF333333),
        ("#333333", "#000000", 0xFF000000),
    ],
)
def test_a_dark_link_on_a_dark_box_has_its_link_colour(box_text, colour, argb):
    css = {
        "box": {"background-color": "#222222", "color": box_text},
        "k": {"color": colour},
    }
    box = _one(
        '<div class="box"><p id="x">box <a class="k" href="#x">link</a></p></div>', css
    )
    (b,) = box["blocks"]
    assert _link(b, "link") == argb


@pytest.mark.unit
def test_a_black_link_whose_target_isnt_found_is_black_text(tmp_path):
    """QA on #732, P732b B01-B04: Previewer writes it `$19` black, in a grey
    paragraph too; the near-black rule is for links."""
    css = {"gp": {"color": "#666666"}, "k": {"color": "black"}}
    body = FILLER + '<p class="gp">See <a class="k" href="#nowhere">Here</a> now.</p>'
    style = _found(_book(tmp_path, body, css))["See Here now."][1]["Here"]
    assert style["$19"] == 0xFF000000
    assert "$576" not in style


@pytest.mark.unit
def test_an_unfound_links_colour_is_darkened_against_its_box(tmp_path):
    """QA on #732, P732b C01-C08: as text, measured against the box."""
    from kfxgen.inline_style import css_text_colour

    css = {
        "box": {"background-color": "#222222", "color": "#eeeeee"},
        "k": {"color": "#333333"},
    }
    body = FILLER + (
        '<div class="box"><p>See <a class="k" href="#nowhere">Here</a> now.</p></div>'
    )
    style = _found(_book(tmp_path, body, css))["See Here now."][1]["Here"]
    assert style["$19"] == css_text_colour("#333333", 0x222222)
    assert style["$19"] != 0xFF333333


@pytest.mark.unit
def test_a_black_link_writes_no_run_style(tmp_path):
    """No colour to write, so no style of its own, as on main (P728c Q12)."""
    css = {"gp": {"color": "#666666"}, "k": {"color": "black"}}
    body = FILLER + '<p class="gp" id="t">See <a class="k" href="#t">Here</a> now.</p>'
    assert _found(_book(tmp_path, body, css))["See Here now."][1]["Here"] == {}


@pytest.mark.unit
def test_an_unfound_black_link_with_its_own_direction_is_black(tmp_path):
    """A run with only a direction gets a direction style alone (#710): an
    unfound link's colour has to make it a run style. A link holding only a
    direction element is one run with both (#713's M05)."""
    css = {"gp": {"color": "#666666"}, "k": {"color": "black"}}
    body = FILLER + (
        '<p class="gp">See <a class="k" href="#nowhere"><span dir="rtl">Here</span></a>'
        " now.</p>"
    )
    style = _found(_book(tmp_path, body, css))["See Here now."][1]["Here"]
    assert style["$19"] == 0xFF000000


@pytest.mark.unit
@pytest.mark.parametrize("inner", ["black", "#1a1a1a"])
def test_a_black_word_inside_a_coloured_link_takes_the_links_colour(inner):
    """QA on #732, P732 B09, P732k K10-K13: Previewer writes the red link's
    `$576` over the whole link, its black word too."""
    css = {"r": {"color": "#cc0000"}, "k": {"color": inner}}
    b = _one(
        '<p id="x"><a class="r" href="#x">pre <span class="k">word</span> post</a></p>',
        css,
    )
    assert [_link(b, w) for w in ("pre", "word", "post")] == [0xFFCC0000] * 3


@pytest.mark.unit
def test_a_black_word_inside_an_unfound_red_link_is_red(tmp_path):
    """P732l L06: Previewer writes the word red, as text."""
    css = {"r": {"color": "#cc0000"}, "k": {"color": "black"}}
    body = FILLER + (
        '<p>See <a class="r" href="#nowhere">pre <span class="k">word</span></a> now.</p>'
    )
    runs = _found(_book(tmp_path, body, css))["See pre word now."][1]
    drawn = [s.get("$19") for t, s in runs.items() if "word" in t]
    assert drawn and all(c == 0xFFCC0000 for c in drawn if c is not None)
    assert not any("$576" in s for s in runs.values())


@pytest.mark.unit
@pytest.mark.parametrize(
    "inner, argb",
    [
        # P728e: WordDumb's links inside links (#317). Previewer gives the
        # inner link its own colour, or none: not the outer link's red.
        ("k", None),
        ("", None),
        ("g", GREY),
    ],
)
def test_a_link_inside_a_red_link_has_only_its_own_colour(inner, argb):
    css = {
        "r": {"color": "#cc0000"},
        "k": {"color": "black"},
        "g": {"color": "#999999"},
    }
    b = _one(
        f'<p id="x"><a class="r" href="#x">outer <a class="{inner}" href="#y">in</a></a>'
        '<span id="y">y</span></p>',
        css,
    )
    assert _link(b, "outer") == 0xFFCC0000
    assert _link(b, "in") == argb


#: QA on #732 (P732p, P732q, P732t) and P728f: a red link holding a black
#: element, and whether Previewer drops the link's colour (True: drawn black).
_ALL_IT_SHOWS = [
    ("X05", '<p>A <a class="r" href="#t"><b class="k">W</b></a> b.</p>', True),
    # A hidden sibling: the library book's chapter headings.
    (
        "T02",
        '<h1><a class="r" href="#t"><span class="h">Chapter</span> <span class="k">W</span>'
        "</a></h1>",
        True,
    ),
    ("T04", '<p>A <a class="r" href="#t"> <b class="k">W</b></a> b.</p>', True),
    (
        "Q01",
        '<p>A <a class="r" href="#t"><span><b class="k">W</b></span></a> b.</p>',
        True,
    ),
    ("Q03", '<p>A<a class="r" href="#t"><sup class="k">W</sup></a> b.</p>', True),
    # A trailing space that stays, spaces on both sides.
    ("T05", '<p>A <a class="r" href="#t"><b class="k">W</b> </a> b.</p>', False),
    ("Q05", '<p>A<a class="r" href="#t"> <b class="k">W</b> </a> b.</p>', False),
    # A picture beside it; an element inside it.
    (
        "Q02",
        '<p>A <a class="r" href="#t"><img src="x.png"/><b class="k">W</b></a> b.</p>',
        False,
    ),
    (
        "Q10",
        '<p>A <a class="r" href="#t"><b class="k">W<span class="g">G</span></b></a> b.</p>',
        False,
    ),
    (
        "K10",
        '<p>A <a class="r" href="#t">pre <b class="k">W</b> post</a> b.</p>',
        False,
    ),
    # P728f: a leading space stays after a letter, also with the link the
    # first thing in a span, and goes after a space or at the block's start.
    ("L01", '<p>A<a class="r" href="#t"> <b class="k">W</b></a> b.</p>', False),
    ("L03", '<p><a class="r" href="#t"> <b class="k">W</b></a> b.</p>', True),
    (
        "L04",
        '<p>A<span><a class="r" href="#t"> <b class="k">W</b></a></span> b.</p>',
        False,
    ),
    (
        "L05",
        '<p>A <span><a class="r" href="#t"> <b class="k">W</b></a></span> b.</p>',
        True,
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "case, html, dropped", _ALL_IT_SHOWS, ids=[c[0] for c in _ALL_IT_SHOWS]
)
@pytest.mark.parametrize("inner", ["black", "#101010"])
def test_a_black_element_that_is_all_a_link_shows_takes_its_colour_away(
    case, html, dropped, inner
):
    css = {
        "r": {"color": "#cc0000"},
        "k": {"color": inner},
        "g": {"color": "#999999"},
        "h": {"display": "none"},
    }
    (b,) = _blocks(html.replace('href="#t"', 'href="#t" id="t"'), _resolver(css))
    assert _link(b, "W") == (None if dropped else 0xFFCC0000)


@pytest.mark.unit
def test_the_library_books_chapter_heading_writes_no_link_colour(tmp_path):
    """QA's library book: `<h1><a><span sr-only>Chapter</span> <span
    black>12</span></a></h1>`, drawn black by Previewer."""
    css = {
        "r": {"color": "#6161ff"},
        "k": {"color": "#000000"},
        "h": {"display": "none"},
    }
    body = FILLER + (
        '<h1 id="u"><a class="r" href="#u"><span class="h">Chapter</span> '
        '<span class="k">12</span></a></h1><p>Words.</p>'
    )
    (runs,) = [
        r
        for t, (_e, r) in _found(_book(tmp_path, body, css)).items()
        if t.endswith("12")
    ]
    assert not any("$576" in s for s in runs.values())
