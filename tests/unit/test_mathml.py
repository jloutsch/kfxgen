"""MathML is written as text a reader can follow (#235).

Kindle Previewer 4 typesets each equation as a drawing (`yj_mathml`).
kfxgen doesn't draw yet, and writes the text instead: exponents
and subscripts as raised and lowered runs, operators spaced, fractions as
a/b and roots as √x, with parentheses around anything longer than a single
token."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import FLAG_BOLD, FLAG_ITALIC, FLAG_SUB, FLAG_SUPER

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'
_SHOWN = {FLAG_SUPER: "^", FLAG_SUB: "_", FLAG_ITALIC: "i", FLAG_BOLD: "b"}


def _blocks(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )


def _runs(block):
    """The block's text cut into runs, each tagged with its shift and emphasis:
    [("r", "i"), ("2", "^")]. Untagged text is tagged ""."""
    text = block["text"]
    tags = [""] * len(text)
    for start, length, flags in block.get("spans") or []:
        mark = "".join(_SHOWN[f] for f in sorted(_SHOWN) if f in flags)
        for i in range(start, start + length):
            tags[i] += mark
    runs = []
    for ch, tag in zip(text, tags):
        tag = "".join(sorted(set(tag)))
        if runs and runs[-1][1] == tag:
            runs[-1] = (runs[-1][0] + ch, tag)
        else:
            runs.append((ch, tag))
    return runs


def _math(inner, **attrs):
    extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
    (block,) = _blocks(f"<p>A <math {M}{extra}>{inner}</math> B</p>")
    return block


def test_an_exponent_is_raised():
    block = _math("<msup><mi>r</mi><mn>2</mn></msup>")
    assert block["text"] == "A r2 B"
    assert _runs(block) == [("A ", ""), ("r", "i"), ("2", "^"), (" B", "")]


def test_a_subscript_is_lowered():
    block = _math("<msub><mi>x</mi><mi>i</mi></msub>")
    assert _runs(block) == [("A ", ""), ("x", "i"), ("i", "_i"), (" B", "")]


def test_a_subscript_and_an_exponent_together():
    block = _math("<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>")
    assert _runs(block) == [("A ", ""), ("x", "i"), ("i", "_i"), ("2", "^"), (" B", "")]


def test_a_simple_fraction_reads_a_slash_b():
    assert _math("<mfrac><mn>1</mn><mn>2</mn></mfrac>")["text"] == "A 1/2 B"


def test_a_longer_numerator_is_in_parentheses():
    block = _math(
        "<mfrac><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow><mi>c</mi></mfrac>"
    )
    assert block["text"] == "A (a + b)/c B"


def test_a_square_root_of_a_token():
    assert _math("<msqrt><mi>x</mi></msqrt>")["text"] == "A √x B"


def test_a_square_root_of_a_sum_is_in_parentheses():
    assert (
        _math("<msqrt><mi>x</mi><mo>+</mo><mn>1</mn></msqrt>")["text"] == "A √(x + 1) B"
    )


def test_a_root_index_is_raised_before_the_sign():
    block = _math("<mroot><mi>x</mi><mn>3</mn></mroot>")
    assert _runs(block) == [("A ", ""), ("3", "^"), ("√", ""), ("x", "i"), (" B", "")]


def test_operators_are_spaced():
    block = _math("<mi>a</mi><mo>=</mo><mi>b</mi><mo>&#xD7;</mo><mi>c</mi>")
    assert block["text"] == "A a = b × c B"


def test_a_leading_minus_is_a_sign_not_an_operator():
    assert _math("<mo>-</mo><mi>b</mi><mo>+</mo><mn>1</mn>")["text"] == "A −b + 1 B"


def test_operators_inside_a_script_are_not_spaced():
    block = _math(
        "<munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>"
        "<mi>n</mi></munderover><mi>i</mi>"
    )
    assert _runs(block) == [
        ("A ∑", ""),
        ("i", "_i"),
        ("=1", "_"),
        ("n", "^i"),
        ("i", "i"),
        (" B", ""),
    ]


def test_a_word_identifier_is_upright():
    block = _math("<mi>sin</mi><mi>x</mi>")
    assert _runs(block) == [("A sin", ""), ("x", "i"), (" B", "")]


def test_mathvariant_is_kept():
    block = _math('<mi mathvariant="normal">d</mi><mi mathvariant="bold">v</mi>')
    assert _runs(block) == [("A d", ""), ("v", "b"), (" B", "")]


def test_an_accent_over_one_letter_combines_with_it():
    assert _math("<mover><mi>x</mi><mo>&#xAF;</mo></mover>")["text"] == "A x\u0304 B"


def test_a_matrix_reads_row_by_row():
    block = _math(
        "<mrow><mo>(</mo><mtable><mtr><mtd><mn>1</mn></mtd><mtd><mn>2</mn></mtd></mtr>"
        "<mtr><mtd><mn>3</mn></mtd><mtd><mn>4</mn></mtd></mtr></mtable><mo>)</mo></mrow>"
    )
    assert block["text"] == "A (1, 2; 3, 4) B"


def test_mfenced_writes_its_fences_and_separators():
    assert _math("<mfenced><mi>a</mi><mi>b</mi></mfenced>")["text"] == "A (a, b) B"


def test_text_and_space():
    block = _math(
        "<mtext>if</mtext><mspace width='1em'/><mi>x</mi><mo>&gt;</mo><mn>0</mn>"
    )
    assert block["text"] == "A if x > 0 B"


def test_a_tex_annotation_is_not_text():
    block = _math(
        "<semantics><msup><mi>z</mi><mn>2</mn></msup>"
        '<annotation encoding="application/x-tex">z^2</annotation></semantics>'
    )
    assert block["text"] == "A z2 B"


def test_source_whitespace_between_elements_is_not_text():
    block = _math(
        "\n  <mrow>\n    <mi>a</mi>\n    <mo>+</mo>\n    <mi>b</mi>\n  </mrow>\n"
    )
    assert block["text"] == "A a + b B"


def test_a_prefixed_math_element_is_read():
    (block,) = _blocks(
        '<p>A <m:math xmlns:m="http://www.w3.org/1998/Math/MathML">'
        "<m:msup><m:mi>y</m:mi><m:mn>3</m:mn></m:msup></m:math> B</p>"
    )
    assert _runs(block) == [("A ", ""), ("y", "i"), ("3", "^"), (" B", "")]


def test_alttext_stands_in_for_an_empty_equation():
    assert _math("", alttext="E = mc^2")["text"] == "A E = mc^2 B"


def test_the_quadratic_formula():
    block = _math(
        "<mi>x</mi><mo>=</mo><mfrac><mrow><mo>-</mo><mi>b</mi><mo>&#xB1;</mo><msqrt><msup>"
        "<mi>b</mi><mn>2</mn></msup><mo>-</mo><mn>4</mn><mi>a</mi><mi>c</mi></msqrt></mrow>"
        "<mrow><mn>2</mn><mi>a</mi></mrow></mfrac>",
        display="block",
    )
    assert block["text"] == "A x = (−b ± √(b2 − 4ac))/(2a) B"


def test_an_id_inside_an_equation_stays_an_anchor():
    xhtml = (
        f'<p>A <math {M}><mi id="v">v</mi></math> B</p>'
        '<p><a href="c.xhtml#v">to v</a></p>'
    )
    (block, _link) = _blocks(xhtml)
    assert block["anchor_offsets"]["c.xhtml#v"] == 2


def test_the_tail_after_an_equation_keeps_the_paragraphs_flags():
    (block,) = _blocks(f"<p><i>A <math {M}><mn>2</mn></math> B</i></p>")
    assert _runs(block) == [("A 2 B", "i")]


@pytest.mark.parametrize("op", ["+", "=", "&#x2264;", "&#x2192;"])
def test_each_operator_gets_spaces(op):
    text = _math(f"<mi>a</mi><mo>{op}</mo><mi>b</mi>")["text"]
    assert (
        text.startswith("A a ")
        and text.endswith(" b B")
        and len(text) == len("A a + b B")
    )


@pytest.mark.parametrize("display", ["", ' display="block"'])
def test_an_equation_straight_in_the_body_is_its_own_paragraph(display):
    blocks = _blocks(
        f"<p>before</p><math {M}{display}><mi>x</mi><mo>=</mo>"
        "<msup><mi>b</mi><mn>2</mn></msup></math><p>after</p>"
    )
    assert [b["text"] for b in blocks] == ["before", "x = b2", "after"]
    assert _runs(blocks[1]) == [("x", "i"), (" = ", ""), ("b", "i"), ("2", "^")]


def test_a_display_equation_is_centred():
    blocks = _blocks(f'<p>before</p><math {M} display="block"><mi>x</mi></math>')
    assert (blocks[1]["block_style"] or {}).get("align") == "center"


def test_an_inline_equation_on_its_own_is_not_centred():
    blocks = _blocks(f"<p>before</p><math {M}><mi>x</mi></math>")
    assert (blocks[1]["block_style"] or {}).get("align") is None


def test_spaces_inside_a_token_are_trimmed():
    block = _math("<msup><mi>\n  r\n</mi><mn> 2 </mn></msup>")
    assert _runs(block) == [("A ", ""), ("r", "i"), ("2", "^"), (" B", "")]


def test_a_comma_is_followed_by_a_space():
    block = _math("<mi>f</mi><mo>(</mo><mi>x</mi><mo>,</mo><mi>y</mi><mo>)</mo>")
    assert block["text"] == "A f(x, y) B"


def test_a_sign_after_an_opening_parenthesis_is_not_spaced():
    assert _math("<mo>(</mo><mo>-</mo><mi>x</mi><mo>)</mo>")["text"] == "A (−x) B"


def test_an_exponent_over_a_number_needs_no_parentheses():
    block = _math("<mfrac><msup><mi>x</mi><mn>2</mn></msup><mn>2</mn></mfrac>")
    assert block["text"] == "A x2/2 B"


def test_an_xml_annotation_is_not_text():
    block = _math(
        "<semantics><mi>z</mi>"
        '<annotation-xml encoding="MathML-Presentation"><mi>q</mi></annotation-xml></semantics>'
    )
    assert block["text"] == "A z B"


def test_a_phantom_is_not_text():
    assert _math("<mi>a</mi><mphantom><mi>b</mi></mphantom>")["text"] == "A a B"


@pytest.mark.parametrize(
    ("inner", "text"),
    [
        ("x+1", "A x+1 B"),
        ("<mrow>y<mo>=</mo>2</mrow>", "A y=2 B"),
        (
            '<mrow><mi>x</mi></mrow><span xmlns="http://www.w3.org/1999/xhtml">gloss</span>',
            "A xgloss B",
        ),
        ("<semantics>z<annotation>z^2</annotation></semantics>", "A z B"),
    ],
)
def test_text_outside_token_elements_is_kept_as_plain_text(inner, text):
    """Invalid MathML, but main printed this text (#401 QA)."""
    assert _math(inner)["text"] == text


@pytest.mark.parametrize("invisible", ["&#x2061;", "&#x2062;", "&#x2063;", "&#x2064;"])
def test_an_invisible_operator_is_not_written(invisible):
    """Function application, invisible times, separator and plus have no glyph,
    and a Kindle font may draw one as a box (#401 QA)."""
    assert _math(f"<mi>f</mi><mo>{invisible}</mo><mi>x</mi>")["text"] == "A fx B"


def test_text_after_a_child_alone_is_kept():
    assert _math("<mrow><mi>y</mi>2</mrow>")["text"] == "A y2 B"


def test_an_invisible_operator_is_dropped_from_plain_text_too():
    assert _math("x<mo>&#x2062;</mo>y")["text"] == "A xy B"


def test_content_mathml_in_an_annotation_leaves_the_equation_structured():
    """LaTeXML writes a Content MathML annotation by default (#401 re-check)."""
    block = _math(
        "<semantics><mrow><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo><mn>1</mn></mrow>"
        '<annotation-xml encoding="MathML-Content"><apply><plus/><apply><power/>'
        "<ci>x</ci><cn>2</cn></apply><cn>1</cn></apply></annotation-xml></semantics>"
    )
    assert _runs(block) == [("A ", ""), ("x", "i"), ("2", "^"), (" + 1 B", "")]


@pytest.mark.parametrize(
    ("inner", "text"),
    [
        ("<mi>sin</mi><mo>&#x2061;</mo><mi>x</mi>", "A sin x B"),
        ("<mi>f</mi><mo>&#x2061;</mo><mi>x</mi>", "A fx B"),
        ("<mi>sin</mi><mo>&#x2061;</mo><mo>(</mo><mi>x</mi><mo>)</mo>", "A sin(x) B"),
    ],
)
def test_a_function_name_is_spaced_from_its_argument(inner, text):
    """With U+2061 dropped, a name longer than one letter keeps a space before
    an argument that has no bracket (#401 re-check)."""
    assert _math(inner)["text"] == text


@pytest.mark.parametrize(
    ("inner", "runs"),
    [
        (
            "<mi>sin</mi><mo>&#x2061;</mo><mrow><mo>(</mo><mi>x</mi><mo>)</mo></mrow>",
            [("A sin(", ""), ("x", "i"), (") B", "")],
        ),
        (
            "<mi>sin</mi><mo>&#x2061;</mo><mfenced><mi>x</mi></mfenced>",
            [("A sin(", ""), ("x", "i"), (") B", "")],
        ),
        (
            "<msup><mi>sin</mi><mn>2</mn></msup><mo>&#x2061;</mo><mi>x</mi>",
            [("A sin", ""), ("2", "^"), (" ", ""), ("x", "i"), (" B", "")],
        ),
        (
            "<msub><mi>log</mi><mn>2</mn></msub><mo>&#x2061;</mo><mi>x</mi>",
            [("A log", ""), ("2", "_"), (" ", ""), ("x", "i"), (" B", "")],
        ),
        (
            "<msup><mi>f</mi><mn>2</mn></msup><mo>&#x2061;</mo><mi>x</mi>",
            [("A ", ""), ("f", "i"), ("2", "^"), ("x", "i"), (" B", "")],
        ),
    ],
)
def test_a_function_name_with_a_script_or_a_bracketed_row(inner, runs):
    """LaTeXML writes the bracket inside an <mrow>; sin²x and log₂x keep the
    space after the name's script (#401 re-check)."""
    assert _runs(_math(inner)) == runs


def test_prescripts_come_before_the_base():
    """An isotope, ¹⁴₆C: prescripts are written first, lowered then raised (#402)."""
    block = _math(
        "<mmultiscripts><mi>C</mi><mprescripts/><mn>6</mn><mn>14</mn></mmultiscripts>"
    )
    assert _runs(block) == [("A ", ""), ("6", "_"), ("14", "^"), ("C", "i"), (" B", "")]


def test_postscripts_follow_the_base_and_none_is_empty():
    block = _math(
        "<mmultiscripts><mi>R</mi><mi>i</mi><none/><none/><mi>j</mi></mmultiscripts>"
    )
    assert _runs(block) == [
        ("A ", ""),
        ("R", "i"),
        ("i", "_i"),
        ("j", "^i"),
        (" B", ""),
    ]


def test_a_string_literal_keeps_its_quotation_marks():
    assert _math("<ms>abc</ms>")["text"] == 'A "abc" B'


def test_a_string_literal_with_its_own_quotes():
    assert _math('<ms lquote="«" rquote="»">abc</ms>')["text"] == "A «abc» B"


FRAC_HEADING = f"<h1>The <math {M}><mfrac><mi>a</mi><mi>b</mi></mfrac><mo>=</mo><mn>1</mn></math> rule</h1>"


def _chapter_texts(chapters, tmp_path):
    from kfxgen.native_generator import NativeKFXGenerator
    from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

    out = tmp_path / "m.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(out)
    )
    frags = load_fragments(out)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if e.get("$145"):
                ref = e["$145"]
                found[str(texts[str(ref["name"])][ref["$403"]])] = len(
                    e.get("$142") or []
                )
    return found


def test_a_heading_with_maths_prints_once_under_a_flattened_label(tmp_path):
    """A label written from the heading's plain text, "The ab=1 rule", names
    the heading "The a/b = 1 rule", which prints once, with its runs (#402)."""
    blocks = _blocks(FRAC_HEADING + "<p>Body.</p>")
    found = _chapter_texts(
        [{"title": "The ab=1 rule", "text": "x", "blocks": blocks}], tmp_path
    )
    assert list(found) == ["The a/b = 1 rule", "Body."]
    assert found["The a/b = 1 rule"] > 0  # the italic a and b


def test_a_chapter_of_only_a_maths_heading_keeps_its_runs(tmp_path):
    blocks = _blocks(FRAC_HEADING)
    found = _chapter_texts(
        [
            {"title": "The ab=1 rule", "text": "The a/b = 1 rule", "blocks": blocks},
            {"title": "Two", "text": "y", "blocks": _blocks("<p>Body.</p>")},
        ],
        tmp_path,
    )
    assert "The ab=1 rule" not in found
    assert found["The a/b = 1 rule"] > 0


def test_a_different_label_still_prints_above_the_heading(tmp_path):
    blocks = _blocks(FRAC_HEADING + "<p>Body.</p>")
    found = _chapter_texts(
        [{"title": "Another label", "text": "x", "blocks": blocks}], tmp_path
    )
    assert list(found) == ["Another label", "The a/b = 1 rule", "Body."]
