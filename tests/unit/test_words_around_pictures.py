"""Words and pictures loose in a container that also holds blocks (#757).

Kindle Previewer 4.0.3 (probe P757, an English book and a vertical Japanese
one): `<div class="c">Before <img/> after<p>…</p></div>` is one paragraph,
the picture inline among the words, and it takes the div's `text-align`.
kfxgen wrote the picture as a block of its own and the words on each side as
two paragraphs, justified. A picture alone between blocks stays a block of
its own, in Previewer as here (R09).
"""

import pytest
from lxml import etree

from kfxgen import converter as conv
from kfxgen._img_tokens import IMG_TOKEN_RE

pytestmark = pytest.mark.unit

RULES = {
    "c": {"text-align": "center"},
    "s": {"text-align": "start"},
    "gone": {"display": "none"},
    "fl": {"float": "left"},
}


def _blocks(body):
    def resolve(e):
        out = {}
        for k in (e.get("class") or "").split():
            out.update(RULES.get(k, {}))
        return out

    doc = etree.fromstring(
        f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'.encode()
    )
    return [
        b
        for b in conv.extract_blocks_from_html(doc, style_resolver=resolve)
        if b.get("text", "").strip()
    ]


def _shape(b):
    """A block's text with each picture as [IMG], and its alignment."""
    text = IMG_TOKEN_RE.sub("[IMG]", b["text"]).strip()
    return text, (b.get("block_style") or {}).get("align")


IMG = '<img src="a.jpg" alt=""/>'


@pytest.mark.parametrize(
    "run, want",
    [
        # P757 R01-R03, R05, R08: the words and the pictures, one paragraph.
        (f"Before {IMG} after", "Before [IMG] after"),
        (f"Before {IMG}", "Before [IMG]"),
        (f"{IMG} after", "[IMG] after"),
        (f"One {IMG} two {IMG} three", "One [IMG] two [IMG] three"),
        (f"<span>Before</span>{IMG}", "Before[IMG]"),
    ],
)
def test_a_picture_among_loose_words_is_in_their_paragraph(run, want):
    first, last = _blocks(f'<div class="c">{run}<p>Last.</p></div>')
    assert _shape(first) == (want, "center")
    assert _shape(last)[0] == "Last."


def test_a_picture_alone_between_blocks_stays_a_block():
    # P757 R09: no words beside it.
    pic, last = _blocks(f'<div class="c">{IMG}<p>Last.</p></div>')
    assert _shape(pic)[0] == "[IMG]" and _shape(last)[0] == "Last."


def test_only_the_run_with_words_takes_its_picture():
    # A picture between two blocks, with no words of its own, is a block
    # even when another run in the container has words.
    got = [
        _shape(b)[0]
        for b in _blocks(
            f'<div class="c">Lead words.<p>One.</p>{IMG}<p>Two.</p>'
            f"Tail {IMG} words.</div>"
        )
    ]
    assert got == ["Lead words.", "One.", "[IMG]", "Two.", "Tail [IMG] words."]


def test_loose_text_takes_its_containers_alignment():
    # P757 R11: centred in a centred div, with no picture.
    loose, _p = _blocks('<div class="c">Loose words.<p>Last.</p></div>')
    assert _shape(loose) == ("Loose words.", "center")


def test_loose_text_in_an_unaligned_container_has_no_alignment():
    loose, _p = _blocks("<div>Loose words.<p>Last.</p></div>")
    assert _shape(loose) == ("Loose words.", None)


def test_a_pictures_id_among_words_stays_on_its_paragraph():
    first, _p = _blocks(
        '<div class="c">Before <img id="f1" src="a.jpg" alt=""/> after<p>Last.</p></div>'
    )
    assert "f1" in first["anchor_ids"]


def test_a_hidden_picture_among_words_gives_only_its_id():
    first, _p = _blocks(
        '<div class="c">Before <img id="h1" class="gone" src="a.jpg" alt=""/>'
        " after<p>Last.</p></div>"
    )
    assert "[IMG]" not in _shape(first)[0] and "h1" in first["anchor_ids"]


def test_a_floated_picture_among_words_floats_in_their_paragraph():
    # P757 R10: one paragraph, the picture floated left in it.
    first, _p = _blocks(
        '<div class="c">Before <img class="fl" src="a.jpg" alt=""/> after'
        "<p>Last.</p></div>"
    )
    assert _shape(first)[0] == "Before [IMG] after"
    (m,) = IMG_TOKEN_RE.finditer(first["text"])
    assert m.group(4) == "left"


def test_start_in_a_right_to_left_container_is_right():
    # As a paragraph's own `text-align: start` (#515).
    loose, _p = _blocks('<div dir="rtl" class="s">Loose words.<p>Last.</p></div>')
    assert _shape(loose) == ("Loose words.", "right")


def test_an_svg_ends_the_run():
    # `_walk_container` writes an <svg> as pictures of its own; a picture
    # after it, with no words of its own, stays a block.
    got = [
        _shape(b)
        for b in _blocks(
            f'<div class="c">Words.<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="10" height="10"><rect width="5" height="5"/></svg>{IMG}'
            "<p>Last.</p></div>"
        )
    ]
    # A block picture has no alignment of its own; a paragraph of the run
    # would be centred.
    assert got[0] == ("Words.", "center") and got[-2] == ("[IMG]", None)
