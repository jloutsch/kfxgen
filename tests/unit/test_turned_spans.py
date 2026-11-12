"""Text set the other way from its line (#247, group B2).

Kindle Previewer 4.0.3 (probe PSB, decoded; both spines alike) writes a
horizontal-tb span in vertical text (S01-S12) as an inline `$270` (`$601
$283`, `$156 $323`) styled `$560 $557`, `$56` 100%, `$478`, `$784`, holding
a `$269` paragraph of its text with its own runs: a link in it (S05), its
bold (S06). The paragraph takes one placeholder character; in the position
map the container takes one position and its paragraph one per character
(S01). A vertical-rl span in a horizontal block (R05) is the same with
`$156 $322`, `$560 $559` and `$57` 100%. kfxgen wrote both as plain text in
the line. Through calibre in tests/integration/test_calibre_turned_spans.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen._img_tokens import IMG_TOKEN_RE
from kfxgen.inline_style import (
    FLAG_BOLD,
    link_target,
    make_link_flag,
    make_turn_flag,
    turn_of,
)
from kfxgen.native_generator import NativeKFXGenerator, _item_widths
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

pytestmark = pytest.mark.unit

XHTML = "http://www.w3.org/1999/xhtml"
CSS = {"b": {"font-weight": "bold"}}


def _paragraphs(body, mark, **kw):
    """The blocks `body` gives in vertical text, with each element whose id
    starts "t" marked as `mark` (`_HORIZONTAL` or `_VERTICAL`)."""
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    for e in root.iter():
        if not isinstance(e.tag, str):
            continue
        e.set(_conv._V_AUTHOR, "")
        if (e.get("id") or "").startswith("t"):
            e.set(mark, "1")
    return _conv.extract_blocks_from_html(
        root, style_resolver=_resolver(CSS), base_href="c.xhtml", **kw
    )


def _turns(block):
    return [(s, turn_of(f)) for s, _n, f in block.get("spans") or () if turn_of(f)]


def test_a_horizontal_span_is_one_placeholder_carrying_its_text():
    (block,) = _paragraphs('<p>縦の<span id="t1">ABC</span>中。</p>', _conv._HORIZONTAL)
    assert block["text"] == "縦の￼中。"
    ((at, (_serial, mode, text, spans)),) = _turns(block)
    assert at == 2 and mode == "horizontal" and text == "ABC" and spans == ()
    # Its ids, and those inside it, stay the paragraph's, at the
    # placeholder: a link to one lands there.
    (block,) = _paragraphs(
        '<p>縦の<span id="t1"><span id="in">ABC</span></span>中。</p>',
        _conv._HORIZONTAL,
        link_targets={"c.xhtml#in"},
    )
    assert {"t1", "in"} <= set(block["anchor_ids"])
    assert block["anchor_offsets"]["c.xhtml#in"] == 2


def test_its_own_runs_go_with_it():
    (block,) = _paragraphs(
        '<p>縦<span id="t1"><span class="b">BO</span>LD <a href="#x">GO</a></span>。</p>',
        _conv._HORIZONTAL,
    )
    ((_at, (_serial, _mode, text, spans)),) = _turns(block)
    assert text == "BOLD GO"
    bold = [(s, n) for s, n, f in spans if FLAG_BOLD in f]
    linked = [(s, n, link_target(f)) for s, n, f in spans if link_target(f)]
    assert bold == [(0, 2)] and linked == [(5, 2, "c.xhtml#x")]
    # Nothing of it on the placeholder: Previewer writes no run outside,
    # not even for the element's own emphasis (PSB S08, <em class="hz">).
    ((_s, _n, flags),) = [s for s in block["spans"] if turn_of(s[2])]
    assert len(flags) == 1
    (block,) = _paragraphs('<p>縦<em id="t1">EM</em>。</p>', _conv._HORIZONTAL)
    ((_s, _n, flags),) = [s for s in block["spans"] if turn_of(s[2])]
    assert len(flags) == 1


def test_a_vertical_span_in_a_horizontal_block_is_one_too():
    (block,) = _paragraphs(
        '<p>Horizontal <span id="t1">縦</span> text.</p>', _conv._VERTICAL
    )
    ((_at, (_serial, mode, text, _spans)),) = _turns(block)
    assert mode == "vertical" and text == "縦"


@pytest.mark.parametrize(
    "inner",
    [
        'AB<img src="a.jpg" alt=""/>CD',
        "<ruby>漢<rt>かん</rt></ruby>",
        "",
    ],
)
def test_what_its_paragraph_cant_hold_stays_in_the_line(inner):
    # A picture among its words, a ruby, or nothing: not turned, as before,
    # and the picture kept.
    blocks = _paragraphs(f'<p>縦<span id="t1">{inner}</span>。</p>', _conv._HORIZONTAL)
    assert not any(_turns(b) for b in blocks)
    if "img" in inner:
        assert IMG_TOKEN_RE.search(blocks[0]["text"])


@pytest.mark.parametrize("tag", ["p", "div"])
def test_a_block_element_is_not_turned(tag):
    # A horizontal block in a table cell, which is walked as one paragraph:
    # a block (#749), not an inline span.
    body = f'<table><tr><td>縦<{tag} id="t1">Block.</{tag}>後</td></tr></table>'
    blocks = _paragraphs(body, _conv._HORIZONTAL)
    assert not any(_turns(b) for b in blocks)


def test_its_container_and_paragraph_take_one_and_a_character_each():
    item = ("turn", "horizontal", "ABC", ())
    assert _item_widths(item, (1, 2)) == [1, 3]


def _book(tmp_path, spans, text="縦￼中", mode="vertical-rl"):
    out = tmp_path / "t.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "章",
                "text": "x",
                "blocks": [
                    {"text": "前。", "spans": [], "writing_mode": mode},
                    {"text": text, "spans": spans},
                    {"text": "後。", "spans": [], "anchor_keys": ["c.xhtml#x"]},
                ],
            }
        ],
        output_path=str(out),
        rtl=True,
        writing_mode="vertical-rl",
    )
    return load_fragments(out)


def _turned_entries(frags):
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    found = []

    def walk(es):
        for e in es:
            if hasattr(e, "get"):
                if str(e.get("$159")) == "$270" and str(e.get("$601")) == "$283":
                    found.append((e, styles.get(str(e.get("$157")), {})))
                walk(e.get("$146") or [])

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"])
    return found


def test_the_generator_writes_a_container_holding_its_paragraph(tmp_path):
    link = make_link_flag("c.xhtml#x")
    flag = make_turn_flag("horizontal", "ABC", [(0, 3, frozenset({link}))])
    frags = _book(tmp_path, [(1, 1, frozenset({flag}))])
    ((entry, style),) = _turned_entries(frags)
    assert str(entry["$156"]) == "$323"
    assert str(style["$560"]) == "$557" and float(style["$56"]["$307"]) == 100
    assert style["$478"] is True and style["$784"] is True
    (inner,) = entry["$146"]
    assert str(inner["$159"]) == "$269"
    (run,) = inner["$142"]
    assert (run["$143"], run["$144"]) == (0, 3) and "$179" in run


def test_a_vertical_one_is_vertical_at_the_lines_height(tmp_path):
    flag = make_turn_flag("vertical", "縦", [])
    frags = _book(tmp_path, [(1, 1, frozenset({flag}))], mode="horizontal-tb")
    ((entry, style),) = _turned_entries(frags)
    assert str(entry["$156"]) == "$322"
    assert str(style["$560"]) == "$559" and float(style["$57"]["$307"]) == 100
    assert "$142" not in entry["$146"][0]
