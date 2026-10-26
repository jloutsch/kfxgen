"""A paragraph that runs the chapter title into its text stays whole,
through calibre (#468).

kfxgen prints a chapter's TOC label as its heading and cuts those words
from the start of the first block, so they don't print twice. A plain
paragraph that only starts with them ("Chapter One was a long day") was
cut into the heading and the rest of the sentence. Kindle Previewer 4.0.3
prints the paragraph whole and adds no heading, and so does kfxgen now.
A book heading that is the title still prints once.

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
    "c1.xhtml": "<p>Chapter One was a long day, said S1.</p><p>S2 more.</p>",
    "c2.xhtml": "<h1>Chapter Two</h1><p>S3 text.</p>",
}
TOC = [("Chapter One", "c1.xhtml"), ("Chapter Two", "c2.xhtml")]


def test_a_paragraph_running_the_title_in_stays_whole(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "runs468.epub", tmp / "runs468.kfx"
    _build(epub, FILES, TOC, "runs468")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    texts = [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    assert texts == [
        "Chapter One was a long day, said S1.",
        "S2 more.",
        "Chapter Two",
        "S3 text.",
    ], f"calibre {version}"
