"""Margins in vertical text (#247).

Kindle Previewer 4.0.3 lays a vertical-rl book's margins out as a browser
does (QA's probe P747 on #747, and P247g, decoded): the user agent's are
logical, the space before and after a block, written right (`$50`) and left
(`$48`) in line heights, and they collapse with their neighbours'; the
book's own are physical: its margin-right and margin-left are that same
space before and after (they replace the user agent's and collapse), its
margin-top and margin-bottom the top and bottom (`$47`, `$49`) in ems, added
to the user agent's space and not collapsed. A <p>'s default 1em above and
below is `$50` 1.11667lh and `$48` 0.833333lh (G01); `.mt { margin-top:
2em }` on a default <p> is `$47` 2em and `$50` 0.833333lh (V03t);
`p { margin: 1em 0 }` is `$47` 1em and `$49` 1em on every <p> (V01).

calibre folds its user agent's margins into its own classes when it
flattens the CSS, so `mark_vertical_margins` records them first, in
`specialize_css_for_output` (a rule's sort key starts 0 for the user
agent's). `_vertical_box` then puts each value in kfxgen's slot for it:
top and bottom for the space before and after, left and right for the
line's start and end, and the generator writes each slot on its side.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.kfxlib_minimal.ion import IonDecimal, IonStruct, IonSymbol
from kfxgen.native_generator import NativeKFXGenerator, _rotate_margins
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

XHTML = "http://www.w3.org/1999/xhtml"


class _Stylizer:
    """calibre's pre-flatten Stylizer as far as this reads it: `rules`, each
    (sort key, selector, cssdict, selector text, href), in cascade order,
    and an element's writing mode by its tag."""

    def __init__(self, rules, modes):
        self.rules = [
            ((origin, 0, 0, 0, 0, i), None, d, t, None)
            for i, (origin, t, d) in enumerate(rules)
        ]
        self._modes = modes

    def style(self, elem):
        mode = self._modes.get(elem.tag.split("}")[-1])
        return {"writing-mode": mode} if mode else {}


def _select(root):
    def select(text):
        if text.startswith("."):
            return [
                e
                for e in root.iter()
                if isinstance(e.tag, str) and text[1:] in (e.get("class") or "").split()
            ]
        return [
            e
            for e in root.iter()
            if isinstance(e.tag, str) and e.tag.split("}")[-1] == text
        ]

    return select


UA_P = (0, "p", {"margin-top": "1em", "margin-bottom": "1em"})
UA_UL = (0, "ul", {"padding-left": "40px"})
BODY = (
    '<p id="ua">G01</p><p id="mt" class="mt">V03t</p>'
    '<p id="st" style="margin: 0">S</p><ul id="ul"><li>u</li></ul>'
)
RULES = [UA_P, UA_UL, (1, ".mt", {"margin-top": "2em"})]


def _marked(modes):
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{BODY}</body></html>')
    _conv.mark_vertical_margins(root, _Stylizer(RULES, modes), _select(root))
    return {
        e.get("id"): (e.get(_conv._V_AUTHOR), e.get(_conv._V_UA))
        for e in root.iter()
        if isinstance(e.tag, str) and e.get("id")
    }


@pytest.mark.unit
@pytest.mark.parametrize("on", ["html", "body"])
def test_a_vertical_file_records_the_books_sides_and_the_user_agents_values(on):
    assert _marked({on: "vertical-rl"}) == {
        "ua": ("", "margin-top:1em;margin-bottom:1em"),
        "mt": ("margin-top", "margin-top:1em;margin-bottom:1em"),
        # A style attribute is the book's; `margin` sets all four sides.
        "st": (
            "margin-top margin-right margin-bottom margin-left",
            "margin-top:1em;margin-bottom:1em",
        ),
        "ul": ("", "padding-left:40px"),
    }


@pytest.mark.unit
@pytest.mark.parametrize("mode", [None, "horizontal-tb"])
def test_a_horizontal_file_is_left_alone(mode):
    assert set(_marked({"html": mode} if mode else {}).values()) == {(None, None)}


# --- Into kfxgen's slots ---------------------------------------------------------


def _slots(author, ua, st):
    elem = etree.Element("p", {_conv._V_AUTHOR: author, _conv._V_UA: ua})
    css = {"margin-top": "x", "padding-sides": ("x",) * 4}
    out = _conv._vertical_box(css, st, elem)
    margins = {
        k: out[k]
        for k in ("margin-top", "margin-right", "margin-bottom", "margin-left")
    }
    return margins, out["padding-sides"]


@pytest.mark.unit
def test_the_user_agents_margins_keep_their_slots():
    # G01: before and after, top and bottom in kfxgen's slots.
    margins, _pad = _slots("", "margin-top:1em;margin-bottom:1em", {})
    assert margins == {
        "margin-top": "1em",
        "margin-right": None,
        "margin-bottom": "1em",
        "margin-left": None,
    }


@pytest.mark.unit
def test_the_books_margin_top_is_the_lines_start_beside_the_user_agents_space():
    # V03t: `.mt { margin-top: 2em }` on a default <p>.
    margins, _pad = _slots(
        "margin-top", "margin-top:1em;margin-bottom:1em", {"margin-top": "2em"}
    )
    assert margins == {
        "margin-top": "1em",
        "margin-right": None,
        "margin-bottom": "1em",
        "margin-left": "2em",
    }


@pytest.mark.unit
def test_the_books_right_and_left_are_the_space_before_and_after():
    # V12: margin-left 2em, margin-right 1em replace the user agent's 1em.
    st = {"margin-left": "2em", "margin-right": "1em"}
    margins, _pad = _slots(
        "margin-right margin-left", "margin-top:1em;margin-bottom:1em", st
    )
    assert margins["margin-top"] == "1em" and margins["margin-bottom"] == "2em"
    assert margins["margin-left"] is None and margins["margin-right"] is None


@pytest.mark.unit
def test_paddings_take_the_same_slots():
    # A list's user agent padding-left (the line's start) stays left; the
    # book's padding-top is the line's start too.
    _m, pad = _slots("", "padding-left:40px", {})
    assert pad == (None, None, None, "40px")
    _m, pad = _slots("padding-top", "", {"padding-top": "1em"})
    assert pad == (None, None, None, "1em")


@pytest.mark.unit
def test_an_unmarked_element_is_unchanged():
    css = {"margin-top": "1em", "padding-sides": ("1em",) * 4}
    assert _conv._vertical_box(dict(css), {}, etree.Element("p")) == css


# --- Writing each slot on its side ---------------------------------------------


def _length(n, unit="$310"):
    return IonStruct(
        IonSymbol("$307"), IonDecimal(n), IonSymbol("$306"), IonSymbol(unit)
    )


def _keys(value):
    return {
        str(k): float(v[IonSymbol("$307")])
        for k, v in value.items()
        if str(k) in ("$47", "$48", "$49", "$50", "$52", "$53", "$54", "$55")
    }


@pytest.mark.unit
def test_each_slot_is_written_on_its_side():
    value = IonStruct(
        IonSymbol("$47"), _length("1.11667"),
        IonSymbol("$49"), _length("0.833333"),
        IonSymbol("$48"), _length("2", "$308"),
        IonSymbol("$50"), _length("1", "$308"),
        IonSymbol("$52"), _length("3", "$308"),
        IonSymbol("$53"), _length("4", "$308"),
    )  # fmt: skip
    _rotate_margins(value)
    assert _keys(value) == {
        "$50": 1.11667,
        "$48": 0.833333,
        "$47": 2.0,
        "$49": 1.0,
        "$55": 3.0,
        "$52": 4.0,
    }


@pytest.mark.unit
def test_kfxgens_own_left_margin_is_dropped():
    value = IonStruct(
        IonSymbol("$48"), _length("0.5", "$314"), IonSymbol("$47"), _length("1")
    )
    _rotate_margins(value)
    assert _keys(value) == {"$50": 1.0}


# --- Through the converter and generator --------------------------------------


def _paragraph_keys(tmp_path, chapters):
    out = tmp_path / "m.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=chapters,
        output_path=str(out),
        rtl=True,
        language="ja",
        writing_mode="vertical-rl",
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    return [
        [_keys(styles[str(e["$157"])]) for e in val(f)["$146"]]
        for f in by_type(frags, "$259")
    ]


def _chapter(mode, marked):
    css = {"m": {"margin-top": "1em", "margin-bottom": "1em"}}
    attrs = f' {_conv._V_AUTHOR}="" {_conv._V_UA}="margin-top:1em;margin-bottom:1em"'
    xhtml = (
        f'<html xmlns="{XHTML}"><body>'
        + f'<p class="m"{attrs if marked else ""}>同じ段落。</p>' * 3
        + "</body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=_resolver(css),
        base_href="c.xhtml",
    )
    for b in blocks:
        b["writing_mode"] = mode
    return {
        "title": "",
        "text": "\n\n".join(b["text"] for b in blocks),
        "blocks": blocks,
    }


@pytest.mark.unit
def test_a_vertical_section_writes_the_space_between_paragraphs_right_and_left(
    tmp_path,
):
    (paras,) = _paragraph_keys(tmp_path, [_chapter("vertical-rl", True)])
    assert paras[-3:] == [
        {"$50": 0.833333},
        {"$50": 0.833333},
        {"$50": 0.833333, "$48": 0.833333},
    ]


@pytest.mark.unit
def test_a_horizontal_section_keeps_top_and_bottom_and_shares_no_moved_style(
    tmp_path,
):
    # The same paragraph in a vertical chapter and a horizontal one (P247f):
    # two styles, not one shared through the style cache.
    vertical, horizontal = _paragraph_keys(
        tmp_path, [_chapter("vertical-rl", True), _chapter("horizontal-tb", False)]
    )
    assert vertical[-1] == {"$50": 0.833333, "$48": 0.833333}
    assert horizontal[-1] == {"$48": 0.5, "$47": 0.833333, "$49": 0.833333}
