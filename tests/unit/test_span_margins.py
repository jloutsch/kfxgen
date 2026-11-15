"""A span's own left and right margin (#781).

pg24407 indents its ingredient lines with `<span style="margin-left: 5em">`;
kfxgen dropped every span's margin. Kindle Previewer 4.0.3 (probes P781 and
P781-2, decoded) writes it on the span's run, `$48`/`$50`: em, % and rem as
given, a px as 0.45pt, a negative one kept, nested spans' summed into one
run. A span opening the paragraph is its text-indent instead, added to the
paragraph's own; in a list item it stays a run. Through calibre in
tests/integration/test_calibre_span_margins.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import (
    FLAG_BOLD,
    MARGIN_LEFT_FLAG,
    MARGIN_RIGHT_FLAG,
    flag_value,
)
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

pytestmark = pytest.mark.unit

XHTML = "http://www.w3.org/1999/xhtml"
CSS = {
    "m5": {"margin-left": "5em"},
    "m2": {"margin-left": "2em"},
    "m1": {"margin-left": "1em"},
    "px": {"margin-left": "20px"},
    "pc": {"margin-left": "10%"},
    "mr": {"margin-right": "2em"},
    "both": {"margin-left": "2em", "margin-right": "1em"},
    "neg": {"margin-left": "-1em"},
    "zero": {"margin-left": "0", "margin-right": "auto"},
    "ti": {"text-indent": "1em"},
    "tip": {"text-indent": "5%"},
    "bm2": {"font-weight": "bold", "margin-left": "2em"},
    "bigm": {"margin-left": "2em", "font-size": "2em"},
    "fl": {"float": "left", "margin-left": "-2.5em", "margin-right": "1em"},
    "neg3": {"margin-left": "-3em"},
    "hang": {"text-indent": "-1em", "margin-left": "2em"},
    "pfn4": {"margin-left": "4em"},
    "pfn1": {"margin-left": "1em"},
    "neg1": {"margin-left": "-1em"},
}


def _blocks(body):
    root = etree.fromstring(
        f'<html xmlns="{XHTML}"><body>{body}</body></html>'.encode()
    )
    return _conv.extract_blocks_from_html(
        root, style_resolver=_resolver(CSS), base_href="c.xhtml"
    )


def _margins(block):
    """(start, length, left, right) per run with a margin."""
    out = []
    for start, length, flags in block.get("spans") or ():
        left, right = (
            flag_value(flags, MARGIN_LEFT_FLAG),
            flag_value(flags, MARGIN_RIGHT_FLAG),
        )
        if left or right:
            out.append((start, length, left, right))
    return out


def test_pg24407s_ingredient_lines_are_indented():
    (block,) = _blocks(
        "<p>\n<span class='m5'>Potato meal.</span><br/>\n<span class='m5'>Sugar.</span>"
        "<br/>\n<span class='m5'>Four eggs.</span><br/>\n</p>"
    )
    # The first line by the paragraph's indent, the others by their runs.
    assert block["block_style"]["indent"] == ("5", "$308")
    assert _margins(block) == [
        (13, 6, ("5", "$308"), None),
        (20, 10, ("5", "$308"), None),
    ]


@pytest.mark.parametrize(
    "cls,left,right",
    [
        ("m2", ("2", "$308"), None),
        ("px", ("20", "$319"), None),
        ("pc", ("10", "$314"), None),
        ("mr", None, ("2", "$308")),
        ("both", ("2", "$308"), ("1", "$308")),
        ("neg", ("-1", "$308"), None),
    ],
)
def test_a_span_in_the_line_carries_its_margins(cls, left, right):
    (block,) = _blocks(f"<p>before <span class='{cls}'>inside</span> after</p>")
    assert _margins(block) == [(7, 6, left, right)]
    assert block["block_style"]["indent"] is None


def test_zero_and_auto_margins_are_none():
    (block,) = _blocks("<p>before <span class='zero'>inside</span> after</p>")
    assert _margins(block) == []


def test_a_span_split_by_bold_takes_each_margin_once():
    (block,) = _blocks(
        "<p>before <span class='both'>plain <b>bold</b> tail</span> after</p>"
    )
    # Left on "plain ", right on " tail"; "bold" has neither (P781 T01).
    assert _margins(block) == [
        (7, 6, ("2", "$308"), None),
        (17, 5, None, ("1", "$308")),
    ]


def test_nested_spans_margins_are_summed():
    (block,) = _blocks(
        "<p>before <span class='m2'><span class='m1'>nested</span></span> after</p>"
    )
    assert _margins(block) == [(7, 6, ("3", "$308"), None)]


def test_a_span_opening_the_paragraph_is_its_indent():
    (block,) = _blocks("<p><span class='m2'>starts</span> the paragraph</p>")
    assert block["block_style"]["indent"] == ("2", "$308")
    assert _margins(block) == []
    # Added to the paragraph's own (P781 S08), nested ones summed (S09).
    (block,) = _blocks("<p class='ti'><span class='m2'>starts</span> it</p>")
    assert block["block_style"]["indent"] == ("3", "$308")
    (block,) = _blocks(
        "<p><span class='m1'><span class='m1'>nested</span></span> spans</p>"
    )
    assert block["block_style"]["indent"] == ("2", "$308")
    # A heading too (S12).
    (block,) = _blocks("<h3><span class='m2'>heading</span> words</h3>")
    assert block["block_style"]["indent"] == ("2", "$308")


def test_an_indent_in_another_unit_keeps_the_run():
    (block,) = _blocks("<p class='tip'><span class='m2'>starts</span> it</p>")
    assert block["block_style"]["indent"] == ("5", "$314")
    assert _margins(block) == [(0, 6, ("2", "$308"), None)]


def test_a_span_of_its_own_size_keeps_the_run():
    # Its em is its own size's, not the paragraph's: as an indent, 2em at
    # twice the size would be half its width (unprobed in Previewer).
    (block,) = _blocks("<p><span class='bigm'>big</span> start</p>")
    assert block["block_style"]["indent"] is None
    assert _margins(block) == [(0, 3, ("2", "$308"), None)]


def test_a_list_item_keeps_the_run():
    (box,) = _blocks("<ul><li><span class='m2'>item</span> words</li></ul>")
    (item,) = box["blocks"]
    assert _margins(item) == [(0, 4, ("2", "$308"), None)]
    assert item["block_style"]["indent"] is None


def test_a_span_margin_and_bold_on_one_run_keep_both():
    (block,) = _blocks("<p>x <span class='bm2'>bold</span> y</p>")
    ((start, length, flags),) = block["spans"]
    assert FLAG_BOLD in flags and flag_value(flags, MARGIN_LEFT_FLAG) == ("2", "$308")


def _run_styles(tmp_path, spans):
    out = tmp_path / "t.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "C",
                "text": "x",
                "blocks": [{"text": "before inside after", "spans": spans}],
            }
        ],
        output_path=str(out),
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    runs = []
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            for r in e.get("$142") or []:
                runs.append(styles[str(r["$157"])])
    return runs


def _length(style, key):
    v = style[key]
    return (str(v["$307"]), str(v["$306"]))


def test_the_generator_writes_the_runs_margins(tmp_path):
    flags = frozenset(
        {(MARGIN_LEFT_FLAG, ("20", "$319")), (MARGIN_RIGHT_FLAG, ("2", "$308"))}
    )
    (style,) = _run_styles(tmp_path, [(7, 6, flags)])
    # A px as 0.45pt (P781 S04: 20px is 9pt).
    assert _length(style, "$48") == ("9", "$318")
    assert _length(style, "$50") == ("2", "$308")


def test_a_floated_spans_margins_are_left_alone():
    # Line numbers hung in the margin (QA's q786 H01): Previewer writes no
    # negative indent or run; the margin places the float.
    (block,) = _blocks("<p><span class='fl'>12</span>The line of verse.</p>")
    assert block["block_style"]["indent"] is None
    assert _margins(block) == []
    (block,) = _blocks("<p>Mid <span class='fl'>12</span> line.</p>")
    assert _margins(block) == []


def test_a_negative_margin_opening_the_paragraph_is_dropped():
    # QA's q786 G07: no indent and no run in Previewer's book.
    (block,) = _blocks("<p><span class='neg3'>out</span> words</p>")
    assert block["block_style"]["indent"] is None
    assert _margins(block) == []
    # G06: a hanging indent keeps its own -1em.
    (block,) = _blocks("<p class='hang'><span class='neg3'>out</span> words</p>")
    assert block["block_style"]["indent"] == ("-1", "$308")
    assert _margins(block) == []


def test_a_negative_margin_opening_the_paragraph_hangs_into_its_margin():
    # pg58169's footnotes: `.ln1 { margin-left: -1em }` opening a note set
    # 4em in; Previewer's 437 such notes hang their numbers.
    (block,) = _blocks("<p class='pfn4'><span class='neg1'>12</span> The note.</p>")
    assert block["block_style"]["indent"] == ("-1", "$308")
    assert _margins(block) == []
    # Past the margin it is dropped.
    (block,) = _blocks("<p class='pfn1'><span class='neg3'>12</span> x</p>")
    assert block["block_style"]["indent"] is None
    assert _margins(block) == []
