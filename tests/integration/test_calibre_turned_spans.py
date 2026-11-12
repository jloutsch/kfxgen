"""Text set the other way from its line, through calibre (#247, group B2).

Kindle Previewer 4.0.3 (probe PSB, decoded; a vertical Japanese book and the
same with an ltr spine, alike): a horizontal-tb span in vertical text is an
inline `$270` (`$601 $283`, `$156 $323`) styled `$560 $557`, `$56` 100%,
`$478`, `$784`, holding a `$269` paragraph of its text (S01-S11), with its
own runs inside: a link (S05), bold (S06), italic (S08); a vertical-rl span
in a horizontal block is the same with `$156 $322`, `$560 $559`, `$57` 100%
(R05). In the position map the outer paragraph reaches the placeholder, the
container takes one position, its paragraph one per character, and the
outer paragraph resumes after the placeholder (S01). kfxgen wrote the text
in the line.

It needs calibre installed, so it is `slow` and skips when `ebook-convert`
is not found.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.integration.test_calibre_vertical_writing import (  # noqa: E402
    REST,
    VERT,
    _write,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = f"html {{ {VERT} }} p {{ margin: 0; text-indent: 1em }} .vt {{ {VERT} }}" + REST
BODY = (
    "<h1>第一章</h1><p>V00 縦の段落。</p>"
    '<p>縦の中に<span class="hz">S01</span>を置く。</p>'
    '<p>一つ<span class="hz">S04a</span>二つ<span class="hz">S04b</span>終わり。</p>'
    '<p>リンク<span class="hz"><a href="c2.xhtml#t">S05</a></span>です。</p>'
    '<p>太字<span class="hz"><b>S06</b></span>です。</p>'
    '<p>強調<em class="hz">S08</em>です。</p>'
    '<p><span class="hz">S10</span></p>'
    '<p>前<span class="hz"><small>Y1</small>0</span>後。</p>'
    '<div class="hz"><p>Horizontal <span class="vt">R05</span> text.</p></div>'
    "<p>V99 縦の段落。</p>"
)


def _book(calibre, name, rtl):  # noqa: F811
    tmp, _env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _write(epub, rtl, CSS, CSS, ch1=BODY)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}

    def walk(entries, outer):
        for e in entries:
            if not hasattr(e, "get"):
                continue
            if str(e.get("$159")) == "$270" and str(e.get("$601")) == "$283":
                (inner,) = e["$146"]
                ref = inner["$145"]
                text = str(texts[str(ref["name"])][ref["$403"]])
                style = styles.get(str(e.get("$157")), {})
                found[text] = (e, style, inner, outer, styles)
            walk(e.get("$146") or [], e)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    (pm,) = by_type(frags, "$265")
    return found, list(val(pm))


@pytest.fixture(scope="module", params=[True, False], ids=["rtl", "ltr"])
def book(calibre, request):  # noqa: F811
    return _book(calibre, f"turn-{request.param}", request.param)


@pytest.mark.parametrize("text", ["S01", "S04a", "S04b", "S05", "S06", "S08", "S10"])
def test_a_horizontal_span_is_an_inline_horizontal_container(book, text):
    found, _pm = book
    entry, style, inner, outer, _styles = found[text]
    assert str(entry["$156"]) == "$323" and str(entry["$159"]) == "$270"
    assert str(style["$560"]) == "$557" and float(style["$56"]["$307"]) == 100
    assert style["$478"] is True and style["$784"] is True
    assert str(inner["$159"]) == "$269"
    # Inline in a paragraph's words.
    assert outer is not None and str(outer["$159"]) == "$269"


def test_its_link_and_emphasis_are_runs_inside_it(book):
    found, _pm = book
    _e, _s, inner, outer, styles = found["S05"]
    (run,) = inner["$142"]
    assert "$179" in run and (run["$143"], run["$144"]) == (0, 3)
    # None outside it, as Previewer writes it.
    assert "$142" not in outer
    _e, _s, inner, _outer, styles = found["S06"]
    (run,) = inner["$142"]
    assert str(styles[str(run["$157"])]["$13"]) == "$361"
    _e, _s, inner, outer, styles = found["S08"]
    (run,) = inner["$142"]
    assert str(styles[str(run["$157"])]["$12"]) == "$382"
    # <em class="hz">'s own italic is inside, not on the paragraph.
    assert "$142" not in outer


def test_a_vertical_span_in_a_horizontal_block_is_a_vertical_container(book):
    found, _pm = book
    entry, style, _inner, _outer, _styles = found["R05"]
    assert str(entry["$156"]) == "$322"
    assert str(style["$560"]) == "$559" and float(style["$57"]["$307"]) == 100


def test_the_position_map_counts_it_as_previewer_does(book):
    # PSB S01: paragraph reaches the placeholder at k; the container at k
    # (one position), its paragraph at k + 1 (three, "S01"), the paragraph
    # again at k + 4, its offset past the placeholder.
    found, pm = book
    entry, _style, inner, outer, _styles = found["S01"]
    by_eid = {}
    for e in pm:
        by_eid.setdefault(int(e["$185"]), []).append(
            (int(e["$184"]), int(e.get("$143", 0)))
        )
    k = by_eid[int(entry["$155"])][0][0]
    assert by_eid[int(inner["$155"])] == [(k + 1, 0)]
    paragraph = by_eid[int(outer["$155"])]
    assert (k, 4) in paragraph and (k + 4, 5) in paragraph


def test_a_size_of_its_own_is_a_run_inside_it(book):
    # QA's Q765 Y10: <small> inside keeps its run, as Previewer writes it.
    found, _pm = book
    _e, _s, inner, _outer, styles = found["Y10"]
    (run,) = inner["$142"]
    assert (run["$143"], run["$144"]) == (0, 2)
    assert "$16" in styles[str(run["$157"])]
