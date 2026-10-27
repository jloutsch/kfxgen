"""Content features ($585) declared only when the book has them, by
kfxlib's own rules (#455): kfxgen declared reflow-section-size 9, yj_hdv
and yj_jpg_rst_marker_present for every book.

- reflow-section-size: 1, left undeclared, for a largest section of up to
  65,536 positions; else min((n - 65536) // 16384 + 2, 256).
- yj_hdv: a picture over 1920 px on a side.
- yj_jpg_rst_marker_present: a JPEG holding a restart marker (FF D0-D7)."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val


def _features(tmp_path, text="Body.", images=None):
    out = tmp_path / "f.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": text}],
        output_path=str(out),
        images=images or {},
    )
    (frag,) = by_type(load_fragments(out), "$585")
    return {
        str(f["$492"]): int(f["$589"]["version"]["$587"]) for f in val(frag)["$590"]
    }


@pytest.mark.unit
def test_a_small_book_declares_only_what_it_has(tmp_path):
    assert _features(tmp_path) == {
        "yj_table": 1,
        "reflow-style": 1,
        "CanonicalFormat": 1,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("chars", "size"), [(60_000, None), (100_000, 4), (300_000, 16)]
)
def test_reflow_section_size_follows_the_largest_section(tmp_path, chars, size):
    words = ("word " * (chars // 5)).strip()
    text = "\n\n".join(words[i : i + 1000] for i in range(0, len(words), 1000))
    assert _features(tmp_path, text).get("reflow-section-size") == size


@pytest.mark.unit
def test_hdv_only_for_a_picture_over_1920(tmp_path):
    tok = "\x00IMG\x01{}\x01\x00".format
    big = {"b.jpg": jpeg_of(2000, 100)}
    small = {"s.jpg": jpeg_of(1920, 100)}
    assert "yj_hdv" in _features(tmp_path, tok("b.jpg"), big)
    assert "yj_hdv" not in _features(tmp_path, tok("s.jpg"), small)


@pytest.mark.unit
def test_rst_marker_only_for_a_jpeg_holding_one(tmp_path):
    tok = "\x00IMG\x01{}\x01\x00".format
    with_rst = {"r.jpg": jpeg_of(400, 300) + b"\xff\xd0"}
    without = {"p.jpg": jpeg_of(400, 300)}
    assert "yj_jpg_rst_marker_present" in _features(tmp_path, tok("r.jpg"), with_rst)
    assert "yj_jpg_rst_marker_present" not in _features(tmp_path, tok("p.jpg"), without)
