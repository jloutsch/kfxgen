"""A link's colour from a `:link` or `:visited` rule, through calibre (#733).

Probe P733, Kindle Previewer 4.0.3's values: `:link` and `:visited` count as
always true, so such a rule colours a link as the same rule without them
would, by specificity and then order, and Previewer writes the colour as
the link's, `$576`/`$577`. `:hover`, `:active` and `:focus` give none, and
a colour in the link's `style` attribute wins. calibre flattens the CSS
before kfxgen reads it, losing which of these rules came later: kfxgen
reads them first, in `specialize_css_for_output`, which only a real
conversion runs.

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
from tests.integration.test_calibre_link_colour import _at  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = (
    "a.l01:link { color: green }"
    " a.l02:visited { color: purple }"
    " a.l03:link { color: green } a.l03:visited { color: purple }"
    " a.l04:visited { color: purple } a.l04:link { color: green }"
    " a.l05:hover { color: orange }"
    " a.l07:link { color: green }"
    " .l08:link { color: green } .l08.l08x { color: #999999 }"
    " .l09.l09x { color: #999999 } .l09:link { color: green }"
    " a.l12:link { color: green } .red { color: red }"
    " p.g18 a:link { color: blue }"
    " a.l19:link, a.l19:visited { color: green }"
    " a.l20:link { color: green } a.l20:hover { color: orange }"
    " a.l21:link { color: green }"
    " a.l27:link { color: green } a.l27 { color: #999999 }"
    " p.v1 a:link { color: green } p.v1 a:visited { color: purple }"
    " p.v2 a { color: #999999 } p.v2 a:visited { color: purple }"
    " p.v3 a[href]:visited { color: purple }"
    " .va:link { color: green } .va:visited { color: purple }"
    " .VB:VISITED { color: purple }"
)
BODY = (
    "<h1>Links</h1>"
    "<p>L01 x <a class='l01' href='#t'>linkrule</a> after.</p>"
    "<p>L02 x <a class='l02' href='#t'>visitedrule</a> after.</p>"
    "<p>L03 x <a class='l03' href='#t'>visitedlater</a> after.</p>"
    "<p>L04 x <a class='l04' href='#t'>linklater</a> after.</p>"
    "<p>L05 x <a class='l05' href='#t'>hoverrule</a> after.</p>"
    "<p>L07 x <a class='l07' href='#t' style='color: #999999'>inlinegrey</a> after.</p>"
    "<p>L08 x <a class='l08 l08x' href='#t'>plainlater</a> after.</p>"
    "<p>L09 x <a class='l09 l09x' href='#t'>pseudolater</a> after.</p>"
    "<p>L12 x <a class='l12' href='#t'>green <span class='red'>redword</span>"
    " end</a> after.</p>"
    "<p class='g18'>L18 x <a href='#t'>descendant</a> after.</p>"
    "<p>L19 x <a class='l19' href='#t'>grouped</a> after.</p>"
    "<p>L20 x <a class='l20' href='#t'>hoverafter</a> after.</p>"
    "<p>L21 x <a class='l21' id='n21'>nohref</a> after.</p>"
    "<p>L27 x <a class='l27' href='#t'>morespecific</a> after.</p>"
    "<p>L28 x <a href='#t'>plainlink</a> after.</p>"
    "<p class='v1'>V1 x <a href='#t'>barevisited</a> after.</p>"
    "<p class='v2'>V2 x <a href='#t'>plainbefore</a> after.</p>"
    "<p class='v3'>V3 x <a href='#t'>hrefvisited</a> after.</p>"
    "<p>V4 x <a class='va' href='#t'>classa</a> after.</p>"
    "<p>V5 x <a class='VB' href='#t'>uppervisited</a> after.</p>"
    + "<p>Filler.</p>" * 5
    + "<p id='t'>T1 target.</p>"
)
GREEN, PURPLE, BLUE, GREY, RED = (
    0xFF008000,
    0xFF800080,
    0xFF0000FF,
    0xFF999999,
    0xFFFF0000,
)


@pytest.fixture(scope="module")
def drawn(calibre):  # noqa: F811
    """{paragraph text: (entry $157, [(start, end, run $157)])}."""
    tmp, env, _version = calibre
    epub, kfx = tmp / "ls733.epub", tmp / "ls733.kfx"
    write_epub(
        epub,
        pages={"c.xhtml": BODY},
        toc=[("Links", "c.xhtml", 1)],
        css=CSS,
        ident="ls733",
    )
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx)], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    assert "could not record link colours" not in run.stdout + run.stderr
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


def _link(drawn, line, word):
    return _at(drawn, line, word)[0]


def test_a_link_or_visited_rule_is_the_links_colour(drawn):
    assert _link(drawn, "L01", "linkrule") == GREEN
    assert _link(drawn, "L02", "visitedrule") == PURPLE
    assert _link(drawn, "L19", "grouped") == GREEN


def test_the_later_of_link_and_visited_wins(drawn):
    assert _link(drawn, "L03", "visitedlater") == PURPLE
    assert _link(drawn, "L04", "linklater") == GREEN


def test_specificity_then_order_decide_against_a_plain_rule(drawn):
    assert _link(drawn, "L08", "plainlater") == GREY
    assert _link(drawn, "L09", "pseudolater") == GREEN
    assert _link(drawn, "L27", "morespecific") == GREEN


def test_hover_gives_no_colour(drawn):
    assert _link(drawn, "L05", "hoverrule") is None
    assert _link(drawn, "L20", "hoverafter") == GREEN


def test_a_colour_in_the_links_style_wins(drawn):
    assert _link(drawn, "L07", "inlinegrey") == GREY


def test_a_span_inside_keeps_its_own_colour(drawn):
    assert _link(drawn, "L12", "green") == GREEN
    assert _link(drawn, "L12", "redword") == RED
    assert _link(drawn, "L12", "end") == GREEN


def test_a_descendant_rule_and_links_without_a_rule(drawn):
    assert _link(drawn, "L18", "descendant") == BLUE
    assert _at(drawn, "L21", "nohref") == (None, None)
    assert _at(drawn, "L28", "plainlink") == (None, None)


def test_a_rule_with_a_bare_a_visited_is_dropped(drawn):
    # P741m/n/o (#741 QA): `a:visited` after a bare `a` is dropped, wherever
    # it is in the selector; a qualified one (`a[href]:visited`) is kept.
    assert _link(drawn, "V1", "barevisited") == GREEN
    assert _link(drawn, "V2", "plainbefore") == GREY
    assert _link(drawn, "V3", "hrefvisited") == PURPLE
    # P741r: after a class name ending in `a` too, but a class keeps its case.
    assert _link(drawn, "V4", "classa") == GREEN
    assert _link(drawn, "V5", "uppervisited") == PURPLE
