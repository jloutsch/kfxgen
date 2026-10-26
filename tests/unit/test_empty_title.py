"""A chapter titled "" gets no heading entry: an empty one sat at the next
paragraph's position, and kfxlib reported "position_id content extra" and
"position_map has extra eids" (#473; direct generator calls only, calibre
never gives one)."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator


@pytest.mark.unit
@pytest.mark.parametrize("title", ["", " "])
def test_an_empty_title_writes_no_heading(title):
    chunks = NativeKFXGenerator()._build_chapter_content(
        [{"title": title, "text": "Body one.", "blocks": None}]
    )["all_chunks"]
    texts = [c["text"] for c in chunks if c["type"] == "text"]
    assert texts == ["Body one."]
