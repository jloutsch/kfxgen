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
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'
R2 = f"<math {M}><msup><mi>r</mi><mn>2</mn></msup></math>"
HALF = f"<math {M}><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"
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
    assert len(refs) == len(paths) == 5  # r, 2; 1, 2, the bar
    assert sorted(int(r["$403"]) for r in refs) == list(range(5))
    assert {str(r["name"]) for r in refs} == {str(bundle["name"])}


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
