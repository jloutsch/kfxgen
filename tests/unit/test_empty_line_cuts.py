"""A paragraph is cut at an empty line (`<br/><br/>`), as Kindle Previewer
4.0.3 writes it (#602, probed 2026-10-28 with a DOCTYPE): each run of empty
lines ends an entry, the entry before keeps one line break, and the next
starts at its words, without the first-line indent. A heading's parts stay
headings. #593 cut there only before a line holding a picture.
Preformatted text is not cut."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

pytestmark = pytest.mark.unit


def _entries(tmp_path, body, css=None, native_tables=False):
    """The book's text entries, as (text, its style) in reading order."""
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        base_href="c.xhtml",
        native_tables=native_tables,
        style_resolver=lambda e: dict(
            (k.strip(), v.strip())
            for k, _, v in (d.partition(":") for d in (e.get("style") or "").split(";"))
            if v
        ),
    )
    out = tmp_path / "e.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "One", "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                got.append((text, styles.get(str(e.get("$157")), {})))
    return [(t, s) for t, s in got if t.strip() not in ("One", "x")], frags


def _texts(tmp_path, body):
    return [t for t, _s in _entries(tmp_path, body)[0]]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "<p>V1 first line<br/><br/>V1 after empty line.</p>",
            ["V1 first line\n", "V1 after empty line."],
        ),
        ("<p>V2 first line<br/>V2 next line.</p>", ["V2 first line\nV2 next line."]),
        (
            "<p>V3 first<br/><br/><br/>V3 after two empty.</p>",
            ["V3 first\n", "V3 after two empty."],
        ),
        (
            "<p>V13 a<br/><br/>V13 b<br/><br/>V13 c.</p>",
            ["V13 a\n", "V13 b\n", "V13 c."],
        ),
        (
            "<p>W5 first<br/> <br/>W5 after a line of one space.</p>",
            ["W5 first\n", "W5 after a line of one space."],
        ),
        (
            "<p><br/><br/>V8 starts with an empty line.</p>",
            ["V8 starts with an empty line."],
        ),
        (
            "<pre>V10 pre first\n\nV10 pre after</pre>",
            ["V10 pre first\n\nV10 pre after"],
        ),
    ],
    ids=[
        "one-empty",
        "no-empty",
        "two-empty",
        "two-cuts",
        "space-line",
        "leading",
        "pre",
    ],
)
def test_an_empty_line_ends_the_entry(tmp_path, body, expected):
    assert _texts(tmp_path, body) == expected


def test_the_part_after_a_cut_has_no_first_line_indent(tmp_path):
    entries, _ = _entries(
        tmp_path,
        '<p style="text-indent: 2em">V4 indented first<br/><br/>V4 indented after.</p>',
    )
    (first, s1), (after, s2) = entries
    assert (first, after) == ("V4 indented first\n", "V4 indented after.")
    assert "$36" in s1
    assert (
        float(
            getattr(s2.get("$36"), "value", s2.get("$36") or {}).get("$307", 0)
            if s2.get("$36")
            else 0
        )
        == 0
    )


def test_italic_and_a_link_run_into_both_parts(tmp_path):
    entries, _ = _entries(
        tmp_path,
        "<p>V5 <i>italic before<br/><br/>italic after</i> plain.</p>"
        '<p>V6 <a href="c.xhtml#x">link before<br/><br/>link after</a> plain.</p><p id="x">X.</p>',
    )
    texts = [t for t, _s in entries]
    assert texts[:4] == [
        "V5 italic before\n",
        "italic after plain.",
        "V6 link before\n",
        "link after plain.",
    ]


def test_a_headings_parts_stay_headings(tmp_path):
    """Previewer: both parts heading-sized and bold, and both listed at the
    heading's level in the heading navigation. Mid-chapter: a chapter's
    opening heading prints as kfxgen's chapter heading (#490), not cut."""
    entries, frags = _entries(
        tmp_path,
        "<p>Intro.</p><h2>W1 CHAPTER I<br/><br/>W1 THE BEGINNING</h2><p>Body.</p>",
    )
    (first, s1), (after, s2) = entries[1:3]
    assert (first, after) == ("W1 CHAPTER I\n", "W1 THE BEGINNING")
    # The same weight and size: bold through calibre's stylesheet, which this
    # test doesn't load (the calibre test checks it).
    assert (str(s1.get("$13")), str(s1.get("$16"))) == (
        str(s2.get("$13")),
        str(s2.get("$16")),
    )
    (nav,) = by_type(frags, "$389")
    level2 = [
        getattr(u, "value", u)
        for c in val(nav)[0]["$392"]
        if str(getattr(c, "value", c)["$235"]) == "$798"
        for u in getattr(c, "value", c)["$247"]
        if str(getattr(u, "value", u)["$238"]) == "$800"
    ]
    assert len(level2) == 1 and len(level2[0]["$247"]) == 2


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<p>X1 first<br/>&#160;<br/>X1 after</p>", ["X1 first\n ", "X1 after"]),
        ("<p>X2 first<br/>&#160;&#160;<br/>X2 after</p>", ["X2 first\n  ", "X2 after"]),
        (
            "<p>X4 first<br/>&#160;<br/>&#160;<br/>X4 after</p>",
            ["X4 first\n ", "X4 after"],
        ),
        ("<p>X5 first<br/><br/>&#160;<br/>X5 after</p>", ["X5 first\n", "X5 after"]),
    ],
    ids=["one-nbsp", "two-nbsp", "two-spacer-lines", "empty-then-spacer"],
)
def test_a_spacer_line_keeps_only_its_first_line_as_spaces(tmp_path, body, expected):
    """A line of no-break spaces, a common Gutenberg spacer: Kindle Previewer
    4.0.3 keeps the line break and the first spacer line's no-break spaces
    as plain spaces, and nothing after (QA's E12 on #625, probed 2026-10-28;
    #626)."""
    assert _texts(tmp_path, body) == expected


def test_a_cells_text_is_cut_into_entries_in_the_cell(tmp_path):
    """`<td>W3 cell first<br/><br/>W3 cell after</td>`: Kindle Previewer 4.0.3
    writes a cell container holding two text entries (#624, probed
    2026-10-28); a plain cell beside it stays one entry."""
    _unused, frags = _entries(
        tmp_path,
        "<table><tr><td>W3 cell first<br/><br/>W3 cell after</td>"
        "<td>W3b</td></tr></table>",
        native_tables=True,
    )
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def shape(e):
        e = getattr(e, "value", e)
        if e.get("$145"):
            return str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        return [
            shape(k)
            for k in e.get("$146") or ()
            if hasattr(getattr(k, "value", k), "get")
        ]

    tables = [
        getattr(e, "value", e)
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and str(e.get("$159")) == "$278"
    ]
    row = shape(tables[0])
    while len(row) == 1 and isinstance(row[0], list):
        row = row[0]
    assert row == [["W3 cell first\n", "W3 cell after"], "W3b"]


def test_a_cut_cells_first_part_has_no_space_above(tmp_path):
    """The cell's single entry had none on main, and Previewer's first part
    has none; the part after the cut is spaced as a body cut's is (#625)."""
    _unused, frags = _entries(
        tmp_path,
        "<table><tr><td>W3 cell first<br/><br/>W3 cell after</td>"
        "<td>W3b</td></tr></table>",
        native_tables=True,
    )
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    above = {}

    def walk(e):
        e = getattr(e, "value", e)
        if not hasattr(e, "get"):
            return
        if e.get("$145"):
            text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            above[text] = "$47" in styles.get(str(e.get("$157")), {})
        for k in e.get("$146") or ():
            walk(k)

    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            walk(e)
    assert (above["W3 cell first\n"], above["W3 cell after"]) == (False, True)
