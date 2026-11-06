"""An <svg> drawn only with shapes is a picture of its own (#709).

Formulas set as glyph outlines (`<symbol>`s of `<path>`s placed by
`<use>`) were dropped: kfxgen took only an <svg>'s `<image>`s (#154) and
`<text>` (#231). Kindle Previewer 4.0.3 refuses Enhanced Typesetting for a
book with one, as for an `<img>` of an SVG file (probe P709, 2026-11-06),
so it is drawn as #438 draws those: rasterised to PNG, sized by its
markup.

The shim has no calibre to rasterise with, so these stand a small PNG in
for `svg_to_png`; tests/integration/test_calibre_inline_svg.py rasterises
for real.
"""

import io

import pytest
from lxml import etree
from PIL import Image

from tests._kfx_introspect import by_type, val
from tests.unit.test_direction import _convert

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
GLYPHS = (
    "<defs><g><symbol overflow='visible' id='g{n}'><path d='M 1 -2 L 3 -2 L 3 -1 L 1 -1 Z'/>"
    "</symbol></g></defs><g style='fill:rgb(0%,0%,0%)'><use xlink:href='#g{n}' x='0' y='4'/>"
    "<use xlink:href='#g{n}' x='5' y='4'/></g>"
)


def _svg(n=1, attrs='style="vertical-align:-1pt" width="24pt" viewBox="0 0 12 4.4"'):
    return f"<svg {NS} {attrs}>{GLYPHS.format(n=n)}</svg>"


@pytest.fixture
def drawn(monkeypatch):
    """The width of each SVG `svg_to_png` is asked to draw."""
    from kfxgen import converter

    calls = []
    buf = io.BytesIO()
    Image.new("RGB", (96, 32), (0, 0, 0)).save(buf, "PNG")

    def fake(raw, log=None, scale=2):
        assert scale == 1
        calls.append(etree.fromstring(raw).get("width"))
        return buf.getvalue(), (96, 32)

    monkeypatch.setattr(converter, "svg_to_png", fake)
    return calls


def _pictures(frags):
    """[(style keys, resource)] of every `$271`, in file order."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []

    def visit(nodes):
        for e in nodes:
            if not hasattr(e, "get"):
                continue
            if str(e.get("$159")) == "$271":
                s = dict(styles.get(str(e.get("$157")), {}))
                out.append(({str(k): s[k] for k in s}, str(e.get("$175"))))
            visit(e.get("$146") or ())

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"])
    return out


def _book(tmp_path, body):
    return _convert(tmp_path, body, html_attrs=' lang="en"', language="en")


def _em(v):
    return round(float(v["$307"]), 5), str(v["$306"])


@pytest.mark.unit
def test_an_inline_formula_of_paths_is_a_picture_in_its_words(tmp_path, drawn):
    frags = _book(tmp_path, f"<p>V01 words <span>{_svg()}</span> after.</p>")
    ((style, _res),) = _pictures(frags)
    # 24pt at 1em = 12pt, as an <img width="24pt"> (#541); its height
    # follows its shape; its vertical-align a shift in pt (#565).
    assert _em(style["$56"]) == (2.0, "$308")
    assert _em(style["$57"]) == (0.66667, "$308")
    assert _em(style["$31"]) == (-1.0, "$318")
    assert str(style["$546"]) == "$377"
    # Drawn at 4 times its size, in its own markup (#709).
    assert drawn == ["96pt"]


@pytest.mark.unit
def test_a_display_formula_is_a_picture_block(tmp_path, drawn):
    frags = _book(
        tmp_path,
        "<p>V02 before.</p><div>"
        + _svg(2, 'width="60pt" viewBox="0 0 12 4.4"')
        + "</div><p>V03 after.</p>",
    )
    assert len(_pictures(frags)) == 1


@pytest.mark.unit
def test_two_copies_of_one_formula_share_one_picture(tmp_path, drawn):
    frags = _book(tmp_path, f"<p>V01 {_svg()} and {_svg()} again.</p>")
    pics = _pictures(frags)
    assert len(pics) == 2
    assert pics[0][1] == pics[1][1]
    assert len(drawn) == 1


@pytest.mark.unit
def test_a_formula_in_a_table_cell_is_drawn_at_its_own_size(tmp_path, drawn):
    # A picture in a cell has no style; its pixels are its size (#441).
    _book(
        tmp_path,
        f"<table><tr><td>C01 {_svg()}</td><td>other</td></tr></table>",
    )
    assert drawn == ["24pt"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "inner",
    [
        # Pictures and words keep their own handling (#154, #231).
        "<image xlink:href='p.png' width='10' height='10'/><path d='M0 0 L1 1 Z'/>",
        "<text x='0' y='10'>SVGWORDS</text><path d='M0 0 L1 1 Z'/>",
        # Nothing drawn: shapes only in <defs>.
        "<defs><path id='p' d='M0 0 L1 1 Z'/></defs>",
    ],
    ids=["image", "text", "defs-only"],
)
def test_other_svgs_are_not_drawn_as_a_picture(tmp_path, drawn, inner):
    _book(tmp_path, f"<p>V01 <svg {NS} width='20' height='20'>{inner}</svg> after.</p>")
    assert drawn == []


@pytest.mark.unit
def test_the_svg_drawn_gets_the_height_its_viewbox_gives():
    from kfxgen.converter import _svg_picture_bytes

    svg = etree.fromstring(_svg())
    own = etree.fromstring(_svg_picture_bytes(svg))
    assert (own.get("width"), own.get("height")) == ("24pt", "8.8pt")
    assert own.tail is None
    big = etree.fromstring(_svg_picture_bytes(svg, 4))
    assert (big.get("width"), big.get("height")) == ("96pt", "35.2pt")
