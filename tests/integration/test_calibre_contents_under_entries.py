"""A contents heading whose listings sit under its own TOC entries (#782),
through calibre.

pg26209's shape: `<h2>CONTENTS.</h2>`, then a table of links under each
`<h3>`, each `<h3>` a child of "CONTENTS." in the NCX. kfxgen rebuilt the
heading's chapter as a listing of every entry, and the tables followed it.
Kindle Previewer 4.0.3 prints the heading and the tables only. The unit
tests are in tests/unit/test_contents_in_sections.py.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. Last run against calibre 9.18.0.
"""

import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from lxml import etree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>'
        f"{title}</title></head><body>{body}</body></html>"
    )


def _table(ids):
    rows = "".join(
        f'<tr><td>{i} recipe</td><td><a href="c0.xhtml#{i}">13</a></td></tr>'
        for i in ids
    )
    return f"<table>{rows}</table>"


def _epub(out):
    recipes = "<h1>C0 Breads</h1>" + "".join(
        f'<p id="{i}">{i} words of the recipe.</p>' for i in ("r1", "r2", "r3")
    )
    contents = (
        '<h2 id="toc">CONTENTS.</h2><h3 id="s1">BREADS.</h3>'
        + _table(["r1", "r2"])
        + '<h3 id="s2">EGGS.</h3>'
        + _table(["r3"])
    )
    files = {
        "c0.xhtml": _page("C0", recipes),
        "c1.xhtml": _page("C1", contents),
        "c2.xhtml": _page("C2", "<h2>C2 License</h2><p>C2 words.</p>"),
    }

    def point(n, label, src, kids=""):
        return (
            f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>{label}</text>'
            f'</navLabel><content src="{src}"/>{kids}</navPoint>'
        )

    ncx = (
        '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>X</text></docTitle><navMap>'
        + point(1, "C0 Breads", "c0.xhtml")
        + point(
            2,
            "CONTENTS.",
            "c1.xhtml#toc",
            point(3, "BREADS.", "c1.xhtml#s1") + point(4, "EGGS.", "c1.xhtml#s2"),
        )
        + point(5, "C2 License", "c2.xhtml")
        + "</navMap></ncx>"
    )
    items = "".join(
        f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
        for n, f in enumerate(files)
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>C782</dc:title><dc:identifier id="id">c782</dc:identifier>'
        f'<dc:language>en</dc:language></metadata><manifest>{items}<item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">'
        + "".join(f'<itemref idref="i{n}"/>' for n in range(len(files)))
        + "</spine></package>"
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    for doc in [opf, ncx, container, *files.values()]:
        etree.fromstring(doc.encode())
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        for name, doc in files.items():
            z.writestr(name, doc)


def _texts(kfx):
    """How often each line of text is printed, cells included."""
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    lines = Counter()
    for story in by_type(frags, "$259"):
        for e in iter_entries(val(story)["$146"]):
            ref = e.get("$145") if hasattr(e, "get") else None
            if ref is not None:
                text = str(content[str(ref["name"])][int(ref["$403"])])
                lines.update(t.strip() for t in text.split("\n"))
    return lines


def test_the_heading_and_its_tables_print_once(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "c782.epub", tmp / "c782.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    lines = _texts(kfx)
    assert lines["CONTENTS."] == 1, version
    # A rebuilt page would print every entry again: "C0 Breads", "BREADS."
    # and "EGGS." twice.
    assert (lines["C0 Breads"], lines["BREADS."], lines["EGGS."]) == (1, 1, 1)
    assert lines["r1 recipe"] == 1 and lines["r3 recipe"] == 1
