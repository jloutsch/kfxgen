"""A chapter opening with a table that prints its title keeps the table as
written, through calibre (#470).

kfxgen printed the TOC label as a heading and cut the title out of the
table: a row "Chapter Four | S4 page twelve" kept an empty cell where the
title was, and a title spelled over rows lost those rows. Kindle Previewer
4.0.3 prints the table as written, with no heading over it (QA's #452
probe), and so does kfxgen now. The TOC entry opens the table.

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
    "c4.xhtml": "<table><tr><td>Chapter Four</td><td>S4 page twelve</td></tr>"
    "<tr><td>S4b</td><td>row two</td></tr></table><p>S4 text.</p>",
    "c8.xhtml": "<table><tr><td>Chapter</td></tr><tr><td>Eight</td></tr>"
    "<tr><td>S8 row three</td></tr></table><p>S8 text.</p>",
}
TOC = [("Chapter Four", "c4.xhtml"), ("Chapter Eight", "c8.xhtml")]


def test_a_table_printing_the_title_is_kept_whole(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "tbl470.epub", tmp / "tbl470.kfx"
    _build(epub, FILES, TOC, "tbl470")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get")
    ]

    def text(e):
        r = e.get("$145")
        return str(content[str(r["name"])][int(r["$403"])]) if r else None

    texts = [t for t in map(text, entries) if t]
    assert texts == [
        "Chapter Four",
        "S4 page twelve",
        "S4b",
        "row two",
        "S4 text.",
        "Chapter",
        "Eight",
        "S8 row three",
        "S8 text.",
    ], f"calibre {version}"

    # Each TOC entry opens its chapter's table.
    by_id = {e["$155"]: e for e in entries if "$155" in e}
    landings = []
    for f in by_type(frags, "$389"):
        for c in val(f)[0]["$392"]:
            c = getattr(c, "value", c)
            if str(c["$235"]) == "$212":
                for u in c["$247"]:
                    u = getattr(u, "value", u)
                    tgt = getattr(u["$246"], "value", u["$246"])
                    landings.append(text(by_id[tgt["$155"]]))
    assert landings == ["Chapter Four", "Chapter"], f"calibre {version}"
