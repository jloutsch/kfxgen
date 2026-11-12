"""A vertical-rl block inside a horizontal-tb block of vertical text,
through calibre (#247, QA's P749 Q09c).

Kindle Previewer 4.0.3 (probe PSB, decoded; a vertical Japanese book and the
same with an ltr spine, alike): the vertical block is a `$269` container
with `$560: $559`, `$57` 100%, `$478` and `$784`, inside the horizontal
block's container (R01, R02); a <div> of two paragraphs is one (R03);
`vertical-lr` is a plain paragraph (R04). Its margin-top and -bottom meet
the horizontal block's, its left and right stay on it in line heights (R06).
Text in it keeps its alignment (R07); a picture in it is sized by height as
in vertical text (R08, R09). kfxgen wrote it as a horizontal paragraph.

It needs calibre installed, so it is `slow` and skips when `ebook-convert`
is not found.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.integration.test_calibre_vertical_writing import (  # noqa: E402
    REST,
    VERT,
    _write,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

VLR = "writing-mode: vertical-lr; -webkit-writing-mode: vertical-lr;"
# As the probe's: paragraphs without margins, so only R06's own count.
CSS = (
    f"html {{ {VERT} }} p {{ margin: 0; text-indent: 1em }}"
    f" .vt {{ {VERT} }} .vlr {{ {VLR} }}"
    " .bx { border: 1px solid black } .fill { background-color: #dddddd }" + REST
)
BODY = (
    "<h1>第一章</h1><p>V00 縦の段落。</p>"
    '<div class="hz"><p class="vt">R01 横の箱の中の縦。</p></div>'
    '<div class="hz"><p>R02a Before.</p><p class="vt">R02 縦。</p>'
    "<p>R02b After.</p></div>"
    '<div class="hz"><div class="vt"><p>R03a 縦一。</p><p>R03b 縦二。</p></div></div>'
    '<div class="hz"><p class="vlr">R04 左から右。</p></div>'
    '<div class="hz"><p class="vt" style="margin: 1em">R06 余白。</p></div>'
    "<p>V06 縦の段落。</p>"
    '<div class="hz"><p class="vt" style="margin: 2em 1em">R10 上下。</p></div>'
    "<p>V10 縦の段落。</p>"
    '<div class="hz" style="text-align: center"><p class="vt">R07 中央。</p></div>'
    '<div class="hz"><div class="vt"><p>R09 縦。</p><img src="w.jpg" alt="R09"/>'
    "</div></div>"
    # QA's Q763: T01 a first paragraph with margins; T02 a padded vertical
    # block; T06, T07 bordered and filled; T09 a vertical list; T12 a
    # heading first.
    '<div class="hz"><div class="vt"><p style="margin: 1em 2em">T01a 縦一。</p>'
    "<p>T01b 縦二。</p></div></div>"
    '<div class="hz"><div class="vt" style="padding: 1em"><p>T02 余白。</p></div></div>'
    '<div class="hz"><div class="vt bx"><p>T06 枠。</p></div></div>'
    '<div class="hz"><div class="vt fill"><p>T07 塗り。</p></div></div>'
    '<div class="hz"><ul class="vt"><li>T09 項目。</li></ul></div>'
    '<div class="hz"><div class="vt"><h2>T12 見出し</h2><p>T12b 後。</p></div></div>'
    "<p>V99 縦の段落。</p>"
)


def _tree(frags):
    """{first word of each text entry or alt of each picture: (its style,
    [its containers' styles, innermost first, each with its "@eid" and "@type"])}."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}

    def style(e):
        own = styles.get(str(e.get("$157")), {})
        return {str(k): str(v) for k, v in own.items() if str(k) != "$173"}

    def walk(entries, chain):
        for e in entries:
            if not hasattr(e, "get"):
                continue
            ref = e.get("$145")
            if ref:
                text = str(texts[str(ref["name"])][ref["$403"]])
                # A list item's first word after its bullet.
                words = [w for w in text.split() if w != "•"] or [text]
                out[words[0]] = (style(e), chain)
            elif str(e.get("$159")) == "$271":
                out[str(e.get("$584"))] = (style(e), chain)
            kids = e.get("$146")
            if kids:
                walk(
                    kids,
                    [
                        {
                            **style(e),
                            "@eid": str(e.get("$155")),
                            "@type": str(e.get("$159")),
                        },
                        *chain,
                    ],
                )

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], [])
    return out


def _book(calibre, name, rtl):  # noqa: F811
    from tests._helpers import decodable_jpeg_of

    tmp, _env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _write(
        epub, rtl, CSS, CSS, ch1=BODY, images=[("w.jpg", decodable_jpeg_of(400, 100))]
    )
    _ebook_convert(calibre, epub, kfx, True)
    return _tree(load_fragments(kfx))


@pytest.fixture(scope="module", params=[True, False], ids=["rtl", "ltr"])
def tree(calibre, request):  # noqa: F811
    return _book(calibre, f"psb-{request.param}", request.param)


def _is_vertical(st):
    return (
        st.get("$560") == "$559"
        and "100" in st.get("$57", "")
        and st.get("$478") == "True"
        and st.get("$784") == "True"
    )


def _is_horizontal(st):
    return st.get("$560") == "$557"


@pytest.mark.parametrize("words", ["R01", "R02", "R03a", "R03b", "R06", "R07"])
def test_a_vertical_block_in_a_horizontal_one_is_a_vertical_container(tree, words):
    _st, chain = tree[words]
    assert _is_vertical(chain[0]), chain[0]
    # A plain container, as Previewer's: not a box ($270).
    assert chain[0]["@type"] == "$269"
    assert _is_horizontal(chain[1]), chain[1]


def test_its_neighbours_stay_horizontal(tree):
    # R02: the paragraphs around it are in the horizontal block only.
    for words in ("R02a", "R02b"):
        _st, chain = tree[words]
        assert _is_horizontal(chain[0])


def test_two_paragraphs_share_one_container(tree):
    # R03: one vertical container round both.
    first, second = tree["R03a"][1][0], tree["R03b"][1][0]
    assert _is_vertical(first) and first["@eid"] == second["@eid"]


def test_vertical_lr_is_a_plain_paragraph(tree):
    # R04: Previewer writes no vertical container for vertical-lr.
    _st, chain = tree["R04"]
    assert _is_horizontal(chain[0])


def test_its_margins_are_laid_out_in_the_horizontal_block(tree):
    # R06, margin: 1em. Its left and right stay on the vertical container,
    # in line heights; its top and bottom meet the horizontal block's.
    _st, chain = tree["R06"]
    for key in ("$48", "$50"):
        assert "0.833333" in chain[0][key] and "$310" in chain[0][key]
    assert "$47" not in chain[0] and "$49" not in chain[0]
    for key in ("$47", "$49"):
        assert "0.833333" in chain[1][key]


def test_its_text_keeps_its_alignment(tree):
    # R07: centred by the horizontal block around it.
    st, _chain = tree["R07"]
    assert st.get("$34") == "$320"


def test_a_picture_in_it_is_sized_by_height(tree, request):
    # R09: as in vertical text, `$57` by height: 100 px of 640 (512 with an
    # ltr spine). Previewer also puts it in a plain container with an ltr
    # spine; kfxgen doesn't (in the PR's Not covered).
    st, chain = tree["R09"]
    height = 15.625 if "rtl" in request.node.callspec.id else 19.531
    assert set(st) == {"$57"} and str(height) in st["$57"]
    assert any(_is_vertical(c) for c in chain)


def _lh(value):
    """A `{$307: Decimal('x'), $306: $310}` value's x, in line heights."""
    assert "$310" in value, value
    return float(value.split("Decimal('")[1].split("'")[0])


def test_its_first_blocks_margin_before_is_its_own(tree):
    # T01: the first paragraph's margin before (its right, 2em) meets the
    # vertical block's, on its container as `$50` 1.66667lh, not on the
    # horizontal block above it.
    st, chain = tree["T01a"]
    assert abs(_lh(chain[0]["$50"]) - 1.66667) < 1e-5
    assert "$47" not in chain[1] and "$49" not in chain[1]
    # The paragraph keeps the rest, turned as vertical text: its top and
    # bottom 1em, its left (after it) 1.66667lh.
    assert "$50" not in st
    assert "1" in st["$47"] and "1" in st["$49"]
    assert abs(_lh(st["$48"]) - 1.66667) < 1e-5


def test_a_heading_first_gives_its_margin_to_the_block(tree):
    # T12: an h2's margin before, on the vertical container.
    _st, chain = tree["T12"]
    assert _is_vertical(chain[0]) and "$50" in chain[0]
    assert "$47" not in chain[1]


def test_its_padding_is_by_its_own_axes(tree):
    # T02, padding: 1em: top and bottom 1em, left and right 0.833333lh.
    _st, chain = tree["T02"]
    for key in ("$52", "$54"):
        assert "$308" in chain[0][key] and "'1'" in chain[0][key]
    for key in ("$53", "$55"):
        assert abs(_lh(chain[0][key]) - 0.833333) < 1e-6


@pytest.mark.parametrize("words, key", [("T06", "$88"), ("T07", "$70")])
def test_a_bordered_or_filled_vertical_block_is_a_box(tree, words, key):
    # T06, T07: a `$270` with its border or fill and the vertical keys.
    _st, chain = tree[words]
    assert chain[0]["@type"] == "$270" and key in chain[0]
    assert _is_vertical(chain[0])


def test_a_vertical_list_in_a_horizontal_block_is_vertical(tree):
    # T09: Previewer writes the list itself vertical (`$276`); kfxgen writes
    # its items in a vertical container.
    _st, chain = tree["T09"]
    assert any(_is_vertical(c) for c in chain)


def test_a_vertical_paragraphs_own_margins_go_by_their_sides(tree):
    # PSB3 R10, `<p class="vt" style="margin: 2em 1em">`: its top and bottom
    # (2em) on the horizontal block, its left and right (1em) on itself.
    _st, chain = tree["R10"]
    for key in ("$48", "$50"):
        assert abs(_lh(chain[0][key]) - 0.833333) < 1e-6
    for key in ("$47", "$49"):
        assert abs(_lh(chain[1][key]) - 1.66667) < 1e-5
