"""A floated image floats, and the text beside it wraps (#243).

kfxgen already wrote a floated image as its own entry before its paragraph,
at its own width, but not floated: the text began below it. Kindle Previewer
4 (kpr4, decoded) writes the float as `$140` on the image's style, `$59`
left and `$61` right, with no centring; the text after it wraps. An image
alone on its line (`<div><img class="fl"/></div><p>`) is a top-level
floated image followed by the paragraph, the shape kfxgen writes for every
floated image. A wrapper holding only the image (`<p class="fl"><img/></p>`)
floats at its own width, with the image filling it. The Voyage (5.13.6)
wraps text beside Previewer's floated image.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen._img_tokens import IMG_TOKEN_RE
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

XHTML = "http://www.w3.org/1999/xhtml"
WORDS = "Words that wrap beside the floated picture. " * 6


class _Style:
    def __init__(self, own):
        self._own = own

    def get(self, k, default=None):
        return self._own.get(k, default)

    def __getitem__(self, k):
        return self._own.get(k)


class _Stylizer:
    def __init__(self, css):
        self.css = css

    def style(self, elem):
        return _Style(self.css.get(elem.get("class") or "", {}))


def _resolver(css):
    return _conv._build_style_resolver(
        None, None, None, stylizer_factory=lambda oeb, item: _Stylizer(css)
    )


CSS = {
    "fl": {"float": "left", "width": "30%"},
    "fr": {"float": "right", "width": "30%"},
    "fln": {"float": "left"},
    "pfl": {"float": "left", "width": "30%"},
    "full": {"width": "100%"},
}


def _blocks(body, css=CSS):
    xhtml = f'<html xmlns="{XHTML}"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=_resolver(css),
        native_tables=True,
        base_href="c.xhtml",
    )


def _tokens(blocks):
    """(size, float) of each image token in the blocks, in order."""
    return [
        (m.group(3) or None, m.group(4) or None)
        for b in blocks
        for m in IMG_TOKEN_RE.finditer(b.get("text") or "")
    ]


def _entries(body, css=CSS, tmp_path=None):
    """Each top-level storyline entry of the book's one chapter, in order:
    ("image", its style) or ("text", its text)."""
    blocks = _blocks(body, css)
    text = "\n\n".join(b["text"] for b in blocks)
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "One", "text": text, "blocks": blocks}],
        output_path=str(out),
        images={"i.jpg": jpeg_of(600, 400)},
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if str(e.get("$159")) == "$271":
                found.append(("image", styles.get(str(e.get("$157")), {})))
            elif e.get("$145"):
                ref = e["$145"]
                found.append(("text", str(texts[str(ref["name"])][ref["$403"]])))
    return found


# --- The converter: the float travels on the image token -------------------


@pytest.mark.unit
def test_an_image_floated_left_in_a_paragraph_is_a_left_float():
    blocks = _blocks(f'<p><img class="fl" src="i.jpg" alt="a"/>{WORDS}</p>')
    assert _tokens(blocks) == [("w=30%", "left")]


@pytest.mark.unit
def test_an_image_floated_right_is_a_right_float():
    blocks = _blocks(f'<p><img class="fr" src="i.jpg" alt="a"/>{WORDS}</p>')
    assert _tokens(blocks) == [("w=30%", "right")]


@pytest.mark.unit
def test_an_image_not_floated_has_the_token_it_had():
    blocks = _blocks(f'<p><img class="full" src="i.jpg" alt="a"/>{WORDS}</p>')
    assert _tokens(blocks) == [("w=100%", None)]
    assert blocks[0]["text"].startswith("\x00IMG\x01i.jpg\x01a\x01w=100%\x00")


@pytest.mark.unit
def test_a_floated_image_with_no_size_is_floated():
    blocks = _blocks(f'<p><img class="fln" src="i.jpg" alt="a"/>{WORDS}</p>')
    assert _tokens(blocks) == [(None, "left")]


@pytest.mark.unit
def test_an_image_alone_in_its_div_is_floated():
    blocks = _blocks(f'<div><img class="fl" src="i.jpg" alt="a"/></div><p>{WORDS}</p>')
    assert _tokens(blocks) == [("w=30%", "left")]


@pytest.mark.unit
def test_a_floated_wrapper_of_only_an_image_floats_it_at_its_width():
    """Previewer: `<p class="pfl"><img/></p>` floats at the wrapper's 30%."""
    blocks = _blocks(f'<p class="pfl"><img src="i.jpg" alt="a"/></p><p>{WORDS}</p>')
    assert _tokens(blocks) == [("w=30%", "left")]


@pytest.mark.unit
def test_a_floated_span_of_only_an_image_floats_it():
    css = dict(CSS, sfl={"float": "left", "width": "30%"})
    body = f'<p><span class="sfl"><img src="i.jpg" alt="a"/></span>{WORDS}</p>'
    assert _tokens(_blocks(body, css)) == [("w=30%", "left")]


@pytest.mark.unit
def test_a_floated_wrapper_with_text_does_not_float_its_image():
    body = f'<p class="pfl"><img src="i.jpg" alt="a"/>A caption.</p><p>{WORDS}</p>'
    assert _tokens(_blocks(body)) == [(None, None)]


# --- The generator: $140 on the image's style ------------------------------


def _image_style(entries):
    (style,) = [s for kind, s in entries if kind == "image"]
    return style


@pytest.mark.unit
@pytest.mark.parametrize(("cls", "side"), [("fl", "$59"), ("fr", "$61")])
def test_a_floated_image_is_written_floated_before_its_text(tmp_path, cls, side):
    body = f'<p><img class="{cls}" src="i.jpg" alt="a"/>{WORDS}</p>'
    entries = _entries(body, tmp_path=tmp_path)
    kinds = [k for k, _ in entries]
    i = kinds.index("image")
    assert entries[i + 1] == ("text", WORDS.strip())
    style = entries[i][1]
    assert style["$140"] == side
    assert float(style["$56"]["$307"]) == 30.0
    assert "$580" not in style


@pytest.mark.unit
def test_an_image_not_floated_keeps_its_style(tmp_path):
    body = f'<p><img class="full" src="i.jpg" alt="a"/>{WORDS}</p>'
    style = _image_style(_entries(body, tmp_path=tmp_path))
    assert "$140" not in style
    assert style["$580"] == "$320"


@pytest.mark.unit
def test_a_floated_and_a_plain_image_of_one_width_have_two_styles(tmp_path):
    css = dict(CSS, w30={"width": "30%"})
    body = (
        f'<p><img class="fl" src="i.jpg" alt="a"/>{WORDS}</p>'
        f'<p><img class="w30" src="i.jpg" alt="b"/>{WORDS}</p>'
    )
    styles = [s for k, s in _entries(body, css, tmp_path) if k == "image"]
    assert [s.get("$140") for s in styles] == ["$59", None]
    assert [float(s["$56"]["$307"]) for s in styles] == [30.0, 30.0]


@pytest.mark.unit
def test_a_floated_image_with_no_size_is_floated_at_its_pixel_width(tmp_path):
    body = f'<p><img class="fln" src="i.jpg" alt="a"/>{WORDS}</p>'
    style = _image_style(_entries(body, tmp_path=tmp_path))
    assert style["$140"] == "$59"


@pytest.mark.unit
def test_a_book_with_no_float_writes_the_styles_it_did(tmp_path):
    """Same names and values: a floated image's style is allocated apart."""
    body = f'<p><img class="full" src="i.jpg" alt="a"/>{WORDS}</p>'
    blocks = _blocks(body)
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "One", "text": blocks[0]["text"], "blocks": blocks}],
        output_path=str(out),
        images={"i.jpg": jpeg_of(600, 400)},
    )
    names = {str(f.fid) for f in by_type(load_fragments(out), "$157")}
    assert "s_img_w0" in names
    assert not any("_f" in n for n in names if n.startswith("s_img"))


@pytest.mark.unit
def test_a_floated_wrapper_of_two_images_floats_neither():
    """Previewer floats the wrapper as one container; giving each image the
    wrapper's width would draw them wrong, so they stay as they were."""
    body = (
        '<p class="pfl"><img src="i.jpg" alt="a"/><img src="i.jpg" alt="b"/></p>'
        f"<p>{WORDS}</p>"
    )
    assert [side for _size, side in _tokens(_blocks(body))] == [None, None]


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        f'<img class="fl" src="i.jpg" alt="a"/><p>{WORDS}</p>',
        f'<div><img class="fl" src="i.jpg" alt="a"/><p>{WORDS}</p></div>',
    ],
    ids=["in-body", "beside-a-paragraph"],
)
def test_an_image_among_blocks_is_floated(body):
    """Previewer's bare `<img class="fl">` before a paragraph: the image is a
    block of its own, through the container walk, not a paragraph's."""
    assert _tokens(_blocks(body)) == [("w=30%", "left")]


@pytest.mark.unit
def test_a_linked_floated_image_is_not_floated_and_keeps_its_link(tmp_path):
    """The Voyage (5.13.6) drops the link of a floated picture: the tap turns
    the page, in kfxgen's container shape and in Previewer's `$179` on the
    picture alike (#378 device check). The link matters more than the wrap,
    so the picture is written unfloated in its link container, as on main."""
    body = (
        f'<p><a href="c.xhtml#t"><img class="fl" src="i.jpg" alt="a"/></a>{WORDS}</p>'
        '<p id="t">Target.</p>'
    )
    blocks = _blocks(body)
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "One",
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": blocks,
                "file": "c.xhtml",
            }
        ],
        output_path=str(out),
        images={"i.jpg": jpeg_of(600, 400)},
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    entries = [
        e for st in by_type(frags, "$259") for e in iter_entries(val(st)["$146"])
    ]
    (picture,) = [e for e in entries if str(e.get("$159")) == "$271"]
    assert "$140" not in styles[str(picture["$157"])]
    (wrapper,) = [e for e in entries if any(k is picture for k in e.get("$146") or [])]
    assert [str(sp["$179"]) for sp in wrapper["$142"]] == ["body_anchor_0"]


@pytest.mark.unit
def test_a_full_width_image_in_a_floated_wrapper_takes_the_wrappers_width():
    """`p.fl { width: 30% } p.fl img { width: 100% }`: the image fills the
    wrapper, so it floats at 30%, not 100% (#378 review)."""
    body = f'<p class="pfl"><img class="full" src="i.jpg" alt="a"/></p><p>{WORDS}</p>'
    assert _tokens(_blocks(body)) == [("w=30%", "left")]
