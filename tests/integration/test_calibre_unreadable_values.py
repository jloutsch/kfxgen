"""A chapter holding a CSS length or an attribute width kfxgen can't read
is kept, through calibre (#792).

Before, the helper raised, and the spine loop left the whole file out
("Spine item N parse failed"): a length in inches spelled with a dotless
or dotted i, in a style attribute or a stylesheet, and a width attribute
of "1.2.3%" on a table or a cell, which calibre passes on as written. A
picture's width or height attribute of 400 digits, among a paragraph's
words, failed the whole book (QA on #797).
calibre itself drops the malformed colours as syntax errors, and its own
CSS parser fails on numbers of 400 or 5,000 digits, so those aren't here.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import io
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import (  # noqa: E402
    by_type,
    iter_entries,
    load_fragments,
    val,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import _ncx, _page  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CASES = {
    "INDENTWORD": ("", '<p style="text-indent: 1ın">INDENTWORD dotless.</p>'),
    "RULEWORD": (".k { text-indent: 1ın }", '<p class="k">RULEWORD in a rule.</p>'),
    "TABLEWORD": (
        "",
        '<table width="1.2.3%"><tr><td>TABLEWORD table.</td><td>x</td></tr></table>',
    ),
    "CELLWORD": (
        "",
        '<table><tr><td width="1.2.3%">CELLWORD cell.</td><td>x</td></tr></table>',
    ),
    "WIDEWORD": (
        "",
        f'<p>WIDEWORD a <img src="p.jpg" alt="" width="{"9" * 400}"/> picture.</p>',
    ),
    "TALLWORD": (
        "",
        f'<p>TALLWORD a <img src="p.jpg" alt="" height="{"9" * 400}"/> picture.</p>',
    ),
}


def _write(path):
    pages = {
        f"c{i}.xhtml": (css, f"<h1>Part {i}</h1>{body}")
        for i, (css, body) in enumerate(CASES.values())
    }
    pages["end.xhtml"] = ("", "<h1>End</h1><p>ENDWORD last.</p>")
    items = "".join(
        f'<item id="x{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in enumerate(pages)
    )
    items += '<item id="p" href="p.jpg" media-type="image/jpeg"/>'
    spine = "".join(f'<itemref idref="x{i}"/>' for i in range(len(pages)))
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="u"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="u">urn:kfxgen:uv792</dc:identifier><dc:title>uv792'
        "</dc:title><dc:language>en</dc:language></metadata>"
        f'<manifest>{items}<item id="ncx" href="toc.ncx" '
        f'media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">{spine}'
        "</spine></package>"
    )
    toc = [(b.split("</h1>")[0][4:], h, 0) for h, (_, b) in pages.items()]
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
        z.writestr("toc.ncx", _ncx(toc))
        jpeg = io.BytesIO()
        Image.new("RGB", (40, 30), (90, 90, 90)).save(jpeg, "JPEG")
        z.writestr("p.jpg", jpeg.getvalue())
        for name, (css, body) in pages.items():
            z.writestr(name, _page(body, ' xml:lang="en"', css))


def test_a_chapter_with_a_value_kfxgen_cannot_read_is_kept(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "uv792.epub", tmp / "uv792.kfx"
    _write(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    words = {
        w
        for f in by_type(frags, "$145")
        for t in val(f)["$146"]
        for w in str(t).split()
    }
    # Text beside a picture is written in its entry, not in a $145 (#625).
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            for t in e.get("$146") or () if hasattr(e, "get") else ():
                if isinstance(t, str):
                    words.update(t.split())
    missing = [w for w in [*CASES, "ENDWORD"] if w not in words]
    assert missing == [], f"calibre {version}"
