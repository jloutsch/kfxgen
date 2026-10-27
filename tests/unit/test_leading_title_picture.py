"""A first page outside the TOC, opening with a picture and words in one
paragraph, prints the words once (#525). kfxgen titled the page with those
words (#133) and printed them as a heading too, because the paragraph's
picture kept it from matching its title."""

import pytest

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import NullLog, jpeg_of
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

pytestmark = pytest.mark.unit

WORDS = "Words beside the picture."


def _item(href, *texts):
    blocks = [
        {"text": t, "spans": [], "block_style": None, "anchor_ids": []} for t in texts
    ]
    return {"href": href, "text": "\n\n".join(texts), "blocks": blocks}


def _texts(tmp_path, first):
    spine = [_item("first.xhtml", *first), _item("c.xhtml", "One", "Text.")]
    toc = [{"title": "One", "href": "c.xhtml", "level": 0}]
    chapters = _conv._assemble_chapters_by_coordinate(spine, toc, NullLog())
    out = tmp_path / "w.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=chapters,
        images={"pic.jpg": jpeg_of(300, 200)},
        output_path=str(out),
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    return [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]


@pytest.mark.parametrize("more", [[], ["Second paragraph."]], ids=["alone", "more"])
def test_the_words_beside_a_picture_print_once(tmp_path, more):
    token = _conv._make_img_token("pic.jpg", "")
    texts = _texts(tmp_path, [f"{token} {WORDS}", *more])
    assert [t for t in texts if WORDS in t] == [WORDS]


def test_a_first_paragraph_of_words_alone_still_titles_the_page(tmp_path):
    """No picture: the words are the page's heading, printed once, as
    before."""
    texts = _texts(tmp_path, [WORDS, "Second paragraph."])
    assert texts.count(WORDS) == 1
