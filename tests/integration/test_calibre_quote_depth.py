"""<q> marks follow the quote depth as Kindle Previewer 4.0.3 counts it,
through calibre (#504): open-quote raises the depth and close-quote lowers
it, across the document; a keyword prints its mark only alone, open-quote
in ::before and close-quote in ::after. Each expected line is Previewer's
own output for the same markup, one spine file per case, less the
zero-width spaces it puts around generated text.

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

CSS = """
q.b1::before { content: "[[" }
q.battr::before { content: attr(lang) ": " }
q.aopen::after { content: open-quote }
q.anone::after { content: none }
q.noopen::before { content: no-open-quote }
q.mix::before { content: "<" open-quote }
q.aclose2::after { content: ">" close-quote }
"""
CASES = {
    "c1": ('<p>C1 <q class="b1">x</q> end</p>', ["C1 [[x end"]),
    "c3": ('<p>C3 <q class="battr" lang="fr">x</q> end</p>', ["C3 fr: x end"]),
    "c6": ('<p>C6 <q class="b1">a <q>b</q> c</q> end</p>', ["C6 [[a “b” c end"]),
    "c8": (
        '<p>C8 <q class="anone">x</q> then</p><p>C8b <q>y</q> end</p>',
        ["C8 “x then", "C8b ‘y’ end"],
    ),
    "c9": ('<p>C9 <q class="noopen">x</q> <q>y</q> end</p>', ["C9 x” “y” end"]),
    "d5": ('<p>D5 <q class="aopen">x</q> <q>y</q> end</p>', ["D5 “x ‘y’ end"]),
    "d6": ('<p>D6 <q class="mix">x</q> <q>y</q> end</p>', ["D6 <x” “y” end"]),
    "d4": ('<p>D4 <q class="aclose2">x</q> end</p>', ["D4 “x> end"]),
}


def _page(body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><html '
        'xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><title>p</title>'
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )


def _epub(path):
    items = "".join(
        f'<item id="{k}" href="{k}.xhtml" media-type="application/xhtml+xml"/>'
        for k in CASES
    )
    spine = "".join(f'<itemref idref="{k}"/>' for k in CASES)
    navs = "".join(
        f'<navPoint id="n{k}" playOrder="{i}"><navLabel><text>{k.upper()}</text>'
        f'</navLabel><content src="{k}.xhtml"/></navPoint>'
        for i, k in enumerate(CASES, 1)
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
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">q504</dc:identifier><dc:title>Q</dc:title>'
            f"<dc:language>en</dc:language></metadata><manifest>{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'</manifest><spine toc="ncx">{spine}</spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            f'version="2005-1"><head/><docTitle><text>Q</text></docTitle><navMap>'
            f"{navs}</navMap></ncx>",
        )
        for k, (body, _) in CASES.items():
            z.writestr(f"{k}.xhtml", _page(body))


def test_quote_depth_as_previewer_counts_it(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "q504.epub", tmp / "q504.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    lines = [
        str(t)
        for f in by_type(load_fragments(kfx), "$145")
        for t in val(f)["$146"]
        if str(t)[:1] in "CD"
    ]
    expected = [line for _, want in CASES.values() for line in want]
    assert sorted(lines) == sorted(expected), f"calibre {version}"
