"""A superscript or subscript inside another is layered on it, as Kindle
Previewer 4.0.3 writes it: one run raised (or lowered) over the outer text,
and a second, later run over the inner text with its own shift and a smaller
size (#536, my probe 2026-10-29). kfxgen let the outer superscript win, so
`x<sup>2<sub>i</sub></sup>` showed "i" raised on a Paperwhite (5.19.2),
where Previewer's file lowers it (#522)."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import SUPERSCRIPT_FONT_SIZE, NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

pytestmark = pytest.mark.unit


def _inline_css(e):
    return dict(
        (k.strip(), v.strip())
        for k, _, v in (d.partition(":") for d in (e.get("style") or "").split(";"))
        if v
    )


def _runs(tmp_path, body, style_resolver=None):
    """{paragraph text: [(run text, $44, $16)]} in written order."""
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        base_href="c.xhtml",
        style_resolver=style_resolver,
    )
    out = tmp_path / "n.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "One", "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not (hasattr(e, "get") and e.get("$145")):
                continue
            t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            runs = []
            for r in e.get("$142") or ():
                r = getattr(r, "value", r)
                a, n = int(r["$143"]), int(r["$144"])
                y = styles.get(str(r.get("$157")), {})
                size = y.get("$16")
                runs.append(
                    (
                        t[a : a + n],
                        str(y.get("$44")),
                        float(getattr(size, "value", size)["$307"]) if size else None,
                    )
                )
            got[t] = runs
    return got


S = SUPERSCRIPT_FONT_SIZE


def test_a_subscript_inside_a_superscript_is_layered_on_it(tmp_path):
    runs = _runs(tmp_path, "<p>N1 x<sup>2<sub>i</sub></sup> end.</p>")["N1 x2i end."]
    assert [(t, v) for t, v, _s in runs] == [
        ("2", "$370"),
        ("i", "$370"),
        ("i", "$371"),
    ]
    assert runs[2][2] == pytest.approx(S * S)


def test_a_superscript_inside_a_subscript_is_layered_on_it(tmp_path):
    runs = _runs(tmp_path, "<p>N2 y<sub>j<sup>3</sup></sub> end.</p>")["N2 yj3 end."]
    assert [(t, v) for t, v, _s in runs] == [
        ("j", "$371"),
        ("3", "$371"),
        ("3", "$370"),
    ]


def test_three_levels_each_layer_once(tmp_path):
    runs = _runs(tmp_path, "<p>N5 q<sup>r<sub>s<sup>t</sup></sub></sup> end.</p>")[
        "N5 qrst end."
    ]
    assert [(t, v) for t, v, _s in runs] == [
        ("r", "$370"),
        ("s", "$370"),
        ("s", "$371"),
        ("t", "$370"),
        ("t", "$371"),
        ("t", "$370"),
    ]
    assert runs[-1][2] == pytest.approx(S**3)


def test_a_single_shift_is_unchanged(tmp_path):
    runs = _runs(tmp_path, "<p>N6 plain<sup>9</sup> and H<sub>2</sub>O.</p>")[
        "N6 plain9 and H2O."
    ]
    assert [(t, v) for t, v, _s in runs] == [("9", "$370"), ("2", "$371")]


def test_text_top_inside_a_superscript_keeps_the_superscripts_size(tmp_path):
    """Previewer layers text-top on the inner run with no size of its own,
    so it stays the superscript's size, a sized span's too (#536)."""
    runs = _runs(
        tmp_path,
        "<p>N3 a<sup>b<span style='vertical-align:text-top;font-size:10px'>c"
        "</span>d</sup> end.</p>",
        style_resolver=_inline_css,
    )["N3 abcd end."]
    layered = [(t, v, size) for t, v, size in runs if v == "$447"]
    assert layered == [("c", "$447", pytest.approx(S))]
