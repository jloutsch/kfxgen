"""Spans drawn as blocks, through calibre's real Stylizer (#233).

Verse, addresses and letter closings are often one `<span>` per line, made a
line by CSS (`display: block`). The lines used to run together ("AB"). Each
is now a line of its paragraph. The unit tests use a fake resolver; this
checks that calibre's computed `display` reaches the plugin.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """
span.line { display: block; }
span.i2 { display: block; margin-left: 2em; }
a.blocklink { display: block; }
p.se > span { display: block; }
p.se > span + br { display: none; }
span.plain { font-style: italic; }
"""

#: (file, case id, markup, the paragraphs expected between the case's
#: heading and its END line).
CASES = [
    (
        "c1",
        "SPANS",
        '<p><span class="line">Line one</span><span class="line">Line two</span>'
        '<span class="i2">Line three</span></p>',
        ["Line one\nLine two\nLine three"],
    ),
    (
        "c1",
        "AROUND",
        '<p>Lead <span class="line">middle</span> tail</p>',
        ["Lead\nmiddle\ntail"],
    ),
    (
        "c1",
        "LINK",
        '<p>See <a class="blocklink" href="https://example.org/">the note</a> below.</p>',
        ["See\nthe note\nbelow."],
    ),
    (
        "c1",
        "SEVERSE",
        '<p class="se"><span>First line</span>\n<br/>\n<span>Second line</span>'
        "\n<br/>\n<span>Third line</span></p>",
        ["First line\nSecond line\nThird line"],
    ),
    (
        "c1",
        "INLINE",
        '<p><span class="plain">Not</span> <span class="plain">a block</span></p>',
        ["Not a block"],
    ),
    (
        "c1",
        "HEADING",
        '<h3><span class="line">Chapter One</span>'
        '<span class="line">The Beginning</span></h3>',
        ["Chapter One\nThe Beginning"],
    ),
]


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    return _convert(calibre, "spans", [c[:3] for c in CASES], CSS)


@pytest.mark.parametrize(
    "cid,expected", [(c[1], c[3]) for c in CASES], ids=[c[1] for c in CASES]
)
def test_block_spans_through_the_real_stylizer(converted, cid, expected):
    by_case, calibre_version = converted
    texts = [text for text, _ in by_case[cid]["rows"]]
    assert texts == expected, f"calibre {calibre_version}, case {cid}"
