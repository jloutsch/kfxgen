"""Floated images through calibre's real Stylizer (#243).

calibre folds an <img>'s width attribute into its CSS and reports `float`
as declared; the unit tests' fake style can only assume both. This converts
a book of floated images with calibre and checks each picture's `$140`.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. A linked picture is not floated: the
Voyage drops a floated picture's link (#378 device check). CI has no calibre,
so this is local-only. Last run against calibre 9.16.0, where every case passes.
"""

import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._helpers import decodable_jpeg_of  # noqa: E402
from tests._kfx_introspect import (  # noqa: E402
    by_type,
    iter_entries,
    load_fragments,
    val,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """img.fl { float: left; width: 30%; } img.fr { float: right; width: 30%; }
p.pfl { float: left; width: 25%; } div.figl { float: left; width: 40%; }
div.figl img { width: 100%; } img.w30 { width: 30%; }"""

WORDS = "words that wrap beside the floated picture. " * 4

#: (alt, markup): each picture's alt names its case.
CASES = [
    ("left", f'<p id="lt"><img class="fl" src="i.jpg" alt="left"/>Left {WORDS}</p>'),
    ("right", f'<p><img class="fr" src="i.jpg" alt="right"/>Right {WORDS}</p>'),
    ("ownline", f'<div><img class="fl" src="i.jpg" alt="ownline"/></div><p>Own {WORDS}</p>'),
    ("wrapper", f'<p class="pfl"><img src="i.jpg" alt="wrapper"/></p><p>Wrap {WORDS}</p>'),
    ("attr", f'<p><img class="fl" src="i.jpg" alt="attr" width="200"/>Attr {WORDS}</p>'),
    ("linked", f'<p><a href="c.xhtml#lt"><img class="fl" src="i.jpg" alt="linked"/></a>Linked {WORDS}</p>'),
    ("plain", f'<p><img class="w30" src="i.jpg" alt="plain"/>Plain {WORDS}</p>'),
    (
        "figure",
        '<div class="figl"><img src="i.jpg" alt="figure"/><p>A caption.</p></div>'
        f"<p>Figure {WORDS}</p>",
    ),
]  # fmt: skip


def _build(path, cases=None, css=None):
    """An EPUB of `cases` (default: this file's) styled by `css`."""
    body = "".join(html for _, html in (cases or CASES))
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>F</title><link rel="stylesheet" type="text/css" href="s.css"/>'
        f"</head><body>{body}</body></html>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">f'
            "</dc:identifier><dc:title>Float cases</dc:title><dc:language>en"
            '</dc:language></metadata><manifest><item id="c" href="c.xhtml" '
            'media-type="application/xhtml+xml"/><item id="s" href="s.css" '
            'media-type="text/css"/><item id="i" href="i.jpg" media-type="image/jpeg"/>'
            '</manifest><spine><itemref idref="c"/></spine></package>',
        )
        z.writestr("c.xhtml", xhtml)
        z.writestr("s.css", css or CSS)
        z.writestr("i.jpg", decodable_jpeg_of(600, 400))


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{alt: (the picture's style, the text entry after it)}, and calibre's
    version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "floats.epub", tmp / "floats.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    order = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if str(e.get("$159")) == "$271":
                order.append(
                    ("image", str(e.get("$584")), styles.get(str(e.get("$157")), {}))
                )
            elif e.get("$145"):
                ref = e["$145"]
                order.append(("text", str(texts[str(ref["name"])][ref["$403"]]), None))
    out = {}
    for i, (kind, alt, style) in enumerate(order):
        if kind == "image":
            nxt = next((t for k, t, _ in order[i + 1 :] if k == "text"), None)
            out[alt] = (style, nxt)
    return out, version


@pytest.mark.parametrize(
    ("alt", "side", "width", "next_text"),
    [
        ("left", "$59", 30.0, "Left"),
        ("right", "$61", 30.0, "Right"),
        ("ownline", "$59", 30.0, "Own"),
        ("wrapper", "$59", 25.0, "Wrap"),
        ("attr", "$59", None, "Attr"),
        ("linked", None, 30.0, "Linked"),
        ("plain", None, 30.0, "Plain"),
        ("figure", None, None, "A caption."),
    ],
)
def test_a_floated_picture_through_the_real_stylizer(
    converted, alt, side, width, next_text
):
    by_alt, version = converted
    style, nxt = by_alt[alt]
    assert style.get("$140") == side, f"calibre {version}"
    assert ("$580" in style) == (side is None), f"calibre {version}"
    if width is not None:
        assert float(style["$56"]["$307"]) == width, f"calibre {version}"
    assert nxt.startswith(next_text), f"calibre {version}: {nxt[:30]}"
