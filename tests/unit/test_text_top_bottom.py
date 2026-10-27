"""vertical-align text-top and text-bottom align a run with the top or the
bottom of the line; they neither raise nor shrink it (#485). Kindle
Previewer 4.0.3 writes $44 $447 and $449 with no size change (probed
2026-10-26); kfxgen wrote a 0.75rem superscript and subscript."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _run_style(tmp_path, value):
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<p>Before <span>RUN</span> after.</p></body></html>"
    )
    root = etree.fromstring(xhtml.encode())
    span = root.find(".//{http://www.w3.org/1999/xhtml}span")

    def resolver(elem):
        return {"vertical-align": value} if elem is span else {}

    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=resolver, base_href="c.xhtml"
    )
    out = tmp_path / "v.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    (span_style,) = [
        styles[str(sp["$157"])]
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get")
        for sp in e.get("$142") or []
        if int(sp["$144"]) == 3
    ]
    return span_style


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "symbol"), [("text-top", "$447"), ("text-bottom", "$449")]
)
def test_text_top_and_bottom_are_aligned_not_raised(tmp_path, value, symbol):
    style = _run_style(tmp_path, value)
    assert str(style["$44"]) == symbol
    assert "$16" not in style


@pytest.mark.unit
def test_super_is_still_a_superscript(tmp_path):
    style = _run_style(tmp_path, "super")
    assert str(style["$44"]) == "$370"
    assert "$16" in style
