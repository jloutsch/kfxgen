"""縦中横 and emphasis marks in vertical text (#247).

Kindle Previewer 4.0.3 (probe P247, decoded):

- `text-combine-upright: all` (V02, "12" and "31"): each element is an
  inline container of its own in the paragraph's words, `$601: $283`, its
  text in a content string, its style `$560: $557`, `$707: $573`,
  `$708: $383`. In the paragraph it takes one offset, as an equation does,
  and its text a position per character.
- `text-emphasis-style: filled sesame` (V07): a run with `$717: $734`.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import css_text_emphasis
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

XHTML = "http://www.w3.org/1999/xhtml"
VERTICAL = f'{_conv._V_AUTHOR}="" {_conv._V_UA}=""'


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "kfx"),
    [
        ("filled sesame", "filled sesame"),
        ("sesame", "filled sesame"),
        ("open dot", "open dot"),
        ("dot open", "open dot"),
        ("filled", "filled"),
        ("open", "open"),
        ("double-circle", "filled double-circle"),
        ("none", "none"),
        (None, None),
        ('"*"', '"*"'),  # a mark of the book's own (QA's P748)
        ("'*'", '"*"'),
        ("dot circle", None),
    ],
)
def test_emphasis_styles_read_as_previewer_writes_them(value, kfx):
    assert css_text_emphasis(value) == kfx


def _book(tmp_path, body, css, mark=True):
    xhtml = f'<html xmlns="{XHTML}"><body>{body}</body></html>'
    root = etree.fromstring(xhtml.encode())
    if mark:
        for e in root.iter():
            e.set(_conv._V_AUTHOR, "")
            e.set(_conv._V_UA, "")
    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=_resolver(css), base_href="c.xhtml"
    )
    for b in blocks:
        b["writing_mode"] = "vertical-rl"
    out = tmp_path / "t.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "",
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": blocks,
            }
        ],
        output_path=str(out),
        rtl=True,
        language="ja",
        writing_mode="vertical-rl",
    )
    return blocks, load_fragments(out)


TCY = {"t": {"text-combine-upright": "all"}}
BODY = '<p>数字は<span class="t">12</span>月<span class="t">31</span>日。</p>'


@pytest.mark.unit
def test_tcy_takes_one_placeholder_in_the_paragraph(tmp_path):
    blocks, _frags = _book(tmp_path, BODY, TCY)
    assert blocks[0]["text"] == "数字は￼月￼日。"


@pytest.mark.unit
def test_tcy_is_an_inline_container_holding_its_text(tmp_path):
    _blocks, frags = _book(tmp_path, BODY, TCY)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    strings = {
        str(f.fid): [str(t) for t in val(f)["$146"]] for f in by_type(frags, "$145")
    }
    (story,) = by_type(frags, "$259")
    (entry,) = [e for e in val(story)["$146"] if "$146" in e]
    words = entry["$146"]
    assert [w for w in words if isinstance(w, str)] == ["数字は", "月", "日。"]
    boxes = [w for w in words if not isinstance(w, str)]
    assert [str(b["$601"]) for b in boxes] == ["$283", "$283"]
    assert [strings[str(b["$145"]["name"])][b["$145"]["$403"]] for b in boxes] == [
        "12",
        "31",
    ]
    style = styles[str(boxes[0]["$157"])]
    assert {k: str(v) for k, v in style.items() if k != "$173"} == {
        "$560": "$557",
        "$707": "$573",
        "$708": "$383",
    }


@pytest.mark.unit
def test_tcy_text_takes_a_position_per_character(tmp_path):
    # P247 V02's $265: the paragraph resumes one offset on, two positions on.
    _blocks, frags = _book(tmp_path, BODY, TCY)
    (story,) = by_type(frags, "$259")
    (entry,) = [e for e in val(story)["$146"] if "$146" in e]
    eid = int(entry["$155"])
    (pmap,) = by_type(frags, "$265")
    rows = [
        (int(r["$184"]), int(r["$185"]), int(r.get("$143", 0)))
        for r in val(pmap)
        if int(r["$185"]) == eid
        or int(r["$185"])
        in [int(b["$155"]) for b in entry["$146"] if not isinstance(b, str)]
    ]
    starts = [r for r in rows if r[1] == eid]
    first, box = starts[0][0], [r for r in rows if r[1] != eid][0][0]
    assert box == first + 3  # after 数字は
    # The text resumes one offset after the placeholder, two positions on.
    assert (box + 2, eid, 4) in rows


@pytest.mark.unit
def test_outside_a_vertical_file_tcy_is_left_as_text(tmp_path):
    blocks, _frags = _book(tmp_path, BODY, TCY, mark=False)
    assert blocks[0]["text"] == "数字は12月31日。"


@pytest.mark.unit
def test_emphasis_marks_are_written_on_their_run(tmp_path):
    # P247 V07.
    css = {"e": {"text-emphasis-style": "filled sesame"}}
    _blocks, frags = _book(
        tmp_path, '<p>ここに<span class="e">傍点</span>を打つ。</p>', css
    )
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (story,) = by_type(frags, "$259")
    runs = [
        (int(r["$143"]), int(r["$144"]), str(styles[str(r["$157"])].get("$717")))
        for e in val(story)["$146"]
        for r in e.get("$142") or ()
    ]
    assert (3, 2, "$734") in runs


@pytest.mark.unit
def test_a_style_inside_tcy_is_the_placeholders(tmp_path):
    # QA's P748 X06: `<span class="tcy"><b>56</b></span>`, a bold run over
    # the container, as Previewer writes it.
    css = dict(TCY, b={"font-weight": "bold"})
    blocks, frags = _book(
        tmp_path,
        '<p>数字は<span class="t"><span class="b">56</span></span>です。</p>',
        css,
    )
    assert blocks[0]["text"] == "数字は\ufffcです。"
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (story,) = by_type(frags, "$259")
    runs = [
        (int(r["$143"]), int(r["$144"]), str(styles[str(r["$157"])].get("$13")))
        for e in val(story)["$146"]
        for r in e.get("$142") or ()
    ]
    assert (3, 1, "$361") in runs


@pytest.mark.unit
def test_a_style_on_part_of_tcy_is_not_the_placeholders(tmp_path):
    css = dict(TCY, b={"font-weight": "bold"})
    _blocks, frags = _book(
        tmp_path,
        '<p>数字は<span class="t"><span class="b">5</span>6</span>です。</p>',
        css,
    )
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (story,) = by_type(frags, "$259")
    bold = [
        r
        for e in val(story)["$146"]
        for r in e.get("$142") or ()
        if str(styles[str(r["$157"])].get("$13")) == "$361"
    ]
    assert bold == []


@pytest.mark.unit
def test_a_mark_of_the_books_own_is_written_as_it(tmp_path):
    # QA's P748: Previewer writes $717 "*".
    css = {"e": {"text-emphasis-style": "'*'"}}
    _blocks, frags = _book(tmp_path, '<p>ここに<span class="e">傍点</span>。</p>', css)
    marks = [
        str(val(f).get("$717")) for f in by_type(frags, "$157") if "$717" in val(f)
    ]
    assert marks == ["*"]


@pytest.mark.unit
def test_a_plain_span_inside_tcy_adds_no_run(tmp_path):
    # QA's P748 X06's second: `<span class="tcy">7<span>8</span></span>`.
    _blocks, frags = _book(
        tmp_path, '<p>数字は<span class="t">7<span>8</span></span>です。</p>', TCY
    )
    (story,) = by_type(frags, "$259")
    assert [r for e in val(story)["$146"] for r in e.get("$142") or ()] == []


@pytest.mark.unit
def test_an_inner_style_that_undoes_the_outer_one_is_the_placeholders(tmp_path):
    # QA's P748d D1: `<b>太字<tcy><span class="nb">12</span></tcy>と</b>`:
    # Previewer writes the container $13 $350: not bold.
    css = dict(TCY, bb={"font-weight": "bold"}, nb={"font-weight": "normal"})
    _blocks, frags = _book(
        tmp_path,
        '<p>前<span class="bb">太字<span class="t"><span class="nb">12</span></span>'
        "と</span>後</p>",
        css,
    )
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (story,) = by_type(frags, "$259")
    weights = {
        int(r["$143"]): str(styles[str(r["$157"])].get("$13"))
        for e in val(story)["$146"]
        for r in e.get("$142") or ()
    }
    # Not bold: kfxgen leaves normal weight unwritten where Previewer
    # writes $350, as it does without the 縦中横; it draws the same.
    assert weights.get(1) == "$361" and weights.get(3) in (None, "$350")
