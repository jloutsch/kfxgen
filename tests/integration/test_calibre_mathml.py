"""MathML, through calibre (#235).

calibre's EPUB input and its stylesheet reach `<math>` before kfxgen does.
This converts the #235 probe's main shapes with calibre: an exponent, a
subscript, a fraction, a root, a prefixed `<m:math>`, a TeX annotation, an
equation in a table cell and a display equation straight in the body. Each
equation is drawn in its paragraph, as Kindle Previewer draws it: the
plugin's bundled ziamath is unpacked from its zip inside calibre and draws
it, and the display equation is centred. The paragraphs below show each
drawing as [its alt text], which is #401's text form when the equation has
no `alttext`.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
"""

import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'
XHTML = (
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en"><head>'
    "<title>{0}</title></head><body>{1}</body></html>"
)

#: (paragraph text kfxgen writes, its markup, [(start, length, "$370" or "$371")]).
CASES = [
    (
        "M1 r2",
        f"M1 <math {M}><msup><mi>r</mi><mn>2</mn></msup></math>",
        [(4, 1, "$370")],
    ),
    (
        "M2 xi",
        f"M2 <math {M}><msub><mi>x</mi><mi>i</mi></msub></math>",
        [(4, 1, "$371")],
    ),
    (
        "M3 (a + b)/c",
        f"M3 <math {M}><mfrac><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow><mi>c</mi></mfrac></math>",
        [],
    ),
    (
        "M4 √(x + 1)",
        f"M4 <math {M}><msqrt><mi>x</mi><mo>+</mo><mn>1</mn></msqrt></math>",
        [],
    ),
    (
        "M5 y3",
        '<span>M5 </span><m:math xmlns:m="http://www.w3.org/1998/Math/MathML">'
        "<m:msup><m:mi>y</m:mi><m:mn>3</m:mn></m:msup></m:math>",
        [(4, 1, "$370")],
    ),
    (
        "M6 z2",
        f"M6 <math {M}><semantics><msup><mi>z</mi><mn>2</mn></msup>"
        '<annotation encoding="application/x-tex">z^2</annotation></semantics></math>',
        [(4, 1, "$370")],
    ),
]
CASES += [
    (
        "M7 614C",
        f"M7 <math {M}><mmultiscripts><mi>C</mi><mprescripts/><mn>6</mn><mn>14</mn>"
        "</mmultiscripts></math>",
        [(3, 1, "$371"), (4, 2, "$370")],
    ),
    ('M8 "abc"', f"M8 <math {M}><ms>abc</ms></math>", []),
]
#: A chapter whose heading holds an equation, under a TOC label written from
#: the heading's plain text (#402).
FLAT_LABEL = "The ab=1 rule"
FRAC_HEADING_TEXT = "The a/b = 1 rule"
FRAC_HEADING = (
    f"<h1>The <math {M}><mfrac><mi>a</mi><mi>b</mi></mfrac><mo>=</mo><mn>1</mn></math>"
    " rule</h1><p>After the heading.</p>"
)
DISPLAY = (
    f'<math {M} display="block"><mi>x</mi><mo>=</mo><mfrac><mrow><mo>-</mo><mi>b</mi>'
    "<mo>&#xB1;</mo><msqrt><msup><mi>b</mi><mn>2</mn></msup><mo>-</mo><mn>4</mn><mi>a</mi>"
    "<mi>c</mi></msqrt></mrow><mrow><mn>2</mn><mi>a</mi></mrow></mfrac></math>"
)
DISPLAY_TEXT = "x = (−b ± √(b2 − 4ac))/(2a)"


def _build(path):
    body = "<h1>Maths</h1>" + "".join(f"<p>{html}</p>" for _, html, _ in CASES)
    body += f"<p>Display:</p>{DISPLAY}<p>After.</p>"
    body += f"<table><tr><td><math {M}><msup><mi>e</mi><mn>2</mn></msup></math></td><td>x</td></tr></table>"
    files = {
        "nav.xhtml": XHTML.format(
            "Nav",
            '<nav epub:type="toc"><ol><li><a href="c1.xhtml">Maths</a></li>'
            f'<li><a href="c2.xhtml">{FLAT_LABEL}</a></li></ol></nav>',
        ),
        "c1.xhtml": XHTML.format("Maths", body),
        "c2.xhtml": XHTML.format("Heading", FRAC_HEADING),
    }
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        '<head><meta name="dtb:uid" content="mathml"/></head><docTitle><text>MathML</text></docTitle>'
        '<navMap><navPoint id="n1" playOrder="1"><navLabel><text>Maths</text></navLabel>'
        '<content src="c1.xhtml"/></navPoint><navPoint id="n2" playOrder="2"><navLabel>'
        f'<text>{FLAT_LABEL}</text></navLabel><content src="c2.xhtml"/></navPoint></navMap></ncx>'
    )
    items = (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml" properties="mathml"/>'
        '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml" properties="mathml"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">mathml'
        "</dc:identifier><dc:title>MathML</dc:title><dc:language>en</dc:language>"
        '<meta property="dcterms:modified">2026-10-20T00:00:00Z</meta></metadata>'
        f'<manifest>{items}</manifest><spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine></package>'
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
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        for name, data in files.items():
            z.writestr(name, data)


def _bracketed(text):
    """'M1 r2' as kfxgen now prints it: 'M1 [r2]', the equation drawn."""
    label, alt = text.split(" ", 1)
    return f"{label} [{alt}]"


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """({paragraph text, each drawn equation as [its alt text]: (its style,
    [path count per equation])}, the book's fragments), and calibre's
    version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "mathml.epub", tmp / "mathml.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}

    def note(ref):
        return str(texts[str(ref["name"])][ref["$403"]])

    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not hasattr(e, "get") or str(e.get("$159")) != "$269":
                continue
            if e.get("$145"):
                parts, counts = [note(e["$145"])], []
            elif isinstance(e.get("$146"), list):
                parts, counts = [], []
                for x in e["$146"]:
                    if isinstance(x, str):
                        parts.append(str(x))
                        continue
                    alt = next(
                        note(n["$145"]) for n in x["$683"] if str(n["$687"]) == "$584"
                    )
                    ((shape,),) = [inner["$146"] for inner in x["$146"]]
                    parts.append(f"[{alt}]")
                    counts.append(len(shape["$250"]))
            else:
                continue
            out["".join(parts)] = (styles.get(str(e.get("$157")), {}), counts)
    return (out, frags), version


@pytest.mark.parametrize("text", [t for t, _, _ in CASES])
def test_an_equation_is_drawn_in_its_paragraph(converted, text):
    (paragraphs, _frags), version = converted
    expected = _bracketed(text)
    assert expected in paragraphs, f"calibre {version}: {list(paragraphs)}"
    ((paths,),) = [paragraphs[expected][1]]
    assert paths > 0, f"calibre {version}"


def test_a_display_equation_is_its_own_centred_paragraph(converted):
    (paragraphs, _frags), version = converted
    expected = f"[{DISPLAY_TEXT}]"
    assert expected in paragraphs, f"calibre {version}: {list(paragraphs)}"
    style, counts = paragraphs[expected]
    assert str(style.get("$34")) == "$320", f"calibre {version}: {style}"
    assert len(counts) == 1 and counts[0] >= 16, f"calibre {version}"


def test_an_equation_in_a_table_cell(converted):
    (paragraphs, _frags), version = converted
    assert any("[e2]" in t for t in paragraphs), (
        f"calibre {version}: {list(paragraphs)}"
    )


def test_a_heading_with_maths_prints_once_under_a_flattened_label(converted):
    (paragraphs, _frags), version = converted
    assert FLAT_LABEL not in paragraphs, f"calibre {version}: {list(paragraphs)}"
    assert f"The [{FRAC_HEADING_TEXT[4:-5]}] rule" in paragraphs, (
        f"calibre {version}: {list(paragraphs)}"
    )


def test_the_book_declares_its_drawings(converted):
    (_paragraphs, frags), version = converted
    assert len(by_type(frags, "$692")) == 1, f"calibre {version}"
    (features,) = by_type(frags, "$585")
    assert "yj_mathml" in [str(f["$492"]) for f in val(features)["$590"]]
