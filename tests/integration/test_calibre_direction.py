"""Right-to-left books through calibre's real Stylizer (#246).

The unit tests (tests/unit/test_direction.py) run through the test shim, which
reads no CSS. CSS `direction` and `unicode-bidi`, bordered boxes and
`text-align: start/end` all depend on calibre's computed style, so this
converts one book with the real `ebook-convert` and reads each labelled
paragraph's `$157` back out of the KFX.

Expected values are Kindle Previewer 4.0.3's, decoded from its own output of
the same markup in the #246 probes (my probe 1 and QA's Q246 and Q246-prec
probes, 2026-11-03). calibre 9.17's UA sheet (templates/html.css) sets no
`direction` or `unicode-bidi`, so what the Stylizer reports for them is the
book's own CSS.

Needs calibre: `slow`, skipped when `ebook-convert` is not found, and not run
in CI.
"""

import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_list_markers import (  # noqa: E402
    CALIBRE_CUSTOMIZE,
    EBOOK_CONVERT,
    _build_plugin,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

AR = "مرحبا بالعالم"
LAB = re.compile(r"\b[A-Z]\d\d\b")

CSS = """
p { margin: 0.5em 0; }
p.ltr { direction: ltr; }
p.rtl { direction: rtl; }
p.embed { unicode-bidi: embed; }
p.plain { unicode-bidi: plaintext; }
p.over { unicode-bidi: bidi-override; }
p.iso-over { unicode-bidi: isolate-override; }
p.start, td.start, th.start { text-align: start; }
p.end, td.end { text-align: end; }
div.over { unicode-bidi: bidi-override; }
div.plain { unicode-bidi: plaintext; }
p.norm, div.norm { unicode-bidi: normal; }
div.css-rtl { direction: rtl; }
div.box { border: 1px solid black; padding: 0.5em; }
"""

# Page 1: <html dir="rtl" lang="ar">.
RTL_PAGE = "".join(
    [
        f"<p>A01 {AR}</p>",
        f'<p class="ltr">A08 {AR} css ltr</p>',
        f'<p class="embed">U01 {AR} embed</p>',
        f'<p class="plain">U02 {AR} plaintext</p>',
        f'<p class="over">U03 {AR} override</p>',
        f'<p class="iso-over">U04 {AR} isolate-override</p>',
        f'<p class="start">A04 {AR} start</p>',
        f'<p class="end">A05 {AR} end</p>',
        f'<div class="box"><p>Q15 {AR} boxed</p></div>',
        # Q96/Q97 (Previewer probe W246): an explicit normal is written, and
        # is the nearest unicode-bidi, as a dir's isolate is.
        f'<p class="norm">Q96 {AR} normal</p>',
        f'<div class="norm"><p>Q97 {AR} in div normal</p></div>',
        f"<p>A99 {AR} last</p>",
    ]
)
# Page 2: no dir, lang="en".
LTR_PAGE = "".join(
    [
        "<p>B01 English words</p>",
        f'<p class="rtl">B03 {AR} css rtl</p>',
        '<div class="css-rtl"><p dir="ltr">Q50 words</p></div>',
        f'<div dir="rtl"><p class="ltr">Q51 {AR}</p></div>',
        f'<p dir="rtl" class="ltr">Q52 {AR}</p>',
        f'<p dir="ltr" class="rtl">Q53 {AR}</p>',
        '<div class="css-rtl"><div dir="ltr"><p>Q54 words</p></div></div>',
        '<p class="start">B08 start</p>',
        # QA's Q67/Q68: unicode-bidi from an ancestor's CSS reaches the paragraph.
        '<div class="css-rtl over"><p>Q67 English in div bidi-override</p></div>',
        f'<div class="plain"><p>Q68 {AR} in div plaintext</p></div>',
        # Q82/Q91 (Previewer probes V246, W246): plaintext keeps a direction.
        f'<p dir="rtl" class="plain">Q82 {AR} own dir plaintext</p>',
        '<div dir="ltr"><p class="plain">Q91 English under div ltr</p></div>',
        # QA's Q60/Q61: a cell's own start/end in a right-to-left table.
        '<table dir="rtl"><tr>'
        f'<td class="start">Q60 {AR} td start</td>'
        f'<td class="end">Q61 {AR} td end</td>'
        f'<th class="start">Q65 {AR} th start</th>'
        "</tr></table>",
    ]
)


def _page(body, html_attrs):
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{html_attrs}><head><title>t</title>'
        '<link rel="stylesheet" type="text/css" href="s.css"/></head>'
        f"<body>{body}</body></html>"
    )


def _build_epub(path):
    pages = {
        "c1.xhtml": _page(RTL_PAGE, ' dir="rtl" lang="ar" xml:lang="ar"'),
        "c2.xhtml": _page(LTR_PAGE, ' lang="en" xml:lang="en"'),
    }
    items = "".join(
        f'<item id="x{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in enumerate(pages)
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="u">dir-calibre</dc:identifier><dc:title>Direction</dc:title>'
        "<dc:creator>A</dc:creator><dc:language>ar</dc:language>"
        '<meta property="dcterms:modified">2026-11-03T00:00:00Z</meta></metadata>'
        f'<manifest>{items}<item id="css" href="s.css" media-type="text/css"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
        '<spine toc="ncx" page-progression-direction="rtl">'
        + "".join(f'<itemref idref="x{i}"/>' for i in range(len(pages)))
        + "</spine></package>"
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>t</text></docTitle><navMap>'
        '<navPoint id="n1" playOrder="1"><navLabel><text>One</text></navLabel>'
        '<content src="c1.xhtml"/></navPoint>'
        '<navPoint id="n2" playOrder="2"><navLabel><text>Two</text></navLabel>'
        '<content src="c2.xhtml"/></navPoint></navMap></ncx>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        z.writestr("s.css", CSS)
        for h, b in pages.items():
            z.writestr(h, b)


@pytest.fixture(scope="module")
def frags(tmp_path_factory):
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        pytest.skip("calibre not found (ebook-convert / calibre-customize)")
    tmp = tmp_path_factory.mktemp("calibre_direction")
    config = tmp / "calibre-config"
    config.mkdir()
    env = dict(os.environ, CALIBRE_CONFIG_DIRECTORY=str(config))
    plugin = tmp / "kfxgen.zip"
    _build_plugin(plugin)
    subprocess.run(
        [CALIBRE_CUSTOMIZE, "-a", str(plugin)], env=env, check=True, capture_output=True
    )
    epub, kfx = tmp / "dir.epub", tmp / "dir.kfx"
    _build_epub(epub)
    # calibre's EPUB input unpacks into the working directory.
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx)],
        env=env,
        cwd=tmp,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr[-2000:]
    return load_fragments(kfx)


def _keys(style):
    return {
        k: str(style[k]) for k in ("$10", "$682", "$674", "$34", "$159") if k in style
    }


@pytest.fixture(scope="module")
def entries(frags):
    """{label: (own keys, [(container type, its keys), ...] outermost first)}."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}

    def visit(nodes, above):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            s = dict(styles.get(str(e.get("$157")), {}))
            s.update(e)
            ref = e.get("$145")
            if hasattr(ref, "get"):
                m = LAB.search(str(texts[str(ref["name"])][int(ref["$403"])]))
                if m:
                    found[m.group(0)] = (_keys(s), above)
            visit(e.get("$146") or (), [*above, _keys(s)])

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"], [])
    return found


def _bidi(keys):
    return {k: v for k, v in keys.items() if k in ("$10", "$682", "$674")}


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        # Page dir rtl, nothing on the paragraph.
        ("A01", {"$10": "ar", "$682": "$375", "$674": "$676"}),
        # A08: CSS direction on the paragraph beats the page's dir; the
        # isolate from that dir stays.
        ("A08", {"$10": "ar", "$682": "$376", "$674": "$676"}),
        # U01-U04 (QA's Q17-Q19): the paragraph's own unicode-bidi. plaintext
        # keeps the page's direction (Previewer probe W246, Q90).
        ("U01", {"$10": "ar", "$682": "$375", "$674": "$675"}),
        ("U02", {"$10": "ar", "$682": "$375", "$674": "$679"}),
        ("U03", {"$10": "ar", "$682": "$375", "$674": "$677"}),
        ("U04", {"$10": "ar", "$682": "$375", "$674": "$678"}),
        ("Q96", {"$10": "ar", "$682": "$375", "$674": "$350"}),
        ("Q97", {"$10": "ar", "$682": "$375", "$674": "$350"}),
        # Page 2, no dir: plain, and CSS direction alone (no $674).
        ("B01", {"$10": "en"}),
        ("B03", {"$10": "en", "$682": "$375"}),
        # Q50-Q54: the cascade, with dir a declaration on its own element.
        ("Q50", {"$10": "en", "$682": "$376", "$674": "$676"}),
        ("Q51", {"$10": "en", "$682": "$376", "$674": "$676"}),
        ("Q52", {"$10": "en", "$682": "$376", "$674": "$676"}),
        ("Q53", {"$10": "en", "$682": "$375", "$674": "$676"}),
        ("Q54", {"$10": "en", "$682": "$376", "$674": "$676"}),
        # Q67/Q68: the nearest unicode-bidi, the paragraph's or an ancestor's.
        ("Q67", {"$10": "en", "$682": "$375", "$674": "$677"}),
        # Q68: no direction anywhere above it, so no $682.
        ("Q68", {"$10": "en", "$674": "$679"}),
        ("Q82", {"$10": "en", "$682": "$375", "$674": "$679"}),
        ("Q91", {"$10": "en", "$682": "$376", "$674": "$679"}),
    ],
)
def test_direction_as_previewer_writes_it(entries, label, expected):
    assert _bidi(entries[label][0]) == expected


def test_text_align_start_and_end_follow_the_paragraphs_direction(entries):
    assert entries["A04"][0].get("$34") == "$61"
    assert entries["A05"][0].get("$34") == "$59"
    # B08: start with no dir is left.
    assert entries["B08"][0].get("$34") == "$59"


def _align(entry):
    """The alignment that applies: the entry's own, else its nearest
    container's."""
    own, above = entry
    for keys in (own, *reversed(above)):
        if "$34" in keys:
            return keys["$34"]
    return None


def test_a_cells_own_start_and_end_follow_the_tables_direction(entries):
    # Q60/Q61, Previewer 4.0.3: start is right and end left in a
    # right-to-left table (#515).
    assert _align(entries["Q60"]) == "$61"
    assert _align(entries["Q61"]) == "$59"
    # A header cell's start stays centred (#616).
    assert _align(entries["Q65"]) == "$320"


def test_a_box_carries_the_keys_and_its_paragraph_none(entries):
    own, above = entries["Q15"]
    box = next(k for k in above if k.get("$159") == "$270")
    assert _bidi(box) == {"$10": "ar", "$682": "$375", "$674": "$676"}
    assert _bidi(own) == {}


def test_the_spine_turns_the_pages_right_to_left(frags):
    assert str(val(by_type(frags, "$538")[0])["$192"]) == "$375"


def test_an_arabic_book_declares_ar_reflow_language(frags):
    feats = [
        str(getattr(x, "value", x)["$492"])
        for x in val(by_type(frags, "$585")[0])["$590"]
    ]
    assert "ar-reflow-language" in feats
