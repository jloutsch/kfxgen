"""The cover's picture, used in the text too, is shown there, sharing the
cover's resource rather than storing its bytes twice (#475)."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

COVER = jpeg_of(600, 800)
OTHER = jpeg_of(300, 200)


def _book(tmp_path, images):
    tok = "\x00IMG\x01{}\x01\x00".format
    text = f"A {tok('cover.jpg')} B {tok('b.jpg')} C"
    out = tmp_path / "c.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "One", "text": text, "blocks": [{"text": text, "spans": []}]}
        ],
        cover_image=COVER,
        images=images,
        output_path=str(out),
    )
    frags = load_fragments(out)
    pictures = [
        str(e["$175"])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$175") is not None
    ]
    return pictures, sorted(str(f.fid) for f in by_type(frags, "$164"))


@pytest.mark.unit
def test_the_covers_picture_in_the_text_shares_its_resource(tmp_path):
    pictures, resources = _book(tmp_path, {"cover.jpg": COVER, "b.jpg": OTHER})
    # The cover page (#32), then the text's two pictures; one resource each.
    assert pictures == ["cover_img", "cover_img", "img_0"]
    assert resources == ["cover_img", "img_0"]


@pytest.mark.unit
def test_a_different_picture_named_like_the_cover_is_its_own(tmp_path):
    pictures, resources = _book(
        tmp_path, {"cover.jpg": jpeg_of(400, 400), "b.jpg": OTHER}
    )
    assert pictures == ["cover_img", "img_1", "img_0"]
    assert resources == ["cover_img", "img_0", "img_1"]
