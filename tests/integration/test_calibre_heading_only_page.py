"""A chapter whose page is only its heading, through calibre (#717).

The book of tests/unit/test_heading_only_page.py, with pg100's CSS. Each
title page prints its heading in the book's style, as Kindle Previewer
4.0.3 writes the same book: centred (`$34` `$320`), 1.5em, normal weight,
and its margins as space above and below. A rule under the heading is
kept, and a box holding only the heading is drawn round it.

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
from tests.unit.test_contents_pages import write_epub  # noqa: E402
from tests.unit.test_heading_only_page import CSS, PAGES, TOC, entries  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]


@pytest.fixture(scope="module")
def frags(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "h717.epub", tmp / "h717.kfx"
    write_epub(epub, pages=PAGES, toc=TOC, css=CSS, ident="h717")
    _ebook_convert(calibre, epub, kfx, True)
    return load_fragments(kfx), version


def _num(v):
    v = getattr(v, "value", v)
    return (
        round(float(getattr(v["$307"], "value", v["$307"])), 3)
        if v is not None
        else None
    )


def _sym(v):
    return str(getattr(v, "value", v)) if v is not None else None


def _section(frags, title):
    (section,) = [s for s in entries(frags) if any(t == title for t, _ in s)]
    return section


# Kindle Previewer 4.0.3's values for the same book: `$47` and `$49` in
# line heights, the heading's own size and line height.
@pytest.mark.parametrize(
    "title, above, below, after",
    [
        ("RULED TITLE", 2.222, None, ["$596"]),
        ("BARE TITLE", 2.222, 0.833, []),
        ("BOXED TITLE", 1.667, 0.833, ["$596"]),
        ("PLAIN TITLE", 1.667, 0.833, []),
    ],
)
def test_a_title_alone_prints_in_the_books_heading_style(
    frags, title, above, below, after
):
    frags, version = frags
    section = _section(frags, title)
    (style,) = [s for t, s in section if t == title]
    got = {
        "align": _sym(style.get("$34")),
        "size": _num(style.get("$16")),
        "weight": _sym(style.get("$13")),
        "above": _num(style.get("$47")),
        "below": _num(style.get("$49")),
    }
    want = {
        "align": "$320",
        "size": 1.5,
        "weight": "$350",
        "above": above,
        "below": below,
    }
    assert got == want, f"calibre {version}: {got}"
    labels = [t for t, _ in section]
    assert labels[labels.index(title) + 1 :] == after, f"calibre {version}: {labels}"


def test_a_box_holding_only_the_title_is_drawn_round_it(frags):
    frags, version = frags
    labels = [t for t, _ in _section(frags, "BOXED TITLE")]
    assert labels[:2] == ["$270", "BOXED TITLE"], f"calibre {version}: {labels}"
