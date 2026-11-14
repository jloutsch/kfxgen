"""A TOC entry naming an id at a file's very end, through calibre (#772).

Some books end each file with an empty `<div id>` and point the next
chapter's TOC entry at it (QA's Q771C, from one library book). kfxgen
started that chapter on the file's last block, so the block (a whole list,
since #771) printed after the next chapter's heading. Kindle Previewer 4.0.3
keeps every block in its own file's chapter. The entry now starts its
chapter at the next file, where #338 already sends its link.

It needs calibre installed, so it is `slow` and skips when `ebook-convert`
is not found.
"""

import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import (  # noqa: E402
    by_type,
    iter_entries,
    load_fragments,
    val,
    walk_for_key,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import _ncx, _page  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BODY = {
    "f0.xhtml": "<p>Title words.</p><div id='x0'></div>",
    "f1.xhtml": "<h2>C1 Heading</h2><p>C1 words.</p>"
    "<ol><li>C1 one</li><li>C1 two</li></ol><div id='x1'></div>",
    "f2.xhtml": "<h2>C2 Heading</h2><p>C2 words.</p><p>C2 last line.</p>"
    "<div id='x2'></div>",
    "f3.xhtml": "<h2>C3 Heading</h2><p>C3 words.</p>",
}
TOC = [
    ("C1 Heading", "f0.xhtml#x0", 1),
    ("C2 Heading", "f1.xhtml#x1", 1),
    ("C3 Heading", "f2.xhtml#x2", 1),
]


def _write(path):
    items = "".join(
        f'<item id="x{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in enumerate(BODY)
    )
    spine = "".join(f'<itemref idref="x{i}"/>' for i in range(len(BODY)))
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="u">urn:kfxgen:t772</dc:identifier><dc:title>T772</dc:title>'
        "<dc:creator>A</dc:creator><dc:language>en</dc:language></metadata>"
        f'<manifest>{items}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        f'<spine toc="ncx">{spine}</spine></package>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", _ncx(TOC))
        for h, b in BODY.items():
            z.writestr(h, _page(b, ' xml:lang="en"', "p { margin: 0 }"))


def _sections(kfx):
    """Each section's texts, in reading order."""
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    stories = {}
    for f in by_type(frags, "$259"):
        stories[str(val(f)["$176"])] = [
            str(texts[str(r["name"])][r["$403"]])
            for e in iter_entries(val(f)["$146"])
            if hasattr(e, "get") and (r := e.get("$145"))
        ]
    secs = {
        str(val(f)["$174"]): [str(s) for s in walk_for_key(val(f), "$176")]
        for f in by_type(frags, "$260")
    }
    (doc,) = by_type(frags, "$538")
    order = [str(n) for n in val(doc)["$169"][0]["$170"]]
    return [
        [t for s in dict.fromkeys(secs[n]) for t in stories.get(s, [])] for n in order
    ]


def test_each_chapter_keeps_its_own_files_blocks(calibre, tmp_path):  # noqa: F811
    tmp, _env, _version = calibre
    epub, kfx = tmp / "t772.epub", tmp / "t772.kfx"
    _write(epub)
    _ebook_convert(calibre, epub, kfx, True)
    assert _sections(kfx) == [
        ["Title words."],
        ["C1 Heading", "C1 words.", "C1 one", "C1 two"],
        ["C2 Heading", "C2 words.", "C2 last line."],
        ["C3 Heading", "C3 words."],
    ]
