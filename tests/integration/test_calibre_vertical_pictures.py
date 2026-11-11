"""Pictures directly in vertical text, through calibre (#247).

Probe PVA (Kindle Previewer 4.0.3, decoded; a vertical Japanese book) and
PVAL (the same with an ltr spine, whose vertical-rl file is vertical but the
book isn't, #746):
- A picture in vertical text is a block `$271` sized by its height, `$57` =
  pixel height against the column (640 px in a vertical book, 512 with an ltr
  spine), capped at 100%. No other key, except `$580` from the block's
  text-align left, center or right, and `$546 $377` when the markup sizes it.
  kfxgen wrote its horizontal block picture: `$56` by width, `$580` centred,
  `$65`, `$785`, `$546`.
- A percentage width or height keeps its axis; a width in pixels gives the
  height at the picture's proportions.
- A linked one is inline in a plain container that carries the link.

And in a vertical book's horizontal block (P247k K02 and K10, PVA H02, H10):
- a picture in a `<p>` sits in a container carrying the paragraph's
  text-indent;
- a picture set to `display: block` is a block `$271` with its width, not
  inline in a container.

It needs calibre installed, so it is `slow` and skips when `ebook-convert`
is not found.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import load_fragments  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.integration.test_calibre_vertical_writing import (  # noqa: E402
    REST,
    VERT,
    _pictures,
    _write,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _img(alt, src="w.jpg", attrs=""):
    return f'<img src="{src}" alt="{alt}"{attrs}/>'


WIDE = ' style="width: 50%"'
HIGH = ' style="height: 50%"'
ATTRS = ' width="100" height="25"'
BLOCK = ' style="display: block"'
LINK = '<a href="c2.xhtml#t">{}</a>'
INDENT = '<p style="text-indent: 1em">{}</p>'
BODY = (
    "<h1>第一章</h1><p>V00 縦の段落。</p>"
    f"<div>{_img('V01')}</div>"
    f'<div style="text-align: center">{_img("V04")}</div>'
    f'<div style="text-align: left">{_img("V05")}</div>'
    f'<div style="text-align: right">{_img("V06")}</div>'
    f"<div>{LINK.format(_img('V07'))}</div>"
    f"<div>{_img('V09', attrs=WIDE)}</div>"
    f"<div>{_img('V10', attrs=HIGH)}</div>"
    f"<div>{_img('V13', 't.jpg')}</div>"
    f"<div>{_img('V15', attrs=ATTRS)}</div>"
    f"{INDENT.format(LINK.format(_img('V18')))}"
    f'<div class="hz">{INDENT.format(_img("H02"))}</div>'
    f'<div class="hz">{_img("H10", attrs=BLOCK)}</div>'
    f'<div class="hz">{INDENT.format(LINK.format(_img("H18")))}</div>'
    f'<div class="hz">{_img("H19", attrs=BLOCK)}<p>H19 横の段落。</p></div>'
    f'<div class="hz">{_img("K01")}</div>'
    "<p>V99 縦の段落。</p>"
)


def _book(calibre, name, rtl):  # noqa: F811
    from tests._helpers import decodable_jpeg_of

    tmp, _env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    css = f"html {{ {VERT} }}" + REST
    _write(
        epub,
        rtl,
        css,
        css,
        ch1=BODY,
        images=[
            ("w.jpg", decodable_jpeg_of(400, 100)),
            ("t.jpg", decodable_jpeg_of(200, 1000)),
        ],
    )
    _ebook_convert(calibre, epub, kfx, True)
    return _pictures(load_fragments(kfx))


@pytest.fixture(scope="module")
def vertical(calibre):  # noqa: F811
    return _book(calibre, "pva", True)


@pytest.fixture(scope="module")
def ltr(calibre):  # noqa: F811
    return _book(calibre, "pval", False)


def _pct(own, key):
    """A `$56`/`$57` percentage, as a number."""
    value = own[key]
    assert "$314" in value, value
    return float(value.split("Decimal('")[1].split("'")[0])


@pytest.mark.parametrize("pictures, height", [("vertical", 15.625), ("ltr", 19.531)])
def test_a_picture_in_vertical_text_is_sized_by_its_height(request, pictures, height):
    # PVA, PVAL V01: 100 px high, of a 640 px column (512 with an ltr spine).
    entry, own, _parent, _around = request.getfixturevalue(pictures)["V01"]
    assert entry.get("$601") is None
    assert set(own) == {"$57"} and _pct(own, "$57") == height


@pytest.mark.parametrize(
    "alt, align", [("V04", "$320"), ("V05", "$59"), ("V06", "$61")]
)
def test_its_blocks_alignment_is_its_580(vertical, alt, align):
    _entry, own, _parent, _around = vertical[alt]
    assert set(own) == {"$57", "$580"} and own["$580"] == align


def test_a_tall_picture_is_capped_at_the_column(vertical):
    # V13: 1000 px high is more than the column: 100%.
    _entry, own, _parent, _around = vertical["V13"]
    assert set(own) == {"$57"} and _pct(own, "$57") == 100


def test_a_percentage_keeps_its_axis(vertical):
    # V09, V10: width 50% is `$56`, height 50% `$57`, both with `$546`.
    _entry, own, _parent, _around = vertical["V09"]
    assert set(own) == {"$56", "$546"} and _pct(own, "$56") == 50
    _entry, own, _parent, _around = vertical["V10"]
    assert set(own) == {"$57", "$546"} and _pct(own, "$57") == 50


def test_a_width_in_pixels_gives_the_height(vertical):
    # V15: width 100 of a 400x100 picture is 25 px high: 25/640.
    _entry, own, _parent, _around = vertical["V15"]
    assert set(own) == {"$57", "$546"} and _pct(own, "$57") == 3.906


def test_a_linked_picture_in_vertical_text_is_inline_in_a_plain_container(
    vertical,
):
    # V07: the link on a container with no style, the picture inline.
    entry, own, parent, around = vertical["V07"]
    assert str(entry.get("$601")) == "$283" and set(own) == {"$57"}
    assert parent.get("$142") and around == {}


@pytest.mark.parametrize("alt", ["H02", "H18", "V18"])
def test_a_paragraphs_indent_is_on_its_pictures_container(vertical, alt):
    # PVA H02, P247k K02: the container carries the <p>'s text-indent; a
    # linked one's too, in a horizontal block (H18) and in vertical text
    # (V18).
    entry, _own, parent, around = vertical[alt]
    assert str(entry.get("$601")) == "$283"
    assert set(around) == {"$36"} and "$308" in around["$36"]
    assert bool(parent.get("$142")) == (alt != "H02")


def test_without_an_indent_the_container_has_no_style(vertical):
    # P247k K01.
    _entry, _own, _parent, around = vertical["K01"]
    assert around == {}


@pytest.mark.parametrize("pictures", ["vertical", "ltr"])
@pytest.mark.parametrize("alt", ["H10", "H19"])
def test_a_display_block_picture_in_a_horizontal_block_is_a_block(
    request, pictures, alt
):
    # PVA, PVAL H10, P247k K10: a block `$271` with only its width, in the
    # horizontal block's own container; also beside a paragraph (H19).
    entry, own, _parent, around = request.getfixturevalue(pictures)[alt]
    assert entry.get("$601") is None and set(own) == {"$56"}
    assert around.get("$560") == "$557"
