"""A browser block nested in another element, through calibre's real
Stylizer (#387).

The unit tests' fake style can't show what calibre leaves of an <aside>,
<header> or <details> inside a <div>, or whether a filled sidebar in one
reaches the generator. This converts a book of the #387 probe's shapes with
calibre and checks that each paragraph is its own text entry, as in Kindle
Previewer's book, and that the sidebar's box is drawn.

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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_floats import _build as _build_floats  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """div.side { background-color: #eeeeee; border: 1px solid black; padding: 0.5em; }
aside.styled { margin-left: 2em; font-style: italic; }"""

#: Each case's paragraphs start with its name, then its name and "b".
CASES = [
    ("A1", "<div><aside><h3>A1 heading</h3><p>A1b para.</p></aside></div>"),
    ("A2", '<section><div><aside><div class="side"><h3>A2 sidebar</h3><p>A2b para.</p></div></aside></div></section>'),
    ("A4", "<div><aside>A4 lead text <p>A4b para.</p></aside></div>"),
    ("A6", '<div><aside class="styled"><p>A6 styled.</p><p>A6b second.</p></aside></div>'),
    ("A7", "<div><header><h3>A7 header</h3><p>A7b para.</p></header></div>"),
    ("A8", "<div><footer><p>A8 footer.</p><p>A8b two.</p></footer></div>"),
    ("A9", "<div><main><p>A9 main.</p><p>A9b two.</p></main></div>"),
    ("A10", "<div><nav><p>A10 nav.</p><p>A10b two.</p></nav></div>"),
    ("A13", "<div><details><summary>A13 summary</summary><p>A13b para.</p></details></div>"),
    ("B3", "<div><center><p>B3 center.</p><p>B3b two.</p></center></div>"),
    ("B7", "<div>B7 lead <figcaption>B7b caption</figcaption> B7c tail.</div>"),
]  # fmt: skip


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """[(first word of each text entry, its style, the fill of the box
    around it)] in reading order, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "nested.epub", tmp / "nested.kfx"
    _build_floats(epub, CASES, CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []

    def walk(entries, fill):
        for e in entries:
            if isinstance(e, str):
                continue
            st = styles.get(str(e.get("$157")), {})
            here = st.get("$70", fill)
            if e.get("$145"):
                r = e["$145"]
                out.append((str(texts[str(r["name"])][r["$403"]]).split()[0], st, here))
            walk(e.get("$146") or [], here)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return out, version


@pytest.mark.parametrize(
    "case",
    ["A1", "A2", "A4", "A6", "A7", "A8", "A9", "A10", "A13", "B3"],
)
def test_each_paragraph_is_its_own_entry(converted, case):
    entries, version = converted
    words = [w for w, _st, _fill in entries]
    assert case in words and f"{case}b" in words, f"calibre {version}: {words}"


def test_a_caption_between_running_text_is_its_own_entry(converted):
    entries, version = converted
    words = [w for w, _st, _fill in entries]
    i = words.index("B7")
    assert words[i : i + 3] == ["B7", "B7b", "B7c"], f"calibre {version}"


def test_a_sidebar_in_a_nested_aside_is_drawn(converted):
    entries, version = converted
    fills = {w: fill for w, _st, fill in entries}
    assert fills["A2"] is not None and fills["A2b"] is not None, f"calibre {version}"
    assert fills["A1"] is None


def test_a_styled_nested_asides_paragraphs_keep_its_style(converted):
    """Previewer writes the aside's indent and italic on each paragraph."""
    entries, version = converted
    styles = {w: st for w, st, _fill in entries}
    for word in ("A6", "A6b"):
        assert str(styles[word].get("$12")) == "$382", f"calibre {version}"
        assert styles[word].get("$48") is not None, f"calibre {version}"
