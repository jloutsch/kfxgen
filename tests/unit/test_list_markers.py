"""List items carry their number or bullet in the text (#201).

kfxgen has no list structure: each `<li>` becomes a paragraph of its own, and
nothing drew the marker a browser puts in front of it. For a bulleted list
that is a formatting loss; for a numbered one it is content — "step 2" and
"note 14" exist only in the markup in about a third of a real library.

The marker is now written into the item's text. These tests pin the counting
rules (`start`, `reversed`, `value`, `type`, list-style-type), the cases that
must *not* gain a marker (`list-style: none`, `display: block`, an item that
already prints its own number), and that the spans and anchors after the
marker still land on the text they belonged to.
"""

import sys
import tempfile
from pathlib import Path

import pytest
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin"))

from kfxgen import converter  # noqa: E402
from kfxgen.converter import (  # noqa: E402
    _build_style_resolver,
    _format_ordinal,
    extract_blocks_from_html,
)
from tests._helpers import NullLog  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

pytestmark = [pytest.mark.tier1, pytest.mark.unit]


def _doc(body):
    return etree.fromstring(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"{body}</body></html>".encode()
    )


def _texts(body, resolver=None):
    return [
        b["text"] for b in extract_blocks_from_html(_doc(body), style_resolver=resolver)
    ]


# ── The shape in the issue ───────────────────────────────────────────────────


def test_the_issue_example_gains_numbers_and_bullets():
    assert _texts(
        "<p>Steps:</p>"
        "<ol><li>Open the box</li><li>Remove the tray</li></ol>"
        "<ul><li>Apple</li><li>Pear</li></ul>"
    ) == ["Steps:", "1. Open the box", "2. Remove the tray", "• Apple", "• Pear"]


def test_the_numbers_reach_the_container(tmp_path):
    """Through a real conversion: the number is in the `$145` text a reader
    is shown, not only in the block dict."""
    page = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<p>Steps:</p><ol><li>Open the box</li><li>Remove the tray</li></ol>"
        "<ul><li>Apple</li><li>Pear</li></ul>"
        "</body></html>"
    ).encode()
    epub = (
        EpubBuilder()
        .set_metadata(title="A Title", author="An Author")
        .add_chapter("Chapter One", page)
        .build(tmp_path, "book")
    )
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "book.kfx"
        converter.convert_oeb_to_kfx(
            EpubAsOeb(str(epub)), str(out), opts=None, log=NullLog()
        )
        frags = load_fragments(out)
    texts = [str(t) for f in by_type(frags, "$145") for t in val(f)["$146"]]
    for expected in ("1. Open the box", "2. Remove the tray", "• Apple", "• Pear"):
        assert expected in texts


# ── Counting ─────────────────────────────────────────────────────────────────


def test_start_and_type_attributes():
    assert _texts('<ol start="4" type="i"><li>a</li><li>b</li></ol>') == [
        "iv. a",
        "v. b",
    ]


def test_value_resets_the_count_and_later_items_follow_it():
    assert _texts('<ol><li>a</li><li value="10">b</li><li>c</li></ol>') == [
        "1. a",
        "10. b",
        "11. c",
    ]


def test_reversed_counts_down_from_the_item_count():
    assert _texts('<ol reversed=""><li>a</li><li>b</li><li>c</li></ol>') == [
        "3. a",
        "2. b",
        "1. c",
    ]


def test_reversed_with_start():
    assert _texts('<ol reversed="" start="10"><li>a</li><li>b</li></ol>') == [
        "10. a",
        "9. b",
    ]


def test_hidden_item_takes_no_number():
    assert _texts('<ol><li>a</li><li hidden="">x</li><li>b</li></ol>') == [
        "1. a",
        "2. b",
    ]


def test_nested_lists_count_independently():
    assert _texts(
        '<ol><li>Part<ol type="a"><li>One</li><li>Two</li></ol></li><li>Next</li></ol>'
    ) == ["1. Part", "a. One", "b. Two", "2. Next"]


@pytest.mark.parametrize(
    ("n", "style", "expected"),
    [
        (1, "decimal", "1"),
        (3, "decimal-leading-zero", "03"),
        (27, "lower-alpha", "aa"),
        (28, "upper-latin", "AB"),
        (1994, "upper-roman", "MCMXCIV"),
        (4, "lower-roman", "iv"),
        (2, "lower-greek", "β"),
        # Out of range for the style: CSS falls back to decimal.
        (0, "lower-roman", "0"),
        (0, "lower-alpha", "0"),
        (4000, "upper-roman", "4000"),
        # A style kfxgen does not know counts in decimal, as CSS says.
        (7, "cjk-ideographic", "7"),
    ],
)
def test_format_ordinal(n, style, expected):
    assert _format_ordinal(n, style) == expected


# ── Where the marker goes ────────────────────────────────────────────────────


def test_marker_lands_on_the_first_text_inside_the_item():
    """Endnote lists wrap each note in a <p>; the number belongs on the note's
    first paragraph and only that one."""
    assert _texts("<ol><li><p>First para.</p><p>Second para.</p></li></ol>") == [
        "1. First para.",
        "Second para.",
    ]


def test_image_only_item_stays_an_image_and_the_count_goes_on():
    blocks = extract_blocks_from_html(
        _doc('<ol><li><img src="a.png" alt=""/></li><li>two</li></ol><p>after</p>')
    )
    assert blocks[0]["text"].startswith("\x00IMG")
    assert [b["text"] for b in blocks[1:]] == ["2. two", "after"]


def test_outer_marker_is_not_lost_when_the_item_opens_with_a_list():
    assert _texts("<ol><li>a</li><li><ul><li>x</li></ul></li></ol>") == [
        "1. a",
        "2. • x",
    ]


def test_spans_and_anchors_move_with_the_text():
    (block,) = extract_blocks_from_html(
        _doc('<ol><li><a id="top"/>a <em>b</em><a id="mid"/>c</li></ol>'),
        base_href="ch.xhtml",
    )
    assert block["text"] == "1. a bc"
    ((start, length, flags),) = block["spans"]
    assert block["text"][start : start + length] == "b"
    assert "italic" in flags
    # An anchor at the start points at the item, marker included; one inside
    # the text still points at the character it was declared before.
    assert block["anchor_offsets"]["ch.xhtml#top"] == 0
    assert block["text"][block["anchor_offsets"]["ch.xhtml#mid"]] == "c"


# ── No marker ────────────────────────────────────────────────────────────────


def test_list_style_none_shows_nothing():
    assert _texts("<ol><li>a</li></ol>", lambda e: {"list-style-type": "none"}) == ["a"]


def test_display_other_than_list_item_shows_nothing():
    assert _texts(
        "<ol><li>a</li></ol>",
        lambda e: {"list-style-type": "decimal", "display": "block"},
    ) == ["a"]


def test_computed_style_overrides_the_markup():
    assert _texts(
        "<ol><li>a</li><li>b</li></ol>",
        lambda e: {"list-style-type": "upper-alpha", "display": "list-item"},
    ) == ["A. a", "B. b"]


def test_quoted_string_style_is_the_marker():
    assert _texts("<ul><li>a</li></ul>", lambda e: {"list-style-type": '"– "'}) == [
        "– a"
    ]


def test_item_type_attribute_outranks_the_list():
    assert _texts('<ol><li type="I">a</li><li>b</li></ol>') == ["I. a", "2. b"]


@pytest.mark.parametrize(
    ("item", "printed"),
    [
        ('<a href="#r1">1</a>. The note.', "1. The note."),
        ("1) The note.", "1) The note."),
        ("(1) The note.", "(1) The note."),
        ("[1] The note.", "[1] The note."),
        ("1 The note.", "1 The note."),
        ("i. The note.", "i. The note."),
        ("a) The note.", "a) The note."),
    ],
)
def test_item_that_prints_its_own_number_is_left_alone(item, printed):
    assert _texts(f"<ol><li>{item}</li></ol>") == [printed]


@pytest.mark.parametrize(
    "item",
    [
        "I went home.",  # a pronoun, not a numeral
        "A man walked in.",  # an article, not a letter
        "12 angry men.",  # a figure, not this item's number
    ],
)
def test_prose_that_opens_like_a_number_still_gets_one(item):
    assert _texts(f"<ol><li>{item}</li></ol>") == [f"1. {item}"]


# ── The resolver hands over what the marker needs ────────────────────────────


class _Style:
    """Calibre's Style: `.get` is the element's own value, `[prop]` the
    computed one with inheritance."""

    def __init__(self, own, computed):
        self._own, self._computed = own, computed

    def get(self, k, default=None):
        return self._own.get(k, default)

    def __getitem__(self, k):
        return self._computed[k]


class _Stylizer:
    def __init__(self, style):
        self._style = style

    def style(self, elem):
        return self._style


def test_resolver_reads_inherited_list_style_type_and_own_display():
    # list-style-type is set on the <ol> and inherited by the item, so only
    # the computed value has it; display is the item's own.
    st = _Style(own={"display": "list-item"}, computed={"list-style-type": "none"})
    resolver = _build_style_resolver(
        None, None, NullLog(), stylizer_factory=lambda oeb, item: _Stylizer(st)
    )
    css = resolver(object())
    assert css["list-style-type"] == "none"
    assert css["display"] == "list-item"


# ── Typed bullets (#206 review) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "glyph", ["•", "◦", "▪", "■", "●", "○", "‣", "⁃", "–", "—", "-", "*", "·"]
)
def test_an_item_that_types_its_own_bullet_gets_no_second_one(glyph):
    """Found in a library book: items typed `◦ ` in a list whose CSS doesn't
    hide the marker read `• ◦ …`. A typed bullet is the marker, the same way
    a typed number is."""
    assert _texts(f"<ul><li>{glyph} typed</li><li>plain</li></ul>") == [
        f"{glyph} typed",
        "• plain",
    ]


def test_a_numbered_item_that_starts_with_a_dash_still_gets_its_number():
    """Adversarial: only a *bullet* marker yields to a typed bullet. In a
    numbered list the number is content and the dash is part of the text."""
    assert _texts("<ol><li>– a dash item</li><li>– another</li></ol>") == [
        "1. – a dash item",
        "2. – another",
    ]


@pytest.mark.parametrize("text", ["-5 degrees", "*emphasis* here", "—em dash lead"])
def test_a_dash_or_asterisk_without_a_space_is_text_not_a_bullet(text):
    """Adversarial: a dash or asterisk counts as a typed bullet only when a
    space follows it. "-5 degrees" is a negative number, not a list marker."""
    assert _texts(f"<ul><li>{text}</li></ul>") == [f"• {text}"]


@pytest.mark.parametrize(
    "glyph", ["•", "◦", "▪", "▫", "■", "□", "●", "○", "‣", "⁃", "·"]
)
def test_a_bullet_shape_counts_even_with_no_space_after_it(glyph):
    """A shape glyph at the start of a list item is a typed bullet whether or
    not a space follows it: "•item" read "• •item". (From the #206 author's
    follow-up commit 294a691.)"""
    assert _texts(f"<ul><li>{glyph}item</li><li>plain</li></ul>") == [
        f"{glyph}item",
        "• plain",
    ]


def test_a_numbered_item_that_starts_with_a_shape_still_gets_its_number():
    """Adversarial: only a bullet marker yields; a number is content."""
    assert _texts("<ol><li>•item</li></ol>") == ["1. •item"]
