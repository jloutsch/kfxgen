"""MathML equations drawn, as Kindle Previewer 4 draws them (#235).

An equation takes one character in its paragraph, a placeholder carrying
its drawing. The paragraph is written as Previewer writes it: its text and
the equation in one list, the equation a container holding a container
holding one shape of glyph paths, which point into the book's path
bundle. When an equation can't be drawn, it is written as #401's text."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import math_of
from kfxgen.kfxlib_minimal.ion import IonDecimal
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'
R2 = f"<math {M}><msup><mi>r</mi><mn>2</mn></msup></math>"
HALF = f"<math {M}><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"
QUAD = (
    f"<math {M}><mi>x</mi><mo>=</mo><mfrac><mrow><mo>-</mo><mi>b</mi><mo>±</mo>"
    "<msqrt><msup><mi>b</mi><mn>2</mn></msup><mo>-</mo><mn>4</mn><mi>a</mi>"
    "<mi>c</mi></msqrt></mrow><mrow><mn>2</mn><mi>a</mi></mrow></mfrac></math>"
)
PLACEHOLDER = "\ufffc"


def _blocks(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )


def _maths(block):
    """(offset, math) for each drawn equation in the block."""
    return [
        (start, math_of(flags))
        for start, _length, flags in block.get("spans") or ()
        if math_of(flags)
    ]


def _book(tmp_path, chapters):
    from kfxgen.native_generator import NativeKFXGenerator

    out = tmp_path / "m.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(out)
    )
    return load_fragments(out)


def _chapter(body, title="One"):
    return {"title": title, "text": "x", "blocks": _blocks(body)}


def _entries(frags):
    return [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get")
    ]


def _mixed(frags):
    """The $146 list of each paragraph holding an equation."""
    return [
        list(e["$146"])
        for e in _entries(frags)
        if str(e.get("$159")) == "$269" and isinstance(e.get("$146"), list)
    ]


def _shape(container):
    (inner,) = container["$146"]
    (shape,) = inner["$146"]
    return inner, shape


# -- converter ---------------------------------------------------------------


@pytest.mark.unit
def test_an_equation_is_one_placeholder_carrying_its_drawing():
    (block,) = _blocks(f"<p>Area {R2} here</p>")
    assert block["text"] == f"Area {PLACEHOLDER} here"
    ((offset, (_serial, drawing, alt, source)),) = _maths(block)
    assert offset == 5
    assert drawing.width > 0 and len(drawing.paths) == 2
    assert alt == "r2"
    assert etree.fromstring(source).tag == "{http://www.w3.org/1998/Math/MathML}math"


@pytest.mark.unit
def test_alttext_names_the_equation():
    (block,) = _blocks(
        f'<p><math {M} alttext="r squared"><msup><mi>r</mi><mn>2</mn></msup></math></p>'
    )
    ((_offset, (_serial, _drawing, alt, _source)),) = _maths(block)
    assert alt == "r squared"


@pytest.mark.unit
def test_the_source_is_the_equation_alone():
    (block,) = _blocks(f"<p>A {R2} tail text</p>")
    ((_offset, (_serial, _drawing, _alt, source)),) = _maths(block)
    assert source.endswith("</math>") and "tail" not in source


@pytest.mark.unit
def test_an_equation_that_cant_be_drawn_is_text(monkeypatch):
    monkeypatch.setattr(_conv, "draw_math", lambda _math: None)
    (block,) = _blocks(f"<p>Area {R2} here</p>")
    assert block["text"] == "Area r2 here"
    assert _maths(block) == []


@pytest.mark.unit
def test_text_outside_the_tokens_is_text_not_a_drawing():
    """Invalid MathML stays #401's plain text."""
    (block,) = _blocks(f"<p><math {M}>stray <mi>x</mi></math></p>")
    assert PLACEHOLDER not in block["text"] and "stray" in block["text"]


@pytest.mark.unit
def test_an_equation_too_deep_to_draw_is_text():
    """ziamath's work doubles with each row nested in a row: a 60-deep
    continued fraction is written as #401's text, not drawn."""
    deep = "<mn>1</mn>"
    for _ in range(60):
        deep = f"<mrow><mn>1</mn><mo>+</mo><mfrac><mn>1</mn>{deep}</mfrac></mrow>"
    (block,) = _blocks(f"<p>Deep <math {M}>{deep}</math></p>")
    assert block["text"].startswith("Deep 1 + 1/(1 + 1/(")
    assert _maths(block) == []


@pytest.mark.unit
def test_a_display_equation_is_a_centred_paragraph_of_its_drawing():
    blocks = _blocks(
        f'<p>Before.</p><math {M} display="block">{R2[len(f"<math {M}>") :]}<p>After.</p>'
    )
    assert [b["text"] for b in blocks] == ["Before.", PLACEHOLDER, "After."]
    assert blocks[1]["block_style"]["align"] == "center"
    assert len(_maths(blocks[1])) == 1


@pytest.mark.unit
def test_an_id_inside_an_equation_is_kept():
    (block,) = _blocks(f'<p>A <math {M} id="eq1"><mi>x</mi></math></p>')
    assert any("eq1" in k for k in block.get("anchor_keys") or []) or any(
        "eq1" in k for k in (block.get("anchor_offsets") or {})
    )


# -- generator ---------------------------------------------------------------


@pytest.mark.unit
def test_a_paragraph_holds_its_text_and_the_equation(tmp_path):
    frags = _book(tmp_path, [_chapter(f"<p>Area {R2} here</p>")])
    (mixed,) = _mixed(frags)
    assert [str(x) for x in mixed if isinstance(x, str)] == ["Area ", " here"]
    (container,) = [x for x in mixed if not isinstance(x, str)]
    assert str(container["$159"]) == "$270"
    assert str(container["$615"]) == "$688" and str(container["$601"]) == "$283"
    inner, shape = _shape(container)
    assert str(inner["$615"]) == "$689" and str(shape["$159"]) == "$272"
    assert all(str(p["$159"]) == "$273" for p in shape["$250"])


@pytest.mark.unit
def test_the_shape_points_into_the_path_bundle(tmp_path):
    frags = _book(tmp_path, [_chapter(f"<p>{R2} and {HALF}</p>")])
    ((bundle_frag,),) = [by_type(frags, "$692")]
    bundle = val(bundle_frag)
    paths = list(bundle["$693"])
    (mixed,) = _mixed(frags)
    refs = [
        p["$249"] for x in mixed if not isinstance(x, str) for p in _shape(x)[1]["$250"]
    ]
    assert len(refs) == 5  # r, 2; 1, 2, the bar
    assert sorted({int(r["$403"]) for r in refs}) == list(range(len(paths)))
    assert {str(r["name"]) for r in refs} == {str(bundle["name"])}


@pytest.mark.unit
def test_a_glyph_used_again_is_stored_once(tmp_path):
    frags = _book(tmp_path, [_chapter(f"<p>{R2} and {R2}</p><p>{R2}</p>")])
    (bundle_frag,) = by_type(frags, "$692")
    assert len(val(bundle_frag)["$693"]) == 2  # r and 2


@pytest.mark.unit
def test_each_glyph_is_placed_where_it_was_drawn(tmp_path):
    (block,) = _blocks(f"<p>{QUAD}</p>")
    ((_o, (_s, drawing, _a, _src)),) = _maths(block)
    frags = _book(tmp_path, [{"title": "One", "text": "x", "blocks": [block]}])
    (mixed,) = _mixed(frags)
    _inner, shape = _shape(mixed[0])
    placed = [[float(v) for v in p["$98"]] for p in shape["$250"]]
    assert placed == [list(m) for _g, _p, m in drawing.paths]


@pytest.mark.unit
def test_every_decimal_fits_in_32_bits(tmp_path):
    """The Oasis reads a decimal's digits as a 32-bit number: x = 4474.5
    written as 4474.500000 wrapped round to 179.5, drawing the glyph over
    the equation's first."""
    frags = _book(tmp_path, [_chapter(f"<p>{QUAD}</p>")])
    found = []

    def walk(node):
        if isinstance(node, IonDecimal):
            found.append(node)
        elif hasattr(node, "values"):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for f in frags:
        walk(val(f))
    assert any(d > 4294 for d in found)
    digits = {d: int("".join(map(str, d.as_tuple().digits))) for d in found}
    assert {d: n for d, n in digits.items() if n >= 2**32} == {}


@pytest.mark.unit
def test_the_shape_is_sized_in_ems_and_sits_on_the_baseline(tmp_path):
    (block,) = _blocks(f"<p>A {R2}</p>")
    ((_o, (_s, drawing, _a, _src)),) = _maths(block)
    frags = _book(tmp_path, [{"title": "One", "text": "x", "blocks": [block]}])
    (mixed,) = _mixed(frags)
    _inner, shape = _shape(mixed[1])
    assert int(shape["$66"]) == drawing.width and int(shape["$67"]) == drawing.height
    assert float(shape["$56"]["$307"]) == pytest.approx(drawing.width / 1000)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    box = styles[str(mixed[1]["$157"])]
    assert float(box["$56"]["$307"]) == pytest.approx(drawing.width / 1000)
    shift = styles[str(shape["$157"])]["$31"]
    assert float(shift["$307"]) == pytest.approx(-drawing.depth / 1000)
    assert str(shift["$306"]) == "$308"


@pytest.mark.unit
def test_the_alt_text_and_source_are_kept(tmp_path):
    frags = _book(
        tmp_path,
        [_chapter(f'<p>A <math {M} alttext="r squared"><mi>r</mi></math></p>')],
    )
    (mixed,) = _mixed(frags)
    texts = {
        str(f.fid): [str(t) for t in val(f)["$146"]] for f in by_type(frags, "$145")
    }
    notes = {
        str(n["$687"]): texts[str(n["$145"]["name"])][int(n["$145"]["$403"])]
        for n in mixed[1]["$683"]
    }
    assert notes["$584"] == "r squared"
    assert notes["$690"].startswith("<math") and "<mi>r</mi>" in notes["$690"]


@pytest.mark.unit
def test_many_equations_keep_content_fragments_under_8_kb(tmp_path):
    body = "".join(f"<p>{i} {R2}</p>" for i in range(150))
    frags = _book(tmp_path, [_chapter(body)])
    for f in by_type(frags, "$145"):
        strings = [str(s).encode("utf-8") for s in val(f)["$146"]]
        assert sum(len(s) for s in strings[:-1]) < 8192


@pytest.mark.unit
def test_a_book_with_drawings_declares_them(tmp_path):
    frags = _book(tmp_path, [_chapter(f"<p>A {R2}</p>")])
    (features,) = by_type(frags, "$585")
    names = [str(f["$492"]) for f in val(features)["$590"]]
    assert "yj_mathml" in names
    (caps,) = by_type(frags, "$593")
    assert "kfxgen.pidMapWithOffset" in [str(c["$492"]) for c in val(caps)]


@pytest.mark.unit
def test_a_book_without_drawings_declares_nothing_new(tmp_path, monkeypatch):
    monkeypatch.setattr(_conv, "draw_math", lambda _math: None)
    frags = _book(tmp_path, [_chapter(f"<p>A {R2}</p>")])
    (features,) = by_type(frags, "$585")
    assert "yj_mathml" not in [str(f["$492"]) for f in val(features)["$590"]]
    assert by_type(frags, "$692") == []
    (caps,) = by_type(frags, "$593")
    assert "kfxgen.pidMapWithOffset" not in [str(c["$492"]) for c in val(caps)]


@pytest.mark.unit
@pytest.mark.parametrize(
    "body, expected",
    [
        (f"<p>{R2} starts</p>", [None, " starts"]),
        (f"<p>Ends {R2}</p>", ["Ends ", None]),
        (f"<p>Two {R2}{HALF} together</p>", ["Two ", None, None, " together"]),
    ],
)
def test_where_the_equation_falls_in_its_paragraph(tmp_path, body, expected):
    frags = _book(tmp_path, [_chapter(body)])
    (mixed,) = _mixed(frags)
    assert [str(x) if isinstance(x, str) else None for x in mixed] == expected


@pytest.mark.unit
def test_position_ids_are_unique(tmp_path):
    frags = _book(tmp_path, [_chapter(f"<p>{R2} a {HALF}</p><p>{R2}</p>")])
    eids = [int(e["$155"]) for e in _entries(frags) if "$155" in e]
    assert len(eids) == len(set(eids))


@pytest.mark.unit
def test_a_location_after_an_equation_counts_the_paragraphs_offset():
    """A paragraph resumes after an equation at its own offset: a location
    falling there names that offset, not the positions since it began."""
    from kfxgen.native_generator import POSITIONS_PER_LOCATION, place_locations

    section = [(9, 1), (1, 5, 0), (2, 1), (3, 1), (4, 1), (1, 300, 6)]
    locations = place_locations([section])
    # Position 110: the paragraph's second part starts at position 9, offset 6.
    assert (1, 6 + POSITIONS_PER_LOCATION - 9) in locations


@pytest.mark.unit
def test_a_book_of_display_equations_declares_no_offsets(tmp_path):
    """An equation alone in its paragraph leaves no position part-way into
    one, and kfxlib flags the capability declared without one."""
    frags = _book(
        tmp_path,
        [_chapter(f'<p>Before.</p><math {M} display="block"><mi>x</mi></math>')],
    )
    (caps,) = by_type(frags, "$593")
    assert "kfxgen.pidMapWithOffset" not in [str(c["$492"]) for c in val(caps)]
    (features,) = by_type(frags, "$585")
    assert "yj_mathml" in [str(f["$492"]) for f in val(features)["$590"]]


def _lines(frags):
    """Each storyline paragraph as it reads, a drawn equation as [EQ]."""
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []
    for e in _entries(frags):
        if e.get("$145"):
            ref = e["$145"]
            out.append(str(texts[str(ref["name"])][ref["$403"]]))
        elif str(e.get("$159")) == "$269" and isinstance(e.get("$146"), list):
            out.append(
                "".join(str(x) if isinstance(x, str) else "[EQ]" for x in e["$146"])
            )
    return out


FRAC = "<mfrac><mi>a</mi><mi>b</mi></mfrac><mo>=</mo><mn>1</mn>"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("alttext", "label"),
    [
        ("", "The ab=1 rule"),
        ("a over b equals 1", "The a over b equals 1 rule"),
        (r"\frac{a}{b}=1", r"The \frac{a}{b}=1 rule"),
        ("", "The a/b=1 rule"),
        ("a over b equals 1", "The a/b=1 rule"),
    ],
)
def test_a_drawn_heading_prints_once_under_any_label_for_it(tmp_path, alttext, label):
    """The book's heading prints once with its drawing, as on Kindle
    Previewer's page, whichever way the label writes the equation (#404)."""
    alt = f' alttext="{alttext}"' if alttext else ""
    body = f"<h1>The <math {M}{alt}>{FRAC}</math> rule</h1><p>Body.</p>"
    frags = _book(tmp_path, [{"title": label, "text": "x", "blocks": _blocks(body)}])
    assert _lines(frags) == ["The [EQ] rule", "Body."]


@pytest.mark.unit
def test_a_drawn_heading_split_over_two_blocks_prints_once(tmp_path):
    body = (
        f"<h1>The <math {M}><mfrac><mi>a</mi><mi>b</mi></mfrac></math></h1>"
        "<h1>rule</h1><p>Body.</p>"
    )
    frags = _book(
        tmp_path, [{"title": "The ab rule", "text": "x", "blocks": _blocks(body)}]
    )
    assert _lines(frags) == ["The [EQ] rule", "Body."]


@pytest.mark.unit
def test_a_different_label_still_prints_above_a_drawn_heading(tmp_path):
    body = f"<h1>The <math {M}>{FRAC}</math> rule</h1><p>Body.</p>"
    frags = _book(
        tmp_path, [{"title": "Another label", "text": "x", "blocks": _blocks(body)}]
    )
    assert _lines(frags) == ["Another label", "The [EQ] rule", "Body."]


@pytest.mark.unit
def test_a_label_mixes_alttext_and_text_as_its_equations_have_them(tmp_path):
    """One equation has `alttext` and one doesn't: the label writes each as
    it can (#404 mutations)."""
    body = (
        f'<h1>Both <math {M} alttext="r squared"><msup><mi>r</mi><mn>2</mn></msup>'
        f"</math> and <math {M}><mfrac><mi>a</mi><mi>b</mi></mfrac></math></h1>"
        "<p>Body.</p>"
    )
    frags = _book(
        tmp_path,
        [{"title": "Both r squared and a/b", "text": "x", "blocks": _blocks(body)}],
    )
    assert _lines(frags) == ["Both [EQ] and [EQ]", "Body."]


@pytest.mark.unit
def test_an_equation_in_a_split_headings_second_block_stays_in_place(tmp_path):
    """The second block's runs move along by the first's length (#404
    mutations)."""
    body = f"<h1>The rule</h1><h1>for <math {M}><mi>x</mi></math></h1><p>Body.</p>"
    frags = _book(
        tmp_path,
        [{"title": "The rule for x", "text": "x", "blocks": _blocks(body)}],
    )
    assert _lines(frags) == ["The rule for [EQ]", "Body."]


@pytest.mark.unit
def test_a_heading_without_maths_still_prints_as_its_label(tmp_path):
    """Only a heading holding an equation prints as the book writes it: one
    of plain text keeps printing as its label, without its line breaks (54
    of the Gutenberg 90 would change otherwise; #404 mutations)."""
    frags = _book(
        tmp_path,
        [
            {
                "title": "Part One",
                "text": "x",
                "blocks": _blocks("<h1>Part<br/>One</h1><p>Body.</p>"),
            }
        ],
    )
    assert _lines(frags) == ["Part One", "Body."]


RUBY_MATHS = (
    f"<h1><ruby>漢<rt>kan</rt></ruby>字 <math {M}><mi>x</mi></math></h1><p>Body.</p>"
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "label", ["漢字 x", "漢(kan)字 x", "漢（kan）字 x", "漢kan字 x"]
)
def test_a_heading_with_ruby_and_a_drawn_equation_prints_once(tmp_path, label):
    """A label giving the ruby's base alone, its reading in parentheses or
    run in, as `_ruby_heading_title` takes them: the heading prints once,
    with its reading and its drawing, as on Kindle Previewer's page (#410)."""
    frags = _book(
        tmp_path, [{"title": label, "text": "x", "blocks": _blocks(RUBY_MATHS)}]
    )
    assert _lines(frags) == ["漢字 [EQ]", "Body."]
    readings = [str(r["$145"]) for f in by_type(frags, "$756") for r in val(f)["$146"]]
    assert readings == ["kan"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ruby", "label"),
    [
        ("<ruby>漢<rp>(</rp><rt>kan</rt><rp>)</rp></ruby>字", "漢字 x"),
        ("<ruby>漢<rt>kan</rt></ruby><ruby>字<rt></rt></ruby>", "漢(kan)字 x"),
    ],
)
def test_rp_fallbacks_and_an_empty_reading_are_not_in_the_label(tmp_path, ruby, label):
    """<rp> is the fallback for readers without ruby and an empty <rt> has
    no reading: neither is written into a label (#410 mutations)."""
    body = f"<h1>{ruby} <math {M}><mi>x</mi></math></h1><p>Body.</p>"
    frags = _book(tmp_path, [{"title": label, "text": "x", "blocks": _blocks(body)}])
    assert _lines(frags) == ["漢字 [EQ]", "Body."]


@pytest.mark.unit
def test_a_split_heading_matches_under_its_alttext_label(tmp_path):
    """A block without an equation is read the same way in every label form
    (#410 mutations)."""
    body = (
        f'<h1>The <math {M} alttext="a over b"><mfrac><mi>a</mi><mi>b</mi></mfrac>'
        "</math></h1><h1>rule</h1><p>Body.</p>"
    )
    frags = _book(
        tmp_path, [{"title": "The a over b rule", "text": "x", "blocks": _blocks(body)}]
    )
    assert _lines(frags) == ["The [EQ] rule", "Body."]


@pytest.mark.unit
@pytest.mark.parametrize("rp", [("《", "》"), ("【", "】")])
def test_a_label_of_the_source_text_keeps_rp_brackets(tmp_path, rp):
    """A label written from the heading's source text reads its <rp> as they
    stand, whatever brackets they hold (#410 QA: 《》 printed twice)."""
    body = (
        f"<h1><ruby>漢<rp>{rp[0]}</rp><rt>kan</rt><rp>{rp[1]}</rp></ruby>字 "
        f"<math {M}><mi>x</mi></math></h1><p>Body.</p>"
    )
    label = f"漢{rp[0]}kan{rp[1]}字 x"
    frags = _book(tmp_path, [{"title": label, "text": "x", "blocks": _blocks(body)}])
    assert _lines(frags) == ["漢字 [EQ]", "Body."]


PREFIX_HEADING = f"<h1>The <math {M}><mi>x</mi></math> rule and more</h1><p>Body.</p>"


@pytest.mark.unit
def test_a_label_opening_a_drawn_heading_names_the_whole_heading(tmp_path):
    """The label is the heading's first words: the heading prints once, whole,
    as on Kindle Previewer's page (#408)."""
    frags = _book(
        tmp_path,
        [{"title": "The x rule", "text": "x", "blocks": _blocks(PREFIX_HEADING)}],
    )
    assert _lines(frags) == ["The [EQ] rule and more", "Body."]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "label"),
    [
        (PREFIX_HEADING, "The x ru"),
        (
            f"<p>The <math {M}><mi>x</mi></math> rule and more</p><p>Body.</p>",
            "The x rule",
        ),
    ],
)
def test_a_label_ending_mid_word_or_opening_a_paragraph_prints_above_it(
    tmp_path, body, label
):
    """Only whole words, and only a heading: prose that opens with the label's
    words is not the chapter's heading (#408)."""
    frags = _book(tmp_path, [{"title": label, "text": "x", "blocks": _blocks(body)}])
    assert _lines(frags) == [label, "The [EQ] rule and more", "Body."]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "label", "lines"),
    [
        # A rule above the heading is passed over, as for a whole match.
        ("<hr/>" + PREFIX_HEADING, "The x rule", ["The [EQ] rule and more", "Body."]),
        # Any label form opens the heading: here its alttext.
        (
            f'<h1>The <math {M} alttext="ex"><mi>x</mi></math> rule and more</h1>'
            "<p>Body.</p>",
            "The ex rule",
            ["The [EQ] rule and more", "Body."],
        ),
        # An empty label opens nothing.
        (PREFIX_HEADING, "", ["The [EQ] rule and more", "Body."]),
    ],
)
def test_a_label_opening_a_drawn_heading_after_a_rule_or_by_alttext(
    tmp_path, body, label, lines
):
    """(#408 mutations)"""
    frags = _book(tmp_path, [{"title": label, "text": "x", "blocks": _blocks(body)}])
    assert [line for line in _lines(frags) if line] == lines
