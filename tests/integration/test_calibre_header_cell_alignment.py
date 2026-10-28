"""Table cell alignment as Kindle Previewer 4.0.3 writes it (#589, probed
2026-10-27): a header cell is centred unless it sets its own alignment; a
data cell takes alignment set on itself, its row or its table, but not from
outside the table, such as a `body { text-align: justify }`. kfxgen took any
inherited alignment, so a whole book's header cells came out justified.

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

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

ROWS = (
    "<tr><th>{p}H1 plain</th><td>{p}D1 cell</td></tr>"
    "<tr><th style='text-align:left'>{p}H2 css left</th><td>{p}D2 cell</td></tr>"
    "<tr><th align='right'>{p}H3 align right</th><td>{p}D3 cell</td></tr>"
    "<tr style='text-align:right'><th>{p}H4 row right</th><td>{p}D4 cell</td></tr>"
    "<tr><td>{p}D5 td first</td><th>{p}H5 second</th></tr>"
)
PAGES = {
    "a.xhtml": ("", f"<table>{ROWS.format(p='A ')}</table>"),
    # The same, in a page whose body is justified.
    "b.xhtml": (
        "body{text-align:justify}",
        f"<table>{ROWS.format(p='B ')}"
        "<tr><th><p>B H6 para in th</p></th><td>B D6 cell</td></tr></table>",
    ),
    "c.xhtml": (
        "",
        "<table><tr style='text-align:justify'><th>K1 th row justify</th>"
        "<td>K1 td row justify</td></tr></table>"
        "<table style='text-align:right'><tr><th>K2 th table right</th>"
        "<td>K2 td table right</td></tr></table>"
        "<div style='text-align:right'><table><tr><th>K3 th div right</th>"
        "<td>K3 td div right</td></tr></table></div>"
        "<table><tr><td style='text-align:justify'>K4 td own justify</td>"
        "<th style='text-align:justify'>K4 th own justify</th></tr></table>",
    ),
}

#: Previewer's `$34` for each cell, by the cell's first words.
CENTRE, LEFT, RIGHT, JUSTIFY = "$320", "$59", "$61", "$321"
PREVIEWER = {
    **{
        f"{p} {k}": v
        for p in ("A", "B")
        for k, v in {
            "H1 plain": CENTRE,
            "D1 cell": LEFT,
            "H2 css left": LEFT,
            "D2 cell": LEFT,
            "H3 align right": RIGHT,
            "D3 cell": LEFT,
            "H4 row right": CENTRE,
            "D4 cell": RIGHT,
            "D5 td first": LEFT,
            "H5 second": CENTRE,
        }.items()
    },
    "B H6 para in th": CENTRE,
    "B D6 cell": LEFT,
    "K1 th row justify": CENTRE,
    "K1 td row justify": JUSTIFY,
    "K2 th table right": CENTRE,
    "K2 td table right": RIGHT,
    "K3 th div right": CENTRE,
    "K3 td div right": LEFT,
    "K4 td own justify": JUSTIFY,
    "K4 th own justify": JUSTIFY,
}


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
        items = "".join(
            f'<item id="f{n}" href="{f}" media-type="application/xhtml+xml"/>'
            for n, f in enumerate(PAGES)
        )
        refs = "".join(f'<itemref idref="f{n}"/>' for n in range(len(PAGES)))
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">h589</dc:identifier><dc:title>H</dc:title>'
            f"<dc:language>en</dc:language></metadata><manifest>{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'</manifest><spine toc="ncx">{refs}</spine></package>',
        )
        points = "".join(
            f'<navPoint id="n{n}" playOrder="{n + 1}"><navLabel><text>Page {n}'
            f'</text></navLabel><content src="{f}"/></navPoint>'
            for n, f in enumerate(PAGES)
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>H</text></docTitle>'
            f"<navMap>{points}</navMap></ncx>",
        )
        for name, (css, body) in PAGES.items():
            z.writestr(
                name,
                '<?xml version="1.0" encoding="utf-8"?><html '
                'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title>'
                f"<style>{css}</style></head><body><h1>Page</h1>{body}"
                "<p>After.</p></body></html>",
            )


def test_cells_are_aligned_as_previewer_aligns_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "h589.epub", tmp / "h589.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                if text in PREVIEWER:
                    got[text] = str(styles[str(e["$157"])].get("$34"))
    wrong = {
        t: (got.get(t), want) for t, want in PREVIEWER.items() if got.get(t) != want
    }
    assert not wrong, f"calibre {version}: (kfxgen, Previewer) {wrong}"
