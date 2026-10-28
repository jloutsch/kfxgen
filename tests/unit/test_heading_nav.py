"""The heading navigation ($798) is built from the book's headings, grouped
by level, as Kindle Previewer 4.0.3 builds it (#461, probed 2026-10-26):
one unit per level, $799 for h1 to $804 for h6, each at its level's first
heading, holding one child per heading. kfxgen built it from the TOC,
taking in entries that point at paragraphs. Only <h1>-<h6> count, and a
book with none has no $798 (#575, probed 2026-10-27)."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _containers(tmp_path, chapters_html):
    """The `$389` containers by kind, and each entry's text by position."""
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
    kinds = {}
    for c in val(nav)[0]["$392"]:
        c = getattr(c, "value", c)
        kinds[str(c["$235"])] = c
    return kinds, text


def _nav(tmp_path, chapters_html):
    kinds, text = _containers(tmp_path, chapters_html)
    headings = kinds["$798"]
    out = []
    for unit in headings["$247"]:
        u = getattr(unit, "value", unit)
        kids = [
            text.get(getattr(k, "value", k)["$246"]["$155"])
            for k in u.get("$247") or []
        ]
        # The unit itself lands at its level's first heading.
        out.append((str(u["$238"]), text.get(u["$246"]["$155"]), kids))
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
        ("$799", "One", ["One", "Two"]),
        ("$800", "Sub", ["Sub", "Again"]),
        ("$801", "Deep", ["Deep"]),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "first",
    [
        "<p>Body only.</p>",
        # A styled paragraph printed in the label's place (#409).
        "<p class='ct'>One</p><p>Body.</p>",
    ],
    ids=["label", "paragraph"],
)
def test_only_h1_to_h6_are_listed(tmp_path, first):
    """Previewer lists the book's <h1>-<h6> only: not a chapter title kfxgen
    prints, nor a paragraph printed as one (#575; #461 listed both as h1)."""
    got = _nav(
        tmp_path,
        [("One", first), ("Two", "<h1>Two</h1><p>Body.</p><h2>Sub</h2><p>Para.</p>")],
    )
    assert got == [("$799", "Two", ["Two"]), ("$800", "Sub", ["Sub"])]


@pytest.mark.unit
def test_a_book_without_headings_has_no_heading_navigation(tmp_path):
    """Previewer writes no $798 for a book with no <h1>-<h6>; kfxgen built
    one from the TOC (#575). The TOC itself stays."""
    kinds, _text = _containers(
        tmp_path,
        [("One", "<p class='ct'>One</p><p>Body.</p>"), ("Two", "<p>Body two.</p>")],
    )
    assert "$798" not in kinds
    assert "$212" in kinds


@pytest.mark.unit
def test_units_follow_each_levels_first_heading(tmp_path):
    """Previewer writes a level's unit where that level first appears, not in
    level order: an <h2> before the first <h1> puts `$800` first (#584)."""
    got = _nav(
        tmp_path,
        [
            ("Preface", "<h2>Preface</h2><p>Body.</p>"),
            ("One", "<h1>One</h1><p>Body.</p><h2>Sub</h2><p>Para.</p>"),
        ],
    )
    assert got == [("$800", "Preface", ["Preface", "Sub"]), ("$799", "One", ["One"])]
