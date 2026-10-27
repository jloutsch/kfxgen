"""The heading navigation ($798) is built from the book's headings, grouped
by level, as Kindle Previewer 4.0.3 builds it (#461, probed 2026-10-26):
one unit per level, $799 for h1 to $804 for h6, each at its level's first
heading, holding one child per heading. kfxgen built it from the TOC,
taking in entries that point at paragraphs."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _nav(tmp_path, chapters_html):
    chapters = []
    for title, html in chapters_html:
        xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{html}</body></html>'
        blocks = _conv.extract_blocks_from_html(
            etree.fromstring(xhtml.encode()), base_href="c.xhtml"
        )
        chapters.append({"title": title, "text": "x", "blocks": blocks})
    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book("T", "A", chapters, output_path=str(out))
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    text = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text[e["$155"]] = str(
                    content[str(e["$145"]["name"])][int(e["$145"]["$403"])]
                )
    (nav,) = by_type(frags, "$389")
    (headings,) = [
        getattr(c, "value", c)
        for c in val(nav)[0]["$392"]
        if str(getattr(c, "value", c)["$235"]) == "$798"
    ]
    out = []
    for unit in headings["$247"]:
        u = getattr(unit, "value", unit)
        kids = [
            text.get(getattr(k, "value", k)["$246"]["$155"])
            for k in u.get("$247") or []
        ]
        out.append((str(u["$238"]), kids))
    return out


@pytest.mark.unit
def test_heading_navigation_groups_the_books_headings_by_level(tmp_path):
    got = _nav(
        tmp_path,
        [
            (
                "One",
                "<h1>One</h1><p>Body.</p><h2>Sub</h2><p>Para.</p><h3>Deep</h3><p>B.</p>",
            ),
            ("Two", "<h1>Two</h1><p>Body.</p><h2>Again</h2><p>More.</p>"),
        ],
    )
    assert got == [
        ("$799", ["One", "Two"]),
        ("$800", ["Sub", "Again"]),
        ("$801", ["Deep"]),
    ]
