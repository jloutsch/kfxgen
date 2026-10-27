"""A small picture keeps its shape: a width only, as for any other picture,
with no height and no baseline shift (#462). kfxgen wrote every picture up
to 300 px with an aspect ratio up to 1.4 as a 3em square, shifted down 9 pt,
copied from an older reference file. Kindle Previewer 4.0.3 sizes a picture
alone in its paragraph by its pixel width."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val
from tests.unit.test_native_generator import _png


@pytest.mark.unit
@pytest.mark.parametrize("dims", [(16, 16), (300, 220), (220, 300)])
def test_a_small_picture_keeps_its_shape(tmp_path, dims):
    out = tmp_path / "s.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": "\x00IMG\x01p.png\x01\x00"}],
        output_path=str(out),
        images={"p.png": _png(*dims)},
    )
    frags = load_fragments(out)
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    (style,) = [
        styles[str(e["$157"])]
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and str(e.get("$159")) == "$271"
    ]
    assert str(style["$56"]["$306"]) == "$314"
    assert float(str(style["$56"]["$307"])) == round(dims[0] / 496 * 100, 3)
    assert "$57" not in style
    assert "$31" not in style
