"""A header cell set to a normal weight is not bold (#597), as Kindle
Previewer 4.0.3 writes it (probed 2026-10-28, a page with a DOCTYPE): a
`<th>` is bold unless the cell itself sets `font-weight: normal`; a normal
weight on its row or a `<div>` round the table doesn't reach it, and its
paragraphs keep their own. kfxgen made every header cell bold.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BODY = (
    "<h1>Cells</h1><table>"
    "<tr><th>H1 plain</th><td>D1 plain</td></tr>"
    "<tr><th style='font-weight: bold'>H2 bold</th><td>D2</td></tr>"
    "<tr><th style='font-weight: bolder'>H3 bolder</th><td>D3</td></tr>"
    "<tr><th style='font-weight: normal'>H4 normal</th><td>D4</td></tr>"
    "<tr><th style='font-weight: 400'>H13 400</th><td>D13</td></tr>"
    "<tr><th style='font-weight: lighter'>H14 lighter</th><td>D14</td></tr>"
    "<tr><th><p>H5 p</p></th><td>D5</td></tr>"
    "<tr style='font-weight: normal'><th>H8 normal row</th><td>D8</td></tr>"
    "<tr><th style='font-weight: normal'><p>H9 first</p><p>H9 second</p></th>"
    "<td>D9</td></tr>"
    "<tr><th style='font-weight: normal'><p style='font-weight: bold'>H10 bold"
    "</p><p>H10 plain</p></th><td>D10</td></tr>"
    "<tr><th style='font-weight: normal'><p>H12 only p</p></th><td>D12</td></tr>"
    "</table>"
    "<div style='font-weight: normal'><table><tr><th>H11 normal div</th>"
    "<td>D11</td></tr></table></div><p>After.</p>"
)

#: Each cell's weight in Previewer's file: its entry's `$13`, else the
#: nearest container's. Previewer writes a plain header cell's as `$362`
#: (#597's other half), kfxgen as `$361`; both are bold here.
PREVIEWER = {
    "H1 plain": "bold",
    "D1 plain": "normal",
    "H2 bold": "bold",
    "H3 bolder": "bold",
    "H4 normal": "normal",
    "H13 400": "normal",
    "H14 lighter": "normal",  # Previewer: $356, weight 200
    "H5 p": "bold",
    "H8 normal row": "bold",
    "H9 first": "normal",
    "H9 second": "normal",
    "H10 bold": "bold",
    "H10 plain": "normal",
    "H11 normal div": "bold",
}
WEIGHT = {"$361": "bold", "$362": "bold"}


def _epub(path):
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
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">c597</dc:identifier><dc:title>C597</dc:title>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>C</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>Cells</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html PUBLIC '
            '"-//W3C//DTD XHTML 1.1//EN" '
            '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            f"<body>{BODY}</body></html>",
        )


def test_a_header_cell_set_to_normal_weight_is_not_bold(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "c597.epub", tmp / "c597.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    found = {}

    def weight(e):
        return styles.get(str(e.get("$157")), {}).get("$13")

    def walk(nodes, up):
        for e in nodes or ():
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            if e.get("$145"):
                t = str(text[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                w = next((x for x in (weight(e), *reversed(up)) if x), None)
                found[t] = WEIGHT.get(str(w), "normal")
            walk(e.get("$146"), [*up, weight(e)])

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], [])
    got = {t: found.get(t) for t in PREVIEWER}
    assert got == PREVIEWER, f"calibre {version}: {got}"
    # A header cell's only paragraph, set normal on the cell: normal too.
    assert found.get("H12 only p") == "normal", f"calibre {version}: {found}"
