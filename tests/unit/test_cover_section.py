import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.kfxlib_minimal.ion import IS  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402
from tests._helpers import jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402

pytestmark = pytest.mark.unit


def _build(text, images, cover=None):
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    try:
        kwargs = {
            "title": "T",
            "author": "A",
            "chapters": [{"title": "Pages", "text": text}],
            "output_path": path,
            "images": images,
        }
        if cover is not None:
            kwargs["cover_image"] = cover
        gen.generate_full_book(**kwargs)
        return load_fragments(path)
    finally:
        os.unlink(path)


class TestCoverSection:
    def test_cover_section_is_a_block_of_the_cover_size(self):
        frags = _build("Some prose.", {}, cover=jpeg_of(1495, 2200))
        sections = {str(f.fid): val(f) for f in by_type(frags, "$260")}
        cover = sections["c0"][IS("$141")][0]
        assert (cover[IS("$66")], cover[IS("$67")]) == (1495, 2200)
        assert str(cover[IS("$159")]) == "$270"
        assert str(cover[IS("$156")]) == "$326"
        assert str(cover[IS("$140")]) == "$320"
        body = sections["c1"][IS("$141")][0]
        assert str(body[IS("$159")]) == "$269"
        assert IS("$66") not in body

    def test_a_cover_of_unknown_size_keeps_the_inline_section(self):
        cover = b"\xff\xd8\xff\xfe\x00\x02\x0a\x0a\xff\xd9"  # COM segment, no SOF
        assert NativeKFXGenerator._detect_image_dimensions(cover) == (None, None)
        frags = _build("Some prose.", {}, cover=cover)
        sections = {str(f.fid): val(f) for f in by_type(frags, "$260")}
        entry = sections["c0"][IS("$141")][0]
        assert str(entry[IS("$159")]) == "$269"
        assert IS("$66") not in entry
