"""Inline CSS through calibre's real Stylizer (#250).

Each of these is read from calibre's computed style, and every other test of
it runs without a Stylizer: strike-through and underline (#237), monospace
from a font-family rule and from calibre's own stylesheet for `<code>`
(#242), a span raised by `vertical-align: super` (#52), and the line breaks
and spaces of `white-space: pre` (#202). Each is set by a class or by
calibre's stylesheet, so the run's style comes from the Stylizer, and a
plain word in the same paragraph is the control.

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

CSS = (
    ".mono { font-family: monospace }"
    ".u { text-decoration: underline }"
    ".up { vertical-align: super }"
    ".pre { white-space: pre }"
)
BODY = (
    "<h1>Chapter</h1>"
    "<p>PLAINWORD <code>CODEWORD</code> <span class='mono'>MONOWORD</span></p>"
    "<p>PLAINWORD <del>STRUCKWORD</del> <span class='u'>UNDERWORD</span></p>"
    "<p>PLAINWORD x<span class='up'>RAISEDWORD</span></p>"
    "<p class='pre'>PRE ONE\n  PRE TWO</p>"
)


def _build_epub(path):
    page = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title>'
        '<link rel="stylesheet" type="text/css" href="s.css"/></head>'
        f"<body>{BODY}</body></html>"
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head><meta name="dtb:uid" content="t"/></head>'
        "<docTitle><text>Inline CSS</text></docTitle><navMap>"
        '<navPoint id="p1" playOrder="1"><navLabel><text>Chapter</text></navLabel>'
        '<content src="ch.xhtml"/></navPoint></navMap></ncx>'
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
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">t'
            "</dc:identifier><dc:title>Inline CSS</dc:title><dc:language>en"
            "</dc:language></metadata><manifest>"
            '<item id="ch" href="ch.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="css" href="s.css" media-type="text/css"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="ch"/></spine></package>',
        )
        z.writestr("ch.xhtml", page)
        z.writestr("s.css", CSS)
        z.writestr("toc.ncx", ncx)


@pytest.fixture(scope="module")
def book(calibre):  # noqa: F811
    """(paragraph texts, {word: its run's style}) of the converted book."""
    tmp, _env, _version = calibre
    epub, kfx = tmp / "inline.epub", tmp / "inline.kfx"
    _build_epub(epub)
    _ebook_convert(calibre, epub, kfx, native=True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    paras, runs = [], {}
    for story in by_type(frags, "$259"):
        for entry in iter_entries(val(story)["$146"]):
            ref = entry.get("$145")
            if not ref:
                continue
            text = str(texts[str(ref["name"])][ref["$403"]])
            paras.append(text)
            for r in entry.get("$142") or []:
                word = text[r["$143"] : r["$143"] + r["$144"]]
                runs[word] = styles.get(str(r.get("$157")), {})
    return paras, runs


def test_code_and_a_monospace_rule_are_monospace(book):
    _, runs = book
    for word in ("CODEWORD", "MONOWORD"):
        assert "monospace" in str(runs[word].get("$11")), word
    assert "PLAINWORD" not in runs


def test_struck_and_underlined_runs_carry_their_lines(book):
    _, runs = book
    assert str(runs["STRUCKWORD"].get("$27")) == "$328"
    assert str(runs["UNDERWORD"].get("$23")) == "$328"


def test_a_super_span_is_raised(book):
    _, runs = book
    assert str(runs["RAISEDWORD"].get("$44")) == "$370"


def test_preformatted_text_keeps_its_lines_and_spaces(book):
    paras, _ = book
    (pre,) = [p for p in paras if p.startswith("PRE ONE")]
    first, second = pre.split("\n")
    assert first == "PRE ONE"
    assert second.replace(" ", " ") == "  PRE TWO"
