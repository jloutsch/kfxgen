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
                ]
            )
        ],
    )
    plain, centred, sized = styles
    # 100 px high of a 640 px column.
    assert set(plain) == {"$57"} and "15.625" in plain["$57"]
    assert set(centred) == {"$57", "$580"} and centred["$580"] == "$320"
    # 100 px wide is 25 px high: 25/640, and the markup sized it.
    assert set(sized) == {"$57", "$546"} and "3.906" in sized["$57"]


def test_in_a_horizontal_file_the_picture_is_unchanged(tmp_path):
    # A file that isn't vertical-rl: kfxgen's block picture, by width.
    (style,) = _picture_styles(
        tmp_path, [_chapter([_block(_make_img_token(PIC, ""))], mode="horizontal-tb")]
    )
    assert "$56" in style and style["$580"] == "$320"


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
