"""A box whose only picture the book doesn't have, with an id waiting for it,
converts through calibre (#434).

The paragraph before the box holds only a missing picture too, so its id
waits for the next block. The box emits nothing of its own, and the native
writer raised a bare StopIteration looking for its first paragraph.

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
FILES = {
    "front.xhtml": XHTML.format(
        "F", '<h1>Front</h1><p><a href="c1.xhtml#pic">to the picture</a></p>'
    ),
    "c1.xhtml": XHTML.format(
        "1",
        "<h1>Chapter One</h1><p>Lead.</p>"
        '<p id="pic"><img src="missing.png" alt=""/></p>'
        '<div style="border: 1px solid black; padding: 0.5em">'
        '<p><img src="gone.png" alt=""/></p></div>'
        "<p>After.</p>",
    ),
}
TOC = [("Front", "front.xhtml"), ("Chapter One", "c1.xhtml")]


def _build(path):
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, n in enumerate(FILES)
    )
    spine = "".join(f'<itemref idref="i{i}"/>' for i in range(len(FILES)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">emptybox'
        "</dc:identifier><dc:title>Empty box</dc:title><dc:language>en"
        f"</dc:language></metadata><manifest>{items}"
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        f'</manifest><spine toc="ncx">{spine}</spine></package>'
    )
    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{t}</text></navLabel>'
        f'<content src="{h}"/></navPoint>'
        for i, (t, h) in enumerate(TOC, 1)
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>Empty box</text></docTitle>'
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
        for name, data in FILES.items():
            z.writestr(name, data)


def test_the_link_lands_on_the_paragraph_after_the_box(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "empty-box.epub", tmp / "empty-box.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e for st in by_type(frags, "$259") for e in iter_entries(val(st)["$146"])
    ]
    by_id = {e["$155"]: e for e in entries}
    anchors = {str(f.fid): val(f)["$183"]["$155"] for f in by_type(frags, "$266")}
    (target,) = [
        by_id[anchors[str(sp["$179"])]]
        for e in entries
        for sp in e.get("$142") or []
        if "$179" in sp
    ]
    r = target["$145"]
    assert str(content[str(r["name"])][r["$403"]]) == "After.", f"calibre {version}"
