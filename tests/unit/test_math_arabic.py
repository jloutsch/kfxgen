"""Arabic in MathML, and right-to-left equations (#708).

Kindle Previewer 4.0.3, probe P708 (2026-11-06):

- A character the maths font has no glyph for (STIX Two Math has no Arabic)
  is text inside the drawing, drawn by the Kindle's own font: a `$269`
  child of the shape, `$16` 16, placed by a `$250` entry `{$474: its id,
  $98: matrix, $159: $270}` scaled 51.978. Each takes one position after
  the shape, in `$265` and `$264`. kfxgen drew a missing-glyph box.
- `<math dir="rtl">` is laid out left to right with each row's children
  reversed (`<math>`, `<mrow>`, a table row's cells), a script written
  before its base (`msup` as `mmultiscripts` with prescripts) and a square
  root as `menclose notation="top right"`. A token's text keeps its order
  (`<mn>١٢</mn>`), and a fraction's or a sum's parts keep theirs.

Where kfxgen differs on purpose:
- A word of missing characters is one text, so the Kindle joins its
  letters; Previewer writes each letter alone, in logical order left to
  right (`دالة` as د ا ل ة), which reads backwards and unjoined.
- Every bracket and comparison is mirrored; Previewer mirrors ( ) { } < >
  but not [ ] ⟨ ⟩ ≤, so `[x]` read `]x[` and ≤ turned round in meaning.
- A square root keeps all its children; Previewer kept only the first.
"""

import pytest
from lxml import etree

from kfxgen import math_draw
from kfxgen.math_draw import MathText
from tests._kfx_introspect import by_type, val
from tests.unit.test_mathml_native import _book, _chapter, _entries

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def _rtl(body):
    return math_draw._right_to_left(
        etree.fromstring(f"<math {M} dir='rtl'>{body}</math>")
    )


def _shape_of(m):
    """`m` as (tag, text, children) for comparing."""
    tag = etree.QName(m).localname
    kids = [_shape_of(c) for c in m]
    return (
        (tag, (m.text or "").strip(), kids) if kids else (tag, (m.text or "").strip())
    )


def _texts(math):
    drawing = math_draw.draw(etree.fromstring(math))
    return [p.text for p in drawing.paths if isinstance(p, MathText)]


# -- right to left, as Previewer writes it ------------------------------------


@pytest.mark.unit
def test_a_row_is_reversed_and_its_tokens_keep_their_text():
    # M02: <mn>١٢</mn> stays ١٢.
    m = _rtl("<mn>١٢</mn><mo>+</mo><mn>٣</mn><mo>=</mo><mn>١٥</mn>")
    assert [c.text for c in m] == ["١٥", "=", "٣", "+", "١٢"]
    assert m.get("dir") == "ltr"


@pytest.mark.unit
def test_a_fraction_and_a_sum_keep_their_parts_in_order():
    # M03, M05: the parts in order, a row inside them reversed.
    m = _rtl("<mfrac><mi>س</mi><mn>٢</mn></mfrac><mo>+</mo><mi>ص</mi>")
    assert _shape_of(m)[2][2] == ("mfrac", "", [("mi", "س"), ("mn", "٢")])
    m = _rtl(
        "<munderover><mo>∑</mo><mrow><mi>ن</mi><mo>=</mo><mn>١</mn></mrow>"
        "<mi>م</mi></munderover><mi>ن</mi>"
    )
    under = _shape_of(m)[2][1]
    assert under == (
        "munderover",
        "",
        [
            ("mo", "∑"),
            ("mrow", "", [("mn", "١"), ("mo", "="), ("mi", "ن")]),
            ("mi", "م"),
        ],
    )


@pytest.mark.unit
def test_a_script_goes_before_its_base_and_a_root_opens_on_the_left():
    # M11: msup as prescripts; msqrt as menclose "top right", keeping all
    # its children (Previewer kept only the first).
    m = _rtl(
        "<msqrt><mi>س</mi><mo>+</mo><mn>١</mn></msqrt><mo>−</mo>"
        "<msup><mi>ص</mi><mn>٢</mn></msup>"
    )
    assert _shape_of(m)[2] == [
        (
            "mmultiscripts",
            "",
            [("mi", "ص"), ("mprescripts", ""), ("none", ""), ("mn", "٢")],
        ),
        ("mo", "−"),
        ("menclose", "", [("mn", "١"), ("mo", "+"), ("mi", "س")]),
    ]
    assert m[2].get("notation") == "top right"


@pytest.mark.unit
def test_both_scripts_go_before_the_base():
    # QA on #730: msubsup too, its subscript and superscript prescripts.
    m = _rtl("<msubsup><mi>س</mi><mn>١</mn><mn>٢</mn></msubsup>")
    assert _shape_of(m)[2] == [
        (
            "mmultiscripts",
            "",
            [("mi", "س"), ("mprescripts", ""), ("mn", "١"), ("mn", "٢")],
        )
    ]


@pytest.mark.unit
def test_a_table_rows_cells_are_reversed():
    # M12.
    m = _rtl("<mtable><mtr><mtd><mi>س</mi></mtd><mtd><mn>١</mn></mtd></mtr></mtable>")
    (row,) = m[0]
    assert [c[0].text for c in row] == ["١", "س"]


@pytest.mark.unit
def test_brackets_and_comparisons_are_mirrored():
    # M10. Previewer mirrors ( ) { } < > only.
    m = _rtl(
        "<mi>س</mi><mo>&lt;</mo><mi>ص</mi><mo>≤</mo><mo>[</mo><mi>ع</mi><mo>]</mo>"
        "<mo>{</mo><mn>١</mn><mo>}</mo><mo>⟨</mo><mi>ل</mi><mo>⟩</mo><mo>→</mo><mi>م</mi>"
    )
    assert "".join(c.text for c in m) == "م→⟨ل⟩{١}[ع]≥ص>س"


@pytest.mark.unit
def test_a_left_to_right_equation_is_left_as_it_is():
    src = f"<math {M}><mi>x</mi><mo>(</mo><mi>y</mi><mo>)</mo></math>"
    assert "dir" not in math_draw._source(etree.fromstring(src))
    assert "(" in math_draw._source(etree.fromstring(src)).split("y")[0]


# -- characters the maths font lacks -----------------------------------------


@pytest.mark.unit
def test_arabic_letters_and_digits_are_text_in_the_drawing():
    texts = _texts(f"<math {M} dir='rtl'><mi>س</mi><mo>+</mo><mn>١٢</mn></math>")
    assert texts == ["١٢", "س"]


@pytest.mark.unit
def test_a_word_is_one_text_and_neighbours_apart_are_two():
    # M04's <mtext>دالة</mtext> is one word; M12's two cells are two.
    assert _texts(f"<math {M}><mtext>دالة</mtext></math>") == ["دالة"]
    # A space between two words: two texts.
    assert _texts(f"<math {M}><mtext>دالة ثابتة</mtext></math>") == ["دالة", "ثابتة"]
    # Two variables side by side are two, not one word (QA on #730).
    assert _texts(f"<math {M}><mi>س</mi><mi>ص</mi><mo>+</mo><mi>سص</mi></math>") == [
        "س",
        "ص",
        "سص",
    ]
    assert _texts(
        f"<math {M}><mtable><mtr><mtd><mn>١</mn></mtd><mtd><mi>س</mi></mtd></mtr>"
        "</mtable></math>"
    ) == ["١", "س"]


@pytest.mark.unit
def test_text_is_placed_on_the_baseline_at_previewers_scale():
    drawing = math_draw.draw(
        etree.fromstring(f"<math {M}><mi>س</mi><mo>+</mo><mi>x</mi></math>")
    )
    (text,) = [p for p in drawing.paths if isinstance(p, MathText)]
    glyph = [p for p in drawing.paths if not isinstance(p, MathText)][0]
    k, _b, _c, d, _x, y = text.matrix
    # Previewer's 51.978 at the main size; a glyph's baseline is the same.
    assert (k, d) == (51.978, 51.978)
    assert y == glyph[2][5]


@pytest.mark.unit
def test_a_script_is_text_at_its_own_smaller_size():
    drawing = math_draw.draw(
        etree.fromstring(f"<math {M}><msup><mi>س</mi><mn>٢</mn></msup></math>")
    )
    sizes = [p.matrix[0] for p in drawing.paths if isinstance(p, MathText)]
    assert sizes[0] == 51.978 and sizes[1] < sizes[0]


@pytest.mark.unit
def test_latin_maths_has_no_text():
    assert _texts(f"<math {M}><mi>x</mi><mo>+</mo><mn>12</mn></math>") == []


# -- written into the book ---------------------------------------------------

WORD = f"<math {M} dir='rtl'><mi>ق</mi><mo>=</mo><mtext>دالة</mtext></math>"


def _shape(frags):
    (shape,) = [e for e in _entries(frags) if str(e.get("$159")) == "$272"]
    return shape


@pytest.mark.unit
def test_each_text_is_a_child_of_the_shape_placed_by_its_id(tmp_path):
    frags = _book(tmp_path, [_chapter(f"<p>P01 {WORD} after.</p>")])
    shape = _shape(frags)
    kids = list(shape["$146"])
    texts = {
        str(f.fid): [str(x) for x in val(f)["$146"]] for f in by_type(frags, "$145")
    }
    words = [texts[str(k["$145"]["name"])][int(k["$145"]["$403"])] for k in kids]
    assert words == ["دالة", "ق"]
    assert all(int(k["$16"]) == 16 and str(k["$159"]) == "$269" for k in kids)
    placed = [p for p in shape["$250"] if "$474" in p]
    assert [int(p["$474"]) for p in placed] == [int(k["$155"]) for k in kids]
    assert all(str(p["$159"]) == "$270" for p in placed)


@pytest.mark.unit
def test_a_text_takes_a_position_for_each_character(tmp_path):
    # kfxlib counts a text child's content: دالة four positions, ق one.
    # One each, as for a glyph, made every later position one off.
    frags = _book(tmp_path, [_chapter(f"<p>P01 {WORD} after.</p>")])
    kids = list(_shape(frags)["$146"])
    (pmap,) = by_type(frags, "$265")
    rows = [(int(r["$184"]), int(r["$185"])) for r in val(pmap)]
    starts = {eid: off for off, eid in rows}
    after = {eid: rows[i + 1][0] for i, (_o, eid) in enumerate(rows[:-1])}
    widths = [after[int(k["$155"])] - starts[int(k["$155"])] for k in kids]
    assert widths == [4, 1]
    (sections,) = by_type(frags, "$264")
    listed = {int(x) for s in val(sections) for x in s["$181"]}
    assert {int(k["$155"]) for k in kids} <= listed


@pytest.mark.unit
def test_the_paragraph_resumes_after_each_equation_at_its_own_offset(tmp_path):
    # QA on #730: with a text one position wide in the count, every later
    # part of the paragraph started too early.
    frags = _book(tmp_path, [_chapter(f"<p>P01 {WORD} mid {WORD} end.</p>")])
    (pmap,) = by_type(frags, "$265")
    rows = [(int(r["$184"]), int(r["$185"]), r.get("$143")) for r in val(pmap)]
    (para,) = {eid for _o, eid, start in rows if start is not None}
    parts = [(off, int(start)) for off, eid, start in rows if eid == para and start]
    # Where the paragraph reaches each equation and resumes after it,
    # counted from its first position: "P01 " is 4; an equation is 3 ids
    # and texts of 4 and 1, 8 positions; " mid " is 5.
    first = next(off for off, eid, start in rows if eid == para)
    assert [(off - first, start) for off, start in parts] == [
        (4, 4),
        (12, 5),
        (17, 10),
        (25, 11),
    ]
