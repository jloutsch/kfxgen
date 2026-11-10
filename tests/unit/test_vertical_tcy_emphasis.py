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
        ('"*"', None),  # a mark of the book's own
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
