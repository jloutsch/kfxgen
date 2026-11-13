"""A block picture's place, through calibre (#769).

Kindle Previewer 4.0.3 (probes PBI, PBJ and PBR, decoded) places a picture
alone in its block as the block places its text:
- `$580` from the block's text-align: left or start `$59` (J01, J19),
  center `$320`, inherited too (J03), right `$61` (J12). None when the block
  is justified or unaligned (J02, B01), and none in an rtl book either (R02).
  `display: block` with auto side margins centres it (J04); auto margins on
  an inline one don't (J16).
- `$48`: the picture's own left margin (J05), its block's (B02), the
  containers' (B03, J11) and, on the block's first line only (J20), its
  text-indent (B01, J07), a hanging one taken off (J08). An em indent is in
  the block's font size (J17); a px one 0.45pt (J18). The same in an rtl
  book (R01, R06).
- `$50`: the right margins (B02, J12, R03).
- A linked picture is inline in a container that takes the block's
  alignment and indent (B07).
kfxgen centred every block picture and wrote none of the margins: the
Paperwhite (5.19.2) showed Previewer's placed and kfxgen's centred (S769).

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
from tests.integration.test_calibre_vertical_writing import _write  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = " ".join(
    [
        "p { margin: 0.5em 0; text-indent: 1.5em }",
        "p.flat { text-indent: 0 }",
        "p.left { text-align: left; text-indent: 0 }",
        "p.just { text-align: justify; text-indent: 0 }",
        "p.start { text-align: start; text-indent: 0 }",
        "div.c { text-align: center }",
        "img.auto { display: block; margin: 0 auto }",
        "img.ownml { margin-left: 2em }",
        "img.inlauto { margin-left: auto; margin-right: auto }",
        "img.blk { display: block }",
        "p.mlind { margin-left: 2em }",
        "p.hang { text-indent: -1.5em; margin-left: 3em }",
        "p.ml { margin-left: 2em; margin-right: 2em; text-indent: 0 }",
        "div.ml3 { margin-left: 3em }",
        "p.rightmr { text-align: right; margin-right: 2em; text-indent: 0 }",
        "p.cml { text-align: center; margin-left: 2em; text-indent: 0 }",
        "p.big { font-size: 2em }",
        "p.px { text-indent: 20px }",
        "p.mr { margin-right: 2em; text-indent: 0 }",
        "div.mr3 { margin-right: 3em }",
        "p.cen { text-align: center; text-indent: 0 }",
        ".m20 { margin-left: 20%; margin-right: 20% }",
        "p.ml3l { margin-left: 3em; text-indent: 0; text-align: left }",
        "p.pct { text-indent: 10% }",
        "img.neg { margin-left: -1em }",
        "img.ml10 { margin-left: 10% }",
        "img.mln5 { margin-left: -5% }",
        "img.autol { display: block; margin-left: auto; margin-right: 0 }",
    ]
)


def _img(alt, cls=""):
    cls = f' class="{cls}"' if cls else ""
    return f'<img src="c.jpg" alt="{alt}"{cls}/>'


LTR = (
    "<h1>Chapter</h1><p>V00 A paragraph.</p>"
    f"<p>{_img('B01')}</p>"
    f'<p class="ml">{_img("B02")}</p>'
    f'<div class="ml3">{_img("B03")}</div>'
    f'<p><a href="c2.xhtml#t">{_img("B07")}</a></p>'
    f'<p class="left">{_img("J01")}</p>'
    f'<p class="just">{_img("J02")}</p>'
    f'<div class="c"><p class="flat">{_img("J03")}</p></div>'
    f'<p class="flat">{_img("J04", "auto")}</p>'
    f'<p class="flat">{_img("J05", "ownml")}</p>'
    f'<p class="mlind">{_img("J07")}</p>'
    f'<p class="hang">{_img("J08")}</p>'
    f'<div class="ml3"><p class="ml">{_img("J11")}</p></div>'
    f'<p class="rightmr">{_img("J12")}</p>'
    f'<p class="cml">{_img("J13")}</p>'
    f"<p>{_img('J14', 'blk')}</p>"
    f'<p class="flat">{_img("J16", "inlauto")}</p>'
    f'<p class="big">{_img("J17")}</p>'
    f'<p class="px">{_img("J18")}</p>'
    f'<p class="start">{_img("J19")}</p>'
    f"<p>{_img('J20a')}<br/>{_img('J20b')}</p>"
    f"<div>{_img('J21')}</div>"
    f'<p class="cen"><a href="c2.xhtml#t">{_img("K04")}</a></p>'
    f'<p class="ml"><a href="c2.xhtml#t">{_img("K05")}</a></p>'
    f'<div class="mr3"><p class="flat">{_img("K06")}</p></div>'
    f'<p class="mlind"><a href="c2.xhtml#t">{_img("K08")}</a></p>'
    f'<div class="ml3"><p class="flat"><a href="c2.xhtml#t">{_img("L01")}</a></p></div>'
    f'<div class="mr3"><p class="ml"><a href="c2.xhtml#t">{_img("L02")}</a></p></div>'
    # QA's Q770: nested % margins, negative and right-auto ones.
    f'<div class="m20"><div class="m20"><div class="m20"><p class="flat">{_img("Y11")}</p></div></div></div>'
    f'<p class="flat">{_img("Y12", "neg")}</p>'
    f'<p class="flat">{_img("Y17", "autol")}</p>'
    f'<div class="m20"><div class="m20"><p class="flat">{_img("Y26", "ml10")}</p></div></div>'
    f'<div class="m20"><p class="ml3l">{_img("Y31")}</p></div>'
    f'<div class="m20"><div class="m20"><p>{_img("Y32")}</p></div></div>'
    f'<div class="m20"><p class="flat">{_img("Y33", "ml10")}</p></div>'
    f'<div class="m20"><p class="pct">{_img("Y35")}</p></div>'
    f'<div class="m20"><div class="m20"><p class="flat">{_img("Y37", "mln5")}</p></div></div>'
    "<p>V99 A paragraph.</p>"
)
RTL = (
    "<h1>Chapter</h1><p>V00 A paragraph.</p>"
    f"<p>{_img('R01')}</p>"
    f'<p class="flat">{_img("R02")}</p>'
    f'<p class="mr">{_img("R03")}</p>'
    f'<p class="mlind">{_img("R06")}</p>'
    "<p>V99 A paragraph.</p>"
)


def _book(calibre, name, rtl, body):  # noqa: F811
    """{alt: (the picture's style, the style of the entry holding it)}."""
    from tests._helpers import decodable_jpeg_of

    tmp, _env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _write(
        epub,
        rtl,
        CSS,
        CSS,
        lang="ar" if rtl else "en",
        ch1=body,
        images=[("c.jpg", decodable_jpeg_of(300, 200))],
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}

    def style(e):
        own = styles.get(str(e.get("$157")), {}) if e is not None else {}
        return {str(k): str(v) for k, v in own.items() if str(k) != "$173"}

    out = {}

    def walk(entries, parent):
        for e in entries:
            if not hasattr(e, "get"):
                continue
            if str(e.get("$159")) == "$271":
                out[str(e.get("$584"))] = (style(e), style(parent))
            walk(e.get("$146") or [], e)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return out


@pytest.fixture(scope="module")
def ltr(calibre):  # noqa: F811
    return _book(calibre, "pbj", False, LTR)


@pytest.fixture(scope="module")
def rtl(calibre):  # noqa: F811
    return _book(calibre, "pbr", True, RTL)


def _pct(style, key):
    v = style.get(key)
    if v is None:
        return None
    assert "$314" in v, v
    return float(v.split("'")[1])


@pytest.mark.parametrize(
    "alt, align",
    [
        ("B01", None),  # unaligned
        ("J01", "$59"),  # left
        ("J02", None),  # justified
        ("J03", "$320"),  # centred by its container
        ("J04", "$320"),  # display: block, auto margins
        ("J12", "$61"),  # right
        ("J13", "$320"),
        ("J16", None),  # auto margins, inline
        ("J19", "$59"),  # start
        ("J21", None),
    ],
)
def test_aligned_as_its_block(ltr, alt, align):
    assert ltr[alt][0].get("$580") == align


@pytest.mark.parametrize(
    "alt, left, right",
    [
        ("B01", 4.688, None),  # indent 1.5em
        ("B02", 6.25, 6.25),  # its block's margins, 2em
        ("B03", 9.375, None),  # its container's, 3em
        ("J01", None, None),
        ("J05", 6.25, None),  # its own, 2em
        ("J07", 10.938, None),  # margin 2em and indent 1.5em
        ("J08", 4.688, None),  # margin 3em, hanging 1.5em
        ("J11", 15.625, 6.25),  # 3em and 2em
        ("J12", None, 6.25),
        ("J13", 6.25, None),
        ("J14", 4.688, None),  # display: block keeps the indent
        ("J17", 9.375, None),  # 1.5em in a 2em block
        ("J18", 2.344, None),  # 20px
        ("J20a", 4.688, None),  # the first line's indent
        ("J20b", None, None),  # not the second's
        ("K06", None, 9.375),  # its container's right margin, 3em
    ],
)
def test_its_margins_and_indent(ltr, alt, left, right):
    style = ltr[alt][0]
    assert (_pct(style, "$48"), _pct(style, "$50")) == (left, right)


@pytest.mark.parametrize(
    "alt, left, right",
    [
        # Each % of the width the % margins outside it leave, as Previewer
        # writes it (QA's Q770): 20 + 12 + 7.2.
        ("Y11", 39.2, 39.2),
        ("Y12", -3.125, None),  # a negative one kept
        ("Y26", 35.6, 32.0),  # 20 + 12, and the img's 10% of 36
        ("Y31", 29.375, 20.0),  # an em margin isn't scaled
        ("Y32", 45.021, 32.0),  # an em indent is divided by the 0.36 left
        ("Y33", 26.0, 20.0),
        ("Y35", 26.0, 20.0),  # a 10% indent of the 60 left
        ("Y37", 30.2, 32.0),  # -5% of 36
    ],
)
def test_nested_margins_as_previewer_adds_them(ltr, alt, left, right):
    style = ltr[alt][0]
    assert (_pct(style, "$48"), _pct(style, "$50")) == (left, right)


def test_a_left_auto_margin_alone_sets_it_right(ltr):
    # QA's Q770 Y17: display: block; margin-left: auto; margin-right: 0.
    assert ltr["Y17"][0].get("$580") == "$61"


def test_a_linked_one_has_its_blocks_indent_on_its_container(ltr):
    picture, container = ltr["B07"]
    assert "$580" not in picture and "$48" not in picture
    assert "$34" not in container
    assert "1.5" in container["$36"] and "$308" in container["$36"]


def test_a_linked_one_in_a_centred_block_is_centred(ltr):
    # PBK K04.
    assert ltr["K04"][1].get("$34") == "$320"


@pytest.mark.parametrize(
    "alt, left, right",
    [
        ("K05", 6.25, 6.25),  # its block's margins, 2em
        ("K08", 6.25, None),  # 2em, its indent apart
        # Its containers' too (PBL L01: 3em; L02: 2em, and 2em and 3em).
        ("L01", 9.375, None),
        ("L02", 6.25, 15.625),
    ],
)
def test_a_linked_ones_container_has_its_blocks_margins(ltr, alt, left, right):
    # PBK K05, K08.
    _picture, container = ltr[alt]
    assert "$34" not in container
    assert (_pct(container, "$48"), _pct(container, "$50")) == (left, right)


@pytest.mark.parametrize(
    "alt, left, right",
    [
        ("R01", 4.688, None),
        ("R02", None, None),
        ("R03", None, 6.25),
        ("R06", 10.938, None),
    ],
)
def test_in_an_rtl_book_the_same(rtl, alt, left, right):
    style = rtl[alt][0]
    assert "$580" not in style
    assert (_pct(style, "$48"), _pct(style, "$50")) == (left, right)
