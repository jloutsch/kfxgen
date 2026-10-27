"""Pages after a book's Contents page that its TOC doesn't list are kept
through calibre (#551 review). The Contents entry's section runs on to the
next TOC entry, so they were part of it, and rebuilding the Contents page
replaced them: one library book lost a foreword and an introduction, 64
blocks, which Kindle Previewer 4.0.3 prints.

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
from tests.integration.test_calibre_cover_in_text import _page  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

FILES = {
    "contents.xhtml": "<h1>Contents</h1><p><a href='one.xhtml'>Chapter One</a></p>"
    "<p><a href='two.xhtml'>Chapter Two</a></p>",
    "foreword.xhtml": "<h1>Foreword</h1><p>Foreword words.</p>",
    "intro.xhtml": "<h1>Introduction</h1><p>Introduction words.</p>",
    "one.xhtml": "<h1>Chapter One</h1><p>One words.</p>",
    "two.xhtml": "<h1>Chapter Two</h1><p>Two words.</p>",
}
TOC = [
    ("Contents", "contents.xhtml"),
    ("Chapter One", "one.xhtml"),
    ("Chapter Two", "two.xhtml"),
]


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
            for n, f in enumerate(FILES)
        )
        refs = "".join(f'<itemref idref="f{n}"/>' for n in range(len(FILES)))
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">cs551</dc:identifier><dc:title>C</dc:title>'
            f"<dc:language>en</dc:language></metadata><manifest>{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'</manifest><spine toc="ncx">{refs}</spine></package>',
        )
        points = "".join(
            f'<navPoint id="n{n}" playOrder="{n + 1}"><navLabel><text>{t}</text>'
            f'</navLabel><content src="{h}"/></navPoint>'
            for n, (t, h) in enumerate(TOC)
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>C</text></docTitle>'
            f"<navMap>{points}</navMap></ncx>",
        )
        for name, body in FILES.items():
            z.writestr(name, _page(body))


def test_unlisted_pages_after_contents_are_kept(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "cs551.epub", tmp / "cs551.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    texts = [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    for words in ("Foreword", "Foreword words.", "Introduction", "Introduction words."):
        assert texts.count(words) == 1, f"calibre {version}: {words!r}"
