"""MathML, through calibre (#235).

calibre's EPUB input and its stylesheet reach `<math>` before kfxgen does.
This converts the #235 probe's main shapes with calibre: an exponent, a
subscript, a fraction, a root, a prefixed `<m:math>`, a TeX annotation, an
equation in a table cell and a display equation straight in the body. Each
paragraph reads as text, with its exponents raised (`$44: $370`) and its
subscripts lowered (`$371`), and the display equation is centred.

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


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{paragraph text: (its style, [(start, length, shift)])}, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "mathml.epub", tmp / "mathml.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not e.get("$145"):
                continue
            text = str(texts[str(e["$145"]["name"])][e["$145"]["$403"]])
            shifts = []
            for ev in e.get("$142") or []:
                shift = styles.get(str(ev.get("$157")), {}).get("$44")
                if shift is not None:
                    shifts.append((ev["$143"], ev["$144"], str(shift)))
            out[text] = (styles.get(str(e.get("$157")), {}), shifts)
    return out, version


@pytest.mark.parametrize(("text", "shifts"), [(t, s) for t, _, s in CASES])
def test_an_equation_reads_as_text_with_its_scripts_shifted(converted, text, shifts):
    paragraphs, version = converted
    assert text in paragraphs, f"calibre {version}: {list(paragraphs)}"
    assert paragraphs[text][1] == shifts, f"calibre {version}"


def test_a_display_equation_is_its_own_centred_paragraph(converted):
    paragraphs, version = converted
    assert DISPLAY_TEXT in paragraphs, f"calibre {version}: {list(paragraphs)}"
    style, shifts = paragraphs[DISPLAY_TEXT]
    assert str(style.get("$34")) == "$320", f"calibre {version}: {style}"
    assert shifts == [(DISPLAY_TEXT.index("2"), 1, "$370")], f"calibre {version}"


def test_an_equation_in_a_table_cell(converted):
    paragraphs, version = converted
    assert paragraphs.get("e2 x", (None, None))[1] == [(1, 1, "$370")], (
        f"calibre {version}: {list(paragraphs)}"
    )


def test_a_heading_with_maths_prints_once_under_a_flattened_label(converted):
    paragraphs, version = converted
    assert FLAT_LABEL not in paragraphs, f"calibre {version}: {list(paragraphs)}"
    assert FRAC_HEADING_TEXT in paragraphs, f"calibre {version}: {list(paragraphs)}"
