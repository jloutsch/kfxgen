"""Header cell alignment as Kindle Previewer 4.0.3 writes it (#589, probed
2026-10-27, pages with a DOCTYPE): a header cell is centred unless it sets
its own alignment, whatever its row, table or page sets. kfxgen took any
inherited alignment, so a justified book's header cells came out justified.
Data cells inherit as usual, as kfxgen already had them.

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
    "a.xhtml": (
        "",
        f"<table>{ROWS.format(p='A ')}"
        # QA on #600: a header cell's own paragraph in a page that sets no
        # alignment.
        "<tr><th><p style='text-align:right'>A H7 own para</p></th>"
        "<td>A D7 cell</td></tr>"
        "<tr><th><p style='text-align:right'>A H8 own right</p><p>A H8 second</p>"
        "</th><td>A D8 cell</td></tr></table>",
    ),
    # The same, in a page whose body is justified.
    "b.xhtml": (
        "body{text-align:justify}",
        f"<table>{ROWS.format(p='B ')}"
        "<tr><th><p>B H6 para in th</p></th><td>B D6 cell</td></tr>"
        "<tr><td><p style='text-align:right'>B P7 own right</p><p>B P7 second</p></td>"
        "<td>B D7 cell</td></tr>"
        "<tr><th><p style='text-align:right'>B H8 own para right</p></th>"
        "<td>B D8 cell</td></tr>"
        "<tr><th><p style='text-align:right'>B H9 own right</p><p>B H9 second</p></th>"
        "<td>B D9 cell</td></tr></table>",
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
        "<th style='text-align:justify'>K4 th own justify</th></tr></table>"
        # A header cell set to its row's alignment keeps it (#601).
        "<table><tr style='text-align:right'><th style='text-align:right'>"
        "K5 th own right in right row</th><td>K5 td</td></tr></table>"
        "<table><tr style='text-align:justify'><th style='text-align:justify'>"
        "K6 th own justify in justify row</th><td>K6 td</td></tr></table>",
    ),
    # Keywords on a header cell (#616, QA's Previewer probes on #615).
    "e.xhtml": (
        "",
        "<table><tr style='text-align:right'><th style='text-align:inherit'>"
        "M1 th inherit in right row</th><td>M1 td</td></tr>"
        "<tr><th style='text-align:inherit'>M2 th inherit in plain row</th>"
        "<td>M2 td</td></tr>"
        "<tr><th style='text-align:initial'>M3 th initial</th><td>M3 td</td></tr>"
        "<tr><th style='text-align:start'>M4 th start</th>"
        "<td style='text-align:start'>M4 td start</td></tr>"
        "<tr><th style='text-align:unset'>M5 th unset</th><td>M5 td</td></tr></table>",
    ),
    # The same, set by class (#601).
    "d.xhtml": (
        "tr.r{text-align:right} th.r{text-align:right} th.c{text-align:center}",
        "<table><tr class='r'><th class='r'>L1 th class right in right row</th>"
        "<td>L1 td</td></tr>"
        "<tr class='r'><th class='c'>L2 th class centre in right row</th>"
        "<td>L2 td</td></tr>"
        "<tr class='r'><th>L3 th plain in right row</th><td>L3 td</td></tr></table>",
    ),
}

#: Previewer's `$34` for each cell, by the cell's first words, from its file
#: for these pages with a DOCTYPE (#589, probed 2026-10-27). A header cell is
#: centred unless it sets its own alignment; a data cell inherits as usual.
#: A data cell nothing aligns has no `$34`, in kfxgen's file too since #599.
CENTRE, LEFT, RIGHT, JUSTIFY, NONE = "$320", "$59", "$61", "$321", "None"
PREVIEWER = {
    "A H1 plain": CENTRE,
    "A D1 cell": NONE,
    "A D2 cell": NONE,
    "A D3 cell": NONE,
    "A D5 td first": NONE,
    "A D7 cell": NONE,
    "A D8 cell": NONE,
    "A H2 css left": LEFT,
    "A H3 align right": RIGHT,
    "A H4 row right": CENTRE,
    "A D4 cell": RIGHT,
    "A H5 second": CENTRE,
    "A H7 own para": RIGHT,
    "A H8 own right": RIGHT,
    "A H8 second": CENTRE,
    "B H1 plain": CENTRE,
    "B D1 cell": JUSTIFY,
    "B H2 css left": LEFT,
    "B D2 cell": JUSTIFY,
    "B H3 align right": RIGHT,
    "B D3 cell": JUSTIFY,
    "B H4 row right": CENTRE,
    "B D4 cell": RIGHT,
    "B D5 td first": JUSTIFY,
    "B H5 second": CENTRE,
    "B H6 para in th": CENTRE,
    "B D6 cell": JUSTIFY,
    # A paragraph's own alignment in a cell of two (#532).
    "B P7 own right": RIGHT,
    "B P7 second": JUSTIFY,
    "B D7 cell": JUSTIFY,
    # A header cell's paragraph with its own alignment keeps it.
    "B H8 own para right": RIGHT,
    "B D8 cell": JUSTIFY,
    # And in a header cell of two paragraphs, the other centred.
    "B H9 own right": RIGHT,
    "B H9 second": CENTRE,
    "B D9 cell": JUSTIFY,
    "K1 th row justify": CENTRE,
    "K1 td row justify": JUSTIFY,
    "K2 th table right": CENTRE,
    "K2 td table right": RIGHT,
    "K3 th div right": CENTRE,
    "K3 td div right": RIGHT,
    "K4 td own justify": JUSTIFY,
    "K4 th own justify": JUSTIFY,
    # Keywords (#616): inherit is the parent's, initial left, start and
    # unset centre (QA's probes on #615).
    "M1 th inherit in right row": RIGHT,
    "M2 th inherit in plain row": LEFT,
    "M3 th initial": LEFT,
    "M4 th start": CENTRE,
    "M4 td start": LEFT,
    "M5 th unset": CENTRE,
    # Its own alignment, equal to its row's, is kept (#601, probed 2026-10-28).
    "K5 th own right in right row": RIGHT,
    "K5 td": RIGHT,
    "K6 th own justify in justify row": JUSTIFY,
    "K6 td": JUSTIFY,
    "L1 th class right in right row": RIGHT,
    "L1 td": RIGHT,
    "L2 th class centre in right row": CENTRE,
    "L2 td": RIGHT,
    "L3 th plain in right row": CENTRE,
    "L3 td": RIGHT,
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
                '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html PUBLIC '
                '"-//W3C//DTD XHTML 1.1//EN" '
                '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html '
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
