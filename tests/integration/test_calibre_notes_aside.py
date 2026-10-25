"""A TOC entry on a footnote-marked box that holds the Notes heading, through
calibre (#431).

The note rules (#203, #282) drop a TOC entry that names a single note, but
not one on an element holding a heading: a "Notes" heading in the notes
section is a real contents entry. The unit test of that rule runs through
the shim, which reads no CSS, so every entry there starts a chapter. calibre
knows where the book breaks the page, and an entry with no break before it
is a place in the chapter before it (#363), still in the contents. So this
checks the contents pane: the "Notes" entry is there and lands on its
heading, a chapter of its own only when the book breaks the page.

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
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_converter import _epub_with_toc  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _body(aside_style):
    return (
        '<h1 id="one">Chapter One</h1><p>x<a epub:type="noteref" href="#n1">1</a></p>'
        f'<aside epub:type="footnote" id="notes"{aside_style}><h1>Notes</h1>'
        '<p id="n1">1. Note.</p></aside>'
    )


def _contents_pane(frags):
    """[(label, text of the entry it lands on)] for the top-level TOC."""
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    by_id = {
        e["$155"]: e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    }

    def text(e):
        return str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])

    for f in by_type(frags, "$389"):
        for container in val(f)[0]["$392"]:
            c = getattr(container, "value", container)
            if str(c["$235"]) != "$212":
                continue
            out = []
            for u in c["$247"]:
                u = getattr(u, "value", u)
                label = getattr(u["$241"], "value", u["$241"])
                target = getattr(u["$246"], "value", u["$246"])
                landing = by_id.get(target.get("$155"))
                out.append(
                    (str(label["$244"]), text(landing) if landing is not None else None)
                )
            return out
    return None


@pytest.mark.parametrize(
    ("aside_style", "sections"),
    [
        pytest.param("", 1, id="no-page-break"),
        pytest.param(' style="page-break-before: always"', 2, id="page-break"),
    ],
)
def test_the_notes_entry_is_kept_and_lands_on_its_heading(
    calibre,  # noqa: F811
    tmp_path,
    aside_style,
    sections,
):
    tmp, _env, version = calibre
    epub = _epub_with_toc(
        tmp_path,
        _body(aside_style),
        [("Chapter One", "index.xhtml#one"), ("Notes", "index.xhtml#notes")],
        name=f"notes-aside-{len(aside_style)}",
    )
    kfx = tmp / f"notes-aside-{len(aside_style)}.kfx"
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    assert _contents_pane(frags) == [
        ("Chapter One", "Chapter One"),
        ("Notes", "Notes"),
    ], f"calibre {version}"
    assert len(by_type(frags, "$260")) == sections
