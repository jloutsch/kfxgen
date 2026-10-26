"""A section opening with the book's own heading prints that heading alone,
through calibre (#481).

kfxgen printed the TOC label as a heading of its own, then the book's
heading under it when the two differed ("Chapter One", then "Chapter 1"),
and cut a heading that extends its label into two lines ("Chapter 5", then
": The End"). Kindle Previewer 4.0.3 prints only the book's heading, and so
does kfxgen now; the label stays in the TOC, and a link to the heading
lands on it.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

FILES = {
    "c1.xhtml": '<h1 id="h1">Chapter 1</h1><p>S1 text.</p>',
    "c2.xhtml": "<h1>Chapter 5: The End</h1><p>S2 text.</p>",
    "c3.xhtml": '<h2 style="page-break-before: always">PART ONE: The Beginning</h2>'
    "<p>S3 text.</p>",
    "c4.xhtml": "<h1>Chapter Four</h1><p>S4 text.</p>"
    '<p>Back to <a href="c1.xhtml#h1">L1</a>.</p>',
}
TOC = [
    ("Chapter One", "c1.xhtml"),
    ("Chapter 5", "c2.xhtml"),
    ("Part One", "c3.xhtml"),
    ("Chapter Four", "c4.xhtml"),
]


def test_a_section_opening_with_its_heading_prints_it_alone(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "head481.epub", tmp / "head481.kfx"
    _build(epub, FILES, TOC, "head481")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]

    def text(e):
        r = e["$145"]
        return str(content[str(r["name"])][int(r["$403"])])

    assert [text(e) for e in entries] == [
        "Chapter 1",
        "S1 text.",
        "Chapter 5: The End",
        "S2 text.",
        "PART ONE: The Beginning",
        "S3 text.",
        "Chapter Four",
        "S4 text.",
        "Back to L1.",
    ], f"calibre {version}"

    labels = []
    for f in by_type(frags, "$389"):
        for c in val(f)[0]["$392"]:
            c = getattr(c, "value", c)
            if str(c["$235"]) == "$212":
                for u in c["$247"]:
                    u = getattr(u, "value", u)
                    labels.append(str(getattr(u["$241"], "value", u["$241"])["$244"]))
    assert labels == [label for label, _ in TOC], f"calibre {version}"

    by_id = {e["$155"]: e for e in entries}
    anchors = {str(f.fid): val(f)["$183"]["$155"] for f in by_type(frags, "$266")}
    back = entries[-1]
    links = [sp["$179"] for sp in back.get("$142") or [] if "$179" in sp]
    assert [text(by_id[anchors[str(t)]]) for t in links] == ["Chapter 1"]
