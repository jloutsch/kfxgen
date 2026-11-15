"""A span's own margins, through calibre's real Stylizer (#781).

pg24407 indents its ingredient lines with `<span style="margin-left: 5em">`,
and kfxgen dropped every span's margin. Kindle Previewer 4.0.3 (P781) writes
it as the run's `$48`/`$50`, or, for a span opening the paragraph, as the
paragraph's `$36`. This checks calibre's declared margins reach the runs.
The unit tests are in tests/unit/test_span_margins.py.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. Last run against calibre 9.18.0.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _build_epub,
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """
.m2 { margin-left: 2em }
.mr { margin-right: 2em }
"""
CASES = [
    (
        "c1",
        "INGRED",
        '<p>\n<span style="margin-left: 5em;">Potato meal.</span><br/>\n'
        '<span style="margin-left: 5em;">Sugar.</span><br/>\n</p>',
    ),
    ("c1", "MID", "<p>mid <span class='m2'>inside</span> after</p>"),
    ("c1", "PX", '<p>pxx <span style="margin-left: 20px">inside</span> after</p>'),
    ("c1", "RIGHT", "<p>rgt <span class='mr'>inside</span> after</p>"),
    ("c1", "START", "<p><span class='m2'>starts</span> the paragraph</p>"),
    ("c1", "ITEM", "<ul><li><span class='m2'>item</span> words</li></ul>"),
    # pg22091's shape: a line after an empty one opens with a span.
    (
        "c1",
        "CUT",
        "<p>cut first<br/>\n<br/>\n<span style='margin-left: 1em;'>after blank</span><br/>\n<br/>\n"
        "cut last</p>",
    ),
    (
        "c1",
        "FLOAT",
        '<p><span style="float: left; margin-left: -2.5em">12</span>Floated number line.</p>',
    ),
    (
        "c1",
        "LINK",
        "<p>lnk <a class='m2' href='c1.xhtml#lt'>link</a> after</p><p id='lt'>link target</p>",
    ),
]


def _length(style, key):
    v = style.get(key)
    return None if v is None else (str(v["$307"]), str(v["$306"]))


@pytest.fixture(scope="module")
def entries(calibre):  # noqa: F811
    """{text: (its style, [(offset, length, run style)])}, and the version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "margins.epub", tmp / "margins.kfx"
    _build_epub(epub, CASES, CSS, {})
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    out = {}
    for story in by_type(frags, "$259"):
        for e in iter_entries(val(story)["$146"]):
            ref = e.get("$145")
            if ref is None:
                continue
            text = str(content[str(ref["name"])][int(ref["$403"])])
            runs = [
                (int(r["$143"]), int(r["$144"]), styles.get(str(r.get("$157")), {}))
                for r in e.get("$142") or []
            ]
            out[text] = (styles[str(e["$157"])], runs)
    return out, version


def test_pg24407s_lines_are_indented(entries):
    by_text, version = entries
    style, runs = by_text["Potato meal.\nSugar."]
    # The first line by the paragraph's indent, the second by its run.
    assert _length(style, "$36") == ("5", "$308"), version
    assert [(s, n, _length(r, "$48")) for s, n, r in runs] == [(13, 6, ("5", "$308"))]


@pytest.mark.parametrize(
    "text,key,value,run",
    [
        ("mid inside after", "$48", ("2", "$308"), (4, 6)),
        # A px as 0.45pt (P781 S04).
        ("pxx inside after", "$48", ("9", "$318"), (4, 6)),
        ("rgt inside after", "$50", ("2", "$308"), (4, 6)),
        # A link's run too (P781 S11).
        ("lnk link after", "$48", ("2", "$308"), (4, 4)),
    ],
    ids=["MID", "PX", "RIGHT", "LINK"],
)
def test_a_span_in_the_line_writes_its_margin_on_its_run(
    entries, text, key, value, run
):
    by_text, version = entries
    _style, runs = by_text[text]
    assert [(s, n) for s, n, r in runs if _length(r, key) == value] == [run], version


def test_a_span_opening_the_paragraph_is_its_indent(entries):
    by_text, version = entries
    style, runs = by_text["starts the paragraph"]
    assert _length(style, "$36") == ("2", "$308"), version
    assert not [r for _s, _n, r in runs if _length(r, "$48") == ("2", "$308")]


def test_a_list_item_keeps_the_run(entries):
    by_text, version = entries
    _style, runs = by_text["item words"]
    assert [(s, n, _length(r, "$48")) for s, n, r in runs] == [(0, 4, ("2", "$308"))], (
        version
    )


def test_a_span_opening_a_piece_after_an_empty_line_keeps_its_run(entries):
    # Previewer indents the piece (pg22091: 130 such lines, 3.125%); an
    # indented piece would lose kfxgen's line of space above it (QA's q786
    # G36), so the run stays, at the line's start.
    by_text, version = entries
    (key,) = [t for t in by_text if t.startswith("after blank")]
    style, runs = by_text[key]
    assert _length(style, "$36") in (None, ("0", "$314")), version
    assert [(s, n) for s, n, r in runs if _length(r, "$48") == ("1", "$308")] == [
        (0, 11)
    ]


def test_a_floated_spans_negative_margin_is_left_alone(entries):
    # QA's q786 H01: line numbers hung in the margin.
    by_text, version = entries
    (key,) = [t for t in by_text if "Floated number line." in t]
    style, runs = by_text[key]
    assert _length(style, "$36") in (None, ("0", "$314")), version
    assert not [
        r
        for _s, _n, r in runs
        if r.get("$48") is not None and str(r["$48"]["$307"]).startswith("-")
    ]
