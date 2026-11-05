"""How an <hr> is drawn: its width, alignment, border and fill (#722).

`_rule_look` reads calibre's computed style of an <hr>, as the style
resolver gives it, and `build_rule_style_157` writes it as Kindle Previewer
4.0.3 writes a `$596`. The values are those of the probe P722 (see
tests/integration/test_calibre_rule_look.py, which runs it through calibre).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.converter import _rule_look  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402

pytestmark = pytest.mark.unit

UA = (("inset", 0.72, "currentColor"),) * 4
NONE = ("none", 0.0, "currentColor")


def _css(**kw):
    css = {
        "width": None,
        "height": None,
        "margin-left": "auto",
        "margin-right": "auto",
        "background-color": None,
        "border-sides": UA,
    }
    css.update({k.replace("_", "-"): v for k, v in kw.items()})
    return css


@pytest.mark.parametrize(
    "css, look",
    [
        # calibre's own rule: Previewer writes nothing, the device its own.
        (_css(), {}),
        (_css(width="50%"), {"width": (50.0, "%")}),
        (_css(width="200px"), {"width": (23.4375, "%")}),
        (_css(width="10em"), {"width": (10.0, "em")}),
        (_css(width="30%", margin_left="0"), {"width": (30.0, "%"), "align": "left"}),
        (
            _css(width="30%", margin_right="0"),
            {"width": (30.0, "%"), "align": "right"},
        ),
        (
            _css(width="45%", margin_left="27.5%"),
            {"width": (45.0, "%"), "align": "left", "margin_left": 27.5},
        ),
        # A width and both margins: only the aligned side's margin.
        (
            _css(width="20%", margin_left="40%", margin_right="40%"),
            {"width": (20.0, "%"), "align": "left", "margin_left": 40.0},
        ),
        (
            _css(width="30%", margin_right="10%"),
            {"width": (30.0, "%"), "align": "right", "margin_right": 10.0},
        ),
        # No width: its margins, and no alignment.
        (
            _css(margin_left="10%", margin_right="10%"),
            {"margin_left": 10.0, "margin_right": 10.0},
        ),
        # A black the book sets is written; the text colour isn't.
        (
            _css(border_sides=(("solid", 0.72, "black"), NONE, NONE, NONE)),
            {"border": {"top": ("solid", 0.45, 0xFF000000)}},
        ),
        (
            _css(border_sides=(("solid", 1.44, "currentColor"), NONE, NONE, NONE)),
            {"border": {"top": ("solid", 0.9, None)}},
        ),
        # calibre's inset at another width is the book's.
        (
            _css(border_sides=(("inset", 2.16, "currentColor"),) * 4),
            {
                "border": dict.fromkeys(
                    ("top", "right", "bottom", "left"), ("inset", 1.35, None)
                )
            },
        ),
        # A filled bar.
        (
            _css(height="4px", background_color="#888", border_sides=(NONE,) * 4),
            {"height": (1.8, "pt"), "fill": 0xFF888888},
        ),
        (
            _css(height="0.5em", background_color="#888", border_sides=(NONE,) * 4),
            {"height": (0.5, "em"), "fill": 0xFF888888},
        ),
        # A fill with no height draws nothing.
        (
            _css(background_color="#888", border_sides=(NONE,) * 4),
            {},
        ),
    ],
)
def test_a_rules_look_is_read_from_its_css(css, look):
    assert _rule_look(css) == look


def test_no_css_is_the_default_rule():
    assert _rule_look(None) == {}


def _style(**kw):
    gen = NativeKFXGenerator()
    frag = gen.build_rule_style_157("s0_r", space_above=0.5, space_below=0.5, **kw)
    return {
        str(k): (f"{float(v['$307']):g} {v['$306']}" if hasattr(v, "get") else str(v))
        for k, v in frag.value.items()
        if str(k) not in ("$173", "$47", "$49")
    }


def test_a_rule_with_one_drawn_side_draws_no_other():
    assert _style(border=(("top", ("solid", 0.45, 0xFF000000)),)) == {
        "$84": str(0xFF000000),
        "$89": "$328",
        "$94": "0.45 $318",
        "$95": "0 $318",
        "$96": "0 $318",
        "$97": "0 $318",
    }


def test_an_inset_border_in_the_text_colour_writes_only_its_width():
    sides = tuple(
        (s, ("inset", 1.35, None)) for s in ("top", "right", "bottom", "left")
    )
    assert _style(border=sides) == {"$93": "1.35 $318"}


def test_a_filled_bar_has_no_border():
    assert _style(fill=0xFF888888, height=(1.8, "pt"), width=(50.0, "%")) == {
        "$546": "$377",
        "$56": "50 $314",
        "$62": "1.8 $318",
        "$70": str(0xFF888888),
        "$93": "0 $318",
    }


def test_an_em_width_is_capped_at_the_page():
    assert _style(width=(6.0, "em"), align="right") == {
        "$546": "$377",
        "$56": "6 $308",
        "$65": "100 $314",
        "$580": "$61",
    }
