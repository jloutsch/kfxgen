"""The page Kindle Previewer measures percentages against (#247, P247j):
384pt (a 512px column) in a horizontal book, 480pt (640px) in a vertical one.
Each conversion in kfxgen reads it from `page_scale`."""

import pytest

from kfxgen import converter as conv
from kfxgen import native_generator as ng
from kfxgen.inline_style import _length_percent
from kfxgen.page_scale import (
    HORIZONTAL_PT,
    VERTICAL_PT,
    column_px,
    em_percent,
    page_pt,
    page_width,
)

#: (page width, its value) for each case: horizontal first, then vertical.
WIDTHS = (HORIZONTAL_PT, VERTICAL_PT)


def test_the_page_is_384pt_unless_set():
    assert (page_pt(), column_px(), em_percent()) == (384.0, 512.0, 3.125)


def test_page_width_sets_it_for_the_block_only():
    with page_width(VERTICAL_PT):
        assert (page_pt(), column_px(), em_percent()) == (480.0, 640.0, 2.5)
    assert page_pt() == 384.0


def test_page_width_is_put_back_after_an_error():
    with pytest.raises(RuntimeError):
        with page_width(VERTICAL_PT):
            raise RuntimeError
    assert page_pt() == 384.0


def _each(fn):
    out = []
    for pt in WIDTHS:
        with page_width(pt):
            out.append(fn())
    return out


@pytest.mark.parametrize(
    "length, want",
    [
        # P247j H14 (2em: 6.25%, 5%) and H06 (20px: 2.34375%, 1.875%).
        (("2", "$308"), [6.25, 5.0]),
        (("20", "$319"), [2.34375, 1.875]),
        (("12", "$318"), [3.125, 2.5]),
        # A % is the page's already.
        (("10", "$314"), [10.0, 10.0]),
    ],
)
def test_a_margin_is_a_share_of_the_page(length, want):
    assert _each(lambda: conv._page_share(length)) == pytest.approx(want)
    assert _each(lambda: _length_percent(length)) == pytest.approx(want)


def test_a_percentage_margin_top_is_ems_of_the_page():
    # P247j H05: margin-top 10% is 3.33333lh (4em) in a vertical book.
    css = {"margin-top": "10%"}
    assert _each(lambda: conv._margin_em(css, "top")) == pytest.approx([3.2, 4.0])


def test_a_box_width_in_px_is_a_share_of_the_page():
    # 300px is 135pt: 35.156% of 384pt, 28.125% of 480pt.
    css = {"width": "300px"}
    assert _each(lambda: conv._box_width(css)) == pytest.approx([35.15625, 28.125])


def test_a_rules_side_margin_is_a_share_of_the_page():
    css = {"width": "50%", "margin-left": "2em"}
    got = _each(lambda: conv._rule_look(css).get("margin_left"))
    assert got == pytest.approx([6.25, 5.0])


def test_padding_at_the_sides_is_a_share_of_the_page():
    # P247j H08: 0.5em is 1.563%, 1.25% in a vertical book; a % at the
    # top is ems of the page (10% = 3.2em, 4em), in lh.
    got = _each(lambda: ng._padding_values({"left": 0.5, "top": (None, 10.0)}, None))
    assert [str(v["$53"]["$307"]) for v in got] == ["1.563", "1.25"]
    assert [str(v["$52"]["$307"]) for v in got] == ["2.66667", "3.33333"]


def test_a_pictures_width_is_a_share_of_the_column():
    # P247j H01: 400px is 78.125% of 512px, 62.5% of 640px.
    got = _each(lambda: ng._inline_float_style("left", None, 400, 100)[2])
    assert got == ["78.125", "62.5"]
    got = _each(lambda: ng._size_in_box("w=320px", {"width": 50}))
    assert got == ["w=125.000%", "w=100.000%"]


def test_a_cells_default_padding_is_1px_of_the_page():
    # P247j H10's 1px sides: 0.117%, 0.094% in a vertical book.
    def side():
        frag = ng.NativeKFXGenerator().build_cell_style_157("s_cell")
        return str(frag.value["$53"]["$307"])

    assert _each(side) == ["0.117", "0.094"]


# --- through extract_blocks_from_html: a % margin or padding (#640, #652) ---

_NONE = ("none", "medium", "currentColor")
_TOP = ("solid", 0.72, "black")
_SIDE = ("solid", 20.0, "black")
_RULES = {
    # Each box has a top border, so it holds the margin of its first
    # paragraph, which is then a % of the box's own width (#652).
    "w": {"width": "300px", "border-sides": (_TOP, _NONE, _NONE, _NONE)},
    "b": {"border-sides": (_TOP, _SIDE, _NONE, _SIDE)},
    "pd": {"padding-sides": ("0", "4em", "0", "4em")},
    "pt": {"padding-sides": ("10%", "0", "0", "0")},
    "m": {"margin-top": "10%"},
}


def _first_x(body):
    """The extracted block of the paragraph "x" in `body`."""
    from lxml import etree

    def resolve(e):
        out = {}
        for k in (e.get("class") or "").split():
            out.update(_RULES[k])
        return out

    doc = etree.fromstring(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>z</p>'
        f"{body}</body></html>".encode()
    )

    def flat(blocks):
        for b in blocks:
            yield from flat(b["blocks"]) if b.get("type") == "box" else (b,)

    (x,) = [
        b
        for b in flat(conv.extract_blocks_from_html(doc, style_resolver=resolve))
        if b.get("text") == "x"
    ]
    return x


@pytest.mark.parametrize(
    "body, want",
    [
        # 10% of a 300px (135pt) box is 1.125em on either page.
        ('<div class="w"><p class="m">x</p><p>y</p></div>', [1.125, 1.125]),
        # A box narrowed by two 20pt borders, and by 4em of padding too: a
        # share of a wider page is more ems (values as kfxgen computes them,
        # pinned so each part of the share follows the page).
        ('<div class="b"><p class="m">x</p><p>y</p></div>', [2.99167, 3.79167]),
        ('<div class="pd b"><p class="m">x</p><p>y</p></div>', [2.19167, 2.99167]),
    ],
    ids=["px-width", "border", "padding-em"],
)
def test_a_percent_margin_in_a_box_is_a_share_of_the_box(body, want):
    got = _each(lambda: _first_x(body)["margin_above"])
    assert got == pytest.approx(want, abs=1e-4)


def test_a_percent_padding_top_is_ems_of_the_page():
    # An unboxed container's 10% padding-top: 3.2em, 4em in a vertical book.
    body = '<div class="pt"><p>x</p><p>y</p></div>'
    assert _each(lambda: _first_x(body)["space_above"]) == pytest.approx([3.2, 4.0])


# --- pictures -----------------------------------------------------------------


@pytest.mark.parametrize(
    "hint, want",
    [
        # A height of 40px (24 of the picture's px at 0.6) on a 400 x 100
        # picture gives it 96px of width.
        ("h=40px", ["18.75", "15"]),
        ("w=10em", ["31.25", "25"]),
        ("w=320px", ["62.5", "50"]),
        ("w=240pt", ["62.5", "50"]),
    ],
)
def test_a_floated_pictures_size_is_a_share_of_the_column(hint, want):
    m = ng._SIZE_HINT_RE.match(hint)
    assert _each(lambda: ng._inline_float_style("left", m, 400, 100)[2]) == want


@pytest.mark.parametrize(
    "img, want",
    [
        # A px width beside a % height is a % of the page: 18pt of 384pt,
        # of 480pt.
        ("style='width:40px;height:50%'", ["4.6875$314", "3.75$314"]),
        # A % width alone is ems of the column: 20% of 512px or 640px, at 16px.
        ("style='width:20%'", ["6.4$308", "8$308"]),
    ],
)
def test_an_inline_pictures_width_is_measured_against_the_column(tmp_path, img, want):
    from tests.unit.test_inline_pictures import _picture_style

    assert _each(lambda: _picture_style(tmp_path, img)["$56"]) == want


def _book_styles(tmp_path, body, images):
    """Every $157 style value in a book of `body`, as {fid: {key: text}}."""
    from lxml import etree

    from tests._kfx_introspect import by_type, load_fragments, val

    doc = etree.fromstring(
        f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'.encode()
    )
    out = tmp_path / "s.kfx"
    ng.NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "One", "text": "x", "blocks": conv.extract_blocks_from_html(doc)}
        ],
        images=images,
        output_path=str(out),
    )

    def show(v):
        return (
            f"{v['$307']}{v['$306']}" if hasattr(v, "get") and "$307" in v else str(v)
        )

    return [
        {str(k): show(v) for k, v in val(f).items()}
        for f in by_type(load_fragments(out), "$157")
    ]


def test_a_pictures_own_width_is_a_share_of_the_column(tmp_path):
    # P247j H01: 400px is 78.125% of 512px, 62.5% of 640px.
    from tests._helpers import decodable_jpeg_of

    images = {"w.jpg": decodable_jpeg_of(400, 100)}

    def width():
        styles = _book_styles(tmp_path, "<p><img src='w.jpg' alt=''/></p>", images)
        (w,) = [s["$56"] for s in styles if "$785" in s]
        return w

    assert _each(width) == ["78.125$314", "62.5$314"]


def test_a_contents_entry_is_indented_2em_of_the_page_a_level(tmp_path):
    # kfxgen's Contents page (#238, #313): 6.25% a level, 5% in a vertical
    # book.
    from kfxgen.converter import _rebuild_contents_page
    from tests.unit.test_nested_contents import (
        NullLog,
        _book,
        _chapters,
        _contents_links,
    )

    def section_a():
        chapters = _chapters(breaks_known=True)
        contents = {
            "title": "Contents",
            "text": "Contents",
            "blocks": [],
            "toc_level": 0,
        }
        chapters.insert(0, contents)
        _rebuild_contents_page(contents, chapters, NullLog())
        return dict(_contents_links(_book(chapters, tmp_path / "c.kfx")))["Section A"]

    assert _each(section_a) == pytest.approx([6.25, 5.0])
