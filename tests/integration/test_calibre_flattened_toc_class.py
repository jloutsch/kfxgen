"""A contents listing by the book's own class, through real calibre (#329).

calibre flattens the CSS before kfxgen sees the book, naming each class
after the first element with that style. When a contents page's entries are
an unstyled `<p class="toc">`, the plain paragraphs of every later chapter
share their style and arrive as `class="toc"` too, and kfxgen dropped their
text as a contents listing. kfxgen now records the book's own classes in
`specialize_css_for_output`, which calibre runs on each file before it
flattens it. The test shim never flattens CSS, so this needs calibre.

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
    _page,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

_CONTENTS = (
    "<h1>Contents</h1>"
    '<p class="toc"><a href="c1.xhtml">The Harbour</a></p>'
    '<p class="toc"><a href="c2.xhtml">The Storm</a></p>'
)
_CHAPTERS = {
    "c1": ("The Harbour", ["Boats lay at anchor.", "The tide turned at noon."]),
    "c2": ("The Storm", ["The wind rose after dark.", "By morning it was calm."]),
}
_NCX = (
    '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
    'version="2005-1"><head><meta name="dtb:uid" content="t"/></head>'
    "<docTitle><text>Harbour</text></docTitle><navMap>"
    '<navPoint id="p0" playOrder="1"><navLabel><text>Contents</text></navLabel>'
    '<content src="contents.xhtml"/></navPoint>'
    '<navPoint id="p1" playOrder="2"><navLabel><text>The Harbour</text></navLabel>'
    '<content src="c1.xhtml"/></navPoint>'
    '<navPoint id="p2" playOrder="3"><navLabel><text>The Storm</text></navLabel>'
    '<content src="c2.xhtml"/></navPoint></navMap></ncx>'
)
#: Unstyled entries share the paragraphs' style; styled ones don't.
CSS = {
    "unstyled": "p { margin: 0; }\n",
    "styled": "p { margin: 0; }\n.toc { margin-left: 2em; }\n",
}


def _styled_page(title, body):
    return _page(title, body).replace(
        "</title>", '</title><link rel="stylesheet" type="text/css" href="s.css"/>'
    )


def _build(path, css):
    files = [("contents", "Contents", _CONTENTS)] + [
        (f, title, f"<h1>{title}</h1>" + "".join(f"<p>{t}</p>" for t in paras))
        for f, (title, paras) in _CHAPTERS.items()
    ]
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
            f'<item id="{f}" href="{f}.xhtml" media-type="application/xhtml+xml"/>'
            for f, _, _ in files
        )
        refs = "".join(f'<itemref idref="{f}"/>' for f, _, _ in files)
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">t'
            "</dc:identifier><dc:title>Harbour</dc:title><dc:language>en"
            f"</dc:language></metadata><manifest>{items}"
            '<item id="s" href="s.css" media-type="text/css"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'</manifest><spine toc="ncx">{refs}</spine></package>',
        )
        for f, title, body in files:
            z.writestr(f"{f}.xhtml", _styled_page(title, body))
        z.writestr("s.css", css)
        z.writestr("toc.ncx", _NCX)


def _texts(kfx):
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    return [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if "$145" in e
    ]


@pytest.mark.parametrize("variant", sorted(CSS))
def test_chapter_paragraphs_survive_calibres_class_names(calibre, variant):  # noqa: F811
    tmp, _, version = calibre
    epub = tmp / f"toc-{variant}.epub"
    _build(epub, CSS[variant])
    kfx = tmp / f"toc-{variant}.kfx"
    _ebook_convert(calibre, epub, kfx, native=True)
    texts = _texts(kfx)
    missing = [t for _, paras in _CHAPTERS.values() for t in paras if t not in texts]
    assert not missing, f"calibre {version}, {variant}: lost {missing}"
