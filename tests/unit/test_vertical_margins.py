"""The reading system's margins in vertical text (#247).

Kindle Previewer 4.0.3 (probe P247g, decoded) reads a block's default
margins, the user agent's, as the space before and after it, and in
vertical-rl text writes them on the right (`$50`) and left (`$48`): a <p>'s
1em above and below is `$50` 1.11667lh and `$48` 0.833333lh (G01), an <h2>'s
`$50`/`$48` 0.691667lh (G05), an <h1>'s `$50`, an <hr>'s `$50`/`$48`. The
book's own margin-top and margin-bottom stay top and bottom (`$47`, `$49`:
G03, G07), and its right and left stay right and left (G04, G08).

calibre folds its user agent's margins into its own classes when it
flattens the CSS, so `mark_vertical_margins` tells them apart first, in
`specialize_css_for_output`: a rule's sort key starts 0 for the user agent's.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.kfxlib_minimal.ion import IonDecimal, IonStruct, IonSymbol
from kfxgen.native_generator import NativeKFXGenerator, _rotate_margins
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

XHTML = "http://www.w3.org/1999/xhtml"
ATTR = _conv._UA_MARGINS


class _Style(dict):
    pass


class _Stylizer:
    """calibre's pre-flatten Stylizer as far as this reads it: `rules`, each
    (sort key, selector, cssdict, selector text, href), and the writing
    mode of an element by its tag."""

    def __init__(self, rules, modes):
        self.rules = [
            ((origin, 0, 0, 0, 0, i), None, d, t, None)
            for i, (origin, t, d) in enumerate(rules)
        ]
        self._modes = modes

    def style(self, elem):
        tag = elem.tag.split("}")[-1]
        return _Style(
            {"writing-mode": self._modes.get(tag)} if self._modes.get(tag) else {}
        )


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


UA = (0, "p", {"margin-top": "1em", "margin-bottom": "1em"})
BODY = (
    '<p id="ua">G01</p><p id="mt" class="mt">G03</p><p id="pt" class="pt">G09</p>'
    '<p id="st" style="margin-bottom: 0">S</p><p id="all" style="margin: 0">A</p>'
)
RULES = [
    UA,
    (1, ".mt", {"margin-top": "2em"}),
    (1, ".pt", {"padding-top": "2em"}),
]


def _marked(modes, rules=RULES, body=BODY):
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    _conv.mark_vertical_margins(root, _Stylizer(rules, modes), _select(root))
    return {
        e.get("id"): e.get(ATTR)
        for e in root.iter()
        if isinstance(e.tag, str) and e.get("id")
    }


@pytest.mark.unit
@pytest.mark.parametrize("on", ["html", "body"])
def test_a_vertical_files_user_agent_margins_are_marked(on):
    assert _marked({on: "vertical-rl"}) == {
        "ua": "top bottom",
        "mt": "bottom",  # the book's own margin-top stays top (G03)
        "pt": "bottom",  # its padding-top is written with the margin
        "st": "top",  # a style attribute is the book's
        "all": "",
    }


@pytest.mark.unit
@pytest.mark.parametrize("mode", [None, "horizontal-tb"])
def test_a_horizontal_file_is_left_alone(mode):
    assert set(_marked({"html": mode} if mode else {}).values()) == {None}


# --- Moving them -----------------------------------------------------------------


def _length(n, unit="$310"):
    return IonStruct(
        IonSymbol("$307"), IonDecimal(n), IonSymbol("$306"), IonSymbol(unit)
    )


def _keys(value):
    return {
        str(k): float(v[IonSymbol("$307")])
        for k, v in value.items()
        if str(k) in ("$47", "$48", "$49", "$50")
    }


@pytest.mark.unit
def test_top_moves_right_and_bottom_left():
    value = IonStruct(
        IonSymbol("$48"),
        _length("0.5", "$314"),
        IonSymbol("$47"),
        _length("1.11667"),
        IonSymbol("$49"),
        _length("0.833333"),
    )
    _rotate_margins(value, ["top", "bottom"])
    # kfxgen's own 0.5% left margin gives way (G01).
    assert _keys(value) == {"$50": 1.11667, "$48": 0.833333}


@pytest.mark.unit
def test_only_the_marked_sides_move():
    value = IonStruct(IonSymbol("$47"), _length("2"), IonSymbol("$49"), _length("1"))
    _rotate_margins(value, ["bottom"])
    assert _keys(value) == {"$47": 2.0, "$48": 1.0}


@pytest.mark.unit
def test_a_margin_the_book_set_on_that_side_stays():
    # The book's right and left are the same sides as the space before and
    # after a block in vertical-rl: the book's wins.
    value = IonStruct(
        IonSymbol("$47"),
        _length("1"),
        IonSymbol("$50"),
        _length("2", "$308"),
        IonSymbol("$49"),
        _length("1"),
        IonSymbol("$48"),
        _length("3", "$308"),
    )
    _rotate_margins(value, ["top", "bottom"])
    assert _keys(value) == {"$50": 2.0, "$48": 3.0}


# --- Through the converter and generator --------------------------------------


def _styles(tmp_path, chapter_mode):
    css = {"m": {"margin-top": "1em", "margin-bottom": "1em", "text-indent": "1em"}}
    xhtml = (
        f'<html xmlns="{XHTML}"><body>'
        f'<p class="m" {ATTR}="top bottom">一つ目の段落。</p>'
        f'<p class="m" {ATTR}="top bottom">二つ目の段落。</p>'
        f'<p class="m" {ATTR}="">三つ目の段落。</p>'
        "</body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=_resolver(css),
        base_href="c.xhtml",
    )
    for b in blocks:
        b["writing_mode"] = chapter_mode
    out = tmp_path / "m.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "T",
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": blocks,
            }
        ],
        output_path=str(out),
        rtl=True,
        language="ja",
        writing_mode="vertical-rl",
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (story,) = [f for f in by_type(frags, "$259")]
    # The three paragraphs, after kfxgen's printed chapter title.
    return [_keys(styles[str(e["$157"])]) for e in val(story)["$146"]][-3:]


@pytest.mark.unit
def test_in_a_vertical_section_the_user_agents_margins_are_written_right_and_left(
    tmp_path,
):
    first, second, third = _styles(tmp_path, "vertical-rl")
    # The space between paragraphs, before each: right, not top.
    assert first == {"$48": 0.5, "$50": 0.833333}
    assert second == {"$48": 0.5, "$50": 0.833333}
    # The book's own: top and bottom.
    assert third == {"$48": 0.5, "$47": 0.833333, "$49": 0.833333}


@pytest.mark.unit
def test_in_a_horizontal_section_nothing_moves(tmp_path):
    # P247f: the horizontal file in a vertical book keeps $47/$49.
    assert _styles(tmp_path, "horizontal-tb") == [
        {"$48": 0.5, "$47": 0.833333},
        {"$48": 0.5, "$47": 0.833333},
        {"$48": 0.5, "$47": 0.833333, "$49": 0.833333},
    ]


@pytest.mark.unit
def test_a_moved_style_is_not_reused_for_the_same_paragraph_in_a_horizontal_file(
    tmp_path,
):
    # The same paragraph in a vertical chapter, its margins the reading
    # system's, and in a horizontal one: two styles, not one shared.
    css = {"m": {"margin-top": "1em", "margin-bottom": "1em"}}

    def chapter(mode, mark):
        attr = f' {ATTR}="top bottom"' if mark else ""
        xhtml = (
            f'<html xmlns="{XHTML}"><body>'
            f'<p class="m"{attr}>同じ段落。</p><p class="m"{attr}>同じ段落。</p>'
            "</body></html>"
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

    out = tmp_path / "s.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[chapter("vertical-rl", True), chapter("horizontal-tb", False)],
        output_path=str(out),
        rtl=True,
        language="ja",
        writing_mode="vertical-rl",
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    second = [
        _keys(styles[str(e["$157"])])
        for e in val(list(by_type(frags, "$259"))[1])["$146"]
    ]
    assert second[-1].get("$47") and "$50" not in second[-1]
