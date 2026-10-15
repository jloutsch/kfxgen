"""Bold and italic set by CSS inside a block are carried as runs (#312).

`_walk_inline` took bold and italic from tags only, so `<span class="Strong">`
with `span.Strong { font-weight: bold }` came out regular, and so did the only
paragraph in a table cell (`<td><p class="hdg">`), which is walked inline.

An element whose computed weight or style differs from its parent's starts or
ends the run. Comparing with the parent, not reading the element's own value,
is what keeps inherited emphasis right: in `<p class="hb">plain
<span>x</span></p>` the span inherits the paragraph's bold, and the paragraph's
own bold belongs to the block (written only when fonts are embedded, #34).
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import (
    SMALL_CAPS_FLAG,
    SPACING_FLAG,
    TRANSFORM_FLAG,
    flag_value,
)

#: The parts of calibre's UA sheet (templates/html.css) these tests use.
UA = {
    "b": {"font-weight": "bold"},
    "strong": {"font-weight": "bold"},
    "th": {"font-weight": "bold"},
    "i": {"font-style": "italic"},
    "em": {"font-style": "italic"},
    "cite": {"font-style": "italic"},
}
INHERITED = ("font-weight", "font-style")

CSS = {
    "hb": {"font-weight": "bold"},
    "hi": {"font-style": "italic"},
    "sb": {"font-weight": "bold"},
    "si": {"font-style": "italic"},
    "s700": {"font-weight": "700"},
    "s500": {"font-weight": "500"},
    "bolder": {"font-weight": "bolder"},
    "lighter": {"font-weight": "lighter"},
    "nw": {"font-weight": "normal"},
    "ns": {"font-style": "normal"},
}


def _resolver(rules=CSS):
    """Stands in for calibre's Stylizer, with inheritance: font-weight and
    font-style pass from parent to child unless the child sets its own."""

    def own(e):
        css = dict(UA.get(etree.QName(e).localname, {}))
        css.update(rules.get(e.get("class") or "", {}))
        return css

    def resolve(e):
        out = {"font-weight": "normal", "font-style": "normal"}
        for a in [*reversed(list(e.iterancestors())), e]:
            out.update({k: v for k, v in own(a).items() if k in INHERITED})
        out.update({k: v for k, v in own(e).items() if k not in INHERITED})
        return out

    return resolve


def _doc(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return etree.fromstring(xhtml.encode())


def _runs(text, spans, flag):
    return [text[s : s + n] for s, n, flags in spans if flag in flags]


def _para(body):
    (block,) = _conv.extract_blocks_from_html(_doc(body), style_resolver=_resolver())
    return block["text"], block["spans"]


def _cells(body, native=True):
    blocks = _conv.extract_blocks_from_html(
        _doc(body), style_resolver=_resolver(), native_tables=native
    )
    (table,) = [b["table"] for b in blocks if b.get("table")]
    return [(c["text"], c["spans"]) for row in table["rows"] for c in row["cells"]]


BOLD, ITALIC = _conv.FLAG_BOLD, _conv.FLAG_ITALIC


@pytest.mark.unit
def test_a_css_bold_span_is_a_bold_run():
    text, spans = _para('<p>Text with <span class="sb">SPANBOLD</span> here.</p>')
    assert _runs(text, spans, BOLD) == ["SPANBOLD"]


@pytest.mark.unit
def test_a_css_italic_span_is_an_italic_run():
    text, spans = _para('<p>Text with <span class="si">SPANITALIC</span> here.</p>')
    assert _runs(text, spans, ITALIC) == ["SPANITALIC"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cls", "bold"),
    [("s700", True), ("bolder", True), ("s500", False)],
)
def test_bold_is_weight_600_or_more(cls, bold):
    text, spans = _para(f'<p>a <span class="{cls}">word</span> b</p>')
    assert _runs(text, spans, BOLD) == (["word"] if bold else [])


@pytest.mark.unit
def test_a_span_inheriting_the_paragraphs_bold_is_not_a_run():
    """The paragraph's own bold is the block's: a span inside it that sets
    nothing must not turn bold while the text around it stays regular."""
    text, spans = _para('<p class="hb">plain <span>wrapped</span> plain</p>')
    assert _runs(text, spans, BOLD) == []


@pytest.mark.unit
def test_normal_weight_inside_bold_ends_the_run():
    text, spans = _para('<p><b>bold <span class="nw">normal</span> bold</b></p>')
    assert "normal" not in "".join(_runs(text, spans, BOLD))
    assert _runs(text, spans, BOLD) and all(
        "bold" in r for r in _runs(text, spans, BOLD)
    )


@pytest.mark.unit
def test_lighter_inside_bold_ends_the_run():
    text, spans = _para('<p><b>bold <span class="lighter">light</span></b></p>')
    assert "light" not in "".join(_runs(text, spans, BOLD))


@pytest.mark.unit
def test_normal_style_inside_italic_ends_the_run():
    text, spans = _para('<p><i>slanted <span class="ns">upright</span></i></p>')
    assert "".join(_runs(text, spans, ITALIC)).strip() == "slanted"


@pytest.mark.unit
def test_tags_still_give_runs():
    text, spans = _para("<p><b>BTAG</b> and <em>EMTAG</em> and <cite>CITE</cite></p>")
    assert _runs(text, spans, BOLD) == ["BTAG"]
    assert _runs(text, spans, ITALIC) == ["EMTAG", "CITE"]


@pytest.mark.unit
def test_the_only_paragraph_in_a_cell_keeps_its_css_bold():
    cells = _cells(
        '<table><tr><td><p class="hb">HDG</p></td><td><p class="hi">IT</p></td></tr>'
        "<tr><td>a</td><td>b</td></tr></table>"
    )
    assert _runs(*cells[0], BOLD) == ["HDG"]
    assert _runs(*cells[1], ITALIC) == ["IT"]


@pytest.mark.unit
def test_a_cell_mixing_text_and_a_bold_paragraph_bolds_only_the_paragraph():
    cells = _cells(
        '<table><tr><td>plain <p class="hb">bold</p></td><td>x</td></tr>'
        "<tr><td>a</td><td>b</td></tr></table>"
    )
    assert [r.strip() for r in _runs(*cells[0], BOLD)] == ["bold"]


@pytest.mark.unit
def test_the_rows_fallback_keeps_a_cells_css_bold():
    blocks = _conv.extract_blocks_from_html(
        _doc(
            '<table><tr><td><p class="hb">HDG</p></td><td>v</td></tr>'
            "<tr><td>a</td><td>b</td></tr></table>"
        ),
        style_resolver=_resolver(),
        native_tables=False,
    )
    runs = [r for b in blocks for r in _runs(b["text"], b["spans"], BOLD)]
    assert [r.strip() for r in runs] == ["HDG"]


class _CalibreLikeStyle:
    """Calibre 9.16's Style as measured (#312): `.get()` is the element's own
    specified value; `[prop]` is computed, and passes a numeric font-weight
    through a unit conversion (700 -> 299.197, 600 -> 256.454)."""

    def __init__(self, own, inherited_weight):
        self._own, self._weight = own, inherited_weight

    def get(self, k, default=None):
        return self._own.get(k, default)

    def __getitem__(self, k):
        if k == "font-weight":
            w = self._own.get(k, self._weight)
            return str(int(w) * 0.4274239986702365) if w.isdigit() else w
        return self._own.get(k, "normal")


class _CalibreLikeStylizer:
    def __init__(self, own_by_class):
        self._own = own_by_class

    def style(self, elem):
        inherited = "normal"
        for a in reversed(list(elem.iterancestors())):
            inherited = self._own.get(a.get("class") or "", {}).get(
                "font-weight", inherited
            )
        return _CalibreLikeStyle(self._own.get(elem.get("class") or "", {}), inherited)


def _calibre_resolver(own_by_class):
    return _conv._build_style_resolver(
        None,
        None,
        None,
        stylizer_factory=lambda oeb, item: _CalibreLikeStylizer(own_by_class),
    )


@pytest.mark.unit
@pytest.mark.parametrize("weight", ["600", "700", "900"])
def test_a_numeric_weight_reads_as_bold(weight):
    resolve = _calibre_resolver({"w": {"font-weight": weight}})
    span = _doc('<p><span class="w">x</span></p>').find(".//{*}span")
    assert _conv._css_emphasis(resolve(span))[0] is True


@pytest.mark.unit
def test_a_numeric_weight_is_inherited():
    resolve = _calibre_resolver({"w": {"font-weight": "700"}})
    span = _doc('<p class="w"><span>x</span></p>').find(".//{*}span")
    assert _conv._css_emphasis(resolve(span))[0] is True


@pytest.mark.unit
def test_a_numeric_normal_weight_inside_bold_is_not_bold():
    resolve = _calibre_resolver(
        {"b": {"font-weight": "bold"}, "n": {"font-weight": "400"}}
    )
    span = _doc('<p class="b"><span class="n">x</span></p>').find(".//{*}span")
    assert _conv._css_emphasis(resolve(span))[0] is False


# --- A tag's bold or italic reset by CSS (#362) ---------------------------

RESET = dict(CSS, nw={"font-weight": "normal"}, ns={"font-style": "normal"})


def _reset_para(body, resolver=None):
    resolver = resolver if resolver is not None else _resolver(RESET)
    (block,) = _conv.extract_blocks_from_html(_doc(body), style_resolver=resolver)
    return block["text"], block["spans"]


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["b", "strong"])
def test_a_bold_tag_set_to_normal_weight_is_not_bold(tag):
    text, spans = _reset_para(f'<p>a <{tag} class="nw">reset</{tag}> b</p>')
    assert _runs(text, spans, BOLD) == []


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["i", "em", "cite"])
def test_an_italic_tag_set_to_normal_style_is_not_italic(tag):
    text, spans = _reset_para(f'<p>a <{tag} class="ns">reset</{tag}> b</p>')
    assert _runs(text, spans, ITALIC) == []


@pytest.mark.unit
def test_a_bold_tag_inside_a_css_bold_paragraph_is_still_a_run():
    """The paragraph's own bold is written only with embedded fonts (#34),
    so the tag's run is what keeps `x` bold in a book without them."""
    text, spans = _reset_para('<p class="hb">plain <b>x</b></p>')
    assert _runs(text, spans, BOLD) == ["x"]


@pytest.mark.unit
def test_tags_without_a_stylizer_keep_their_emphasis():
    text, spans = _reset_para(
        "<p>a <b>bold</b> and <i>slanted</i> b</p>", resolver=lambda e: None
    )
    assert _runs(text, spans, BOLD) == ["bold"]
    assert _runs(text, spans, ITALIC) == ["slanted"]


@pytest.mark.unit
def test_a_resolver_that_reports_no_weight_leaves_a_tag_bold():
    """A resolver that knows nothing about weight (only vertical-align, say)
    must not take a tag's bold away."""
    text, spans = _reset_para(
        "<p>a <b>bold</b> b</p>", resolver=lambda e: {"vertical-align": None}
    )
    assert _runs(text, spans, BOLD) == ["bold"]


# --- ...and draws the effect its CSS swaps it for (#239) -------------------

SWAP = dict(
    RESET,
    sp={"font-style": "normal", "letter-spacing": "0.15em"},
    sc={"font-weight": "normal", "font-variant": "small-caps"},
    up={"font-weight": "normal", "text-transform": "uppercase"},
    sp0={"font-style": "normal", "letter-spacing": "normal"},
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tag", "cls", "flag", "kind", "value"),
    [
        ("em", "sp", ITALIC, SPACING_FLAG, ("0.15", "$308")),
        ("i", "sp", ITALIC, SPACING_FLAG, ("0.15", "$308")),
        ("b", "sc", BOLD, SMALL_CAPS_FLAG, "small-caps"),
        ("strong", "up", BOLD, TRANSFORM_FLAG, "uppercase"),
    ],
)
def test_a_tag_whose_css_swaps_its_emphasis_draws_the_swap(tag, cls, flag, kind, value):
    """pg50285's `em { letter-spacing: 0.15em; font-style: normal }` and
    pg64317's `b { font-variant: small-caps; font-weight: normal }`: #362
    kept the tag's style while kfxgen couldn't draw those. Now it draws
    them, as Previewer does, and the words are not emphasised (#239)."""
    body = f'<p>a <{tag} class="{cls}">word</{tag}> b</p>'
    text, spans = _reset_para(body, resolver=_resolver(SWAP))
    assert _runs(text, spans, flag) == []
    (got,) = [flag_value(f, kind) for s, n, f in spans if text[s : s + n] == "word"]
    assert got == value


@pytest.mark.unit
def test_letter_spacing_normal_is_no_swap():
    text, spans = _reset_para('<p>a <em class="sp0">word</em> b</p>', _resolver(SWAP))
    assert _runs(text, spans, ITALIC) == []


@pytest.mark.unit
def test_a_span_under_bold_with_letter_spacing_still_ends_the_bold():
    """Only a tag keeps its own style; a span that sets normal ends it."""
    swap = dict(SWAP, spn={"font-weight": "normal", "letter-spacing": "0.1em"})
    text, spans = _reset_para(
        '<p><b>bold <span class="spn">spaced</span></b></p>', _resolver(swap)
    )
    assert "spaced" not in "".join(_runs(text, spans, BOLD))


@pytest.mark.unit
def test_the_resolver_reports_the_swapped_effects():
    own = {"letter-spacing": "0.15em", "font-variant": "small-caps"}
    resolve = _calibre_resolver({"x": dict(own, **{"text-transform": "uppercase"})})
    css = resolve(_doc('<p><span class="x">w</span></p>').find(".//{*}span"))
    assert (css["letter-spacing"], css["font-variant"], css["text-transform"]) == (
        "0.15em",
        "small-caps",
        "uppercase",
    )
