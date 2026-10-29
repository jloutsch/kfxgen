"""A paragraph is cut at an empty line (#602), through calibre, as Kindle
Previewer 4.0.3 writes it (probed 2026-10-28 on this test's own EPUB, with
a DOCTYPE): each run of empty lines ends an entry; the entry before keeps
one line break; the next starts at its words. Headings, list items and
blockquote paragraphs are cut too; preformatted text is not.

Differences from Previewer's file are listed in DIFFERENT: a paragraph
ending with an empty line keeps no break, kfxgen writes a list item's marker
as text, and calibre collapses spaces round a no-break space. A table
cell's text is cut too since #624.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._helpers import colour_jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CASES = [
    "<p>V1 first line<br/><br/>V1 after empty line.</p>",
    "<p>V2 first line<br/>V2 next line.</p>",
    "<p>V3 first<br/><br/><br/>V3 after two empty.</p>",
    '<p class="ind">V4 indented first<br/><br/>V4 indented after.</p>',
    "<p>V5 <i>italic before<br/><br/>italic after</i> plain.</p>",
    '<p>V6 <a href="c2.xhtml">link before<br/><br/>link after</a> plain.</p>',
    '<p>V7 before<br/><br/><span id="v7id"/>V7 id after.</p>',
    "<p><br/><br/>V8 starts with an empty line.</p>",
    "<p>V9 ends with an empty line.<br/><br/></p>",
    "<pre>V10 pre first\n\nV10 pre after</pre>",
    '<div class="stanza">V11 line one<br/>V11 line two<br/><br/>V11 line three<br/>V11 line four</div>',
    '<p class="mar">V12 margins first<br/><br/>V12 margins after.</p>',
    "<p>V13 a<br/><br/>V13 b<br/><br/>V13 c.</p>",
    "<h2>W1 CHAPTER I<br/><br/>W1 THE BEGINNING</h2>",
    "<ul><li>W2 item first<br/><br/>W2 item after</li></ul>",
    "<table><tr><td>W3 cell first<br/><br/>W3 cell after</td><td>W3b</td></tr></table>",
    "<blockquote><p>W4 quote first<br/><br/>W4 quote after</p></blockquote>",
    "<p>W5 first<br/> <br/>W5 after a line of one space.</p>",
    # A spacer line of no-break spaces (#626).
    "<p>X1 first<br/>&#160;<br/>X1 after</p>",
    "<p>X2 first<br/>&#160;&#160;<br/>X2 after</p>",
    "<p>X4 first<br/>&#160;<br/>&#160;<br/>X4 after</p>",
    "<p>X5 first<br/><br/>&#160;<br/>X5 after</p>",
    # An id inside the blank lines (#626).
    '<p>Y1 first<br/><span id="y1id"/><br/>Y1 after</p>',
    # QA on #628: an id between a picture line and an empty line; an em
    # space line; an id after a spacer's no-break space; spaces round one.
    "<p>X09 one<br/><img src='s.jpg' alt='x9'/><br/><span id='y9'/><br/>X09b two</p>",
    "<p>X06 first<br/>&#8195;<br/>X06 after</p>",
    "<p>L10 first<br/>&#160;<span id='l10'/><br/>L10 after</p>",
    "<p>X03 first<br/> &#160; <br/>X03 after</p>",
]
CSS = (
    ".ind { text-indent: 2em; } .mar { margin-top: 1em; margin-bottom: 2em; margin-left: 3em; } "
    ".stanza { margin-left: 2em; }"
)


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        f'xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title>'
        '<link rel="stylesheet" href="s.css" type="text/css"/></head>'
        f"<body>{body}</body></html>"
    )


def _epub(out, key):
    files = {
        "c1.xhtml": _page(
            "Lines",
            "<h1>Lines</h1>"
            + "".join(CASES)
            + '<p><a href="c1.xhtml#v7id">To v7</a></p>'
            + '<p><a href="c1.xhtml#y1id">To y1</a></p>'
            + '<p><a href="c1.xhtml#y9">To y9</a></p>'
            + '<p><a href="c1.xhtml#l10">To l10</a></p>',
        ),
        "c2.xhtml": _page("Two", "<h1>Two</h1><p>Second chapter.</p>"),
    }
    items = (
        "".join(
            f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
            for n, f in enumerate(files)
        )
        + '<item id="css" href="s.css" media-type="text/css"/>'
        + '<item id="jpg" href="s.jpg" media-type="image/jpeg"/>'
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        "<head/><docTitle><text>V</text></docTitle><navMap>"
        '<navPoint id="a" playOrder="1"><navLabel><text>Lines</text></navLabel><content src="c1.xhtml"/></navPoint>'
        '<navPoint id="b" playOrder="2"><navLabel><text>Two</text></navLabel><content src="c2.xhtml"/></navPoint>'
        "</navMap></ncx>"
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
        'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>V602 {key}</dc:title><dc:identifier id="id">v602-{key}</dc:identifier>'
        f"<dc:language>en</dc:language></metadata><manifest>{items}"
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx"><itemref idref="i0"/><itemref idref="i1"/></spine></package>'
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
        z.writestr("s.css", CSS)
        z.writestr("s.jpg", colour_jpeg_of("RGB", (48, 48), (200, 40, 40)))
        for n, d in files.items():
            z.writestr(n, d)


#: Previewer's text entries for the cases, in reading order.
PREVIEWER = [
    "V1 first line\n",
    "V1 after empty line.",
    "V2 first line\nV2 next line.",
    "V3 first\n",
    "V3 after two empty.",
    "V4 indented first\n",
    "V4 indented after.",
    "V5 italic before\n",
    "italic after plain.",
    "V6 link before\n",
    "link after plain.",
    "V7 before\n",
    "V7 id after.",
    "V8 starts with an empty line.",
    "V9 ends with an empty line.\n",
    "V10 pre first\n\nV10 pre after",
    "V11 line one\nV11 line two\n",
    "V11 line three\nV11 line four",
    "V12 margins first\n",
    "V12 margins after.",
    "V13 a\n",
    "V13 b\n",
    "V13 c.",
    "W1 CHAPTER I\n",
    "W1 THE BEGINNING",
    "W2 item first\n",
    "W2 item after",
    "W3 cell first\n",
    "W3 cell after",
    "W3b",
    "W4 quote first\n",
    "W4 quote after",
    "W5 first\n",
    "W5 after a line of one space.",
    "X1 first\n ",
    "X1 after",
    "X2 first\n  ",
    "X2 after",
    "X4 first\n ",
    "X4 after",
    "X5 first\n",
    "X5 after",
    "Y1 first\n",
    "Y1 after",
    "X09b two",
    "X06 first\n\u2003",
    "X06 after",
    "L10 first\n ",
    "L10 after",
    "X03 first\n  ",
    "X03 after",
]
#: kfxgen's own value where it differs, by Previewer's.
DIFFERENT = {
    "V9 ends with an empty line.\n": ["V9 ends with an empty line."],
    "W2 item first\n": ["\u2022 W2 item first\n"],
    # calibre collapses the spaces round the no-break space before kfxgen
    # sees them, so only the no-break space's own space is left (#628).
    "X03 first\n  ": ["X03 first\n "],
}


def test_a_paragraph_is_cut_at_an_empty_line(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "v602.epub", tmp / "v602.kfx"
    _epub(epub, "t")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                t = str(text[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                if t.lstrip("\u2022 ").startswith(
                    ("V", "W", "X", "Y", "L1", "italic", "link")
                ):
                    got.append(t)
    want = [w for p in PREVIEWER for w in DIFFERENT.get(p, [p])]
    assert got == want, f"calibre {version}: {got}"


def test_an_id_after_the_cut_lands_on_the_next_part(calibre):  # noqa: F811
    """Previewer lands a link to V7's id at the start of "V7 id after."."""
    from tests.unit.test_link_landings import _landings

    tmp, _env, version = calibre
    epub, kfx = tmp / "v602b.epub", tmp / "v602b.kfx"
    _epub(epub, "t2")
    _ebook_convert(calibre, epub, kfx, True)
    assert _landings(kfx)["To v7"] == "V7 id after.", f"calibre {version}"


def test_an_id_in_the_blank_lines_lands_on_the_kept_break(calibre):  # noqa: F811
    """Previewer lands a link to an id inside the blank lines on the part
    before, at its kept line break (#626)."""
    from tests.unit.test_link_landings import _landings

    tmp, _env, version = calibre
    epub, kfx = tmp / "v602c.epub", tmp / "v602c.kfx"
    _epub(epub, "t3")
    _ebook_convert(calibre, epub, kfx, True)
    assert _landings(kfx)["To y1"] == "\n", f"calibre {version}"


@pytest.mark.parametrize(
    ("link", "lands"),
    [("To y9", "X09b two"), ("To l10", " ")],
    ids=["after-a-picture-line", "after-a-spacer-nbsp"],
)
def test_ids_round_a_cut_land_as_in_previewer(calibre, link, lands):  # noqa: F811
    """QA on #628: a link to an id between a picture line and an empty line
    lands on the next part (it was dropped since #625); one after a spacer's
    no-break space lands on its space."""
    from tests.unit.test_link_landings import _landings

    tmp, _env, version = calibre
    epub, kfx = tmp / f"v602-{link[-2:]}.epub", tmp / f"v602-{link[-2:]}.kfx"
    _epub(epub, "t" + link[-2:])
    _ebook_convert(calibre, epub, kfx, True)
    assert _landings(kfx).get(link) == lands, f"calibre {version}"
