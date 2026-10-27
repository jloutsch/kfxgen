"""http, https and mailto links stay links, as Kindle Previewer 4.0.3 writes
them (#474, probed 2026-10-26): one $266 per address holding it in $186,
and the run's $179 naming it. kfxgen wrote them as plain text (#53 dropped
every scheme, so no internal target pointed nowhere). Other schemes stay
plain text."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _links(tmp_path, body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )
    out = tmp_path / "x.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    anchors = {str(f.fid): val(f) for f in by_type(frags, "$266")}
    got = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                for sp in e.get("$142") or []:
                    if "$179" in sp:
                        a = anchors[str(sp["$179"])]
                        word = text[int(sp["$143"]) : int(sp["$143"]) + int(sp["$144"])]
                        got.append((word, str(sp["$179"]), str(a.get("$186"))))
    uris = sorted(str(a["$186"]) for a in anchors.values() if "$186" in a)
    return got, uris


@pytest.mark.unit
def test_external_links_are_written_as_previewer_writes_them(tmp_path):
    got, uris = _links(
        tmp_path,
        '<p>See <a href="http://example.com/">site</a>, mail '
        '<a href="mailto:x@example.com">us</a>, or '
        '<a href="https://example.org/a?b=1#c">page</a>.</p>'
        '<p>Again <a href="http://example.com/">site</a>.</p>',
    )
    assert [(w, u) for w, _a, u in got] == [
        ("site", "http://example.com/"),
        ("us", "mailto:x@example.com"),
        ("page", "https://example.org/a?b=1#c"),
        ("site", "http://example.com/"),
    ]
    # One anchor per address, as Previewer writes it.
    assert got[0][1] == got[3][1]
    assert uris == [
        "http://example.com/",
        "https://example.org/a?b=1#c",
        "mailto:x@example.com",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("href", ["javascript:alert(1)", "tel:123", "ftp://x/y"])
def test_other_schemes_stay_plain_text(tmp_path, href):
    got, uris = _links(tmp_path, f'<p>A <a href="{href}">link</a>.</p>')
    assert got == [] and uris == []
