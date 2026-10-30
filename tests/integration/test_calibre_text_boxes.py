"""A bordered or filled <pre> or <p> is drawn as a box, as Kindle Previewer
draws it, through calibre's real Stylizer (#643).

kfxgen drew boxes only for <div>, <aside>, <section>, <blockquote> and
<article> (#238), so a code listing's fill, borders and padding were lost.
In QA's three lowest-matching library books, Previewer draws 1,803 such
<pre> blocks as `$270` boxes and kfxgen none.

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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_part_headings import (  # noqa: E402
    _book_epub,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _p(label, style):
    return f"<p style='{style}'>{label} words words words words words.</p>"


CODE = (
    "border-top:1px solid #DDD;border-bottom:1px solid #DDD;"
    "background-color:#fbfbfb;padding:1em 2em;margin:0"
)
BODY = (
    "<h1>Boxes</h1>" + _p("Q0", "margin:0")
    + f"<pre style='{CODE}'>Q1 code line one\nQ1 code line two</pre>" + _p("Q2", "margin:0")
    + "<pre style='background-color:#eeeeee;margin:0'>Q3 filled pre</pre>" + _p("Q4", "margin:0")
    + "<pre style='border:1px solid black;margin:0'>Q5 bordered pre</pre>" + _p("Q6", "margin:0")
    + "<pre style='padding:1em 0;margin:0'>Q7 padded pre</pre>" + _p("Q8", "margin:0")
    + _p("Q9", "border:1px solid black;padding:0.5em;margin:1em 0") + _p("QA", "margin:0")
    + _p("QB", "background-color:#eeeeee;margin:0") + _p("QC", "margin:0")
    + "<pre style='margin:0'>QD plain pre</pre>" + _p("QE", "margin:0")
)  # fmt: skip

#: Kindle Previewer 4.0.3's box around each case (my probe, 2026-10-29):
#: its fill, whether it draws a border, and its padding top and bottom.
PREVIEWER = {
    "Q1": (0xFFFBFBFB, True, 0.833),
    "Q3": (0xFFEEEEEE, False, None),
    "Q5": (None, True, None),
    "Q9": (None, True, 0.417),
    "QB": (0xFFEEEEEE, False, None),
}
BORDER_KEYS = {"$88", "$89", "$90", "$91", "$92"}


def _plain(x):
    return getattr(x, "value", x)


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{case: (its box's style or None, its own style)}, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "q643.epub", tmp / "q643.kfx"
    _book_epub(
        epub,
        "Q643",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Boxes", []), ("c1", "c1.xhtml", "Next", [])],
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


def _lh(style, key):
    v = style.get(key)
    return round(float(_plain(v)["$307"]), 3) if v is not None else None


@pytest.mark.parametrize("case", sorted(PREVIEWER))
def test_a_bordered_or_filled_pre_or_p_is_a_box(converted, case):
    got, version = converted
    box, own = got[case]
    fill, bordered, padding = PREVIEWER[case]
    assert box is not None, f"calibre {version}: {case} is not in a box"
    assert (int(box["$70"]) if "$70" in box else None) == fill, (
        f"calibre {version}: {box}"
    )
    assert bool(BORDER_KEYS & set(box)) == bordered, f"calibre {version}: {box}"
    # The box takes the margins; its text has none of its own.
    assert not {"$47", "$49"} & set(own), f"calibre {version}: {own}"
    # The top only: below a box kfxgen adds its own line to the padding
    # (#238), where Previewer uses the margins (#643 item 1).
    if padding is not None:
        assert _lh(box, "$52") == padding, f"calibre {version}: {box}"


@pytest.mark.parametrize("case", ["Q0", "Q2", "Q7", "QD", "QE"])
def test_an_unboxed_pre_or_p_stays_on_the_page(converted, case):
    got, version = converted
    assert got[case][0] is None, f"calibre {version}: {case} is in a box"


def test_a_padded_pre_is_spaced_by_its_padding(converted):
    """Previewer writes a padded <pre> with no border or fill as `$52`/`$54`
    0.833 on its text, with no box; kfxgen writes the same space as margins."""
    got, version = converted
    own = got["Q7"][1]
    after = got["Q8"][1]
    assert _lh(own, "$47") == 0.833, f"calibre {version}: {own}"
    assert (_lh(own, "$49") or _lh(after, "$47")) == 0.833, (
        f"calibre {version}: {own} {after}"
    )
