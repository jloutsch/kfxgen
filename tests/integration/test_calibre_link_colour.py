"""A link's colour through calibre, as Kindle Previewer writes it (#728).

Probes P728b and P728c, Previewer 4.0.3's values: a link's own colour, and
a colour set inside a link, are `$576`/`$577` `{$19: ARGB}` on the link's
runs, as the book gives it; a link inside coloured text keeps that text's
`$19`; a black or near-black link on the page has no colour of its own,
and is drawn in its paragraph's (QA on #732, P732 B05, P732d); in a box a
black link is written as the link's colour. kfxgen layers a link's
inner runs on its run (#715), so the inner run may hold the colour where
Previewer's link run does: the tests read the colour each word is drawn in.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = (
    ".grey { color: #999999 } .red { color: #ff0000 } a.grey { color: #999999 }"
    " p.gp { color: #999999 } .blk { color: black }"
    " .box { background-color: #333333; color: white; padding: 0.5em }"
    " .box a { color: #ffff00 } .box a.blk { color: black } .nb { color: #1a1a1a }"
    " .dg { color: #202020 }"
    " .dbox { background-color: #222222; color: #333333; padding: 0.5em }"
    " .dbox a.blk { color: black }"
)
BODY = (
    "<h1>Links</h1>"
    "<p>Q01 <span class='red'>around <a class='grey' href='#t'>innerlink</a>"
    " it</span> end.</p>"
    "<p>Q03 para <a href='#t'>plainlink</a> end.</p>"
    "<p class='gp'>Q04 grey para <a href='#t'>paralink</a> end.</p>"
    "<p>Q06 <a class='grey' href='#t'>grey <span class='red'>redword</span>"
    " end</a> after.</p>"
    "<p>Q07 note<a class='grey' href='#t'><sup>1</sup></a> after.</p>"
    "<div class='box'><p>Q09 box <a href='#t'>yellowlink</a> end.</p></div>"
    "<p class='gp'>Q12 grey para <a class='blk' href='#t'>blackinlink</a> end.</p>"
    "<p>B05 <span class='red'>red <a class='blk' href='#t'>blackinred</a></span>.</p>"
    "<p class='gp'>D01 <a class='nb' href='#t'>nearblack</a> and"
    " <a class='dg' href='#t'>darkgrey</a>.</p>"
    "<div class='box'><p>X01 box <a class='blk' href='#t'>blackinbox</a> end.</p></div>"
    "<div class='dbox'><p>X02 dark <a class='blk' href='#t'>blackindark</a> end.</p></div>"
    "<p class='gp'>U01 grey <a class='blk' href='#nowhere'>unfoundblack</a> end.</p>"
    + "<p>Filler.</p>" * 5
    + "<p id='t'>T1 target.</p>"
)
GREY, RED = 0xFF999999, 0xFFFF0000


@pytest.fixture(scope="module")
def drawn(calibre):  # noqa: F811
    """{paragraph text: (entry $157, [(start, end, run $157)])}, outer runs
    first."""
    tmp, env, _version = calibre
    epub, kfx = tmp / "lc728.epub", tmp / "lc728.kfx"
    write_epub(
        epub,
        pages={"c.xhtml": BODY},
        toc=[("Links", "c.xhtml", 1)],
        css=CSS,
        ident="lc728",
    )
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx)], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            ref = e.get("$145") if hasattr(e, "get") else None
            if not ref:
                continue
            text = str(texts[str(ref["name"])][ref["$403"]])
            out[text] = (
                styles.get(str(e.get("$157")), {}),
                [
                    (
                        int(r["$143"]),
                        int(r["$143"]) + int(r["$144"]),
                        styles.get(str(r.get("$157")), {}),
                    )
                    for r in e.get("$142") or ()
                ],
            )
    return out


def _at(drawn, line, word):
    """(link colour, `$19`) `word` is drawn in, in the paragraph starting
    `line`: from the innermost run over it with each, kfxgen's inner runs
    layered on the link's, else the paragraph's own `$19`."""
    (text,) = [t for t in drawn if t.startswith(line + " ")]
    at = text.index(word, len(line))
    entry, runs = drawn[text]
    over = [entry] + [s for a, b, s in runs if a <= at and at + len(word) <= b]
    link = next((s["$576"]["$19"] for s in reversed(over) if "$576" in s), None)
    colour = next((s["$19"] for s in reversed(over) if "$19" in s), None)
    return link, colour


def test_a_coloured_link_in_red_text_has_only_its_link_colour(drawn):
    assert _at(drawn, "Q01", "innerlink") == (GREY, None)
    assert _at(drawn, "Q01", "around")[1] == 0xFFEE0000


def test_a_plain_link_has_no_link_colour(drawn):
    assert _at(drawn, "Q03", "plainlink") == (None, None)
    # In a grey paragraph: its grey, darkened as text (#239).
    assert _at(drawn, "Q04", "paralink")[1] in (0xFF757575, 0xFF767676)
    assert _at(drawn, "Q04", "paralink")[0] is None


def test_a_span_inside_a_coloured_link_is_its_own_link_colour(drawn):
    assert _at(drawn, "Q06", "grey")[0] == GREY
    assert _at(drawn, "Q06", "redword")[0] == RED


def test_a_footnote_marker_takes_its_links_colour(drawn):
    assert _at(drawn, "Q07", "1")[0] == GREY


def test_the_colour_is_not_measured_against_a_box(drawn):
    # Previewer writes the yellow as given on the #333333 box.
    assert _at(drawn, "Q09", "yellowlink")[0] == 0xFFFFFF00


def test_a_black_link_in_red_text_is_black(drawn):
    # QA on #732, P732 B05: Previewer draws it black, the red dropped.
    assert _at(drawn, "B05", "blackinred") == (None, None)
    assert _at(drawn, "B05", "red")[1] == 0xFFEE0000


def test_a_near_black_link_has_none_and_a_dark_grey_one_has_its_own(drawn):
    # P732d: none up to 0x1A in every channel, written from 0x20.
    assert _at(drawn, "D01", "nearblack")[0] is None
    assert _at(drawn, "D01", "darkgrey")[0] == 0xFF202020


def test_a_black_link_in_a_box_is_written_black(drawn):
    # P732 B01-B04: Previewer writes $576 black in a box; the box's white
    # text would draw it white otherwise.
    assert _at(drawn, "X01", "blackinbox")[0] == 0xFF000000


def test_a_black_link_in_a_grey_paragraph_is_grey(drawn):
    link, colour = _at(drawn, "Q12", "blackinlink")
    assert link is None
    # #999999 on the page, darkened to 4.5:1 as text (#239); Previewer
    # writes 0xFF757575, kfxgen 0xFF767676.
    assert colour in (0xFF757575, 0xFF767676)


def test_a_black_link_on_a_dark_box_is_written_black(drawn):
    # QA on #732, P732 B03: the box's #333333 text and black both darken to
    # the same colour; Previewer writes the link's black as given.
    assert _at(drawn, "X02", "blackindark")[0] == 0xFF000000


def test_a_black_link_whose_target_isnt_found_is_black_text(drawn):
    # P732b B01-B04: written as text, $19 black, in a grey paragraph.
    assert _at(drawn, "U01", "unfoundblack") == (None, 0xFF000000)
