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
span.cltr { direction: ltr; }
span.iso { unicode-bidi: isolate; }
span.emb, bdo.emb { direction: ltr; unicode-bidi: embed; }
span.plain { unicode-bidi: plaintext; }
span.norm { unicode-bidi: normal; }
span.over { direction: rtl; unicode-bidi: bidi-override; }
span.isoov { direction: ltr; unicode-bidi: isolate-override; }
div.css-rtl { direction: rtl; }
div.box { border: 1px solid black; padding: 0.5em; }
td.crtl { direction: rtl; }
td.cltr { direction: ltr; }
table.trtl { direction: rtl; }
p.box, blockquote.box { border: 1px solid black; padding: 0.5em; }
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
        # #710's I-probe: CSS direction and unicode-bidi on a span.
        f'<p>I06 {AR} <span class="cltr">CSSLTR words</span> {AR}</p>',
        f'<p>I07 {AR} <span class="iso">CSSISO words</span> {AR}</p>',
        f'<p>I08 {AR} <span class="emb">CSSEMB words</span> {AR}</p>',
        f'<p>I17 {AR} <span dir="ltr" class="plain">DIRPLAIN words</span> {AR}</p>',
        f'<p>I18 {AR} <span class="norm">CSSNORM words</span> {AR}</p>',
        f'<p>I19 {AR} <span class="over">CSSOVER words</span> {AR}</p>',
        f'<p>I20 {AR} <span class="isoov">CSSISOOV words</span> {AR}</p>',
        f'<p>K03 {AR} <span dir="rtl" class="cltr">DIRVSCSS words</span> {AR}</p>',
        f'<p>K05 {AR} <bdo dir="rtl" class="emb">BDOCSS words</bdo> {AR}</p>',
        f"<p>I01 {AR} <span dir='ltr'>LTRSPAN words</span> {AR}</p>",
        # A size equal to the paragraph's writes no run of its own (#375);
        # its direction still does.
        f"<p>I40 {AR} <span dir='ltr' style='font-size:100%'>SIZEDIR words</span> {AR}</p>",
        # K42: a paragraph wholly in a sized span takes its size (#375); the
        # span's direction stays a run.
        "<p><span dir='ltr' style='font-size:120%'>I41 WHOLESIZED words</span></p>",
        # #605, P605c E05-E08: a cell's CSS direction equal to its table's
        # is written, without an isolate; a paragraph's own dir equal to its
        # box's is written with one.
        f'<table><tr><td class="crtl">E05 {AR}</td><td>E08 {AR}</td></tr></table>',
        f'<div class="box"><p dir="rtl">E06 {AR}</p><p>E07 {AR}</p></div>',
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
        # #605, P605 T06, T07: CSS direction on a table, and on a cell in a
        # right-to-left table.
        '<table class="trtl"><tr><td>T06 words</td></tr></table>',
        '<table dir="rtl"><tr><td class="cltr">T07 words</td></tr></table>',
        # QA's P735b on #735: a box's own text, straight in it, with the
        # box's own lang or dir.
        '<div class="box" lang="fr">F01 words</div>',
        f'<div class="box" dir="rtl">F02 {AR}</div>',
        '<p class="box" lang="fr">F03 words</p>',
        f'<p class="box" dir="rtl">F04 {AR}</p>',
        # QA's P735b F03-F15: text loose in a box beside a block, a quote, an
        # inline only, dir="auto", a box in a box.
        '<div class="box" lang="fr">F05 loose text<p>F06 para.</p></div>',
        '<div class="box" lang="fr"><p>F07 para.</p>F08 tail text.</div>',
        '<blockquote class="box" lang="fr">F09 direct text</blockquote>',
        '<div class="box" lang="fr"><span>F10 inline only.</span></div>',
        '<div class="box" dir="auto">F11 latin direct text</div>',
        '<div class="box" lang="fr"><div class="box" lang="fr">F12 text</div></div>',
        # QA's Q72-Q75: the same, in cells of two paragraphs.
        '<table dir="rtl"><tr>'
        f'<td class="start"><p>Q72 {AR} first</p><p>Q73 {AR} second</p></td>'
        f'<td class="end"><p>Q74 {AR} first</p><p>Q75 {AR} second</p></td>'
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


def test_a_cell_of_paragraphs_flips_its_own_start_and_end(entries):
    # QA's Q72/Q74: the paragraphs are right/left, as Previewer's. The cell's
    # container has no $34 in Previewer's file; where kfxgen writes one it
    # must agree with its paragraphs.
    for label, side in (("Q72", "$61"), ("Q74", "$59")):
        own, above = entries[label]
        assert own.get("$34") == side
        assert above[-1].get("$34") in (None, side)


def test_a_box_carries_the_keys_and_its_paragraph_none(entries):
    own, above = entries["Q15"]
    box = next(k for k in above if k.get("$159") == "$270")
    assert _bidi(box) == {"$10": "ar", "$682": "$375", "$674": "$676"}
    assert _bidi(own) == {}


def test_a_direction_set_on_the_element_is_written_again(entries):
    # #605, Previewer 4.0.3 (P605c E05-E08): the same as the container's.
    assert _bidi(entries["E05"][0]) == {"$682": "$375"}
    assert _bidi(entries["E08"][0]) == {}
    assert _bidi(entries["E06"][0]) == {"$682": "$375", "$674": "$676"}
    assert _bidi(entries["E07"][0]) == {}


@pytest.mark.parametrize(
    ("label", "box"),
    [
        ("F01", {"$10": "fr"}),
        ("F02", {"$10": "en", "$682": "$375", "$674": "$676"}),
        ("F03", {"$10": "fr"}),
        ("F04", {"$10": "en", "$682": "$375", "$674": "$676"}),
        ("F05", {"$10": "fr"}),
        ("F06", {"$10": "fr"}),
        ("F07", {"$10": "fr"}),
        ("F08", {"$10": "fr"}),
        ("F09", {"$10": "fr"}),
        ("F10", {"$10": "fr"}),
        ("F11", {"$10": "en", "$682": "$376", "$674": "$676"}),
        ("F12", {"$10": "fr"}),
    ],
)
def test_a_boxs_own_text_leaves_the_boxs_keys_on_the_box(entries, label, box):
    # QA's P735b on #735, Previewer 4.0.3: the box carries them, its text
    # entry nothing.
    own, above = entries[label]
    boxes = [k for k in above if k.get("$159") == "$270"]
    # F12: each box carries its own lang.
    assert all(_bidi(b) == box for b in boxes) and boxes
    assert _bidi(own) == {}


def test_css_direction_on_a_table_and_on_a_cell(entries):
    # #605, P605 T06, T07: `$682` alone, no isolate.
    own, above = entries["T06"]
    table = next(k for k in above if k.get("$159") == "$278")
    assert _bidi(table) == {"$10": "en", "$682": "$375"}
    assert _bidi(own) == {}
    assert _bidi(entries["T07"][0]) == {"$682": "$376"}
    # No alignment in either: Previewer writes none (P605 T06, T07).
    assert _align(entries["T06"]) is None
    assert _align(entries["T07"]) is None


def test_the_spine_turns_the_pages_right_to_left(frags):
    assert str(val(by_type(frags, "$538")[0])["$192"]) == "$375"


def test_an_arabic_book_declares_ar_reflow_language(frags):
    feats = [
        str(getattr(x, "value", x)["$492"])
        for x in val(by_type(frags, "$585")[0])["$590"]
    ]
    assert "ar-reflow-language" in feats


@pytest.fixture(scope="module")
def runs(frags):
    """{label: [(run text, its $10/$682/$674)]} for runs with any of them."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}

    def visit(nodes):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            ref = e.get("$145")
            if hasattr(ref, "get"):
                text = str(texts[str(ref["name"])][int(ref["$403"])])
                m = LAB.search(text)
                if m:
                    out = []
                    for r in e.get("$142") or ():
                        r = getattr(r, "value", r)
                        st = dict(styles.get(str(r.get("$157")), {}))
                        st.update(r)
                        keys = _bidi(_keys(st))
                        if keys:
                            a, n = int(st["$143"]), int(st["$144"])
                            out.append((text[a : a + n], keys))
                    found[m.group(0)] = out
            visit(e.get("$146") or ())

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"])
    return found


@pytest.mark.parametrize(
    ("label", "text", "expected"),
    [
        # Previewer 4.0.3, #710's probes: CSS direction alone gives no $674
        # (I06); unicode-bidi alone no $682 (I07); CSS beats the dir on the
        # same span (I17, K03) and on a <bdo> (K30); normal is written (I18).
        ("I06", "CSSLTR words", {"$682": "$376"}),
        ("I07", "CSSISO words", {"$674": "$676"}),
        ("I08", "CSSEMB words", {"$682": "$376", "$674": "$675"}),
        ("I17", "DIRPLAIN words", {"$682": "$376", "$674": "$679"}),
        ("I18", "CSSNORM words", {"$674": "$350"}),
        ("I19", "CSSOVER words", {"$682": "$375", "$674": "$677"}),
        ("I20", "CSSISOOV words", {"$682": "$376", "$674": "$678"}),
        ("K03", "DIRVSCSS words", {"$682": "$376", "$674": "$676"}),
        ("K05", "BDOCSS words", {"$682": "$376", "$674": "$675"}),
        ("I01", "LTRSPAN words", {"$682": "$376", "$674": "$676"}),
        ("I40", "SIZEDIR words", {"$682": "$376", "$674": "$676"}),
        ("I41", "I41 WHOLESIZED words", {"$682": "$376", "$674": "$676"}),
    ],
)
def test_an_inline_elements_css_direction(runs, label, text, expected):
    assert runs[label] == [(text, expected)]
