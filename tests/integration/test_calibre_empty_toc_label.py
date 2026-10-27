"""A TOC entry with an empty or blank label is kept through calibre (#459),
as Kindle Previewer 4.0.3 keeps it, its label as written. calibre's TOC
leaves them out. Previewer drops one with no label at all at the top level
(2 probes), and so does kfxgen.

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


def _point(n, label, src, kids=""):
    return (
        f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>{label}</text>'
        f'</navLabel><content src="{src}"/>{kids}</navPoint>'
    )


# Nested and empty: kept. Top level and empty: dropped. A space: kept.
POINTS = (
    _point(1, "One", "c1.xhtml", _point(2, "", "c1.xhtml#mid"))
    + _point(3, "Two", "c2.xhtml")
    + _point(4, "", "c2.xhtml#end")
    + _point(5, " ", "c3.xhtml")
)


def _epub(path):
    files = {
        "c1.xhtml": "<h1>One</h1><p>First.</p><p id='mid'>Middle words.</p><p>End.</p>",
        "c2.xhtml": "<h1>Two</h1><p>Second.</p><p id='end'>Two ends.</p>",
        "c3.xhtml": "<h1>Three</h1><p>Third.</p>",
    }
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
            for n, f in enumerate(files)
        )
        refs = "".join(f'<itemref idref="f{n}"/>' for n in range(len(files)))
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">el459</dc:identifier><dc:title>E</dc:title>'
            f"<dc:language>en</dc:language></metadata><manifest>{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'</manifest><spine toc="ncx">{refs}</spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>E</text></docTitle>'
            f"<navMap>{POINTS}</navMap></ncx>",
        )
        for name, body in files.items():
            z.writestr(name, _page(body))


def _labels(kfx):
    """(label, the text it lands on) for each contents-pane entry."""
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    text = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text[e["$155"]] = str(
                    content[str(e["$145"]["name"])][int(e["$145"]["$403"])]
                )
    out = []

    def units(us):
        for u in us or []:
            u = getattr(u, "value", u)
            label = str(getattr(u["$241"], "value", u["$241"]).get("$244", ""))
            target = getattr(u["$246"], "value", u["$246"])
            out.append((label, text.get(target.get("$155"))))
            units(u.get("$247"))

    for f in by_type(frags, "$389"):
        for c in val(f)[0]["$392"]:
            c = getattr(c, "value", c)
            if str(c["$235"]) == "$212":
                units(c["$247"])
    return out


def test_an_entry_with_an_empty_label_is_kept(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "el459.epub", tmp / "el459.kfx"
    _epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    labels = _labels(kfx)
    assert labels == [
        ("One", "One"),
        ("", "Middle words."),
        ("Two", "Two"),
        (" ", "Three"),
    ], f"calibre {version}"
