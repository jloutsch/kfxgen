"""Blocks drawn inline, through calibre's real Stylizer (#780).

Project Gutenberg wraps loose text in `div.pg_body_wrapper { display:
inline }`; pg9939's recipe yields printed as three paragraphs, "(",
"Sufficient to Serve Six", ")". Kindle Previewer 4.0.3 (P780, P780J) runs
such a block into the words round it. This checks calibre's computed
`display` reaches the plugin, and that the walk puts calibre's tags back.
The unit tests are in tests/unit/test_inline_blocks.py.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. Last run against calibre 9.16.0.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """
.pg_body_wrapper { display: inline }
.ib { display: inline-block }
.poetry_container.line > div > div { min-width: 45%; display: inline-block; padding: 0 10px }
div.boxed { font-style: normal }
.mlb { display: inline-block; margin-left: 1em }
.bb { display: inline-block; border: 1px solid black }
blockquote.mid { text-align: center }
p.lib { display: inline-block; text-align: left }
p.rule { display: inline-block; border-bottom: 1px solid black }
"""
W = 'class="pg_body_wrapper"'

#: (file, case id, markup, the paragraphs expected between the case's
#: heading and its END line).
CASES = [
    (
        "c1",
        "YIELD",
        f"<div {W}><b>CONSERVE</b></div><div {W}><br/>\n(</div>"
        f"<div {W}><i>Sufficient to Serve Six</i></div><div {W}>)\n\n</div>"
        "<ul><li>1 qt. berries</li></ul>",
        ["CONSERVE\n(Sufficient to Serve Six)", "1 qt. berries"],
    ),
    (
        "c1",
        "BOX",
        f'<div class="boxed"><div {W}>one</div><div {W}> two</div><p>para</p>'
        f"<div {W}>three</div></div>",
        ["one two", "para", "three"],
    ),
    (
        "c1",
        "PANDH",
        f"<p {W}>first p</p><p {W}>second p</p><h3 {W}>heading</h3>",
        ["first psecond pheading"],
    ),
    (
        "c1",
        "IBLOCK",
        '<div class="ib">a</div><div class="ib">b</div>',
        ["ab"],
    ),
    (
        "c1",
        "HOLDS",
        f"<div {W}>before <p>inner</p> after</div>",
        ["before", "inner", "after"],
    ),
    (
        "c1",
        "BOXHOLDS",
        f'<div class="boxed"><div {W}>before <p>inner</p> after</div></div>',
        ["before", "inner", "after"],
    ),
    (
        "c1",
        "BREAKS",
        f"<div {W}>title</div><div {W}><br/>\n</div><div {W}>text</div>",
        ["title", "text"],
    ),
    (
        "c1",
        "ITEM",
        f"<ul><li><div {W}>a</div><div {W}> b</div></li></ul>",
        ["a b"],
    ),
    (
        "c1",
        "CELL",
        f"<table><tr><td><div {W}>c</div><div {W}> d</div></td><td>other</td></tr></table>",
        ["c d", "other"],
    ),
    (
        "c1",
        "VERSE",
        '<div class="poetry_container line"><div><div>half one</div><div>half two</div></div></div>',
        ["half one", "half two"],
    ),
    (
        "c1",
        "MARGINS",
        '<div><div class="mlb">left</div><div class="mlb">right</div></div>',
        ["left", "right"],
    ),
    (
        "c1",
        "BORDERS",
        '<div><div class="bb">left</div><div class="bb">right</div></div>',
        ["left", "right"],
    ),
    (
        "c1",
        "RULE",
        '<p class="rule">ruled</p><p class="rule">twice</p>',
        ["ruled", "twice"],
    ),
    (
        "c2",
        "SPACER",
        f"<p>before</p><div {W}><br/>\n</div><p>after</p>",
        ["before", "after"],
    ),
]


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    # Native tables, the default, so a cell is a paragraph of its own.
    return _convert(calibre, "inline", [c[:3] for c in CASES], CSS, native=True)


@pytest.mark.parametrize(
    "cid,expected", [(c[1], c[3]) for c in CASES], ids=[c[1] for c in CASES]
)
def test_inline_blocks_through_the_real_stylizer(converted, cid, expected):
    by_case, calibre_version = converted
    texts = [text for text, _ in by_case[cid]["rows"]]
    assert texts == expected, f"calibre {calibre_version}, case {cid}"


def test_a_wrapper_of_line_breaks_between_paragraphs_keeps_its_space(converted):
    # P780J J01-J02: Previewer puts 1.01042lh above "after"; kfxgen keeps
    # its spacer (#230), which it wrote before #780: more than the
    # paragraph's own 0.416667lh.
    by_case, calibre_version = converted
    _before, (_text, style) = by_case["SPACER"]["rows"]
    assert Decimal(str(style["$47"]["$307"])) > Decimal("1"), calibre_version


def test_an_inline_block_keeps_its_own_alignment_in_a_centred_quote(calibre):  # noqa: F811
    # QA's library book 7559: Previewer keeps it left-aligned.
    by_case, version = _convert(
        calibre,
        "inline-align",
        [
            (
                "c1",
                "ALIGN",
                '<blockquote class="mid"><p class="lib">left words</p></blockquote>',
            )
        ],
        CSS,
        native=True,
    )
    ((text, style),) = by_case["ALIGN"]["rows"]
    assert text == "left words"
    # $34 $59 is left; a centred paragraph is $34 $320.
    assert str(style.get("$34")) != "$320", version
