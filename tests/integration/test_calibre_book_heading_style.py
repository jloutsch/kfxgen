"""A book heading printed in place of kfxgen's label keeps the book's own
style through calibre (#490). Each expected value is Kindle Previewer
4.0.3's for the same markup (probed 2026-10-26): weight, size and
alignment from the book's CSS and calibre's own sheet (an h1 bold at 2em,
an h2 at 1.5em), and no alignment written where the book sets none.

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
    "c1.xhtml": '<h1 style="font-size: 1em; font-weight: normal; text-align: left; '
    'font-variant: small-caps">Chapter 1</h1><p>One body text.</p>',
    "c2.xhtml": "<h1>Chapter 2</h1><p>Two body text.</p>",
    "c3.xhtml": "<h2>Chapter 3</h2><p>Three body text.</p>",
    "c4.xhtml": '<h2 style="text-align: center; font-style: italic">Chapter 4</h2>'
    "<p>Four body text.</p>",
}
TOC = [
    ("Chapter One", "c1.xhtml"),
    ("Chapter Two", "c2.xhtml"),
    ("Chapter Three", "c3.xhtml"),
    ("Chapter Four", "c4.xhtml"),
]


def test_a_book_heading_keeps_its_own_style(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "hs490.epub", tmp / "hs490.kfx"
    _build(epub, FILES, TOC, "hs490")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                if text.startswith("Chapter"):
                    s = styles[str(e["$157"])]
                    fs = s.get("$16")
                    got[text] = (
                        str(s.get("$13")),
                        float(str(fs["$307"])) if fs is not None else None,
                        str(s.get("$34")) if s.get("$34") is not None else None,
                        str(s.get("$12")) if s.get("$12") is not None else None,
                    )
    assert got == {
        "Chapter 1": ("$350", None, "$59", None),
        "Chapter 2": ("$361", 2.0, None, None),
        "Chapter 3": ("$361", 1.5, None, None),
        "Chapter 4": ("$361", 1.5, "$320", "$382"),
    }, f"calibre {version}"
