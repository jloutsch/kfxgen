"""A text colour is adjusted against the box it sits on, as Kindle
Previewer does (#383).

kfxgen darkened any colour with less than 4.5:1 contrast against white
(#239), even inside a box with a dark fill: white text on a #333 box became
grey on dark grey, and black text stayed black on it. Kindle Previewer 4
(kpr4, decoded probe on #383) measures a colour against the background
drawn behind it, the nearest filled box, or the white page:

- enough contrast, it is kept: white and yellow on #333;
- too little, it moves toward whichever of black or white contrasts more
  with the background, keeping its hue, until it has 4.5:1. #777 and black
  on #333 become about #9B9B9B; white on #DDD about #616161; white on #777
  about #060606. A colour that can't reach 4.5:1 by getting lighter
  without changing its hue (pure red, navy, dark red) is kept as written,
  not brightened part of the way (P734, #734).

A background kfxgen doesn't draw (a paragraph's or a span's own) is not the
background: the text sits on the page. A box filled black is filled (it was
dropped, read as "no colour" the way a black border is).
"""

import pytest

from kfxgen.inline_style import css_text_colour
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _blocks


def _channels(argb):
    return ((argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF)


def _near(argb, previewer):
    """Within 1 of Previewer's value on each channel: Previewer's own
    rounding differs by 1 between cases (#777 and black on #333 give #9B
    and #9C)."""
    return argb >> 24 == 0xFF and all(
        abs(a - b) <= 1 for a, b in zip(_channels(argb), _channels(previewer))
    )


# --- The colour against a background ------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("colour", "background", "previewer"),
    [
        ("#ffffff", 0x333333, 0xFFFFFFFF),  # C1: kept
        ("#ffff00", 0x333333, 0xFFFFFF00),  # C2: kept
        ("#777777", 0x333333, 0xFF9B9B9B),  # C3: lightened
        ("#000000", 0x333333, 0xFF9C9C9C),  # C10: lightened
        ("#ffffff", 0xDDDDDD, 0xFF616161),  # C4: darkened against the box
        ("#888888", 0xDDDDDD, 0xFF606060),  # C5
        ("#ffffff", 0x777777, 0xFF060606),  # C13: black contrasts more
        ("#ff0000", 0x333333, 0xFFFF0000),  # C12's run: can't get lighter
        ("#000000", 0x000000, 0xFF757575),  # C9's box colour
        ("#008000", 0x333333, 0xFF00B400),  # P734 K004: green can reach it
        ("#336699", 0x333333, 0xFF4F9FEE),  # P734 K009
        ("#8b4513", 0x333333, 0xFFF27821),  # P734 K012: brown, on #333 not #555
        ("#701880", 0x222222, 0xFFDE30FE),  # P739b B029: 4.52:1 at 254, brightened
    ],
)
def test_a_colour_is_measured_against_its_background(colour, background, previewer):
    got = css_text_colour(colour, background)
    assert _near(got, previewer), f"{got:08X} vs Previewer's {previewer:08X}"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("colour", "background"),
    [
        ("#000080", 0x333333),  # K001: navy
        ("#800000", 0x333333),  # K002: dark red
        ("#800080", 0x333333),  # K003: purple
        ("#4040c0", 0x333333),  # K017
        ("#202060", 0x222222),  # K036
        ("#000080", 0x000000),  # K037: navy on black
        ("#336699", 0x555555),  # K063
        ("#8b4513", 0x555555),  # K066: saddle brown
        ("#800000", 0x000080),  # K074: on a navy box
        ("#800080", 0x600000),  # K093: on a dark red box
        # QA's P739b, at the cut-off: 4.52:1 at its 255 form, but under
        # 4.5:1 at its 254 form, which is the one Previewer measures.
        ("#80247c", 0x333333),  # B006
        ("#801050", 0x222222),  # B028
    ],
)
def test_a_colour_that_cant_get_light_enough_is_kept(colour, background):
    """Kindle Previewer 4.0.3 (P734, 124 cases on 8 boxes, #734): a colour
    that would need more than its brightest form of the same hue to reach
    4.5:1 on a dark box is written as the book gives it. kfxgen brightened
    it as far as it went: navy became 0xFF0000FF, dark red 0xFFFF0000."""
    assert css_text_colour(colour, background) == 0xFF000000 | int(colour[1:], 16)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("colour", "argb"),
    [("#ffffff", 0xFF767676), ("#ffff00", 0xFF7A7A00), ("#336699", 0xFF336699)],
)
def test_on_the_page_a_colour_is_unchanged(colour, argb):
    """No box: the values #239 writes, exactly."""
    assert css_text_colour(colour) == argb
    assert css_text_colour(colour, None) == argb


# --- Through the converter and generator --------------------------------------

CSS = {
    "dark": {"background-color": "#333333"},
    "black": {"background-color": "#000000"},
    "pdark": {"background-color": "#333333", "color": "#ffffff"},
    "white": {"color": "#ffffff"},
    "grey": {"color": "#777777"},
    "red": {"color": "#ff0000"},
    "plight": {"background-color": "#eeeeee", "color": "#777777"},
    "c222": {"background-color": "#222222"},
    "c333": {"background-color": "#333333"},
    "c000": {"background-color": "#000000"},
    "purple": {"color": "#701880"},
    "brown": {"color": "#8b4513"},
    "blue": {"color": "#336699"},
}


def _texts(body, tmp_path):
    """{first word of each text entry: (its colour, its runs' colours, the
    fill of the box around it)}."""
    blocks = _blocks(body, CSS)
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "One", "text": "\n\n".join(b["text"] for b in blocks),
             "blocks": blocks}
        ],
        output_path=str(out),
    )  # fmt: skip
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}

    def colour(style):
        v = style.get("$19")
        return None if v is None else int(v) & 0xFFFFFFFF

    def walk(entries, fill):
        for e in entries:
            if isinstance(e, str):
                continue
            st = styles.get(str(e.get("$157")), {})
            own = st.get("$70")
            here = int(own) & 0xFFFFFFFF if own is not None else fill
            if e.get("$145"):
                r = e["$145"]
                text = str(texts[str(r["name"])][r["$403"]])
                runs = [
                    colour(styles.get(str(s.get("$157")), {}))
                    for s in e.get("$142") or []
                ]
                found[text.split()[0]] = (colour(st), runs, here)
            walk(e.get("$146") or [], here)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return found


@pytest.mark.unit
def test_white_text_on_a_dark_box_stays_white(tmp_path):
    found = _texts('<div class="dark"><p class="white">Kept white.</p></div>', tmp_path)
    assert found["Kept"] == (0xFFFFFFFF, [], 0xFF333333)


@pytest.mark.unit
def test_text_with_no_colour_on_a_dark_box_is_lightened(tmp_path):
    """Previewer writes the box's own colour, black lightened; on the
    paragraph it is the same on the page."""
    found = _texts('<div class="dark"><p>Default text.</p></div>', tmp_path)
    colour, _runs, fill = found["Default"]
    assert fill == 0xFF333333 and _near(colour, 0xFF9C9C9C)


@pytest.mark.unit
def test_a_box_inside_a_dark_box_keeps_its_background(tmp_path):
    body = '<div class="dark"><div><p class="white">Nested white.</p></div></div>'
    assert _texts(body, tmp_path)["Nested"][0] == 0xFFFFFFFF


@pytest.mark.unit
def test_runs_on_a_dark_box_are_measured_against_it(tmp_path):
    body = (
        '<div class="dark"><p class="white">Mixed <span class="red">red run</span>'
        ' and <span class="grey">grey run</span> end.</p></div>'
    )
    colour, runs, _fill = _texts(body, tmp_path)["Mixed"]
    assert colour == 0xFFFFFFFF
    assert runs[0] == 0xFFFF0000 and _near(runs[1], 0xFF9B9B9B)


@pytest.mark.unit
def test_a_black_box_is_filled_and_its_white_text_kept(tmp_path):
    found = _texts('<div class="black"><p class="white">On black.</p></div>', tmp_path)
    assert found["On"] == (0xFFFFFFFF, [], 0xFF000000)


@pytest.mark.unit
def test_a_paragraphs_own_fill_is_drawn_and_a_spans_is_not(tmp_path):
    """A filled <p> is a box, as Kindle Previewer 4.0.3 draws it (#643): its
    white text stays white on its #333. kfxgen draws no fill for a <span>,
    so its white text sits on the page and is darkened, as before."""
    body = '<p class="pdark">Own fill.</p><p>Plain <span class="white">white run</span>.</p>'
    found = _texts(body, tmp_path)
    assert found["Own"][0] == 0xFFFFFFFF and found["Own"][2] == 0xFF333333
    assert found["Plain"][1] == [0xFF767676]


@pytest.mark.unit
def test_white_text_on_the_page_is_unchanged(tmp_path):
    assert _texts('<p class="white">Page white.</p>', tmp_path)["Page"][0] == 0xFF767676


@pytest.mark.unit
def test_text_from_a_fill_kfxgen_doesnt_draw_is_measured_against_the_page(tmp_path):
    """A filled box kfxgen doesn't draw: a box in a <span> in a <div>, which
    is walked as one paragraph. (The A/B's W158 shape, an <aside> in a
    <div>, was one until #387.) Its white text was measured against the
    fill and would be white on the page; the generator measures any text it
    writes outside a filled box against the page."""
    body = (
        '<div><span><div class="dark"><p class="white">Sidebar white.</p>'
        "</div></span></div>"
    )
    _colour, runs, fill = _texts(body, tmp_path)["Sidebar"]
    # Walked inline, the colour is a run's.
    assert fill is None and runs == [0xFF767676]


@pytest.mark.unit
def test_inside_a_box_a_paragraphs_own_fill_is_the_background(tmp_path):
    """A filled <p> in a box is a box in it, as Previewer nests them (#643):
    #777 text is darkened against the <p>'s #EEE to Previewer's #6C6C6C (my
    probe, 2026-10-29), not lightened against the box's #333."""
    body = '<div class="dark"><p class="plight">Inner fill.</p></div>'
    colour, _runs, fill = _texts(body, tmp_path)["Inner"]
    assert fill == 0xFFEEEEEE and _near(colour, 0xFF6C6C6C)


# --- In a table cell (#740) ---------------------------------------------------


def _cell(cell_class, para_class, paragraphs=1, tag="td"):
    paras = "".join(
        f'<p class="{para_class}">T{n} {para_class} cell.</p>'
        for n in range(paragraphs)
    )
    return f'<table><tr><{tag} class="{cell_class}">{paras}</{tag}></tr></table>'


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["td", "th"])
@pytest.mark.parametrize("paragraphs", [1, 2], ids=["one-paragraph", "two-paragraphs"])
@pytest.mark.parametrize(
    ("cell_class", "para_class", "previewer"),
    [
        ("c222", "purple", 0xFFDE30FE),  # P739t T004
        ("c333", "brown", 0xFFF27821),  # P739t T008
        ("c000", "blue", 0xFF3C78B4),  # P739t T007: has 4.5:1 on white too
    ],
)
def test_a_colour_lightened_for_a_cell_is_not_darkened_for_the_page(
    tmp_path, cell_class, para_class, previewer, paragraphs, tag
):
    """P739t (Kindle Previewer 4.0.3): a colour in a cell is measured against
    the cell's fill only. kfxgen measured a one-paragraph cell's text again
    against the page and darkened #701880 on #222222 to 0xFFC129DD (#740)."""
    found = _texts(_cell(cell_class, para_class, paragraphs, tag), tmp_path)
    colour, runs, _fill = found["T0"]
    drawn = next((c for c in [*runs, colour] if c is not None), None)
    assert _near(drawn, previewer), f"{drawn:08X} vs Previewer's {previewer:08X}"


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["td", "th"])
def test_a_span_in_a_one_paragraph_cell_is_measured_against_the_cell(tmp_path, tag):
    # P740 P07, P08: Previewer 0xFFDE30FE; kfxgen main 0xFFC129DD.
    body = (
        f'<table><tr><{tag} class="c222"><p>S1 plain <span class="purple">'
        f"purplespan</span> end.</p></{tag}></tr></table>"
    )
    _colour, runs, _fill = _texts(body, tmp_path)["S1"]
    assert any(_near(r, 0xFFDE30FE) for r in runs if r is not None), runs


@pytest.mark.unit
def test_after_a_dark_cell_text_is_on_the_page_again(tmp_path):
    """The cell's fill holds only while its own chunk is written. White text
    in a box kfxgen doesn't draw (C16's flattened sidebar) after the table is
    darkened for the page, as it is with no table before it."""
    cells = '<td class="c222"><p class="purple">T0 dark.</p></td>' + "".join(
        f"<td><p>X{n} plain.</p></td>" for n in range(4)
    )
    body = (
        f"<table><tr>{cells}</tr></table>"
        '<div><span><div class="dark"><p class="white">W1 after.</p></div></span></div>'
    )
    colour, runs, _fill = _texts(body, tmp_path)["W1"]
    drawn = next(c for c in [*runs, colour] if c is not None)
    assert _near(drawn, 0xFF767676), f"{drawn:08X}"
