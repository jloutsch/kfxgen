"""A paragraph's and a run's CSS font-size reach the KFX (#241).

The generator set a size only for headings, superscripts and kfxgen's own
pages, so `p.small { font-size: 0.8em }` (copyright lines, captions) and
`span { font-size: 1.5em }` (a word shouted in a picture book) came out at
the main text size. Kindle Previewer 4 (kpr4, decoded) writes each as `$16`
in rem, measured against the book's main text: in a book whose paragraphs
are 1.2em of a 0.75em body, the paragraphs have no size, a 0.9em paragraph
is 0.75, text straight in the body 0.833333 and the h1 1.66667. A 1.5em word
in a 0.8em paragraph is 1.2; 24px is 1.5 (CSS px, 16 to the em). Headings
keep their default sizes (h2 1.5, h3 1.17), and nothing is clamped (0.3 and
6 pass).

Calibre's computed size can't be used: it converts px at the output
profile's DPI (24px came out 5.76pt on the Voyage profile), so the converter
works the size out in CSS px from each element's specified value, up its
ancestors, and the generator measures it against the size most of the
book's text has.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

XHTML = "http://www.w3.org/1999/xhtml"

#: calibre's UA sheet, as `.get()` reports it (calibre 9.16, measured).
UA = {
    "small": {"font-size": "smaller"},
    "big": {"font-size": "larger"},
    "sup": {"font-size": "smaller"},
    "sub": {"font-size": "smaller"},
    "h1": {"font-size": "2em", "font-weight": "bold"},
    "h2": {"font-size": "1.5em", "font-weight": "bold"},
    "h3": {"font-size": "1.17em", "font-weight": "bold"},
}


def _doc(body, html_css="", body_css=""):
    return etree.fromstring(
        f'<html xmlns="{XHTML}" class="{html_css}"><body class="{body_css}">'
        f"{body}</body></html>".encode()
    )


class _Style:
    def __init__(self, own):
        self._own = own

    def get(self, k, default=None):
        return self._own.get(k, default)

    def __getitem__(self, k):
        return self._own.get(k)


class _Stylizer:
    """`.get()` is the element's own value: its class's, or else its tag's,
    then the UA sheet's."""

    def __init__(self, css):
        self._css = css

    def style(self, elem):
        tag = etree.QName(elem).localname
        own = self._css.get(elem.get("class") or "")
        if own is None:
            own = self._css.get(tag, UA.get(tag, {}))
        return _Style(own)


def _resolver(css):
    return _conv._build_style_resolver(
        None, None, None, stylizer_factory=lambda oeb, item: _Stylizer(css)
    )


def _px(css, body, find=".//{*}p", **doc):
    return _resolver(css)(_doc(body, **doc).find(find))["font-size"]


# --- The converter: each element's size in CSS px -------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [
        ("0.8em", 12.8),
        ("2em", 32.0),
        ("150%", 24.0),
        ("24px", 24.0),
        ("9pt", 12.0),
        ("0.3em", 4.8),
        ("6em", 96.0),
        ("large", 18.0),
        ("2ex", 16.0),
        ("1ch", 8.0),
    ],
)
def test_a_paragraph_size_in_css_px(value, expected):
    assert _px({"x": {"font-size": value}}, '<p class="x">t</p>') == pytest.approx(
        expected
    )


@pytest.mark.unit
def test_an_unsized_paragraph_is_16px():
    assert _px({}, "<p>t</p>") == pytest.approx(16.0)


@pytest.mark.unit
def test_a_nested_size_multiplies():
    css = {"x": {"font-size": "0.8em"}, "s": {"font-size": "1.5em"}}
    body = '<p class="x">a <span class="s">b</span></p>'
    assert _px(css, body, find=".//{*}span") == pytest.approx(19.2)


@pytest.mark.unit
def test_a_size_on_the_body_or_a_wrapper_is_inherited():
    css = {"b": {"font-size": "1.2em"}, "d": {"font-size": "75%"}}
    css["i"] = {"font-size": "inherit"}
    body = '<div class="d"><p class="i">t</p></div>'
    assert _px(css, body, body_css="b") == pytest.approx(14.4)


@pytest.mark.unit
@pytest.mark.parametrize("tag, expected", [("small", 16 / 1.2), ("big", 19.2)])
def test_small_and_big_tags(tag, expected):
    """Previewer's step: <small> 0.833333, <big> 1.2."""
    got = _px({}, f"<p>a <{tag}>b</{tag}></p>", find=f".//{{*}}{tag}")
    assert got == pytest.approx(expected, abs=1e-3)


@pytest.mark.unit
def test_an_unreadable_size_is_inherited():
    css = {"d": {"font-size": "0.8em"}, "c": {"font-size": "calc(1em + 2px)"}}
    body = '<div class="d"><p class="c">t</p></div>'
    assert _px(css, body) == pytest.approx(12.8)


@pytest.mark.unit
def test_rem_follows_the_root_element():
    """The web's `html { font-size: 62.5% }`: rem is 10px, not 16."""
    css = {"r": {"font-size": "62.5%"}, "x": {"font-size": "1.2rem"}}
    assert _px(css, '<p class="x">t</p>', html_css="r") == pytest.approx(12.0)


@pytest.mark.unit
def test_compute_block_style_carries_the_px_size():
    from kfxgen.inline_style import compute_block_style

    assert compute_block_style({"font-size": 12.8})["font_size"] == 12.8
    assert compute_block_style({})["font_size"] is None


# --- The generator: sizes against the book's main text --------------------

#: What the resolver reports, in CSS px, by class or else tag.
PX = {
    "small": 12.8,
    "big": 32.0,
    "s15": 24.0,
    "cap": 12.8,
    "main": 14.4,
    "norm": 16.0,
}
PX_BY_TAG = {"sup": 16 / 1.2, "h2": 24.0}

#: Main text, long enough that its size is the book's main size.
MAIN = " ".join(["Main text of the book."] * 20)
FILLER = f"<p>{MAIN}</p>"


def _css_px(elem):
    px = 16.0
    for e in [elem, *elem.iterancestors()]:
        cls, tag = e.get("class") or "", etree.QName(e).localname
        if cls in PX or tag in PX_BY_TAG:
            px = PX.get(cls) or PX_BY_TAG[tag]
            break
    css = {"font-size": px}
    if etree.QName(elem).localname == "h2":
        css["font-weight"] = "bold"
    return css


def _book(tmp_path, body, title="One", chapter_extra=None, resolver=_css_px):
    xhtml = f'<html xmlns="{XHTML}"><body>{body}</body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=resolver,
        native_tables=True,
        base_href="c.xhtml",
    )
    text = "\n\n".join(b["text"] for b in blocks)
    chapter = {"title": title, "text": text, "blocks": blocks}
    chapter.update(chapter_extra or {})
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=[chapter], output_path=str(out)
    )
    return load_fragments(out)


def _found(frags):
    """Each paragraph's text: (its entry $157, [(run text, run $157)])."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            ref = e.get("$145")
            if not ref:
                continue
            text = texts[str(ref["name"])][ref["$403"]]
            runs = [
                (
                    text[r["$143"] : r["$143"] + r["$144"]],
                    styles.get(str(r.get("$157")), {}),
                )
                for r in e.get("$142") or []
            ]
            out[text] = (styles.get(str(e.get("$157")), {}), runs)
    return out


def _fs(style):
    size = style.get("$16")
    return float(size["$307"]) if size is not None else None


@pytest.mark.unit
def test_a_small_paragraph_is_written_small(tmp_path):
    found = _found(_book(tmp_path, FILLER + '<p class="small">Small print.</p>'))
    assert _fs(found["Small print."][0]) == pytest.approx(0.8)
    assert _fs(found[MAIN][0]) is None


@pytest.mark.unit
def test_a_big_paragraph_is_written_big(tmp_path):
    found = _found(_book(tmp_path, FILLER + '<p class="big">Shout.</p>'))
    assert _fs(found["Shout."][0]) == pytest.approx(2.0)


@pytest.mark.unit
def test_the_main_text_has_no_size_whatever_its_css_size(tmp_path):
    """Sizes are measured against the size most of the text has, not 16px."""
    body = f'<p class="main">{MAIN}</p><p class="small">Tail.</p>'
    found = _found(_book(tmp_path, body))
    assert _fs(found[MAIN][0]) is None
    assert _fs(found["Tail."][0]) == pytest.approx(12.8 / 14.4, abs=1e-4)


@pytest.mark.unit
def test_previewers_main_text_book(tmp_path):
    """kpr4's probe: body 0.75em, p 1.2em, p.small 0.9em, text in a div."""
    css = {
        "body": {"font-size": "0.75em"},
        "p": {"font-size": "1.2em"},
        "small": {"font-size": "0.9em"},
    }
    resolve = _resolver(css)
    body = (
        f"<p>{MAIN}</p>"
        '<p class="small">Smallclass.</p><div>Divtext.</div><h1>Heading one</h1>'
    )
    found = _found(_book(tmp_path, body, title="Other", resolver=resolve))
    assert _fs(found[MAIN][0]) is None
    assert _fs(found["Smallclass."][0]) == pytest.approx(0.75)
    assert _fs(found["Divtext."][0]) == pytest.approx(0.8333, abs=1e-4)
    assert _fs(found["Heading one"][0]) == pytest.approx(1.6667, abs=1e-4)


@pytest.mark.unit
def test_a_sized_word_gets_its_own_size(tmp_path):
    body = FILLER + '<p>A <span class="s15">BOOM</span> here.</p>'
    _, runs = _found(_book(tmp_path, body))["A BOOM here."]
    (style,) = [s for t, s in runs if t == "BOOM"]
    assert _fs(style) == pytest.approx(1.5)


@pytest.mark.unit
def test_a_sized_word_is_measured_against_the_main_text(tmp_path):
    body = f'<p class="main">{MAIN}</p><p class="main">A <span class="s15">BOOM</span>.</p>'
    _, runs = _found(_book(tmp_path, body))["A BOOM."]
    (style,) = [s for t, s in runs if t == "BOOM"]
    assert _fs(style) == pytest.approx(24 / 14.4, abs=1e-4)


@pytest.mark.unit
def test_main_text_sized_on_a_span_is_the_main_size(tmp_path):
    """Each paragraph's text in a 0.8em span: Previewer writes no size on
    the paragraphs or the runs, and the h2 is 1.5 x 16 / 12.8 (#375 review)."""
    paras = "".join(f'<p><span class="cap">{MAIN} {i}</span></p>' for i in range(5))
    found = _found(_book(tmp_path, paras + "<h2>Heading two</h2>", title="Other"))
    style, runs = found[f"{MAIN} 0"]
    assert _fs(style) is None
    assert runs == []
    assert _fs(found["Heading two"][0]) == pytest.approx(1.875)


@pytest.mark.unit
def test_a_word_at_the_main_size_in_a_big_paragraph_says_1(tmp_path):
    """Previewer writes 1 for it; without a size it takes the paragraph's."""
    body = FILLER + '<p class="big">Big <span class="norm">plain</span> words.</p>'
    _, runs = _found(_book(tmp_path, body))["Big plain words."]
    (style,) = [s for t, s in runs if t == "plain"]
    assert _fs(style) == pytest.approx(1.0)


@pytest.mark.unit
def test_a_sized_word_inside_a_sized_word_has_its_own_size(tmp_path):
    body = FILLER + (
        '<p>A <span class="s15">big <span class="cap">small</span> big</span>.</p>'
    )
    _, runs = _found(_book(tmp_path, body))["A big small big."]
    sizes = {t: _fs(s) for t, s in runs}
    assert sizes["small"] == pytest.approx(0.8)
    assert sizes["big "] == pytest.approx(1.5)


@pytest.mark.unit
def test_a_nested_sized_run_carries_one_size():
    from kfxgen.inline_style import SIZE_FLAG

    xhtml = (
        f'<html xmlns="{XHTML}"><body><p>A <span class="s15">big '
        '<span class="cap">small</span></span>.</p></body></html>'
    )
    (block,) = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), style_resolver=_css_px
    )
    for _s, _n, flags in block["spans"]:
        sizes = [f for f in flags if isinstance(f, tuple) and f[0] == SIZE_FLAG]
        assert len(sizes) <= 1, sizes


@pytest.mark.unit
def test_an_unsized_tag_carries_no_size():
    from kfxgen.inline_style import run_font_size

    xhtml = f'<html xmlns="{XHTML}"><body><p>A <i>slanted</i> word.</p></body></html>'
    (block,) = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), style_resolver=_css_px
    )
    assert [run_font_size(flags) for _s, _n, flags in block["spans"]] == [None]


@pytest.mark.unit
def test_a_word_the_size_of_its_paragraph_has_no_run(tmp_path):
    body = FILLER + '<p class="small">Small <span class="cap">same</span> size.</p>'
    _, runs = _found(_book(tmp_path, body))["Small same size."]
    assert not [t for t, s in runs if t == "same"]


@pytest.mark.unit
def test_a_superscript_keeps_its_own_size(tmp_path):
    """kfxgen's superscript size and `$44` raise, not calibre's `smaller`."""
    from kfxgen.native_generator import superscript_metrics

    body = FILLER + "<p>E = mc<sup>2</sup> here.</p>"
    _, runs = _found(_book(tmp_path, body))["E = mc2 here."]
    (style,) = [s for t, s in runs if t == "2"]
    assert _fs(style) == pytest.approx(superscript_metrics()[0])


@pytest.mark.unit
def test_an_in_chapter_heading_gets_its_size(tmp_path):
    found = _found(_book(tmp_path, FILLER + "<h2>Unlisted heading</h2>"))
    assert _fs(found["Unlisted heading"][0]) == pytest.approx(1.5)


@pytest.mark.unit
def test_the_books_heading_keeps_its_own_size(tmp_path):
    """The book's heading prints at its own size, as Kindle Previewer prints
    it (#490): a sized run in it doesn't change that size."""
    body = '<h2>The <span class="big">Harbour</span></h2>' + FILLER
    style, runs = _found(_book(tmp_path, body, title="The Harbour"))["The Harbour"]
    assert _fs(style) == pytest.approx(1.5)  # an h2's own 1.5em
    # The word's run is carried onto the heading (#366), without its size.
    assert [t for t, _ in runs] == ["Harbour"]
    assert all(_fs(s) is None for _, s in runs)


@pytest.mark.unit
def test_a_page_kfxgen_sizes_keeps_its_size(tmp_path):
    """A copyright page kfxgen sets small keeps kfxgen's size."""
    body = FILLER + '<p class="big">Notice.</p>'
    found = _found(_book(tmp_path, body, chapter_extra={"font_size": 0.75}))
    assert _fs(found["Notice."][0]) == pytest.approx(0.75)


@pytest.mark.unit
def test_a_sized_link_gets_a_style(tmp_path):
    """A link-only run gets no style (#53); sized, it needs one."""
    body = (
        FILLER + '<p>See <a class="s15" href="c.xhtml#t">this</a> note.</p>'
        '<p id="t">Note.</p>'
    )
    _, runs = _found(_book(tmp_path, body))["See this note."]
    (style,) = [s for t, s in runs if t == "this"]
    assert _fs(style) == pytest.approx(1.5)


@pytest.mark.unit
def test_a_small_table_cell_is_written_small(tmp_path):
    body = FILLER + (
        '<table><tr><td class="small">Cellsmall</td><td>Plaincell</td>'
        "</tr><tr><td>x</td><td>y</td></tr></table>"
    )
    found = _found(_book(tmp_path, body))
    assert _fs(found["Cellsmall"][0]) == pytest.approx(0.8)
    assert _fs(found["Plaincell"][0]) is None


@pytest.mark.unit
def test_no_resolver_writes_no_sizes(tmp_path):
    body = '<p class="small">Small print.</p><h2>Heading</h2>'
    found = _found(_book(tmp_path, body, resolver=None))
    assert _fs(found["Small print."][0]) is None
    assert _fs(found["Heading"][0]) is None
