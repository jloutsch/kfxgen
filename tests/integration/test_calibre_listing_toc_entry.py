"""A TOC entry on a contents listing that ends where the next part starts
keeps the part, through calibre (#433).

The listing's ids went on Part One's heading, so the Contents entry started
there, Part One's entry was skipped and the contents rebuild replaced the
part. The unit tests build the TOC by hand; this one has calibre read it
from the NCX, fragment and all.

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
        "F",
        "<h1>Foreword</h1><p>Foreword text.</p>"
        '<div class="toc" id="box"><p><a href="front.xhtml#p1">Part One</a></p>'
        '<p><a href="c1.xhtml">Chapter One</a></p></div>'
        '<h1 id="p1">Part One</h1><p>Part text.</p>',
    ),
    "c1.xhtml": XHTML.format("1", "<h1>Chapter One</h1><p>Chapter text.</p>"),
}
TOC = [
    ("Foreword", "front.xhtml"),
    ("Contents", "front.xhtml#box"),
    ("Part One", "front.xhtml#p1"),
    ("Chapter One", "c1.xhtml"),
]


def _build(path):
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, n in enumerate(FILES)
    )
    spine = "".join(f'<itemref idref="i{i}"/>' for i in range(len(FILES)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">listing'
        "</dc:identifier><dc:title>Listing entry</dc:title><dc:language>en"
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
        'version="2005-1"><head/><docTitle><text>Listing entry</text></docTitle>'
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


@pytest.fixture(scope="module")
def texts(calibre):  # noqa: F811
    """The text of each paragraph, in reading order, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "listing-entry.epub", tmp / "listing-entry.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = [
        str(content[str(e["$145"]["name"])][e["$145"]["$403"]])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if e.get("$145")
    ]
    return out, version


def test_the_part_after_the_listing_is_kept(texts):
    out, version = texts
    assert "Part text." in out, f"calibre {version}: {out}"
    assert out.index("Foreword text.") < out.index("Part text."), out


def test_the_listing_becomes_the_contents_page(texts):
    out, version = texts
    # The rebuilt page lists the parts, between the foreword and Part One.
    assert out.index("Foreword text.") < out.index("Contents"), out
    assert out.index("Contents") < out.index("Part text."), out
