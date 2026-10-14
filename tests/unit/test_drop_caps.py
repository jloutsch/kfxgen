"""A drop cap reaches the KFX (#240).

Books draw a chapter's first letter large, two ways:
`p::first-letter { float: left; font-size: 3em }` and
`<span class="dropcap">O</span>nce` with the span floated and sized. Both
came out as a plain first letter.

Kindle Previewer 4 (kpr4, decoded) writes both the same way: the paragraph
gets `$125` (the lines the initial drops through) and `$126` (its
characters), and the initial has no size of its own. The lines follow the
initial's size against the main text: 1.5em and 1.8em drop 2 lines, 2em to
3.5em 3, 3.8em to 5em 4, 6em 5, 8em 6, 10em 7, which is floor(2 + 0.55 x
size). A 1.2em floated initial gets no drop cap. A leading quote counts as a
character, and so does each letter of the span ("Th" is 2). An initial
that isn't floated is a raised initial: the letter at its size, no drop.

Calibre keeps `::first-letter` on the paragraph's style (`pseudo_classes`)
for KFX; it makes a span of it only for MOBI and DOCX.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import FLAG_BOLD, FLAG_ITALIC, run_font_size
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

XHTML = "http://www.w3.org/1999/xhtml"

#: Main text, long enough that its size is the book's main size.
MAIN = " ".join(["Main text of the book."] * 20)
FILLER = f"<p>{MAIN}</p>"
REST = "nce upon a time there was a long opening paragraph."


class _Style:
    def __init__(self, own, first_letter):
        self._own = own
        self._pseudo = {"first-letter": first_letter} if first_letter else {}

    def pseudo_classes(self, filter_css):
        """calibre's: the pseudo-element rules, less `filter_css`."""
        return {
            k: {p: v for p, v in css.items() if p not in filter_css}
            for k, css in self._pseudo.items()
        }

    def get(self, k, default=None):
        return self._own.get(k, default)

    def __getitem__(self, k):
        return self._own.get(k)


class _Stylizer:
    """By class: `css` is each class's own CSS, `first` its ::first-letter."""

    def __init__(self, css, first):
        self._css, self._first = css, first

    def style(self, elem):
        cls = elem.get("class") or ""
        return _Style(self._css.get(cls, {}), self._first.get(cls))


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


def _book(tmp_path, body, resolver):
    blocks = _blocks(body, resolver)
    text = "\n\n".join(b["text"] for b in blocks)
    chapter = {"title": "One", "text": text, "blocks": blocks}
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


def _drop(style):
    """(lines, characters) a paragraph style drops its initial through."""
    if "$125" not in style:
        return None
    return int(style["$125"]), int(style["$126"])


def _fs(style):
    size = style.get("$16")
    return float(size["$307"]) if size is not None else None


def _first_letter(size="3em", **more):
    return {"float": "left", "font-size": size, "line-height": "1", **more}


def _span_cap(size="3.2em", **more):
    return {"float": "left", "font-size": size, "line-height": "1", **more}


# --- The resolver: what calibre keeps of ::first-letter --------------------


@pytest.mark.unit
def test_the_resolver_reports_the_first_letter_in_css_px():
    resolve = _resolver(first={"x": _first_letter("3em")})
    p = _blocks_root('<p class="x">Once.</p>').find(".//{*}p")
    fl = resolve(p)["first-letter"]
    assert fl["float"] == "left"
    assert fl["font-size"] == pytest.approx(48.0)


@pytest.mark.unit
def test_the_first_letter_size_is_against_its_paragraph():
    resolve = _resolver(css={"x": {"font-size": "0.8em"}}, first={"x": _first_letter()})
    p = _blocks_root('<p class="x">Once.</p>').find(".//{*}p")
    assert resolve(p)["first-letter"]["font-size"] == pytest.approx(38.4)


@pytest.mark.unit
def test_no_first_letter_reports_none():
    p = _blocks_root("<p>Once.</p>").find(".//{*}p")
    assert _resolver()(p)["first-letter"] is None


def _blocks_root(body):
    return etree.fromstring(
        f'<html xmlns="{XHTML}"><body>{body}</body></html>'.encode()
    )


# --- The converter: the initial's characters and size ----------------------


@pytest.mark.unit
def test_a_floated_first_letter_marks_the_paragraph():
    (b,) = _blocks(
        '<p class="x">O' + REST + "</p>", _resolver(first={"x": _first_letter()})
    )
    assert b["block_style"]["drop_cap"] == 1
    (s, length, flags) = b["spans"][0]
    assert (s, length, run_font_size(flags)) == (0, 1, pytest.approx(48.0))


@pytest.mark.unit
def test_a_leading_quote_is_part_of_the_initial():
    body = '<p class="x">“O' + REST + "</p>"
    (b,) = _blocks(body, _resolver(first={"x": _first_letter()}))
    assert b["block_style"]["drop_cap"] == 2
    assert b["spans"][0][:2] == (0, 2)


@pytest.mark.unit
def test_a_first_letter_inside_emphasis_keeps_the_emphasis():
    body = '<p class="x"><em class="i">Once</em> upon a time.</p>'
    css = {"i": {"font-style": "italic"}}
    (b,) = _blocks(body, _resolver(css=css, first={"x": _first_letter()}))
    assert b["block_style"]["drop_cap"] == 1
    italic = FLAG_ITALIC
    assert [(s, n, italic in f, run_font_size(f)) for s, n, f in b["spans"]] == [
        (0, 1, True, pytest.approx(48.0)),
        (1, 3, True, None),
    ]


@pytest.mark.unit
def test_a_bold_first_letter_is_bold():
    first = {"x": _first_letter(**{"font-weight": "bold"})}
    (b,) = _blocks('<p class="x">O' + REST + "</p>", _resolver(first=first))
    assert FLAG_BOLD in b["spans"][0][2]


@pytest.mark.unit
def test_a_first_letter_not_floated_is_sized_but_not_dropped():
    first = {"x": {"font-size": "3em"}}
    (b,) = _blocks('<p class="x">O' + REST + "</p>", _resolver(first=first))
    assert "drop_cap" not in b["block_style"]
    assert run_font_size(b["spans"][0][2]) == pytest.approx(48.0)


@pytest.mark.unit
def test_a_first_letter_floated_right_is_not_dropped():
    first = {"x": _first_letter(float="right")}
    (b,) = _blocks('<p class="x">O' + REST + "</p>", _resolver(first=first))
    assert "drop_cap" not in b["block_style"]


@pytest.mark.unit
def test_a_floated_span_initial_marks_the_paragraph():
    body = '<p><span class="dc">O</span>' + REST + "</p>"
    (b,) = _blocks(body, _resolver(css={"dc": _span_cap()}))
    assert b["block_style"]["drop_cap"] == 1


@pytest.mark.unit
def test_a_two_letter_span_initial_is_two_characters():
    body = '<p><span class="dc">Th</span>ere once was.</p>'
    (b,) = _blocks(body, _resolver(css={"dc": _span_cap()}))
    assert b["block_style"]["drop_cap"] == 2


@pytest.mark.unit
def test_a_floated_span_after_text_is_not_an_initial():
    # The text before it opens on the span's letter, as the paragraph would.
    body = '<p>O, <span class="dc">O</span>' + REST + "</p>"
    (b,) = _blocks(body, _resolver(css={"dc": _span_cap()}))
    assert "drop_cap" not in b["block_style"]


@pytest.mark.unit
def test_a_span_initial_not_floated_is_not_dropped():
    body = '<p><span class="dc">O</span>' + REST + "</p>"
    (b,) = _blocks(body, _resolver(css={"dc": {"font-size": "3em"}}))
    assert "drop_cap" not in b["block_style"]


# --- The generator: $125 and $126 -------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "size, lines",
    [
        ("1.5em", 2),
        ("1.8em", 2),
        ("2em", 3),
        ("3em", 3),
        ("3.5em", 3),
        ("3.8em", 4),
        ("5em", 4),
        ("6em", 5),
        ("8em", 6),
        ("10em", 7),
    ],
)
def test_the_lines_follow_previewer(tmp_path, size, lines):
    body = FILLER + '<p class="x">O' + REST + "</p>"
    found = _found(_book(tmp_path, body, _resolver(first={"x": _first_letter(size)})))
    style, runs = found["O" + REST]
    assert _drop(style) == (lines, 1)
    # The Kindle sizes the initial to its lines: it has no size of its own.
    assert [r for r in runs if r[0] == "O"] == []


@pytest.mark.unit
@pytest.mark.parametrize("size", [1.2, 1.3, 1.4])
def test_a_small_floated_initial_is_not_dropped(tmp_path, size):
    """Previewer makes no drop cap at 1.2, 1.3 or 1.4em (#376 review)."""
    body = FILLER + '<p class="x">O' + REST + "</p>"
    resolver = _resolver(first={"x": _first_letter(f"{size}em")})
    style, runs = _found(_book(tmp_path, body, resolver))["O" + REST]
    assert _drop(style) is None
    assert [(t, _fs(s)) for t, s in runs] == [("O", pytest.approx(size))]


@pytest.mark.unit
def test_the_lines_are_against_the_main_text_not_the_paragraph(tmp_path):
    """Previewer: a 3em initial in a 1.5em paragraph drops 4 lines (4.5em)."""
    css = {"big": {"font-size": "1.5em"}}
    body = FILLER + '<p class="big">O' + REST + "</p>"
    resolver = _resolver(css=css, first={"big": _first_letter()})
    style, _ = _found(_book(tmp_path, body, resolver))["O" + REST]
    assert _drop(style) == (4, 1)
    assert _fs(style) == pytest.approx(1.5)


@pytest.mark.unit
def test_a_quoted_initial_drops_two_characters(tmp_path):
    body = FILLER + '<p class="x">“O' + REST + "</p>"
    found = _found(_book(tmp_path, body, _resolver(first={"x": _first_letter()})))
    style, runs = found["“O" + REST]
    assert _drop(style) == (3, 2)
    assert runs == []


@pytest.mark.unit
def test_a_span_initial_is_dropped_with_no_size(tmp_path):
    body = FILLER + '<p><span class="dc">O</span>' + REST + "</p>"
    found = _found(_book(tmp_path, body, _resolver(css={"dc": _span_cap()})))
    style, runs = found["O" + REST]
    assert _drop(style) == (3, 1)
    assert runs == []


@pytest.mark.unit
def test_a_bold_initial_keeps_its_bold(tmp_path):
    first = {"x": _first_letter(**{"font-weight": "bold"})}
    body = FILLER + '<p class="x">O' + REST + "</p>"
    style, runs = _found(_book(tmp_path, body, _resolver(first=first)))["O" + REST]
    assert _drop(style) == (3, 1)
    ((text, run),) = runs
    assert text == "O"
    assert run.get("$13") == "$361"
    assert "$16" not in run


@pytest.mark.unit
def test_a_raised_initial_keeps_its_size(tmp_path):
    body = FILLER + '<p class="x">O' + REST + "</p>"
    resolver = _resolver(first={"x": {"font-size": "3em"}})
    style, runs = _found(_book(tmp_path, body, resolver))["O" + REST]
    assert _drop(style) is None
    assert [(t, _fs(s)) for t, s in runs] == [("O", pytest.approx(3.0))]


@pytest.mark.unit
def test_a_paragraph_of_only_the_initial_is_not_dropped(tmp_path):
    body = FILLER + '<p class="x">Q</p>'
    style, _ = _found(_book(tmp_path, body, _resolver(first={"x": _first_letter()})))[
        "Q"
    ]
    assert _drop(style) is None


@pytest.mark.unit
def test_no_resolver_writes_no_drop_cap(tmp_path):
    body = FILLER + '<p><span class="dc">O</span>' + REST + "</p>"
    style, _ = _found(_book(tmp_path, body, None))["O" + REST]
    assert _drop(style) is None
