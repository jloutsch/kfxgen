"""A book with no cover writes no landmarks ($236), as Kindle Previewer
4.0.3 writes none (#460; checked against Previewer's own file for a
coverless book, 2026-10-26). kfxgen wrote a cover landmark ($233) pointing
at the first chapter, which the device may draw as a full-screen cover."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val


def _containers(tmp_path, cover):
    out = tmp_path / "n.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [
            {
                "title": "Chapter 1",
                "text": "Body.",
                "blocks": [{"text": "Body.", "spans": []}],
            }
        ],
        output_path=str(out),
        cover_image=cover,
    )
    (nav,) = by_type(load_fragments(out), "$389")
    return [str(getattr(c, "value", c)["$235"]) for c in val(nav)[0]["$392"]]


@pytest.mark.unit
def test_a_book_with_no_cover_has_no_landmarks(tmp_path):
    assert "$236" not in _containers(tmp_path, None)


@pytest.mark.unit
def test_a_book_with_a_cover_keeps_its_cover_landmark(tmp_path):
    assert "$236" in _containers(tmp_path, jpeg_of(600, 800))
