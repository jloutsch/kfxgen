"""A book heading among a section's first blocks prints with no TOC label
over it, through calibre (#492).

Since #481 only a heading that was the section's first block took the
label's place. A heading after an epigraph, or written in a table cell,
still had kfxgen's label printed above the section. Kindle Previewer 4.0.3
prints the book as written. A section with no heading near its start keeps
its label.

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
    "c1.xhtml": "<p>An epigraph line.</p><h1>Chapter 1</h1><p>S1 text.</p>",
    "c2.xhtml": "<table><tr><td><h1>Chapter 2</h1></td></tr></table><p>S2 text.</p>",
    "c3.xhtml": "<p>S3 text.</p><p>More.</p>",
}
TOC = [
    ("Chapter One", "c1.xhtml"),
    ("Chapter Two", "c2.xhtml"),
    ("Chapter Three", "c3.xhtml"),
]


def test_a_heading_near_the_start_takes_the_labels_place(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "head492.epub", tmp / "head492.kfx"
    _build(epub, FILES, TOC, "head492")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    lines = [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    assert lines == [
        "An epigraph line.",
        "Chapter 1",
        "S1 text.",
        "Chapter 2",
        "S2 text.",
        # No heading: the label stays, so the section has a visible start.
        "Chapter Three",
        "S3 text.",
        "More.",
    ], f"calibre {version}"
