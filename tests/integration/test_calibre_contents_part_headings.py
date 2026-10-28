"""Part headings on a rebuilt contents page (#595), as Kindle Previewer
4.0.3 prints them (probed 2026-10-28): a part's `<h2>` that is also a TOC
entry prints at the heading's size, bold, and is a level-2 heading in the
heading navigation (`$798`). kfxgen printed it as a plain contents line,
0.75em and underlined, with no level. kfxgen keeps the line's link (every
line on its page is one); Previewer links it only when the `<h2>` holds a
link, and underlines neither.

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

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

PARTS = [
    ("p1.xhtml", "Part One", ["c1.xhtml", "c2.xhtml"]),
    ("p2.xhtml", "Part Two", ["c3.xhtml"]),
]
TITLES = {
    "c1.xhtml": "Chapter One",
    "c2.xhtml": "Chapter Two",
    "c3.xhtml": "Chapter Three",
}


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>'
        f'{title}</title><link rel="stylesheet" href="s.css" type="text/css"/></head>'
        f"<body>{body}</body></html>"
    )


def _epub(out, key, linked_part):
    contents = "<h1>Contents</h1>"
    for n, (pf, pt, chs) in enumerate(PARTS):
        if linked_part and n == 1:
            contents += f'<h2><a href="{pf}">{pt}</a></h2>'
        else:
            contents += f"<h2>{pt}</h2>"
        contents += "".join(
            f'<p class="toc"><a href="{c}">{TITLES[c]}</a></p>' for c in chs
        )
    files = {"contents.xhtml": _page("Contents", contents)}
    for pf, pt, chs in PARTS:
        files[pf] = _page(pt, f"<h1>{pt}</h1><p>{pt} opening words.</p>")
        for c in chs:
            files[c] = _page(
                TITLES[c], f"<h2>{TITLES[c]}</h2>" + f"<p>{TITLES[c]} words.</p>" * 15
            )
    spine = list(files)
    css = "h1{font-size:2em} h2{font-size:1.5em; font-weight:bold} p.toc{margin:0}"
    items = (
        "".join(
            f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
            for n, f in enumerate(spine)
        )
        + '<item id="css" href="s.css" media-type="text/css"/>'
    )
    points, order = "", 0
    for pf, pt, chs in PARTS:
        order += 1
        kids = ""
        for c in chs:
            order += 1
            kids += (
                f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{TITLES[c]}</text>'
                f'</navLabel><content src="{c}"/></navPoint>'
            )
        points += (
            f'<navPoint id="p{order}" playOrder="{order}"><navLabel><text>{pt}</text></navLabel>'
            f'<content src="{pf}"/>{kids}</navPoint>'
        )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        f'version="2005-1"><head/><docTitle><text>X</text></docTitle><navMap>'
        f'<navPoint id="c0" playOrder="0"><navLabel><text>Contents</text></navLabel>'
        f'<content src="contents.xhtml"/></navPoint>{points}</navMap></ncx>'
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>H595 {key}</dc:title><dc:identifier id="id">h595-{key}</dc:identifier>'
        f'<dc:language>en</dc:language></metadata><manifest>{items}<item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">'
        + "".join(f'<itemref idref="i{n}"/>' for n in range(len(spine)))
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
        z.writestr("s.css", css)
        for name, doc in files.items():
            z.writestr(name, doc)


def _plain(x):
    return getattr(x, "value", x)


def _contents(kfx):
    """The Contents page's lines: (text, size, weight, linked, underlined),
    and the heading navigation's level-2 headings."""
    frags = load_fragments(kfx)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    words, lines = {}, []
    for st in by_type(frags, "$259"):
        rows = []
        for e in iter_entries(val(st)["$146"]):
            if not (hasattr(e, "get") and e.get("$145")):
                continue
            t = str(text[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            words[int(e["$155"])] = t
            y = styles.get(str(e.get("$157")), {})
            runs = [_plain(r) for r in e.get("$142") or ()]
            size = _plain(y.get("$16"))
            rows.append(
                (
                    t,
                    float(size["$307"]) if size is not None else 1.0,
                    str(y.get("$13")),
                    any("$179" in [str(k) for k in r] for r in runs),
                    any(
                        str(styles.get(str(r.get("$157")), {}).get("$23")) == "$328"
                        for r in runs
                    ),
                )
            )
        if rows and rows[0][0] == "Contents":
            lines = rows
    level2 = []
    for f in by_type(frags, "$389"):
        for nav in val(f):
            for c in nav.get("$392") or ():
                c = _plain(c)
                if str(c.get("$235")) == "$798":
                    for unit in c.get("$247") or ():
                        unit = _plain(unit)
                        if str(unit.get("$238")) == "$800":
                            level2 = [
                                words.get(int(_plain(_plain(k)["$246"])["$155"]))
                                for k in unit.get("$247") or ()
                            ]
    return lines, level2


@pytest.mark.parametrize("linked", [False, True], ids=["plain", "linked"])
def test_a_part_heading_prints_as_a_heading(calibre, linked):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"h595-{linked}.epub", tmp / f"h595-{linked}.kfx"
    _epub(epub, f"t{linked}", linked)
    _ebook_convert(calibre, epub, kfx, True)
    lines, level2 = _contents(kfx)
    parts = {t: (size, weight, link, under) for t, size, weight, link, under in lines}
    for part in ("Part One", "Part Two"):
        size, weight, link, under = parts[part]
        # Previewer: 1.5em and bold, not underlined.
        assert (size, weight, under) == (1.5, "$361", False), (
            f"calibre {version}: {lines}"
        )
        assert link, f"calibre {version}: {part} lost its link"
    # A chapter line is still kfxgen's contents line.
    assert parts["Chapter One"][3], f"calibre {version}: {lines}"
    # Previewer's level 2: the page's part headings, then the chapters'.
    assert level2[:2] == ["Part One", "Part Two"], f"calibre {version}: {level2}"
