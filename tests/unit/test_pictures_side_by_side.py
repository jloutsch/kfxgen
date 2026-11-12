"""Pictures side by side (#762).

Kindle Previewer 4.0.3 (probes PSS, PSS2, decoded, an English book): two or
more pictures in one run with nothing but spaces between are one paragraph
holding them inline, each sized in em (px / 16) or in the markup's own sizes,
in a `<p>` (P01), a `<div>` (P02), with spaces (P03), wider than the page
(P05), sized (P06), one linked (P07), centred (P08), in a cell (P13, P14);
loose beside a block too (P11). A line break between them keeps each a block
of its own (P04), as do separate `<div>`s (P10). kfxgen wrote each as a
block. Through calibre in tests/integration/test_calibre_pictures_side_by_side.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val

pytestmark = pytest.mark.unit

XHTML = "http://www.w3.org/1999/xhtml"


def _pic(href="a.jpg"):
    return f"\x00IMG\x01{href}\x01x\x00"


def _shapes(tmp_path, text):
    """The entries a paragraph of `text` becomes: "picture" for a block
    picture, ("mixed", [...]) for a paragraph holding pictures."""
    out = tmp_path / "s.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "Chapter", "text": "x", "blocks": [{"text": text, "spans": []}]}],
        output_path=str(out),
        images={"a.jpg": jpeg_of(400, 100), "b.jpg": jpeg_of(200, 40)},
    )
    frags = load_fragments(out)
    story = [f for f in by_type(frags, "$259")][-1]
    out = []
    for e in val(story)["$146"]:
        if str(e["$159"]) == "$271":
            out.append("picture")
        elif e.get("$146"):
            out.append(
                ("mixed", [k if isinstance(k, str) else "picture" for k in e["$146"]])
            )
    return out


@pytest.mark.parametrize(
    "text, expected",
    [
        # P01, P02: two together, one paragraph.
        (f"{_pic()}{_pic('b.jpg')}", [("mixed", ["picture", "picture"])]),
        # P03: with spaces between, kept between them.
        (
            f"{_pic('b.jpg')} {_pic('b.jpg')} {_pic('b.jpg')}",
            [("mixed", ["picture", " ", "picture", " ", "picture"])],
        ),
        # P04: a line break between keeps each a block.
        (f"{_pic()}\n{_pic('b.jpg')}", ["picture", "picture"]),
        # One alone stays a block.
        (_pic(), ["picture"]),
    ],
    ids=["two", "spaced", "line-break", "one"],
)
def test_pictures_together_are_one_paragraph(tmp_path, text, expected):
    assert _shapes(tmp_path, text) == expected


def _loose(body):
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    div = root.find(f".//{{{XHTML}}}div")
    return {
        img.get("alt")
        for img in _conv._pictures_among_words(
            div, lambda e: _conv._local_tag(e.tag) in ("p", "div")
        )
    }


def test_loose_pictures_together_beside_a_block_are_inline():
    # P11: two pictures, then a caption paragraph.
    assert _loose(
        '<div><img src="a" alt="1"/><img src="b" alt="2"/><p>Caption.</p></div>'
    ) == {"1", "2"}
    # One alone between blocks stays a block (#757's R09), and one alone
    # after the last block.
    assert _loose('<div><p>A.</p><img src="a" alt="1"/><p>B.</p></div>') == set()
    assert _loose('<div><p>A.</p><img src="a" alt="1"/></div>') == set()
    # A run of two after a block counts too.
    assert _loose(
        '<div><p>A.</p><img src="a" alt="1"/><img src="b" alt="2"/></div>'
    ) == {"1", "2"}
