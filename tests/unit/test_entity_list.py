"""The container's entity list ($419) names each entity once, as Kindle
Previewer 4.0.3 writes it (#520): the cover's resource and location were
listed twice."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val

COVER = jpeg_of(600, 800)
OTHER = jpeg_of(300, 200)
TOK = "\x00IMG\x01{}\x01\x00".format


@pytest.mark.unit
@pytest.mark.parametrize(
    "images",
    [{"b.jpg": OTHER}, {"cover.jpg": COVER, "b.jpg": OTHER}],
    ids=["cover-alone", "cover-in-text"],
)
def test_each_entity_is_listed_once(tmp_path, images):
    text = f"A {TOK('cover.jpg')} B {TOK('b.jpg')} C"
    out = tmp_path / "e.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "Open", "text": "Open.", "blocks": [{"text": "Open."}]},
            {"title": "One", "text": text, "blocks": [{"text": text}]},
        ],
        cover_image=COVER,
        images=images,
        output_path=str(out),
    )
    (entities,) = by_type(load_fragments(out), "$419")
    names = [str(n) for c in val(entities)["$252"] for n in c["$181"]]
    assert "cover_img" in names
    assert [n for n in names if names.count(n) > 1] == []
