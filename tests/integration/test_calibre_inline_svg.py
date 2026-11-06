"""An <svg> drawn only with shapes, through calibre (#709).

Probe P709's shapes: formulas as `<symbol>` glyphs placed by `<use>`, the
way a PDF-to-SVG chain writes them, inline and as a display formula, and
an <svg> of plain paths. Each becomes a PNG picture, drawn at 4 times its
own size, shown at the width its markup gives.

calibre computes an <svg> as width auto, and folds its `style` into a
class with no vertical-align, so its width comes from its attribute and a
`vertical-align` on it is lost (kfxgen keeps one the shim passes on:
tests/unit/test_inline_svg_pictures.py).

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
"""

import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
PLUS = (
    "M 1 -2.5 L 2 -2.5 L 2 -3.5 C 2 -3.8 2.4 -3.8 2.4 -3.5 L 2.4 -2.5 L 3.4 -2.5 "
    "L 3.4 -2.1 L 2.4 -2.1 L 2.4 -1.1 C 2.4 -0.8 2 -0.8 2 -1.1 L 2 -2.1 L 1 -2.1 Z"
)


def _glyphs(n, width, style=""):
    st = f' style="{style}"' if style else ""
    return (
        f'<svg {NS}{st} width="{width}" viewBox="0 0 12 4.4" version="1.1"><defs><g>'
        f'<symbol overflow="visible" id="g{n}"><path style="stroke:none;" d="{PLUS}"/>'
        f'</symbol></g></defs><g style="fill:rgb(0%,0%,0%);fill-opacity:1;">'
        f'<use xlink:href="#g{n}" x="0" y="4.4"/><use xlink:href="#g{n}" x="8" y="4.4"/>'
        "</g></svg>"
    )


BODY = (
    "<h1>Formulas</h1>"
    f"<p>V01 inline <span class='svg_inline'>{_glyphs(1, '24pt', 'vertical-align:-1pt')}"
    "</span> after.</p>"
    f"<p>V02 display below.</p><div class='equation'>{_glyphs(2, '60pt')}</div>"
    f"<p>V03 small <span>{_glyphs(3, '12pt')}</span> after.</p>"
    f"<p>V04 plain <svg {NS} width='20pt' height='10pt' viewBox='0 0 20 10'>"
    "<path d='M 2 8 L 10 2 L 18 8 Z' fill='black'/></svg> after.</p>"
    f"<p>V05 em <span>{_glyphs(5, '3em')}</span> after.</p>"
)
CSS = "div.equation { text-align: center }"


@pytest.fixture(scope="module")
def pictures(calibre):  # noqa: F811
    tmp, _env, _version = calibre
    epub, kfx = tmp / "v709.epub", tmp / "v709.kfx"
    write_epub(
        epub,
        pages={"c.xhtml": BODY},
        toc=[("Formulas", "c.xhtml", 1)],
        css=CSS,
        ident="v709",
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    resources = {str(f.fid): val(f) for f in by_type(frags, "$164")}
    raw = {str(f.fid): bytes(val(f)) for f in by_type(frags, "$417")}
    found = []

    def visit(nodes, before):
        for e in nodes:
            if isinstance(e, str):
                before.append(e)
                continue
            if not hasattr(e, "get"):
                continue
            ref = e.get("$145")
            if hasattr(ref, "get"):
                before.append(str(texts[str(ref["name"])][int(ref["$403"])]))
            if str(e.get("$159")) == "$271":
                s = dict(styles.get(str(e.get("$157")), {}))
                png = raw[str(resources[str(e.get("$175"))]["$165"])]
                label = next(t[:3] for t in reversed(before) if t[:1] == "V")
                found.append((label, s, png))
            visit(e.get("$146") or (), before)

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"], [])
    return found


def test_each_formula_is_one_png_picture(pictures):
    assert [label for label, _s, _png in pictures] == [
        "V01",
        "V02",
        "V03",
        "V04",
        "V05",
    ]
    for _label, _s, png in pictures:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    "label, width",
    [("V01", 2.0), ("V03", 1.0), ("V04", 1.66667), ("V05", 3.0)],
)
def test_an_inline_formula_is_as_wide_as_its_markup_says(pictures, label, width):
    # 24pt, 12pt and 20pt at 1em = 12pt (#541), 3em as written.
    (s,) = [s for lab, s, _png in pictures if lab == label]
    assert (round(float(s["$56"]["$307"]), 5), str(s["$56"]["$306"])) == (
        width,
        "$308",
    )


def test_a_formula_is_drawn_at_four_times_its_own_size(pictures):
    # Qt reads 24pt as 24 px; drawn at 4 times that (#709).
    (png,) = [png for lab, _s, png in pictures if lab == "V01"]
    assert struct.unpack(">II", png[16:24]) == (96, 35)
