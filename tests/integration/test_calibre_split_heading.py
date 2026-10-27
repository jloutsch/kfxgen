"""A TOC label spelling out a heading split over blocks leaves each block its
own entry and style, as Kindle Previewer 4.0.3 writes them (#552). kfxgen
joined them into one heading of its own on as many lines (#409).

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

PAGES = {
    "a.xhtml": (
        "Chapter One: The Start",
        "<h1>Chapter One</h1><h2>The Start</h2><p>Body words.</p>",
    ),
    "b.xhtml": (
        "3. Chapter Title Here",
        "<p>3</p><p>Chapter Title Here</p><p>Body two.</p>",
    ),
    "c.xhtml": ("II The Sea", "<p class='n'>II</p><h2>The Sea</h2><p>Body three.</p>"),
    "d.xhtml": (
        "Part Four Act One Scene",
        "<h1>Part Four</h1><h2>Act One</h2><h3>Scene</h3><p>Body four.</p>",
    ),
}

# (text, font weight, font size in em) for each entry, from Previewer's file
# for this book. Its `$790` is each heading's level, where kfxgen's marks a
# section's first entry; that is not this issue.
PREVIEWER = [
    [
        ("Chapter One", "$361", "2"),
        ("The Start", "$361", "1.5"),
        ("Body words.", "", "1"),
    ],
    [("3", "", "1"), ("Chapter Title Here", "", "1"), ("Body two.", "", "1")],
    [("II", "$361", "2"), ("The Sea", "$361", "1.5"), ("Body three.", "", "1")],
    [
        ("Part Four", "$361", "2"),
        ("Act One", "$361", "1.5"),
        ("Scene", "$361", "1.17"),
        ("Body four.", "", "1"),
    ],
]

# (level, text) of the heading navigation's entries ($798) in Previewer's
# file: the book's <h1>-<h3>, none for the paragraphs.
PREVIEWER_LEVELS = {
    (1, "Chapter One"),
    (1, "Part Four"),
    (2, "The Start"),
    (2, "The Sea"),
    (2, "Act One"),
    (3, "Scene"),
}
LEVEL_UNITS = {"$799": 1, "$800": 2, "$801": 3, "$802": 4, "$803": 5, "$804": 6}


def _page(body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><html '
        'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title><style>'
        ".n{font-weight:bold;font-size:2em;text-align:center}</style></head>"
        f"<body>{body}</body></html>"
    )


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
            for n, f in enumerate(PAGES)
        )
        refs = "".join(f'<itemref idref="f{n}"/>' for n in range(len(PAGES)))
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">sp552</dc:identifier><dc:title>S552</dc:title>'
            f"<dc:language>en</dc:language></metadata><manifest>{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'</manifest><spine toc="ncx">{refs}</spine></package>',
        )
        points = "".join(
            f'<navPoint id="n{n}" playOrder="{n + 1}"><navLabel><text>{label}'
            f'</text></navLabel><content src="{f}"/></navPoint>'
            for n, (f, (label, _)) in enumerate(PAGES.items())
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>S</text></docTitle>'
            f"<navMap>{points}</navMap></ncx>",
        )
        for f, (_, body) in PAGES.items():
            z.writestr(f, _page(body))


def _chapters(kfx):
    """[(text, weight, size)] for each storyline's text entries, and the texts
    of entries with a bottom margin."""
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out, bottom = [], []
    for st in by_type(frags, "$259"):
        rows = []
        for e in iter_entries(val(st)["$146"]):
            if not e.get("$145"):
                continue
            ref, style = e["$145"], styles.get(str(e.get("$157")), {})
            size = style.get("$16")
            text = str(texts[str(ref["name"])][int(ref["$403"])])
            if style.get("$46"):
                bottom.append(text)
            rows.append(
                (
                    text,
                    # Normal weight written out or left out is the same.
                    str(style.get("$13") or "").replace("$350", ""),
                    f"{float(size['$307']):g}" if size else "1",
                )
            )
        out.append(rows)
    return out, bottom


def _convert(calibre):  # noqa: F811
    tmp = calibre[0]
    epub, kfx = tmp / "sp552.epub", tmp / "sp552.kfx"
    if not kfx.exists():
        _epub(epub)
        _ebook_convert(calibre, epub, kfx, True)
    return kfx


def test_each_block_keeps_its_own_entry_and_style(calibre):  # noqa: F811
    version = calibre[2]
    chapters, bottom = _chapters(_convert(calibre))
    for want in PREVIEWER:
        assert want in chapters, f"calibre {version}: {chapters}"
    # Previewer writes none on these; kfxgen's own heading has 2em (#552
    # mutations: printing the first block as that heading kept its words).
    assert not set(bottom) & {t for rows in PREVIEWER for t, _, _ in rows}, (
        f"calibre {version}: {bottom}"
    )


def test_each_heading_keeps_its_level(calibre):  # noqa: F811
    """The heading navigation lists each <h1>-<h3> at its level, as
    Previewer's does; the joined heading was one level-1 entry (#552)."""
    version = calibre[2]
    frags = load_fragments(_convert(calibre))
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    at = {
        int(e["$155"]): str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if e.get("$145")
    }

    def plain(x):  # an annotated value, as `$391::{...}` is written
        return getattr(x, "value", x)

    levels = {
        (LEVEL_UNITS[str(plain(unit)["$238"])], at.get(int(plain(c)["$246"]["$155"])))
        for f in by_type(frags, "$389")
        for nav in val(f)
        for container in map(plain, nav["$392"])
        if str(container["$235"]) == "$798"
        for unit in container["$247"]
        for c in plain(unit)["$247"]
    }
    assert levels == PREVIEWER_LEVELS, f"calibre {version}: {levels}"
