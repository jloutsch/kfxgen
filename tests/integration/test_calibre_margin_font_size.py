"""A margin or padding measured as Kindle Previewer 4.0.3 measures it (#647):
an em in the element's own font size, a px, pt or rem in the main text's;
two touching margins compared at those sizes, and the gap written in the
receiving paragraph's own line heights (my probes, 2026-10-31, pages with a
DOCTYPE).

kfxgen read every margin as the element's own ems, and a box's at the main
size whatever the box's font size. After `pre { margin: 0 0 1.5em;
font-size: 80% }` that put 1.25 lh under the code listing where Previewer
puts 1 (1.2em); QA counted 727, 221 and 67 such paragraph pairs in its three
lowest-matching library books.

Previewer writes a box's own font size on the box (`$16`) and its margins
and padding in that size's line heights. kfxgen writes a box at the main
size, so for a box the expected value is Previewer's times the box's font
size: the same space on the page.

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

BOX = "border:1px solid black;"
F80 = "font-size:80%;"


def _p(label, style="margin:0"):
    return f"<p style='{style}'>{label} words.</p>"


def _pre(label, style):
    return f"<pre style='{style}'>{label} code</pre>"


BODY = (
    "<h1>Sizes</h1>" + _p("A0")
    # A code listing at 80% with 1.5em below: 1.2em, written 1 lh under it.
    + _pre("A1", BOX + F80 + "margin:0 0 1.5em") + _p("A2")
    # Against the next paragraph's 1em: 1.2em is the larger.
    + _pre("F1", BOX + F80 + "margin:0 0 1.5em") + _p("F2", "margin:1em 0 0")
    # Against 0.75em: the gap goes on the paragraph, in its lh.
    + _pre("H1", BOX + F80 + "margin:0 0 1.5em") + _p("H2", "margin:0.75em 0")
    # The paragraph at 80% too: 1.2em in its own 0.96em lines, 1.25 lh.
    + _pre("K1", BOX + F80 + "margin:0 0 1.5em") + _p("K2", F80 + "margin:0.75em 0")
    # A px or rem margin is the main text's, whatever the paragraph's size.
    + _p("V1", F80 + "margin:0 0 24px") + _p("V2")
    + _p("W1", F80 + "margin:0 0 1.5rem") + _p("W2")
    # A larger box: its margin is larger too.
    + f"<div style='{BOX}font-size:150%;margin:0 0 1.5em'><p style='margin:0'>R1 big</p></div>"
    + _p("R2")
    # A box's padding: an em is its own, a px the main text's.
    + _pre("P1", BOX + F80 + "margin:0;padding:1em") + _p("P2")
    + _pre("Y1", BOX + F80 + "margin:0;padding:24px") + _p("Y2")
    + _p("Z0")
)  # fmt: skip

#: (where, key, expected lh): Kindle Previewer 4.0.3's value, times the box's
#: font size for a box (see the module docstring).
EXPECTED = {
    "A1": ("box", "$49", 1.25 * 0.8),
    "F2": ("own", "$47", 1.0),
    "H2": ("own", "$47", 1.0),
    "K2": ("own", "$47", 1.25),
    "V1": ("own", "$49", 0.9375),
    "W1": ("own", "$49", 1.562),
    "R1": ("box", "$49", 1.25 * 1.5),
    "P1": ("box", "$52", 0.8333 * 0.8),
    "Y1": ("box", "$52", 0.9375 * 0.8),
}


def _plain(x):
    return getattr(x, "value", x)


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{case: (its box's style or None, its own style)}, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "m647.epub", tmp / "m647.kfx"
    _book_epub(
        epub,
        "M647",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Sizes", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = {}

    def walk(e, box):
        e = _plain(e)
        if not hasattr(e, "get"):
            return
        style = {str(k): v for k, v in styles.get(str(e.get("$157")), {}).items()}
        if e.get("$145"):
            t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            got[t[:2]] = (box, style)
            return
        for k in e.get("$146") or ():
            walk(k, style if str(e.get("$159")) == "$270" else box)

    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            walk(e, None)
    return got, version


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_a_margin_is_measured_in_its_elements_font_size(converted, case):
    got, version = converted
    box, own = got[case]
    where, key, want = EXPECTED[case]
    style = box if where == "box" else own
    assert style is not None, f"calibre {version}: {case} is not in a box"
    v = style.get(key)
    lh = round(float(_plain(v)["$307"]), 3) if v is not None else None
    assert lh is not None and abs(lh - want) < 0.002, (
        f"calibre {version}: {case} {key} = {lh}, want {want:.4g}: {style}"
    )
