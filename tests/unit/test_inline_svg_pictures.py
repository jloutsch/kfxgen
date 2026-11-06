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
import threading

import pytest
from lxml import etree
from PIL import Image

from tests._kfx_introspect import by_type, iter_entries, val
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
    # Drawn at 4 times its size, in its own markup, in CSS px: 24pt is
    # 32 px (#709).
    assert drawn == ["128"]


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
def test_a_formula_directly_in_the_body_is_a_picture_block(tmp_path, drawn):
    frags = _book(tmp_path, "<p>V02 before.</p>" + _svg(2) + "<p>V03 after.</p>")
    assert len(_pictures(frags)) == 1
    assert drawn == ["128"]


@pytest.mark.unit
def test_a_formula_with_no_width_shows_at_its_own_size(tmp_path, drawn):
    # No width or height: shown at its own size, its pixels drawn at 4
    # times it (96 by 32 here) divided back by 4: 24 px, 1.5em (#529).
    frags = _book(
        tmp_path, f"<p>V01 words {_svg(1, 'viewBox="0 0 12 4.4"')} after.</p>"
    )
    ((style, _res),) = _pictures(frags)
    assert _em(style["$56"]) == (1.5, "$308")


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
    assert drawn == ["32"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "inner",
    [
        # Pictures and words keep their own handling (#154, #231).
        "<image xlink:href='p.png' width='10' height='10'/><path d='M0 0 L1 1 Z'/>",
        "<text x='0' y='10'>SVGWORDS</text><path d='M0 0 L1 1 Z'/>",
        # Nothing drawn: shapes only in <defs>, or an empty group.
        "<defs><path id='p' d='M0 0 L1 1 Z'/></defs>",
        "<title>a title</title><g></g>",
    ],
    ids=["image", "text", "defs-only", "empty-group"],
)
def test_other_svgs_are_not_drawn_as_a_picture(tmp_path, drawn, inner):
    _book(tmp_path, f"<p>V01 <svg {NS} width='20' height='20'>{inner}</svg> after.</p>")
    assert drawn == []


@pytest.mark.unit
def test_the_svg_drawn_gets_the_height_its_viewbox_gives():
    from kfxgen.converter import _svg_picture

    svg = etree.fromstring(_svg())
    raw, scale = _svg_picture(svg)
    own = etree.fromstring(raw)
    # In CSS px, as Qt reads a pt as a px: 24pt is 32 px.
    assert (own.get("width"), own.get("height"), scale) == ("32", "11.7333", 1)
    assert own.tail is None
    raw, scale = _svg_picture(svg, 4)
    big = etree.fromstring(raw)
    assert (big.get("width"), big.get("height"), scale) == ("128", "46.9333", 4)


@pytest.mark.unit
def test_a_height_alone_gets_the_width_its_viewbox_gives():
    from kfxgen.converter import _svg_picture

    svg = etree.fromstring(f"<svg {NS} height='15' viewBox='0 0 12 4'><rect/></svg>")
    own = etree.fromstring(_svg_picture(svg, 4)[0])
    assert (own.get("width"), own.get("height")) == ("180", "60")


@pytest.mark.unit
def test_an_svg_with_no_viewbox_gets_one_of_its_own_size():
    # QA on #727: scaled with no viewBox, its shapes stayed in 1 px units
    # and filled a quarter of the picture.
    from kfxgen.converter import _svg_picture

    svg = etree.fromstring(
        f"<svg {NS} width='40' height='20'><rect width='40' height='20'/></svg>"
    )
    raw, scale = _svg_picture(svg, 4)
    own = etree.fromstring(raw)
    assert (own.get("viewBox"), own.get("width"), own.get("height")) == (
        "0 0 40 20",
        "160",
        "80",
    )
    assert scale == 4


@pytest.mark.unit
@pytest.mark.parametrize("box", ["0,0,20,10", "0, 0, 20, 10", " 0 0,20 10 "])
def test_a_viewbox_with_commas_is_kept(box):
    # QA on #727: commas are valid in a viewBox; read as none, it was
    # replaced by one of the drawn size, and the drawing shrank.
    from kfxgen.converter import _svg_picture

    svg = etree.fromstring(
        f"<svg {NS} width='32' height='16' viewBox='{box}'><rect width='20' height='10'/></svg>"
    )
    own = etree.fromstring(_svg_picture(svg, 4)[0])
    assert (own.get("viewBox"), own.get("width"), own.get("height")) == (
        box,
        "128",
        "64",
    )


@pytest.mark.unit
def test_glyphs_that_use_each_other_are_copied_once(tmp_path):
    # QA on #727: a symbol using a symbol that uses it hung the copy.
    from kfxgen.converter import _svg_picture

    root = etree.fromstring(
        "<div xmlns='http://www.w3.org/1999/xhtml'>"
        f"<svg {NS} style='display:none'><defs>"
        "<symbol id='a'><use xlink:href='#b'/><path d='M0 0 L1 1 Z'/></symbol>"
        "<symbol id='b'><use xlink:href='#a'/><path d='M1 1 L2 2 Z'/></symbol>"
        "</defs></svg>"
        f"<svg {NS} width='10' height='10' viewBox='0 0 10 10'><use xlink:href='#a'/></svg>"
        "</div>"
    )
    formula = [
        e for e in root.iter("{http://www.w3.org/2000/svg}svg") if e.get("width")
    ][0]
    # In a thread, so a loop fails the test rather than hanging it.
    done = []
    worker = threading.Thread(
        target=lambda: done.append(_svg_picture(formula)[0]), daemon=True
    )
    worker.start()
    worker.join(10)
    assert done, "copying the glyphs did not finish"
    own = etree.fromstring(done[0])
    copied = [e.get("id") for e in own.iter("{http://www.w3.org/2000/svg}symbol")]
    assert sorted(copied) == ["a", "b"]


@pytest.mark.unit
def test_an_svg_of_unknown_size_is_drawn_at_its_own():
    # Only a width, no viewBox: Qt's own size; nothing to scale by.
    from kfxgen.converter import _svg_picture

    svg = etree.fromstring(f"<svg {NS} width='40'><rect width='40' height='20'/></svg>")
    assert _svg_picture(svg, 4)[1] == 1


#: MathJax's SVG output: sizes in ex, every glyph a <path> in one hidden
#: <svg> of its own, placed by <use>.
MATHJAX = (
    f"<svg {NS} style='display: none;'><defs id='MathJax_SVG_glyphs'>"
    "<path id='MJMATHI-78' d='M 52 289 L 500 289 L 500 400 L 52 400 Z'/>"
    "</defs></svg>"
    "<p>V01 the formula "
    f"<svg {NS} width='1.294ex' height='1.025ex' viewBox='0 -442 572 453' "
    "style='vertical-align: -0.025ex;'><g stroke='currentColor' fill='currentColor'>"
    "<use xlink:href='#MJMATHI-78' x='0' y='0'/></g></svg> after.</p>"
)


@pytest.mark.unit
def test_a_mathjax_formula_is_sized_in_em(tmp_path, drawn):
    # QA on #727: ex isn't read as a size, so it showed at 8.94em. An ex
    # is half an em, CSS's fallback.
    frags = _book(tmp_path, MATHJAX)
    ((style, _res),) = _pictures(frags)
    assert _em(style["$56"]) == (0.647, "$308")
    assert _em(style["$57"]) == (0.5125, "$308")
    assert _em(style["$31"]) == (-0.0125, "$308")
    # Drawn in px: an ex is 8 px.
    assert drawn == ["41.408"]


@pytest.mark.unit
def test_glyphs_from_another_svg_are_copied_into_the_picture(tmp_path):
    # QA on #727: a <use> of a glyph in MathJax's hidden <svg> drew blank.
    from kfxgen.converter import _svg_picture

    root = etree.fromstring(
        f"<div xmlns='http://www.w3.org/1999/xhtml'>{MATHJAX}</div>"
    )
    formula = [
        e for e in root.iter("{http://www.w3.org/2000/svg}svg") if e.get("width")
    ][0]
    own = etree.fromstring(_svg_picture(formula)[0])
    paths = list(own.iter("{http://www.w3.org/2000/svg}path"))
    assert [p.get("id") for p in paths] == ["MJMATHI-78"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "attrs, inner",
    [
        ("style='display:none'", "<path d='M0 0 L1 1 Z'/>"),
        ("display='none'", "<path d='M0 0 L1 1 Z'/>"),
        ("style='visibility: hidden'", "<path d='M0 0 L1 1 Z'/>"),
        # Qt doesn't draw a nested <svg>.
        ("", f"<svg {NS} width='5' height='5'><path d='M0 0 L1 1 Z'/></svg>"),
    ],
    ids=["display-none", "display-attr", "hidden", "nested-only"],
)
def test_a_hidden_or_nested_svg_is_not_drawn(tmp_path, drawn, attrs, inner):
    _book(
        tmp_path,
        f"<p>V01 <svg {NS} {attrs} width='20' height='20'>{inner}</svg> after.</p>",
    )
    assert drawn == []


@pytest.mark.unit
def test_a_linked_formula_keeps_its_link(tmp_path, drawn):
    # As a linked <img> does (#355). QA on #727: dropping it failed no test.
    frags = _book(
        tmp_path,
        f"<p>V01 see <a href='#t'>{_svg()}</a> after.</p><p id='t'>T01 target</p>",
    )
    linked = []
    for sl in by_type(frags, "$259"):
        for e in iter_entries(val(sl)["$146"]):
            kids = e.get("$146") or []
            if any(
                str(getattr(k, "get", lambda _k: None)("$159")) == "$271" for k in kids
            ):
                linked += [r for r in e.get("$142") or () if "$179" in r]
    assert linked
