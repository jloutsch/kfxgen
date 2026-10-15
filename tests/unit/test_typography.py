"""Small caps, text-transform, letter-spacing and colour reach the KFX (#239).

All four were dropped. Kindle Previewer 4 (kpr4, decoded) writes each as a
style property, on the paragraph or on a run:

- `font-variant: small-caps` as `$583: $369`; `normal` inside small caps as
  `$583: $349`. `font-variant-caps` is not read, `all-small-caps` neither.
- `text-transform` as `$41`: `$372` uppercase, `$373` lowercase, `$374`
  capitalize, `$349` none inside a transformed paragraph. The text itself
  is not changed. `full-width` is dropped.
- `letter-spacing` as `$32`: em, rem and pt as written, a px as 0.45pt
  (3px is 1.35pt), `normal` inside spaced text as 0em. A negative spacing
  and a percentage are dropped.
- `color` as `$19`, an ARGB integer. A colour with less than 4.5:1 contrast
  against white is darkened to it: every grey from #ffffff to #767676 is
  written 0xFF767676 (give or take 1), yellow 0xFF7A7A00, lime 0xFF008A00.
  Black in black text is not written; black inside a grey paragraph is. A
  link's own colour is not written as `$19`.

A heading's are written on it too.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import (
    COLOUR_FLAG,
    SMALL_CAPS_FLAG,
    SPACING_FLAG,
    TRANSFORM_FLAG,
    FLAG_BOLD,
    css_letter_spacing,
    css_text_colour,
    flag_value,
)
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

XHTML = "http://www.w3.org/1999/xhtml"
MAIN = " ".join(["Main text of the book."] * 20)
FILLER = f"<p>{MAIN}</p>"

#: Inherited, with calibre's computed value when nothing sets it.
_INHERITED = {
    "color": "black",
    "font-variant": "normal",
    "text-transform": "none",
    "letter-spacing": "normal",
    "font-weight": "normal",
    "font-style": "normal",
    "font-family": None,
}
#: calibre's UA sheet, for the tags these tests use.
_UA = {"b": {"font-weight": "bold"}, "strong": {"font-weight": "bold"}}


class _Style:
    """calibre's Style as the resolver reads it: `.get` is the element's own
    value, `[...]` the computed one, inherited."""

    def __init__(self, stylizer, elem):
        self._s, self._e = stylizer, elem

    def get(self, k, default=None):
        return self._s.own(self._e).get(k, default)

    def __getitem__(self, k):
        e = self._e
        while e is not None and isinstance(e.tag, str):
            own = self._s.own(e)
            if k in own and own[k] != "inherit":
                return own[k]
            if k not in _INHERITED:
                return None
            e = e.getparent()
        return _INHERITED.get(k)

    def pseudo_classes(self, filter_css):
        rule = self._s.first.get(self._e.get("class") or "")
        return {"first-letter": dict(rule)} if rule else {}


class _Stylizer:
    def __init__(self, css, first):
        self.css, self.first = css, first

    def own(self, elem):
        cls = elem.get("class") or ""
        if cls in self.css:
            return self.css[cls]
        return _UA.get(etree.QName(elem).localname, {})

    def style(self, elem):
        return _Style(self, elem)


def _resolver(css=None, first=None):
    return _conv._build_style_resolver(
        None,
        None,
        None,
        stylizer_factory=lambda oeb, item: _Stylizer(css or {}, first or {}),
    )


def _blocks(body, resolver):
    xhtml = f'<html xmlns="{XHTML}"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=resolver,
        native_tables=True,
        base_href="c.xhtml",
    )


def _one(body, css=None, first=None):
    (b,) = _blocks(body, _resolver(css, first))
    return b


def _run(block, word, kind):
    """The value of a `kind` flag over `word` in a converted block."""
    start = block["text"].index(word)
    for s, n, flags in block.get("spans") or ():
        if s <= start and start + len(word) <= s + n:
            return flag_value(flags, kind)
    return None


def _book(tmp_path, body, css=None, first=None, title="One"):
    blocks = _blocks(body, _resolver(css, first))
    text = "\n\n".join(b["text"] for b in blocks)
    chapter = {"title": title, "text": text, "blocks": blocks}
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=[chapter], output_path=str(out)
    )
    return load_fragments(out)


def _found(frags):
    """Each paragraph's text: (its entry $157, {run text: run $157})."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            ref = e.get("$145")
            if not ref:
                continue
            text = texts[str(ref["name"])][ref["$403"]]
            runs = {
                text[r["$143"] : r["$143"] + r["$144"]]: styles.get(
                    str(r.get("$157")), {}
                )
                for r in e.get("$142") or []
            }
            out[text] = (styles.get(str(e.get("$157")), {}), runs)
    return out


def _spacing(style):
    v = style.get("$32")
    return None if v is None else (str(v["$307"]), str(v["$306"]))


# --- Colour: Previewer's values --------------------------------------------


def _channels(argb):
    return [(argb >> s) & 0xFF for s in (24, 16, 8, 0)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("css", "previewer"),
    [
        ("#c00000", 0xFFC00000),
        ("rgb(200, 0, 0)", 0xFFC80000),
        ("blue", 0xFF0000FF),
        ("#333", 0xFF333333),
        ("black", 0xFF000000),
        ("#000", 0xFF000000),
        ("#767676", 0xFF767676),
        ("#808080", 0xFF767676),
        ("#a0a0a0", 0xFF767676),
        ("#c0c0c0", 0xFF767676),
        ("#e0e0e0", 0xFF767676),
        ("#fefefe", 0xFF767676),
        ("#fff", 0xFF757575),
        ("#ff0", 0xFF7A7A00),
        ("#0f0", 0xFF008A00),
        ("#90ee90", 0xFF4E824E),
        ("#ffc0cb", 0xFF916D74),
        ("#87ceeb", 0xFF527C8E),
    ],
)
def test_a_colour_is_written_as_previewer_writes_it(css, previewer):
    """Within 1 a channel: Previewer itself writes 0x75 or 0x76 for greys."""
    got = css_text_colour(css)
    assert got is not None
    assert all(
        abs(a - b) <= 1
        for a, b in zip(_channels(got), _channels(previewer), strict=True)
    ), hex(got)


@pytest.mark.unit
def test_a_darkened_colour_has_4_5_to_1_contrast_with_white():
    def lum(argb):
        def lin(c):
            c /= 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        _a, r, g, b = _channels(argb)
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    for css in ("#fff", "#ff0", "#0f0", "#90ee90", "#ffc0cb", "#87ceeb", "#808080"):
        assert 1.05 / (lum(css_text_colour(css)) + 0.05) >= 4.5, css


@pytest.mark.unit
@pytest.mark.parametrize(
    "css", ["transparent", "rgba(0, 0, 255, 0)", "windowtext", "", None, "#12"]
)
def test_a_colour_that_draws_nothing_or_cant_be_read_is_none(css):
    assert css_text_colour(css) is None


# --- letter-spacing: Previewer's values ------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("css", "kfx"),
    [
        ("0.2em", ("0.2", "$308")),
        ("1em", ("1", "$308")),
        ("0.1rem", ("0.1", "$505")),
        ("2pt", ("2", "$318")),
        ("3px", ("1.35", "$318")),
        ("1px", ("0.45", "$318")),
        ("-0.05em", None),
        ("-1px", None),
        ("10%", None),
        ("normal", None),
        ("0", None),
        ("0em", None),
        (None, None),
    ],
)
def test_letter_spacing_is_written_as_previewer_writes_it(css, kfx):
    assert css_letter_spacing(css) == kfx


# --- The converter: run flags and block styles -----------------------------


@pytest.mark.unit
def test_a_small_caps_span_is_a_small_caps_run():
    b = _one(
        '<p>a <span class="sc">Word</span> b</p>',
        {"sc": {"font-variant": "small-caps"}},
    )
    assert _run(b, "Word", SMALL_CAPS_FLAG) == "small-caps"
    assert _run(b, "b", SMALL_CAPS_FLAG) is None


@pytest.mark.unit
def test_a_small_caps_paragraph_is_a_block_style_not_a_run():
    b = _one(
        '<p class="sc">All <i>of</i> it</p>', {"sc": {"font-variant": "small-caps"}}
    )
    assert b["block_style"]["small_caps"] == "small-caps"
    assert _run(b, "of", SMALL_CAPS_FLAG) is None


@pytest.mark.unit
def test_normal_inside_small_caps_says_normal():
    css = {"sc": {"font-variant": "small-caps"}, "n": {"font-variant": "normal"}}
    b = _one('<p class="sc">All <span class="n">plain</span> it</p>', css)
    assert _run(b, "plain", SMALL_CAPS_FLAG) == "normal"


@pytest.mark.unit
def test_font_variant_caps_is_not_read():
    """Previewer writes nothing for `font-variant-caps`."""
    b = _one(
        '<p>a <span class="c">Word</span></p>',
        {"c": {"font-variant-caps": "small-caps"}},
    )
    assert _run(b, "Word", SMALL_CAPS_FLAG) is None


@pytest.mark.unit
@pytest.mark.parametrize("value", ["uppercase", "lowercase", "capitalize"])
def test_a_transformed_span_keeps_its_text(value):
    b = _one('<p>a <span class="t">Word</span></p>', {"t": {"text-transform": value}})
    assert b["text"] == "a Word"
    assert _run(b, "Word", TRANSFORM_FLAG) == value


@pytest.mark.unit
def test_none_inside_uppercase_says_none():
    css = {"up": {"text-transform": "uppercase"}, "n": {"text-transform": "none"}}
    b = _one('<p class="up">All <span class="n">plain</span></p>', css)
    assert b["block_style"]["text_transform"] == "uppercase"
    assert _run(b, "plain", TRANSFORM_FLAG) == "none"


@pytest.mark.unit
def test_full_width_is_not_read():
    b = _one(
        '<p>a <span class="t">Word</span></p>', {"t": {"text-transform": "full-width"}}
    )
    assert _run(b, "Word", TRANSFORM_FLAG) is None


@pytest.mark.unit
def test_a_spaced_span_is_a_spaced_run_in_kfx_units():
    b = _one('<p>a <span class="s">Word</span></p>', {"s": {"letter-spacing": "3px"}})
    assert _run(b, "Word", SPACING_FLAG) == ("1.35", "$318")


@pytest.mark.unit
def test_normal_inside_spaced_text_is_zero():
    css = {"s": {"letter-spacing": "0.2em"}, "n": {"letter-spacing": "normal"}}
    b = _one('<p class="s">All <span class="n">plain</span></p>', css)
    assert b["block_style"]["letter_spacing"] == ("0.2", "$308")
    assert _run(b, "plain", SPACING_FLAG) == ("0", "$308")


@pytest.mark.unit
def test_a_coloured_span_is_a_coloured_run():
    b = _one('<p>a <span class="r">Word</span></p>', {"r": {"color": "#c00000"}})
    assert _run(b, "Word", COLOUR_FLAG) == 0xFFC00000
    assert "colour" not in b["block_style"]


@pytest.mark.unit
def test_black_in_black_text_is_no_run():
    b = _one('<p>a <span class="k">Word</span></p>', {"k": {"color": "#000"}})
    assert _run(b, "Word", COLOUR_FLAG) is None


@pytest.mark.unit
def test_a_colour_inherited_from_a_div_is_the_paragraphs():
    css = {"g": {"color": "#333"}, "k": {"color": "black"}}
    b = _one('<div class="g"><p>Grey <span class="k">black</span></p></div>', css)
    assert b["block_style"]["colour"] == 0xFF333333
    assert _run(b, "black", COLOUR_FLAG) == 0xFF000000


@pytest.mark.unit
def test_a_links_colour_is_not_a_run():
    """Previewer writes a link's colour in its link styles, not `$19`."""
    css = {"l": {"color": "#00f"}}
    b = _one('<p id="x">a <a class="l" href="#x">link <i>here</i></a></p>', css)
    assert _run(b, "link", COLOUR_FLAG) is None
    assert _run(b, "here", COLOUR_FLAG) is None


@pytest.mark.unit
def test_an_abbr_takes_small_caps_and_lowercase():
    css = {"a": {"font-variant": "small-caps", "text-transform": "lowercase"}}
    b = _one('<p>The <abbr class="a">NASA</abbr> word.</p>', css)
    assert _run(b, "NASA", SMALL_CAPS_FLAG) == "small-caps"
    assert _run(b, "NASA", TRANSFORM_FLAG) == "lowercase"


@pytest.mark.unit
def test_a_bold_tag_set_to_normal_small_caps_is_small_caps_not_bold():
    """pg64317's `b { font-variant: small-caps; font-weight: normal }`: #362
    kept the bold while kfxgen couldn't draw small caps. Now it can."""
    css = {"sc": {"font-weight": "normal", "font-variant": "small-caps"}}
    b = _one('<p>a <b class="sc">Name</b> b</p>', css)
    assert _run(b, "Name", SMALL_CAPS_FLAG) == "small-caps"
    start = b["text"].index("Name")
    assert not any(
        FLAG_BOLD in f for s, n, f in b.get("spans") or () if s <= start < s + n
    )


@pytest.mark.unit
def test_a_first_letter_colour_goes_on_the_initial():
    first = {"x": {"float": "left", "font-size": "3em", "color": "#900"}}
    b = _one('<p class="x">Once upon a time.</p>', first=first)
    assert _run(b, "O", COLOUR_FLAG) == 0xFF990000
    assert _run(b, "nce", COLOUR_FLAG) is None


@pytest.mark.unit
def test_no_resolver_carries_none_of_them():
    (b,) = _blocks('<p>a <span class="sc">Word</span></p>', None)
    assert not b.get("spans")


# --- The generator: the KFX properties -------------------------------------


@pytest.mark.unit
def test_runs_are_written_as_previewer_writes_them(tmp_path):
    css = {
        "sc": {"font-variant": "small-caps"},
        "up": {"text-transform": "uppercase"},
        "lo": {"text-transform": "lowercase"},
        "cap": {"text-transform": "capitalize"},
        "sp": {"letter-spacing": "3px"},
        "red": {"color": "#c00000"},
        "wh": {"color": "#fff"},
    }
    body = FILLER + (
        '<p>Runs <span class="sc">Caps</span> <span class="up">upper</span> '
        '<span class="lo">LOWER</span> <span class="cap">cap it</span> '
        '<span class="sp">spaced</span> <span class="red">red</span> '
        '<span class="wh">white</span> end.</p>'
    )
    text = "Runs Caps upper LOWER cap it spaced red white end."
    style, runs = _found(_book(tmp_path, body, css))[text]
    assert runs["Caps"]["$583"] == "$369"
    assert runs["upper"]["$41"] == "$372"
    assert runs["LOWER"]["$41"] == "$373"
    assert runs["cap it"]["$41"] == "$374"
    assert _spacing(runs["spaced"]) == ("1.35", "$318")
    assert runs["red"]["$19"] == 0xFFC00000
    assert abs(runs["white"]["$19"] - 0xFF767676) <= 0x010101
    for prop in ("$583", "$41", "$32", "$19"):
        assert prop not in style


@pytest.mark.unit
def test_a_paragraphs_are_written_on_its_style(tmp_path):
    css = {
        "p": {
            "font-variant": "small-caps",
            "text-transform": "uppercase",
            "letter-spacing": "0.2em",
            "color": "#c00000",
        },
        "n": {
            "font-variant": "normal",
            "text-transform": "none",
            "letter-spacing": "normal",
            "color": "black",
        },
    }
    body = FILLER + '<p class="p">All of it <span class="n">but this</span>.</p>'
    style, runs = _found(_book(tmp_path, body, css))["All of it but this."]
    assert style["$583"] == "$369"
    assert style["$41"] == "$372"
    assert _spacing(style) == ("0.2", "$308")
    assert style["$19"] == 0xFFC00000
    reset = runs["but this"]
    assert reset["$583"] == "$349"
    assert reset["$41"] == "$349"
    assert _spacing(reset) == ("0", "$308")
    assert reset["$19"] == 0xFF000000


@pytest.mark.unit
def test_a_plain_book_writes_none_of_them(tmp_path):
    frags = _book(tmp_path, FILLER + "<p>Plain <i>words</i>.</p>", {})
    for f in by_type(frags, "$157"):
        assert not {"$583", "$41", "$32", "$19"} & set(map(str, val(f))), val(f)


@pytest.mark.unit
def test_the_books_heading_css_goes_on_the_chapter_heading(tmp_path):
    """Previewer's `h2 { text-transform: uppercase; color: #800000;
    letter-spacing: 0.1em }`: all three on the heading."""
    css = {
        "h": {
            "text-transform": "uppercase",
            "color": "#800000",
            "letter-spacing": "0.1em",
        }
    }
    body = '<h2 class="h">One</h2>' + FILLER
    style, _runs = _found(_book(tmp_path, body, css))["One"]
    assert style["$41"] == "$372"
    assert style["$19"] == 0xFF800000
    assert _spacing(style) == ("0.1", "$308")
    assert style.get("$13") == "$361"


@pytest.mark.unit
def test_a_heading_without_them_keeps_kfxgens_heading_style(tmp_path):
    frags = _book(tmp_path, "<h2>One</h2>" + FILLER, {})
    style, _runs = _found(frags)["One"]
    assert not {"$583", "$41", "$32", "$19"} & set(style)


@pytest.mark.unit
def test_a_coloured_cell_paragraph_is_coloured(tmp_path):
    css = {"r": {"color": "#c00000"}}
    body = FILLER + '<table><tr><td class="r">Red cell</td><td>Plain</td></tr></table>'
    found = _found(_book(tmp_path, body, css))
    assert found["Red cell"][0]["$19"] == 0xFFC00000
    assert "$19" not in found["Plain"][0]


@pytest.mark.unit
def test_a_small_caps_link_keeps_its_small_caps(tmp_path):
    """A link run with no emphasis gets no style; small caps is a style."""
    css = {"sc": {"font-variant": "small-caps"}}
    body = FILLER + '<p id="t">See <a class="sc" href="#t">Here</a> now.</p>'
    _style, runs = _found(_book(tmp_path, body, css))["See Here now."]
    assert runs["Here"]["$583"] == "$369"


@pytest.mark.unit
def test_a_link_inside_a_coloured_span_keeps_the_spans_colour():
    """Previewer drops only a link's own colour: a link in a red span is red
    (`0xffee0000` on the link, #377 review)."""
    css = {"r": {"color": "#e00"}}
    b = _one(
        '<p id="t">a <span class="r">red <a href="#t">link</a> red</span></p>', css
    )
    assert _run(b, "link", COLOUR_FLAG) == 0xFFEE0000
