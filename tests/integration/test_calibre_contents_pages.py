"""A book with more than one page titled "Contents", through calibre (#714).

The book of tests/unit/test_contents_pages.py: the book's contents page,
then two plays, each with a title and a "Contents" page listing its scenes.
Only the book's contents page is rebuilt. Through calibre, which reads where
the pages break, a play's kept Contents page is not a section of its own: it
runs on from the play's title, as the book has them in one file, since
nothing merged into it is lost.

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

from tests._kfx_introspect import load_fragments  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import _sections, _texts, write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]


@pytest.fixture
def book(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "c714.epub", tmp / "c714.kfx"
    write_epub(epub)
    _ebook_convert(calibre, epub, kfx, True)
    return load_fragments(kfx), version


def test_each_play_keeps_its_own_contents(book):
    frags, version = book
    lines = [t.strip() for t, _ in _texts(frags)]
    for n in (1, 2):
        assert lines.count(f"Scene I. Room {n}") == 1, f"calibre {version}: {lines}"


def test_a_kept_contents_page_runs_on_from_the_plays_title(book):
    frags, version = book
    for n, name in ((1, "PLAY ONE"), (2, "PLAY TWO")):
        (section,) = [s for s in _sections(frags) if f"Scene I. Room {n}" in s]
        assert name in section, f"calibre {version}: {section}"


def test_every_line_has_the_pages_language(book):
    frags, version = book
    missing = [t for t, keys in _texts(frags) if keys != {"$10": "en"}]
    assert not missing, f"calibre {version}: {missing}"


# pg5197's shape: a second "CONTENTS" page, with an entry ("Part I") the TOC
# lists in its file. The kept page and that entry share a section, and the
# book's rebuilt contents page still lists the entry.
PART_PAGES = {
    "c0.xhtml": "<h1 id='w'>My Life</h1>",
    "c1.xhtml": "<h3 id='bc'>Contents</h3><p>Front words.</p>",
    "c2.xhtml": (
        "<h2 id='c'>CONTENTS</h2><h3 id='p1'>Part I. Early years</h3>"
        "<table><tr><td>Childhood</td></tr><tr><td>School</td></tr></table>"
    ),
    "c3.xhtml": "<h2 id='ch'>CHILDHOOD</h2><p>Childhood words.</p>",
}
PART_TOC = [
    ("My Life", "c0.xhtml#w", 0),
    ("Contents", "c1.xhtml#bc", 0),
    ("CONTENTS", "c2.xhtml#c", 0),
    ("Part I. Early years", "c2.xhtml#p1", 0),
    ("CHILDHOOD", "c3.xhtml#ch", 0),
]


def test_the_contents_page_lists_an_entry_after_a_kept_contents_page(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "p714.epub", tmp / "p714.kfx"
    write_epub(epub, pages=PART_PAGES, toc=PART_TOC)
    _ebook_convert(calibre, epub, kfx, True)
    sections = _sections(load_fragments(kfx))
    (rebuilt,) = [s for s in sections if s and s[0] == "Contents"]
    assert "Part I. Early years" in rebuilt, f"calibre {version}: {sections}"
    # The kept page and its part's listing print together.
    (kept,) = [s for s in sections if "CONTENTS" in s]
    assert "Childhood" in kept, f"calibre {version}: {sections}"
