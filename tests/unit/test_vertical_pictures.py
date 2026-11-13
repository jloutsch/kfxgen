"""Pictures directly in vertical-rl text (#247): the generator's style, and
the walker's `display: block` check. Through calibre, and what Kindle
Previewer 4.0.3 writes (probes PVA, PVAL), in
tests/integration/test_calibre_vertical_pictures.py.
"""

import pytest
from lxml import etree

from kfxgen import converter as conv
from kfxgen.converter import _make_img_token
from kfxgen.native_generator import NativeKFXGenerator
from kfxgen.page_scale import VERTICAL_PT, page_width
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val

pytestmark = pytest.mark.unit

PIC = "p.jpg"  # 400 x 100


def _picture_styles(tmp_path, chapters):
    """Each picture's style, in reading order, per storyline."""
    out = tmp_path / "v.kfx"
    with page_width(VERTICAL_PT):
        NativeKFXGenerator().generate_full_book(
            title="T",
            author="A",
            chapters=chapters,
            images={PIC: jpeg_of(400, 100)},
            output_path=str(out),
            rtl=True,
            writing_mode="vertical-rl",
        )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    found = []

    def walk(entries):
        for e in entries:
            if not hasattr(e, "get"):
                continue
            if str(e.get("$159")) == "$271":
                own = styles.get(str(e.get("$157")), {})
                found.append(
                    {str(k): str(v) for k, v in own.items() if str(k) != "$173"}
                )
            walk(e.get("$146") or [])

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"])
    return found


def _block(text, **extra):
    return {"text": text, "spans": [], **extra}


def _chapter(blocks, mode="vertical-rl"):
    first = _block("縦の段落。", writing_mode=mode)
    return {"title": "章", "text": "x", "blocks": [first, *blocks]}


def test_in_vertical_text_a_picture_is_sized_by_its_height_only(tmp_path):
    styles = _picture_styles(
        tmp_path,
        [
            _chapter(
                [
                    _block(_make_img_token(PIC, "")),
                    _block(_make_img_token(PIC, ""), block_style={"align": "center"}),
                    _block(_make_img_token(PIC, "", "w=100px")),
                    _block(_make_img_token(PIC, "", "w=200px", None, "h=100")),
                    _block(_make_img_token(PIC, "", None, None, "h=50px")),
                    _block(_make_img_token(PIC, ""), clear="both"),
                ]
            )
        ],
    )
    plain, centred, sized, both, tall, cleared = styles
    # 100 px high of a 640 px column.
    assert set(plain) == {"$57"} and "15.625" in plain["$57"]
    assert set(centred) == {"$57", "$580"} and centred["$580"] == "$320"
    # 100 px wide at 0.45pt is 11.25pt high, of 480pt; the markup sized it.
    assert set(sized) == {"$57", "$546"} and "2.344" in sized["$57"]
    # A height given wins over the width: 100 px at 0.75pt.
    assert set(both) == {"$57", "$546"} and "15.625" in both["$57"]
    # 7.8125, rounded half up.
    assert "7.813" in tall["$57"]
    # The block's clear stays on the picture.
    assert set(cleared) == {"$57", "$628"} and cleared["$628"] == "$421"


def test_in_a_horizontal_file_the_picture_is_unchanged(tmp_path):
    # A file that isn't vertical-rl: kfxgen's block picture, by width.
    (style,) = _picture_styles(
        tmp_path, [_chapter([_block(_make_img_token(PIC, ""))], mode="horizontal-tb")]
    )
    # Unaligned, as its block is (#769).
    assert "$56" in style and "$580" not in style


def _resolve(elem):
    return {
        k.strip(): v.strip()
        for k, v in (
            part.split(":", 1)
            for part in (elem.get("style") or "").split(";")
            if ":" in part
        )
    }


BLOCK = '<img src="a.jpg" alt="" style="display: block"/>'


@pytest.mark.parametrize(
    "inner, lone",
    [
        (BLOCK, True),
        ('<img src="a.jpg" alt=""/>', False),
        (f"Words {BLOCK}", False),
        (f"{BLOCK} words", False),
        (f"{BLOCK}{BLOCK}", False),
    ],
)
def test_only_a_lone_display_block_picture_counts(inner, lone):
    p = etree.fromstring(f"<p>{inner}</p>")
    assert conv._lone_block_picture(p, _resolve) is lone
    assert conv._lone_block_picture(p, None) is False
