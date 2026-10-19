"""Lists of illustrations and tables, through calibre (#392).

calibre reads an EPUB 3 navigation document itself, and its `<nav>` lists
reach kfxgen however calibre leaves them. This converts the #392 probe's
shapes with calibre: a list of illustrations and one of tables in the
navigation document (in the spine, after its contents listing), one in a
chapter, one nested in a `<section>`, one hidden, and one on a page with a
TOC entry of its own. Each visible list prints its heading and one linked
paragraph per entry, and each link reaches its anchor.

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
    'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{0}</title></head>'
    "<body>{1}</body></html>"
)


def _list(kind, tag, attrs=""):
    items = "".join(
        f'<li><a href="c1.xhtml#{kind}{i}">{tag} item {i}</a></li>' for i in (1, 2)
    )
    return (
        f'<nav epub:type="{kind}"{attrs}><h2>{tag} heading</h2><ol>{items}</ol></nav>'
    )


def _build(path):
    files = {
        "nav.xhtml": XHTML.format(
            "Nav",
            '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>'
            '<li><a href="c1.xhtml">Chapter One</a></li>'
            '<li><a href="loi.xhtml">Illustrations</a></li></ol></nav>'
            + _list("loi", "NI")
            + _list("lot", "NT"),
        ),
        "c1.xhtml": XHTML.format(
            "1",
            "<h1>Chapter One</h1><p>Prose.</p>"
            '<div id="loi1"><p>Figure one.</p></div><div id="loi2"><p>Figure two.</p></div>'
            '<div id="lot1"><p>Table one.</p></div><div id="lot2"><p>Table two.</p></div>'
            + _list("loi", "BI")
            + "<section>"
            + _list("lot", "ST")
            + "</section>"
            + _list("loi", "HI", ' hidden=""'),
        ),
        "loi.xhtml": XHTML.format("Illustrations", _list("loi", "PI")),
    }
    items = "".join(
        f'<item id="i{i}" href="{n}" media-type="application/xhtml+xml"'
        + (' properties="nav"' if n == "nav.xhtml" else "")
        + "/>"
        for i, n in enumerate(files)
    )
    spine = "".join(f'<itemref idref="i{i}"/>' for i in range(len(files)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="u"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">lists'
        "</dc:identifier><dc:title>Lists</dc:title><dc:language>en</dc:language>"
        '<meta property="dcterms:modified">2026-10-19T00:00:00Z</meta></metadata>'
        f"<manifest>{items}</manifest><spine>{spine}</spine></package>"
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
    """[(text, its link targets)] in reading order, the anchors' names, and
    calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "lists.epub", tmp / "lists.kfx"
    _build(epub)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not e.get("$145"):
                continue
            text = str(texts[str(e["$145"]["name"])][e["$145"]["$403"]])
            targets = [
                str(r.get("$179") or styles.get(str(r.get("$157")), {}).get("$179"))
                for r in e.get("$142") or []
                if r.get("$179") or styles.get(str(r.get("$157")), {}).get("$179")
            ]
            out.append((text, targets))
    anchors = {str(f.fid) for f in by_type(frags, "$266")}
    return out, anchors, version


@pytest.mark.parametrize("tag", ["NI", "NT", "BI", "ST", "PI"])
def test_a_list_prints_its_heading_and_linked_entries(converted, tag):
    entries, anchors, version = converted
    texts = [t for t, _ in entries]
    assert f"{tag} heading" in texts, f"calibre {version}: {texts}"
    for i in (1, 2):
        (targets,) = [g for t, g in entries if t.endswith(f"{tag} item {i}")]
        assert targets and all(g in anchors for g in targets), f"calibre {version}"


def test_a_hidden_list_is_dropped(converted):
    entries, _anchors, version = converted
    assert not any("HI " in t for t, _ in entries), f"calibre {version}"
