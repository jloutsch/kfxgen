"""The book's page list (#578), as Kindle Previewer 4.0.3 writes it
(probed 2026-10-28): a `$237` navigation container with one entry per page
break, labelled as the book labels it and landing on the break itself, mid
paragraph too. Previewer takes it from an EPUB 3 nav `page-list`, or from an
NCX `pageList`; with both, the two agree here. kfxgen wrote none.

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

CHAPS = ["c1.xhtml", "c2.xhtml"]
# (file, id, label): page breaks, one mid-paragraph.
PAGES = [
    ("c1.xhtml", "p1", "1"),
    ("c1.xhtml", "p2", "2"),
    ("c2.xhtml", "p3", "3"),
    ("c2.xhtml", "p4", "iv"),
]


def _page(n, f):
    marks = [p for p in PAGES if p[0] == f]
    body = f"<h1>Chapter {n}</h1>"
    for i, (_f, pid, label) in enumerate(marks):
        body += (
            f'<p>Words before page {label}. <span epub:type="pagebreak" id="{pid}" '
            f'title="{label}"/>Words after page {label} in the same paragraph.</p>'
        )
        body += f"<p>More text for chapter {n}, page {label}.</p>" * 10
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>C{n}</title></head><body>{body}</body></html>"
    )


def _epub(out, key, nav_pages, ncx_pages, relabel=None):
    relabel = relabel or {}
    epub3 = nav_pages
    files = {f: _page(n, f) for n, f in enumerate(CHAPS, 1)}
    items = "".join(
        f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
        for n, f in enumerate(CHAPS)
    )
    if epub3:
        plist = (
            '<nav epub:type="page-list"><ol>'
            + "".join(
                f'<li><a href="{f}#{pid}">{relabel.get(label, label)}</a></li>'
                for f, pid, label in PAGES
            )
            + "</ol></nav>"
        )
        files["nav.xhtml"] = (
            '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
            'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<head><title>Nav</title></head><body><nav epub:type="toc"><ol>'
            + "".join(
                f'<li><a href="{f}">Chapter {n}</a></li>'
                for n, f in enumerate(CHAPS, 1)
            )
            + f"</ol></nav>{plist}</body></html>"
        )
        items += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    page_list = (
        "<pageList><navLabel><text>Pages</text></navLabel>"
        + "".join(
            f'<pageTarget id="pt{n}" type="normal" value="{n}" playOrder="{n + 2}">'
            f"<navLabel><text>{relabel.get(label, label)}</text></navLabel>"
            f'<content src="{f}#{pid}"/></pageTarget>'
            for n, (f, pid, label) in enumerate(PAGES, 1)
        )
        + "</pageList>"
        if ncx_pages
        else ""
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>P</text></docTitle><navMap>'
        + "".join(
            f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>Chapter {n}</text></navLabel>'
            f'<content src="{f}"/></navPoint>'
            for n, f in enumerate(CHAPS, 1)
        )
        + f"</navMap>{page_list}</ncx>"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        f'version="{"3.0" if epub3 else "2.0"}" unique-identifier="id"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>P578 {key}</dc:title><dc:identifier id="id">p578-{key}</dc:identifier>'
        "<dc:language>en</dc:language>"
        + (
            '<meta property="dcterms:modified">2026-10-28T00:00:00Z</meta>'
            if epub3
            else ""
        )
        + f'</metadata><manifest>{items}<item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">'
        + "".join(f'<itemref idref="i{n}"/>' for n in range(len(CHAPS)))
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


#: Previewer's entries for each book: (label, the words where it lands).
PREVIEWER = [
    ("1", "Words after page 1"),
    ("2", "Words after page 2"),
    ("3", "Words after page 3"),
    ("iv", "Words after page iv"),
]
CASES = {
    "nav": (True, False),
    "ncx": (False, True),
    "both": (True, True),
    "none": (False, False),
}


def _page_list(kfx):
    frags = load_fragments(kfx)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    words = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                words[int(e["$155"])] = str(
                    text[str(e["$145"]["name"])][int(e["$145"]["$403"])]
                )
    got = []
    for f in by_type(frags, "$389"):
        for nav in val(f):
            for c in nav.get("$392") or ():
                c = getattr(c, "value", c)
                if str(c.get("$235")) != "$237":
                    continue
                for e in c.get("$247") or ():
                    e = getattr(e, "value", e)
                    t = getattr(e["$246"], "value", e["$246"])
                    at = words.get(int(t["$155"]), "")[int(t["$143"]) :]
                    got.append((str(e["$241"]["$244"]), at))
    return got


@pytest.mark.parametrize("case", list(CASES))
def test_the_page_list_as_previewer_writes_it(calibre, case):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"p578-{case}.epub", tmp / f"p578-{case}.kfx"
    _epub(epub, case, *CASES[case])
    _ebook_convert(calibre, epub, kfx, True)
    want = PREVIEWER if any(CASES[case]) else []
    got = [(label, at[: len(w)]) for (label, at), (_l, w) in zip(_page_list(kfx), want)]
    assert len(_page_list(kfx)) == len(want) and got == want, (
        f"calibre {version}: {_page_list(kfx)}"
    )


@pytest.mark.parametrize(
    ("relabel", "want"),
    [
        ({"2": "A8"}, []),
        ({"2": "{2}"}, []),
        ({"2": "8-9"}, []),
        ({"2": "p 2"}, []),
        # Spaces round a label are trimmed: kfxgen's choice; Previewer
        # writes none for a pretty-printed NCX's labels (QA on #612).
        ({"2": "\n  2\n"}, ["1", "2", "3", "iv"]),
        ({"iv": "IV", "3": "Xii"}, ["1", "2", "Xii", "IV"]),
    ],
    ids=["letter-digit", "braces", "range", "two-words", "spaces", "roman"],
)
def test_one_label_previewer_rejects_drops_the_page_list(calibre, relabel, want):  # noqa: F811
    """Kindle Previewer 4.0.3 writes no page list when any label is not all
    digits or all roman-numeral letters (QA's probes on #612)."""
    tmp, _env, version = calibre
    key = "-".join(f"{ord(c)}" for c in "".join(relabel.values()))[:40]
    epub, kfx = tmp / f"p578-r{key}.epub", tmp / f"p578-r{key}.kfx"
    _epub(epub, f"r{key}", False, True, relabel)
    _ebook_convert(calibre, epub, kfx, True)
    got = [label for label, _at in _page_list(kfx)]
    assert got == want, f"calibre {version}: {got}"
