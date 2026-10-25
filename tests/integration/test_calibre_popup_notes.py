"""Pop-up footnote marks through calibre, against what Kindle Previewer
writes for the same shapes (#445).

Previewer 4.0.3, run on this file's book, marks a link with
`epub:type="noteref"` whose target is, or sits inside, an
`epub:type="footnote"` element: `$616: $617` on the link's span and
`$615: $281` on the entry the link lands on, in the same file or another.
Endnotes, `role` notes, a plain link to a footnote and a noteref to a plain
paragraph are left as ordinary links. `EXPECTED` is its output, decoded.

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

X = (
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{0}</title></head>'
    "<body>{1}</body></html>"
)
NOTEREF = '<a epub:type="noteref" href="{0}">{1}</a>'
FILES = {
    "a.xhtml": "<h1>A aside footnote</h1>"
    f"<p>A1 text{NOTEREF.format('#an1', 1)} more.</p>"
    '<aside epub:type="footnote" id="an1"><p>A2 Note one. <a href="#ar">back</a></p>'
    '</aside><p id="ar">A3 after.</p>',
    "b.xhtml": "<h1>B endnote list</h1>"
    f"<p>B1 text{NOTEREF.format('notes.xhtml#bn1', 2)} more.</p>",
    "notes.xhtml": '<h1>Notes</h1><section epub:type="endnotes"><ol>'
    '<li epub:type="endnote" id="bn1"><p>B2 Endnote two.</p></li></ol></section>',
    "c.xhtml": "<h1>C role footnote</h1>"
    '<p>C1 text<a role="doc-noteref" href="#cn1">3</a> more.</p>'
    '<div role="doc-footnote" id="cn1"><p>C2 Note three.</p></div>',
    "d.xhtml": "<h1>D plain link</h1>"
    '<p>D1 text<a href="#dn1">4</a> more.</p><p id="dn1">D2 Plain target.</p>',
    "e.xhtml": "<h1>E footnote paragraph</h1>"
    f"<p>E1 text{NOTEREF.format('#en1', 5)} more.</p>"
    '<p epub:type="footnote" id="en1">E2 Note five.</p>',
    "f.xhtml": "<h1>F endnote aside</h1>"
    f"<p>F1 text{NOTEREF.format('#fn1', 6)} more.</p>"
    '<aside epub:type="endnote" id="fn1"><p>F2 Note six.</p></aside>',
    "g.xhtml": "<h1>G footnote elsewhere</h1>"
    f"<p>G1 text{NOTEREF.format('gnotes.xhtml#gn1', 7)} more.</p>",
    "gnotes.xhtml": "<h1>G notes</h1>"
    '<aside epub:type="footnote" id="gn1"><p>G2 Note seven.</p></aside>',
    "h.xhtml": "<h1>H plain link to footnote</h1>"
    '<p>H1 text<a href="#hn1">8</a> more.</p>'
    '<aside epub:type="footnote" id="hn1"><p>H2 Note eight.</p></aside>',
    "i.xhtml": "<h1>I noteref to plain</h1>"
    f"<p>I1 text{NOTEREF.format('#in1', 9)} more.</p>"
    '<p id="in1">I2 Plain nine.</p>',
    "j.xhtml": "<h1>J two paragraphs</h1>"
    f"<p>J1 text{NOTEREF.format('#jn1', 10)} more.</p>"
    '<aside epub:type="footnote" id="jn1"><p>J2 First para.</p>'
    "<p>J3 Second para.</p></aside><p>J4 after.</p>",
    "k.xhtml": "<h1>K link into the aside</h1>"
    f"<p>K1 text{NOTEREF.format('#kp1', 11)} more.</p>"
    '<aside epub:type="footnote"><p id="kp1">K2 Inner para.</p></aside>',
}
#: Previewer 4.0.3 on these shapes: "link" = $616: $617 on the link span,
#: "note" = $615: $281 on the entry. Unlisted paragraphs have neither.
EXPECTED = {
    "A1": "link",
    "A2": "note",
    "E1": "link",
    "E2": "note",
    "G1": "link",
    "G2": "note",
    "J1": "link",
    "J2": "note",
    "K1": "link",
    "K2": "note",
}


def _build(path):
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, n in enumerate(FILES)
    )
    spine = "".join(f'<itemref idref="i{i}"/>' for i in range(len(FILES)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">popups'
        "</dc:identifier><dc:title>Pop-up notes</dc:title><dc:language>en"
        f"</dc:language></metadata><manifest>{items}"
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        f'</manifest><spine toc="ncx">{spine}</spine></package>'
    )
    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{n}</text></navLabel>'
        f'<content src="{n}"/></navPoint>'
        for i, n in enumerate(FILES, 1)
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>Pop-up notes</text></docTitle>'
        f"<navMap>{points}</navMap></ncx>"
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
        for name, body in FILES.items():
            z.writestr(name, X.format(name, body))


def test_notes_are_marked_as_previewer_marks_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "popup-notes.epub", tmp / "popup-notes.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    marks = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not (hasattr(e, "get") and e.get("$145")):
                continue
            text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            label = text[:2]
            if not (label[:1].isupper() and label[1:].isdigit()):
                continue
            linked = [sp for sp in e.get("$142") or [] if "$179" in sp]
            if "$615" in e:
                marks[label] = "note" if str(e["$615"]) == "$281" else str(e["$615"])
            elif any(str(sp.get("$616")) == "$617" for sp in linked):
                marks[label] = "link"
    assert marks == EXPECTED, f"calibre {version}"
