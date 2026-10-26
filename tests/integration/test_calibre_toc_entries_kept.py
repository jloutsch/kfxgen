"""A second TOC entry on the same spot is kept, through calibre (#459).

It was dropped ("out of document order; skipping split"), where Kindle
Previewer 4.0.3 keeps it. It now lands where the entry before it does: on
the chapter's heading, which kfxgen prints from the first entry's label.

An entry with an empty label, the other half of #459, never reaches
kfxgen: calibre's TOC leaves it out.

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
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_notes_aside import _contents_pane  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

FILES = {
    "c1.xhtml": '<h1 id="start">Chapter One</h1><p>One text.</p>',
    "c2.xhtml": '<h1>Chapter Two</h1><p>Two text.</p><p id="mid">M1 middle.</p>'
    "<p>Two more.</p>",
    "c3.xhtml": "<h1>Chapter Three</h1><p>Three text.</p>",
}
TOC = [
    ("Same spot A", "c1.xhtml#start"),
    ("Same spot B", "c1.xhtml#start"),
    ("Chapter Two", "c2.xhtml"),
    ("Middle", "c2.xhtml#mid"),
    ("Chapter Three", "c3.xhtml"),
]


def test_a_second_entry_on_the_same_spot_is_kept(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "toc459.epub", tmp / "toc459.kfx"
    _build(epub, FILES, TOC, "toc459")
    _ebook_convert(calibre, epub, kfx, True)
    assert _contents_pane(load_fragments(kfx)) == [
        # Both land on the book's heading, which prints in place of the
        # label since #481.
        ("Same spot A", "Chapter One"),
        ("Same spot B", "Chapter One"),
        ("Chapter Two", "Chapter Two"),
        ("Middle", "M1 middle."),
        ("Chapter Three", "Chapter Three"),
    ], f"calibre {version}"
