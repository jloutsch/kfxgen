"""Colour names, hsl() and translucent colours (#383 items 2 and 3).

kfxgen read the 16 basic CSS colour names and 6 more, hex, and rgb(); any
other name and hsl() were unreadable, and an rgba() colour's alpha was
ignored. Since #388 drew black fills, a translucent black shade such as
`rgba(0, 0, 0, 0.1)` became a solid black box.

Kindle Previewer 4 (kpr4, decoded probes on #383) reads every name but
`rebeccapurple` and the system colours, and hsl(), and writes an rgba()
colour's alpha in the ARGB value. Whether a Kindle draws that alpha isn't
known. kfxgen blends a translucent colour onto the white page and writes it
opaque: the same colour where alpha is drawn, and a readable one where it
isn't. The text-colour contrast rule (#239) then applies to the blended
colour.
"""

import pytest

from kfxgen.inline_style import css_rgb, css_text_colour, is_transparent
from tests.unit.test_floats import _blocks


def _near(rgb, want):
    """Within 1 per channel: Previewer's hsl() rounding differs by 1."""
    return all(abs(((rgb >> s) & 0xFF) - ((want >> s) & 0xFF)) <= 1 for s in (16, 8, 0))


# --- Reading a colour -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "rgb"),
    [
        ("darkred", 0x8B0000),
        ("lightgray", 0xD3D3D3),
        ("lightgrey", 0xD3D3D3),
        ("darkgray", 0xA9A9A9),
        ("gainsboro", 0xDCDCDC),
        ("brown", 0xA52A2A),
        ("rebeccapurple", 0x663399),
        ("DarkRed", 0x8B0000),
        ("red", 0xFF0000),
    ],
)
def test_every_css_colour_name_is_read(name, rgb):
    assert css_rgb(name) == rgb


@pytest.mark.unit
@pytest.mark.parametrize("value", ["windowtext", "canvastext", "notacolour"])
def test_a_system_colour_or_an_unknown_name_is_unread_as_in_previewer(value):
    assert css_rgb(value) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "previewer"),
    [
        ("hsl(0, 100%, 25%)", 0x7F0000),
        ("hsl(120, 50%, 80%)", 0xB3E6B3),
        ("hsl(210, 40%, 60%)", 0x7099C2),
        ("hsl(30, 80%, 45%)", 0xCF7317),
        ("hsl(300, 25%, 10%)", 0x1F131F),
        ("hsl(60, 100%, 50%)", 0xFFFF00),
        ("hsl(120deg, 50%, 80%)", 0xB3E6B3),
        ("hsla(120, 50%, 80%, 1)", 0xB3E6B3),
    ],
)
def test_hsl_is_read_as_previewer_reads_it(value, previewer):
    got = css_rgb(value)
    assert got is not None and _near(got, previewer), f"{got} vs {previewer:06X}"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "blended"),
    [
        # A browser's colour on the white page.
        ("rgba(0, 0, 0, 0.1)", 0xE6E6E6),
        ("rgba(0, 0, 0, 0.05)", 0xF2F2F2),
        ("rgba(255, 0, 0, 0.5)", 0xFF8080),
        ("rgba(0, 0, 0, 0.6)", 0x666666),
        ("hsla(240, 100%, 50%, 0.5)", 0x8080FF),
        ("rgba(0, 0, 255, 1)", 0x0000FF),
        ("rgba(0, 0, 255, 1.5)", 0x0000FF),
    ],
)
def test_a_translucent_colour_is_blended_onto_the_page(value, blended):
    assert _near(css_rgb(value), blended)


@pytest.mark.unit
@pytest.mark.parametrize(
    "value", ["rgba(0, 0, 0, 0)", "hsla(0, 0%, 0%, 0)", "transparent"]
)
def test_a_colour_with_no_alpha_draws_nothing(value):
    assert is_transparent(value)


@pytest.mark.unit
def test_a_percentage_alpha_is_unread_as_in_previewer():
    """Previewer draws no fill for `rgba(255, 0, 0, 50%)`."""
    assert css_rgb("rgba(255, 0, 0, 50%)") is None


# --- Text ---------------------------------------------------------------------


def _contrast(argb):
    from kfxgen.inline_style import _luminance

    lum = _luminance(((argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF))
    return 1.05 / (lum + 0.05)


@pytest.mark.unit
def test_translucent_text_is_opaque_and_readable():
    """Previewer writes `3FEE0000`, 25% red; kfxgen writes the blended pink
    darkened to 4.5:1, keeping its hue."""
    got = css_text_colour("rgba(255, 0, 0, 0.25)")
    assert got >> 24 == 0xFF and _contrast(got) >= 4.49
    r, g, b = (got >> 16) & 0xFF, (got >> 8) & 0xFF, got & 0xFF
    # Pink, not the opaque red (`FFEE0000`) the alpha left out gave.
    assert r > g == b > 0


@pytest.mark.unit
def test_dark_translucent_text_is_kept_dark():
    assert _near(css_text_colour("rgba(0, 0, 0, 0.9)"), 0x1A1A1A)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "previewer"),
    [
        ("darkred", 0xFF8B0000),
        ("lightgray", 0xFF767676),
        ("hsl(0, 100%, 25%)", 0xFF7F0000),
    ],
)
def test_text_in_a_name_or_hsl_gets_previewers_colour(value, previewer):
    got = css_text_colour(value)
    assert got >> 24 == 0xFF and _near(got, previewer)


# --- Through the converter ------------------------------------------------------

CSS = {
    "shade": {"background-color": "rgba(0, 0, 0, 0.1)"},
    "dark": {"background-color": "rgba(0, 0, 0, 0.6)"},
    "light": {"background-color": "lightgray"},
    "hsl": {"background-color": "hsl(120, 50%, 80%)"},
    "red": {"background-color": "rgba(255, 0, 0, 50%)"},
    "white": {"color": "#ffffff"},
}


def _box(body):
    (box,) = _blocks(body, CSS)
    assert box["type"] == "box"
    return box


@pytest.mark.unit
def test_a_translucent_black_shade_is_a_light_box_not_a_black_one():
    """Since #388 drew black fills, `rgba(0, 0, 0, 0.1)` became a solid
    black box with mid-grey text on it."""
    box = _box('<div class="shade"><p>Shaded.</p></div>')
    assert _near(box["box"]["background"], 0xFFE6E6E6)
    # The text is measured against the light box: black stays black.
    assert (box["blocks"][0].get("block_style") or {}).get("colour") is None


@pytest.mark.unit
def test_white_text_on_a_translucent_dark_box_stays_white():
    box = _box('<div class="dark"><p class="white">On dark.</p></div>')
    assert _near(box["box"]["background"], 0xFF666666)
    assert box["blocks"][0]["block_style"]["colour"] == 0xFFFFFFFF


@pytest.mark.unit
@pytest.mark.parametrize(("cls", "fill"), [("light", 0xFFD3D3D3), ("hsl", 0xFFB3E6B3)])
def test_a_named_or_hsl_fill_is_drawn(cls, fill):
    box = _box(f'<div class="{cls}"><p>Filled.</p></div>')
    assert _near(box["box"]["background"], fill)


@pytest.mark.unit
def test_a_percentage_alpha_fill_draws_no_box_as_in_previewer():
    blocks = _blocks('<div class="red"><p>Plain.</p></div>', CSS)
    assert [b.get("type") for b in blocks] == [None]


# --- Percentage channels (#394) -----------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "previewer"),
    [
        ("rgb(100%, 0%, 0%)", 0xFF0000),
        ("rgb(0%, 0%, 50%)", 0x000080),
        ("rgb(50.5%, 20%, 10%)", 0x813319),
        ("rgb(150%, 0%, 0%)", 0xFF0000),
    ],
)
def test_percentage_channels_are_read(value, previewer):
    assert css_rgb(value) == previewer


#: (percentage, Previewer's channel), from box fills `rgb(p%, p%, 0%)` in a
#: kpr4 probe (#398 review): floor(p x 2.56), capped at 255. round(p x 2.55)
#: was 1 low on 9 of them.
PREVIEWER_PERCENT = [
    (1, 2), (2, 5), (10, 25), (12.5, 32), (20, 51), (25, 64), (33, 84),
    (33.3, 85), (40, 102), (49.9, 127), (50, 128), (50.5, 129), (60, 153),
    (66.6, 170), (70, 179), (75, 192), (80, 204), (87.5, 224), (90, 230),
    (99, 253), (99.6, 254), (100, 255),
]  # fmt: skip


@pytest.mark.unit
@pytest.mark.parametrize(("percent", "channel"), PREVIEWER_PERCENT)
def test_a_percentage_channel_is_previewers_exactly(percent, channel):
    assert css_rgb(f"rgb({percent}%, {percent}%, 0%)") == (channel << 16) | (
        channel << 8
    )


@pytest.mark.unit
def test_a_translucent_percentage_colour_is_blended():
    assert css_rgb("rgba(100%, 0%, 0%, 0.5)") == 0xFF8080


@pytest.mark.unit
def test_percentage_red_text_gets_previewers_colour():
    """Previewer writes `FFEE0000`: red, darkened to 4.5:1 (#239)."""
    assert css_text_colour("rgb(100%, 0%, 0%)") == 0xFFEE0000
