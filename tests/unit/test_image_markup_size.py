import os
import sys
import tempfile

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen import converter as _conv  # noqa: E402
from kfxgen._img_tokens import IMG_TOKEN_RE  # noqa: E402
from kfxgen.converter import extract_blocks_from_html  # noqa: E402
from kfxgen.kfxlib_minimal.ion import IS  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator, _parse_size_hint  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402

pytestmark = pytest.mark.unit


def _doc(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return etree.fromstring(xhtml.encode())


def _tokens(blocks):
    """(href, alt, size) of every image token, in document order."""
    return [m.groups() for b in blocks for m in IMG_TOKEN_RE.finditer(b["text"])]


# ── converter: what the markup asks for ─────────────────────────────────────


class TestImageSizeHint:
    def test_height_attribute_is_carried(self):
        # A page-scan EPUB's front matter: <img height="98%">.
        blocks = extract_blocks_from_html(
            _doc('<p class="image"><img alt="Image" height="98%" src="p.jpg"/></p>')
        )
        assert _tokens(blocks) == [("p.jpg", "Image", "h=98%")]

    def test_width_wins_over_height(self):
        # Both attributes given: Amazon emits the width alone.
        blocks = extract_blocks_from_html(
            _doc('<div><img src="p.jpg" width="390" height="625"/></div>')
        )
        assert _tokens(blocks) == [("p.jpg", "", "w=390")]

    def test_css_beats_the_attribute_and_max_is_ignored(self):
        def resolver(elem):
            if elem.tag.endswith("img"):
                return {"width": "50%", "height": None, "max-width": "100%"}
            return {}

        blocks = extract_blocks_from_html(
            _doc('<div><img src="p.jpg" height="98%"/></div>'), style_resolver=resolver
        )
        assert _tokens(blocks) == [("p.jpg", "", "w=50%")]

    def test_auto_is_not_a_size(self):
        # calibre's comic pages: .calibre2 { width: auto; height: auto }
        blocks = extract_blocks_from_html(
            _doc('<div><img src="p.jpg"/></div>'),
            style_resolver=lambda elem: {"width": "auto", "height": "auto"},
        )
        assert _tokens(blocks) == [("p.jpg", "", None)]

    def test_natural_size_has_no_hint(self):
        blocks = extract_blocks_from_html(_doc('<p><img src="p.jpg"/></p>'))
        assert _tokens(blocks) == [("p.jpg", "", None)]

    def test_two_field_token_still_matches(self):
        m = IMG_TOKEN_RE.fullmatch("\x00IMG\x01a.jpg\x01alt\x00")
        assert m.groups() == ("a.jpg", "alt", None)


# ── generator: the $157 the size becomes ────────────────────────────────────


def _jpeg(w, h):
    """A JPEG whose SOF0 the generator's dimension scan can read, padded past
    the 100-byte floor below which the generator treats bytes as not an image."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    dims = h.to_bytes(2, "big") + w.to_bytes(2, "big")
    sof0 = b"\xff\xc0\x00\x0b\x08" + dims + b"\x01\x01\x11\x00"
    comment = b"\xff\xfe\x00\x66" + b"\x00" * 100
    return b"\xff\xd8" + app0 + sof0 + comment + b"\xff\xd9"


def _build(text, images, cover=None):
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    try:
        kwargs = {
            "title": "T",
            "author": "A",
            "chapters": [{"title": "Pages", "text": text}],
            "output_path": path,
            "images": images,
        }
        if cover is not None:
            kwargs["cover_image"] = cover
        gen.generate_full_book(**kwargs)
        return load_fragments(path)
    finally:
        os.unlink(path)


def _token(href, size=None):
    return _conv._make_img_token(href, "", size)


def _style(frags, fid):
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    assert fid in styles, f"{fid} not among {sorted(styles)}"
    return styles[fid]


def _magnitude(style, key):
    return float(str(style[IS(key)][IS("$307")]))


class TestSizedImageStyles:
    IMAGES = {"images/p.jpg": _jpeg(390, 625)}

    def test_height_percent_becomes_57(self):
        frags = _build(_token("p.jpg", "h=98%"), self.IMAGES)
        s = _style(frags, "s_img_h0")
        assert _magnitude(s, "$57") == 98.0
        assert IS("$56") not in s
        assert IS("$65") in s  # max-width guard stays

    def test_css_width_percent_passes_through(self):
        s = _style(_build(_token("p.jpg", "w=50%"), self.IMAGES), "s_img_w0")
        assert _magnitude(s, "$56") == 50.0

    def test_width_length_is_divided_by_the_column(self):
        # width="200" -> 200 / 496 of the column, the same arithmetic as a
        # natural width (#145).
        s = _style(_build(_token("p.jpg", "w=200"), self.IMAGES), "s_img_w0")
        assert _magnitude(s, "$56") == round(200 / 496 * 100, 3)

    def test_em_width_is_sixteen_pixels_an_em(self):
        s = _style(_build(_token("p.jpg", "w=10em"), self.IMAGES), "s_img_w0")
        assert _magnitude(s, "$56") == round(160 / 496 * 100, 3)

    def test_unsized_image_is_still_sized_by_its_pixels(self):
        s = _style(_build(_token("p.jpg"), self.IMAGES), "s_img_w0")
        assert _magnitude(s, "$56") == round(390 / 496 * 100, 3)

    def test_one_style_per_distinct_size(self):
        sizes = ["h=98%", "h=98%", "w=50%"]
        frags = _build("\n\n".join(_token("p.jpg", s) for s in sizes), self.IMAGES)
        names = sorted(
            str(f.fid) for f in by_type(frags, "$157") if "img" in str(f.fid)
        )
        assert names == ["s_img_h0", "s_img_w0"]

    def test_hint_parser_edges(self):
        # A height in a length has no reference output; a width is capped
        # at the column.
        assert _parse_size_hint("h=200px", 496) is None
        assert _parse_size_hint("w=900px", 496) == ("w", 100.0)
        assert _parse_size_hint("w=0", 496) is None
        assert _parse_size_hint(None, 496) is None
        assert _parse_size_hint("w=12pt", 496) == ("w", round(16 / 496 * 100, 3))
