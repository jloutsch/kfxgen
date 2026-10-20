"""Ruby annotations, through calibre (#236).

calibre's stylesheet and its EPUB input reach `<ruby>`, `<rt>` and `<rp>`
before kfxgen does. This converts the #236 probe's shapes with calibre: a
reading per character, one per base, `<rp>`, `<rb>`, text around a ruby,
emphasis over the base, a ruby in a heading and in a table cell, and an
`<rtc>`. Each paragraph's text is its base alone, and each ruby's event
names its reading, as Kindle Previewer 4 writes them.

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
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

XHTML = (
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ja" lang="ja"><head>'
    "<title>{0}</title></head><body>{1}</body></html>"
)

#: (paragraph text kfxgen writes, its markup, [(start, length, reading)]).
CASES = [
    (
        "R1 漢字",
        "R1 <ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>",
        [(3, 1, "かん"), (4, 1, "じ")],
    ),
    ("R2 漢字", "R2 <ruby>漢字<rt>かんじ</rt></ruby>", [(3, 2, "かんじ")]),
    (
        "R3 漢字",
        "R3 <ruby>漢字<rp>(</rp><rt>かんじ</rt><rp>)</rp></ruby>",
        [(3, 2, "かんじ")],
    ),
    (
        "R4 東京",
        "R4 <ruby><rb>東京</rb><rt>とうきょう</rt></ruby>",
        [(3, 2, "とうきょう")],
    ),
    (
        "R5 Before 北京 after.",
        "R5 Before <ruby>北京<rt>Běijīng</rt></ruby> after.",
        [(10, 2, "Běijīng")],
    ),
    ("R6 太字", "R6 <b><ruby>太<rt>ふと</rt></ruby>字</b>", [(3, 1, "ふと")]),
    ("R7 漢字かんじ", "R7 <ruby>漢字<rtc><rt>かんじ</rt></rtc></ruby>", []),
]


def _build(path):
    body = "<h1>Ruby</h1>" + "".join(f"<p>{html}</p>" for _, html, _ in CASES)
    body += "<h2><ruby>見出<rt>みだ</rt></ruby>し</h2>"
    body += "<table><tr><td><ruby>表<rt>ひょう</rt></ruby></td><td>x</td></tr></table>"
    files = {
        "nav.xhtml": XHTML.format(
            "Nav",
            '<nav epub:type="toc"><ol><li><a href="c1.xhtml">Ruby</a></li></ol></nav>',
        ),
        "c1.xhtml": XHTML.format("Ruby", body),
    }
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"'
        + (' properties="nav"' if n == "nav.xhtml" else "")
        + "/>"
        for i, n in enumerate(files)
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">ruby'
        "</dc:identifier><dc:title>Ruby</dc:title><dc:language>ja</dc:language>"
        '<meta property="dcterms:modified">2026-10-19T00:00:00Z</meta></metadata>'
        f'<manifest>{items}</manifest><spine><itemref idref="i1"/></spine></package>'
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
        for name, data in files.items():
            z.writestr(name, data)


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{paragraph text: [(start, length, reading)]}, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "ruby.epub", tmp / "ruby.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    readings = {
        (str(f.fid), int(r["$758"])): str(r["$145"])
        for f in by_type(frags, "$756")
        for r in val(f)["$146"]
    }
    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not e.get("$145"):
                continue
            text = str(texts[str(e["$145"]["name"])][e["$145"]["$403"]])
            out[text] = [
                (ev["$143"], ev["$144"], readings[str(ev["$757"]), int(ev["$758"])])
                for ev in e.get("$142") or []
                if "$757" in ev
            ]
    return out, version


@pytest.mark.parametrize(("text", "rubies"), [(t, r) for t, _, r in CASES])
def test_a_paragraph_is_its_base_with_its_readings(converted, text, rubies):
    paragraphs, version = converted
    assert paragraphs.get(text) == rubies, f"calibre {version}: {list(paragraphs)}"


def test_a_heading_keeps_its_ruby(converted):
    paragraphs, version = converted
    assert paragraphs.get("見出し") == [(0, 2, "みだ")], f"calibre {version}"


def test_a_table_cell_keeps_its_ruby(converted):
    paragraphs, version = converted
    assert paragraphs.get("表") == [(0, 1, "ひょう")], f"calibre {version}"
