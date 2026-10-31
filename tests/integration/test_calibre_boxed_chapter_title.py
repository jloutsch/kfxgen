"""A chapter opening with its title in a bordered <div> keeps the box round
the title, as Kindle Previewer 4.0.3 draws it (#646; QA's probe on #644,
mine 2026-10-31, a page with a DOCTYPE).

kfxgen prints a chapter's title as its own heading and cuts the book's
copy. A box holding only the title was dropped with it, so nothing drew the
border; a box holding the title and more kept the rest, with kfxgen's
heading printed above it, outside. The box now opens before kfxgen's
heading.

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_part_headings import (  # noqa: E402
    _book_epub,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BOX = "border:2px solid black;padding:1em;margin:0 0 1em"
BORDER_KEYS = {"$88", "$89", "$90", "$91", "$92"}


def _plain(x):
    return getattr(x, "value", x)


def _entries(kfx):
    """[(text, (its box, the box's style) or None)] in reading order."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = []

    def walk(e, box):
        e = _plain(e)
        if not hasattr(e, "get"):
            return
        style = {str(k): v for k, v in styles.get(str(e.get("$157")), {}).items()}
        if e.get("$145"):
            t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            got.append((t.strip(), box))
            return
        for k in e.get("$146") or ():
            walk(k, (id(e), style) if str(e.get("$159")) == "$270" else box)

    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            walk(e, None)
    return got


def _convert(calibre, key, body):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / f"{key}.epub", tmp / f"{key}.kfx"
    _book_epub(
        epub,
        key,
        body,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Boxed Title", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    return _entries(kfx), version


def _bordered(box):
    return box is not None and bool(BORDER_KEYS & set(box[1]))


def test_a_box_holding_only_the_title_is_drawn_round_the_heading(calibre):  # noqa: F811
    got, version = _convert(
        calibre,
        "T646",
        f"<div style='{BOX}'><h1>Boxed Title</h1></div><p>K0 first words.</p>",
    )
    title = [box for t, box in got if t == "Boxed Title"]
    assert len(title) == 1, f"calibre {version}: {got}"
    assert _bordered(title[0]), f"calibre {version}: {got}"
    after = dict(got)["K0 first words."]
    assert after is None, f"calibre {version}: {got}"


def test_a_box_opening_with_the_title_holds_the_heading_and_the_rest(calibre):  # noqa: F811
    got, version = _convert(
        calibre,
        "U646",
        f"<div style='{BOX}'><h1>Boxed Title</h1><p>K0 in the box.</p></div>"
        "<p>K1 after the box.</p>",
    )
    by_text = dict(got)
    assert [t for t, _ in got].count("Boxed Title") == 1, f"calibre {version}: {got}"
    assert _bordered(by_text["Boxed Title"]), f"calibre {version}: {got}"
    # The same box, not a second one.
    assert by_text["K0 in the box."][0] == by_text["Boxed Title"][0], (
        f"calibre {version}: {got}"
    )
    assert by_text["K1 after the box."] is None, f"calibre {version}: {got}"


def test_a_title_run_into_a_boxed_paragraph_prints_as_written(calibre):  # noqa: F811
    """A box opening with "Boxed Title. K3 …": Kindle Previewer 4.0.3 prints
    no heading and keeps the paragraph whole (QA on #651, my probe). kfxgen
    printed its heading and left ". K3 …" in the box (#654)."""
    got, version = _convert(
        calibre,
        "R654",
        f"<div style='{BOX}'><p>Boxed Title. K3 the words run on.</p></div>"
        "<p>K4 after.</p>",
    )
    texts = [t for t, _ in got]
    assert "Boxed Title" not in texts, f"calibre {version}: {got}"
    assert _bordered(dict(got)["Boxed Title. K3 the words run on."]), (
        f"calibre {version}: {got}"
    )


def test_a_boxed_heading_after_a_lead_paragraph_prints_once(calibre):  # noqa: F811
    """A lead paragraph, then a boxed `<h2>` with `page-break-before`: Kindle
    Previewer 4.0.3 prints the lead, then the boxed heading, and no label
    (QA on #651, my probe). kfxgen printed its label above the lead as well,
    the title twice (#654)."""
    got, version = _convert(
        calibre,
        "P654",
        "<p>W0 a lead paragraph.</p>"
        f"<div style='{BOX};page-break-before:always'><h2>Boxed Title</h2></div>"
        "<p>W1 after the box.</p>",
    )
    texts = [t for t, _ in got]
    assert texts.count("Boxed Title") == 1, f"calibre {version}: {got}"
    assert texts.index("W0 a lead paragraph.") < texts.index("Boxed Title"), (
        f"calibre {version}: {got}"
    )
    assert _bordered(dict(got)["Boxed Title"]), f"calibre {version}: {got}"
