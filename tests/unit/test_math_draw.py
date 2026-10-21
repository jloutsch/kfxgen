"""An equation drawn as glyph outlines (#235).

A drawing is measured in thousandths of an em from its top left corner, y
downwards, with its depth below the baseline; each path is a KFX path list
(0 move, 1 line, 2 quadratic, 3 cubic, 4 close)."""

import pytest
from lxml import etree

from kfxgen import math_draw

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'
R2 = f"<math {M}><msup><mi>r</mi><mn>2</mn></msup></math>"
QUADRATIC = (
    f'<math {M} display="block"><mi>x</mi><mo>=</mo><mfrac><mrow><mo>-</mo><mi>b</mi>'
    "<mo>&#x00B1;</mo><msqrt><msup><mi>b</mi><mn>2</mn></msup><mo>-</mo><mn>4</mn>"
    "<mi>a</mi><mi>c</mi></msqrt></mrow><mrow><mn>2</mn><mi>a</mi></mrow></mfrac></math>"
)
_ARGS = {0: 2, 1: 2, 2: 4, 3: 6, 4: 0}


def _draw(markup):
    return math_draw.draw(etree.fromstring(markup))


def _points(path):
    """The path's points, checking each instruction has its arguments."""
    points, i = [], 0
    while i < len(path):
        code = path[i]
        assert code in _ARGS, path
        args = path[i + 1 : i + 1 + _ARGS[code]]
        assert len(args) == _ARGS[code], path
        points += list(zip(args[::2], args[1::2]))
        i += 1 + _ARGS[code]
    return points


@pytest.mark.unit
def test_an_equation_is_drawn_inside_its_box():
    d = _draw(R2)
    assert 500 < d.width < 1000 and 600 < d.height < 1100
    assert 0 <= d.depth < d.height
    assert len(d.paths) == 2  # r and 2
    for path in d.paths:
        for x, y in _points(path):
            assert -2 <= x <= d.width + 2 and -2 <= y <= d.height + 2


@pytest.mark.unit
def test_a_fraction_bar_is_a_closed_path():
    d = _draw(f"<math {M}><mfrac><mn>1</mn><mn>2</mn></mfrac></math>")
    assert len(d.paths) == 3
    assert any(p[0] == 0 and p[-1] == 4 and len(_points(p)) == 4 for p in d.paths)


@pytest.mark.unit
def test_two_minus_operators_are_drawn():
    """ziamath rewrites `<mo>-</mo>` with a regex that, given two, swallows
    the markup between them."""
    d = _draw(QUADRATIC)
    assert d is not None and len(d.paths) >= 16


@pytest.mark.unit
def test_an_annotation_is_not_drawn():
    plain = _draw(f"<math {M}><msup><mi>z</mi><mn>2</mn></msup></math>")
    annotated = _draw(
        f"<math {M}><semantics><msup><mi>z</mi><mn>2</mn></msup>"
        '<annotation encoding="application/x-tex">z^2</annotation></semantics></math>'
    )
    assert annotated == plain


@pytest.mark.unit
def test_a_prefixed_equation_is_drawn():
    prefixed = _draw(
        '<m:math xmlns:m="http://www.w3.org/1998/Math/MathML"><m:msup><m:mi>r</m:mi>'
        "<m:mn>2</m:mn></m:msup></m:math>"
    )
    assert prefixed == _draw(R2)


@pytest.mark.unit
def test_nothing_is_drawn_without_ziamath(monkeypatch):
    monkeypatch.setattr(math_draw, "_ziamath", lambda: None)
    assert _draw(R2) is None


@pytest.mark.unit
def test_nothing_is_drawn_when_ziamath_fails(monkeypatch):
    class Broken:
        class config:
            pass

        @staticmethod
        def Math(*_a, **_k):
            raise ValueError("no")

    monkeypatch.setattr(math_draw, "_ziamath", lambda: Broken)
    assert _draw(R2) is None


@pytest.mark.unit
def test_an_empty_equation_is_not_drawn():
    assert _draw(f"<math {M}></math>") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "d, path",
    [
        ("M 1 2 L 3 4 Z", [0, 0, 0, 1, 2, 2, 4]),
        ("M 1 2 H 5 V 7", [0, 0, 0, 1, 4, 0, 1, 4, 5]),
        (
            "M 1 2 Q 3 4 5 6 C 1 1 2 2 3 3",
            [0, 0, 0, 2, 2, 2, 4, 4, 3, 0, -1, 1, 0, 2, 1],
        ),
    ],
)
def test_svg_path_commands(d, path):
    assert math_draw._path(d, 1, 2) == path


@pytest.mark.unit
def test_a_relative_svg_command_is_refused():
    assert math_draw._path("M 1 2 l 3 4", 0, 0) is None


@pytest.mark.unit
def test_a_fraction_bar_is_a_rectangle_across_the_fraction():
    d = _draw(f"<math {M}><mfrac><mn>1</mn><mn>2</mn></mfrac></math>")
    (bar,) = [p for p in d.paths if len(p) == 13 and p[-1] == 4]
    (x, y), (right, top), (right2, bottom), (x2, bottom2) = _points(bar)
    assert (top, right2, bottom2, x2) == (y, right, bottom, x)
    assert right - x > d.width / 2 and 0 < bottom - y < d.height / 10


@pytest.mark.unit
def test_annotations_raise_no_ziamath_warning(caplog):
    """ziamath warns of each element it doesn't know; an annotation and
    `<semantics>` are left out before it reads the equation."""
    _draw(
        f"<math {M}><semantics><msup><mi>z</mi><mn>2</mn></msup>"
        '<annotation encoding="application/x-tex">z^2</annotation>'
        '<annotation-xml encoding="MathML-Content"><ci>z</ci></annotation-xml>'
        "</semantics></math>"
    )
    assert [
        r.getMessage() for r in caplog.records if "Undefined" in r.getMessage()
    ] == []
