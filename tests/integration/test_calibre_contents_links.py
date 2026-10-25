"""Links into a contents page kfxgen rebuilds land on it, through calibre
(#436).

The rebuild drops the page's blocks and their anchor keys with them, so a
link to the listing's file, its id or its heading was dropped. A listing
with no block of its own (#433) had carried its id onto the next part's
heading, so a "back to contents" link landed on the part.

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

XHTML = (
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
    "<head><title>{0}</title></head><body>{1}</body></html>"
)
LISTING = (
    '<p><a href="{0}">Part One</a></p><p><a href="c1.xhtml">Chapter One</a></p>'
    '<p><a href="c2.xhtml">Chapter Two</a></p>'
)
# Links to the listing's id, its file, and its heading's id.
BACK = '<p>Links: <a href="{0}#box">L1</a> <a href="{0}">L2</a> <a href="{0}#head">L3</a></p>'
CHAPTERS = {
    "c1.xhtml": "<h1>Chapter One</h1><p>One text.</p>",
    "c2.xhtml": "<h1>Chapter Two</h1><p>Two text.</p>",
}
SHAPES = {
    "listing-before-the-next-part": (
        {
            "front.xhtml": "<h1>Foreword</h1><p>Foreword text.</p>"
            f'<div class="toc" id="box">{LISTING.format("front.xhtml#p1")}</div>'
            '<h1 id="p1">Part One</h1><p>Part text.</p>',
        },
        [
            ("Foreword", "front.xhtml"),
            ("Contents", "front.xhtml#box"),
            ("Part One", "front.xhtml#p1"),
        ],
        "front.xhtml",
        # The file starts with the foreword, which keeps its own key.
        {"L1": "Contents", "L2": "Foreword"},
    ),
    "listing-in-its-own-file": (
        {
            "toc.xhtml": f'<div class="toc" id="box">{LISTING.format("p1.xhtml")}</div>',
            "p1.xhtml": "<h1>Part One</h1><p>Part text.</p>",
        },
        [("Table", "toc.xhtml"), ("Part One", "p1.xhtml")],
        "toc.xhtml",
        {"L1": "Contents", "L2": "Contents"},
    ),
    "a-page-titled-contents": (
        {
            "toc.xhtml": '<h1 id="head">Contents</h1>'
            f'<div id="box">{LISTING.format("p1.xhtml")}</div>',
            "p1.xhtml": "<h1>Part One</h1><p>Part text.</p>",
        },
        [("Contents", "toc.xhtml"), ("Part One", "p1.xhtml")],
        "toc.xhtml",
        {"L1": "Contents", "L2": "Contents", "L3": "Contents"},
    ),
}


def _build(path, files, toc, ident):
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, n in enumerate(files)
    )
    spine = "".join(f'<itemref idref="i{i}"/>' for i in range(len(files)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="u"><metadata '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">{ident}'
        f"</dc:identifier><dc:title>{ident}</dc:title><dc:language>en</dc:language>"
        f'</metadata><manifest>{items}<item id="ncx" href="toc.ncx" '
        f'media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">{spine}'
        "</spine></package>"
    )
    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{t}</text></navLabel>'
        f'<content src="{h}"/></navPoint>'
        for i, (t, h) in enumerate(toc, 1)
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        f'version="2005-1"><head/><docTitle><text>{ident}</text></docTitle>'
        f"<navMap>{points}</navMap></ncx>"
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
        for name, body in files.items():
            z.writestr(name, XHTML.format(name, body))


@pytest.mark.parametrize("shape", list(SHAPES))
def test_links_into_the_rebuilt_contents_page_land_on_it(calibre, shape):  # noqa: F811
    tmp, _env, version = calibre
    files, toc, listing_file, expected = SHAPES[shape]
    files = {**files, **CHAPTERS}
    files["c1.xhtml"] += BACK.format(listing_file)
    toc = toc + [("Chapter One", "c1.xhtml"), ("Chapter Two", "c2.xhtml")]
    epub, kfx = tmp / f"links-{shape}.epub", tmp / f"links-{shape}.kfx"
    _build(epub, files, toc, f"links436{shape}")
    _ebook_convert(calibre, epub, kfx, True)

    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    by_id = {e["$155"]: e for e in entries}
    anchors = {str(f.fid): val(f)["$183"]["$155"] for f in by_type(frags, "$266")}

    def text(e):
        r = e["$145"]
        return str(content[str(r["name"])][int(r["$403"])])

    landed = {}
    for e in entries:
        t = text(e)
        for sp in e.get("$142") or []:
            label = t[int(sp["$143"]) : int(sp["$143"]) + int(sp["$144"])]
            if "$179" in sp and label in ("L1", "L2", "L3"):
                landed[label] = text(by_id[anchors[str(sp["$179"])]])
    assert landed == expected, f"calibre {version}"
