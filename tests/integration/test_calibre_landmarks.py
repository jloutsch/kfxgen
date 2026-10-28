"""The book's contents and start landmarks (#579), as Kindle Previewer 4.0.3
writes them (probed 2026-10-28): a `toc` landmark (`$212`) from the OPF
guide's `toc`, a start landmark (`$396`) from its `text`; in an EPUB 3 book
with a landmarks nav, its `toc` and `bodymatter` instead, the nav winning
over the guide. Each lands where its link points, even on a page that isn't
a contents page. kfxgen wrote only the cover's (#460).

calibre passes on neither the landmarks nav nor a guide the nav overrides,
so kfxgen reads both from the book's own files.

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

CHAPS = [
    ("c1.xhtml", "Chapter One"),
    ("c2.xhtml", "Chapter Two"),
    ("c3.xhtml", "Chapter Three"),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
        'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>{title}</title></head><body>{body}</body></html>"
    )


def _epub(path, key, guide="", landmarks=""):
    files = {
        "contents.xhtml": _page(
            "Contents",
            "<h1>Contents</h1>"
            + "".join(f'<p><a href="{f}">{t}</a></p>' for f, t in CHAPS),
        ),
        "other.xhtml": _page("Other", "<h1>Other page</h1><p>Not the contents.</p>"),
    }
    for f, t in CHAPS:
        files[f] = _page(t, f"<h1>{t}</h1>" + f"<p>{t} words.</p>" * 20)
    spine = list(files)
    epub3 = bool(landmarks)
    items = "".join(
        f'<item id="i{n}" href="{f}" media-type="application/xhtml+xml"/>'
        for n, f in enumerate(spine)
    )
    if epub3:
        files["nav.xhtml"] = _page(
            "Nav",
            '<nav epub:type="toc"><ol>'
            + "".join(f'<li><a href="{f}">{t}</a></li>' for f, t in CHAPS)
            + f'</ol></nav><nav epub:type="landmarks"><ol>{landmarks}</ol></nav>',
        )
        items += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    opf = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        f'version="{"3.0" if epub3 else "2.0"}" unique-identifier="id"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>L579 {key}</dc:title><dc:identifier id="id">l579-{key}</dc:identifier>'
        "<dc:language>en</dc:language>"
        + (
            '<meta property="dcterms:modified">2026-10-28T00:00:00Z</meta>'
            if epub3
            else ""
        )
        + f'</metadata><manifest>{items}<item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">'
        + "".join(f'<itemref idref="i{n}"/>' for n in range(len(spine)))
        + "</spine>"
        + (f"<guide>{guide}</guide>" if guide else "")
        + "</package>"
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>L</text></docTitle><navMap>'
        + "".join(
            f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>{t}</text></navLabel>'
            f'<content src="{f}"/></navPoint>'
            for n, (f, t) in enumerate(CHAPS, 1)
        )
        + "</navMap></ncx>"
    )
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
        z.writestr("toc.ncx", ncx)
        for name, doc in files.items():
            z.writestr(name, doc)


def _ref(kind, href, title="Contents"):
    return f'<reference type="{kind}" title="{title}" href="{href}"/>'


def _lm(kind, href, label):
    return f'<li><a epub:type="{kind}" href="{href}">{label}</a></li>'


#: (guide, landmarks nav) -> Previewer's landmarks: (type, label, the first
#: words where it lands).
CASES = {
    "guide-toc": (
        _ref("toc", "contents.xhtml"),
        "",
        [("$212", "Contents", "Contents")],
    ),
    "none": ("", "", []),
    "nav": (
        "",
        _lm("toc", "contents.xhtml", "Contents")
        + _lm("bodymatter", "c2.xhtml", "Start"),
        [("$212", "Contents", "Contents"), ("$396", "Start", "Chapter Two")],
    ),
    "nav-over-guide": (
        _ref("toc", "other.xhtml"),
        _lm("toc", "contents.xhtml", "Contents")
        + _lm("bodymatter", "c2.xhtml", "Start"),
        [("$212", "Contents", "Contents"), ("$396", "Start", "Chapter Two")],
    ),
    "guide-toc-elsewhere": (
        _ref("toc", "other.xhtml"),
        "",
        [("$212", "Contents", "Other page")],
    ),
    "guide-text": (
        _ref("text", "c2.xhtml", "Begin"),
        "",
        [("$396", "Begin", "Chapter Two")],
    ),
    # Repeated (#609, probed 2026-10-28): the last contents landmark wins,
    # the first start; each where its own entry is.
    "two-guide-toc": (
        _ref("toc", "contents.xhtml") + _ref("toc", "other.xhtml"),
        "",
        [("$212", "Contents", "Other page")],
    ),
    "two-guide-text": (
        _ref("text", "c1.xhtml", "Begin") + _ref("text", "c3.xhtml", "Later"),
        "",
        [("$396", "Begin", "Chapter One")],
    ),
    "two-nav-toc": (
        "",
        _lm("toc", "contents.xhtml", "Contents") + _lm("toc", "other.xhtml", "Other"),
        [("$212", "Other", "Other page")],
    ),
    "two-nav-body": (
        "",
        _lm("bodymatter", "c1.xhtml", "Start") + _lm("bodymatter", "c3.xhtml", "Later"),
        [("$396", "Start", "Chapter One")],
    ),
    "toc-text-toc": (
        _ref("toc", "contents.xhtml")
        + _ref("text", "c2.xhtml", "Begin")
        + _ref("toc", "other.xhtml", "Again"),
        "",
        [("$396", "Begin", "Chapter Two"), ("$212", "Again", "Other page")],
    ),
    # QA's L14 on #620: the first start that lands is kept.
    "first-start-missing": (
        _ref("text", "gone.xhtml", "A") + _ref("text", "c2.xhtml", "B"),
        "",
        [("$396", "B", "Chapter Two")],
    ),
    "nav-toc-only": (
        "",
        _lm("toc", "c3.xhtml", "Contents"),
        [("$212", "Contents", "Chapter Three")],
    ),
}


def _landmarks(kfx):
    frags = load_fragments(kfx)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    first = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                words = str(text[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                first[int(e["$155"])] = words
    got = []
    for f in by_type(frags, "$389"):
        for nav in val(f):
            for c in nav.get("$392") or ():
                c = getattr(c, "value", c)
                if str(c.get("$235")) != "$236":
                    continue
                for e in c.get("$247") or ():
                    e = getattr(e, "value", e)
                    t = getattr(e["$246"], "value", e["$246"])
                    got.append(
                        (
                            str(e["$238"]),
                            str(e["$241"]["$244"]),
                            first.get(int(t["$155"])),
                        )
                    )
    return got


@pytest.mark.parametrize("case", list(CASES))
def test_landmarks_as_previewer_writes_them(calibre, case):  # noqa: F811
    tmp, _env, version = calibre
    guide, nav, want = CASES[case]
    epub, kfx = tmp / f"l579-{case}.epub", tmp / f"l579-{case}.kfx"
    _epub(epub, case, guide, nav)
    _ebook_convert(calibre, epub, kfx, True)
    got = [
        (t, label, (words or "")[: len(w)])
        for (t, label, words), (_t, _l, w) in zip(_landmarks(kfx), want)
    ]
    assert len(_landmarks(kfx)) == len(want) and got == want, (
        f"calibre {version}: {_landmarks(kfx)}"
    )
