"""Links in plain paragraphs through calibre: one link run (#715).

Kindle Previewer 4.0.3, probe P715 (2026-11-06): a link is one run over
its whole text; a part of it in a sized, underlined or italic span is a
run of its own layered on the link run, with no link. The test shim reads
no CSS, so the styles from CSS classes are checked here; the tag cases are
in tests/unit/test_link_runs.py.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
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
from tests.unit.test_contents_pages import write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = (
    "span.big { font-size: 2em }"
    " span.u { text-decoration: underline }"
    " span.it { font-style: italic }"
    " a.b { font-weight: bold }"
    " span.sc { font-size: 0.7em }"
)
_A = "<a href='#t'>"
BODY = (
    "<h1>Links</h1>"
    f"<p>L12 {_A}small <span class='big'>big</span></a> after.</p>"
    f"<p>L18 {_A}u <span class='u'>under</span></a> after.</p>"
    f"<p>L21 {_A}one <span class='it'>slant</span> two</a> after.</p>"
    "<p>L22 <a href='#t' class='b'>T<span class='sc'>HE</span> "
    "R<span class='sc'>EST</span></a> after.</p>"
    f"<p>L23 {_A}<code>mono <i>slant</i></code></a> after.</p>"
    "<p id='t'>T99 target.</p>"
)


@pytest.fixture(scope="module")
def runs(calibre):  # noqa: F811
    tmp, _env, _version = calibre
    epub, kfx = tmp / "l715.epub", tmp / "l715.kfx"
    write_epub(
        epub,
        pages={"c.xhtml": BODY},
        toc=[("Links", "c.xhtml", 1)],
        css=CSS,
        ident="l715",
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found, faces = {}, {}
    for sl in by_type(frags, "$259"):
        for e in val(sl)["$146"]:
            ref = e.get("$145") if hasattr(e, "get") else None
            if not hasattr(ref, "get"):
                continue
            text = str(texts[str(ref["name"])][int(ref["$403"])])
            out = []
            for r in e.get("$142") or ():
                s = dict(styles.get(str(r.get("$157")), {}))
                a, n = int(r["$143"]), int(r["$144"])
                size = s.get("$16")
                out.append(
                    (
                        text[a : a + n],
                        "$179" in r,
                        round(float(size["$307"]), 3) if size else None,
                        str(s["$23"]) if "$23" in s else None,
                        "$12" in s,
                    )
                )
                faces.setdefault(text[:3], []).append(
                    (text[a : a + n], str(s.get("$13")), str(s.get("$11")))
                )
            found[text[:3]] = out
    return found, faces


# (run text, links, $16, $23, italic), as Previewer writes them.
@pytest.mark.parametrize(
    "label, expected",
    [
        (
            "L12",
            [("small big", True, None, None, False), ("big", False, 2.0, None, False)],
        ),
        (
            "L18",
            [
                ("u under", True, None, None, False),
                ("under", False, None, "$328", False),
            ],
        ),
        (
            "L21",
            [
                ("one slant two", True, None, None, False),
                ("slant", False, None, None, True),
            ],
        ),
    ],
)
def test_a_styled_part_of_a_link_is_layered_on_one_link_run(runs, label, expected):
    assert runs[0][label] == expected


def test_a_run_in_a_link_keeps_the_links_weight_and_font(runs):
    # pg8492's contents page: small capitals in a bold link. Each sized
    # part is layered on the bold link run and stays bold; it was written
    # normal weight, measured against the paragraph.
    faces = runs[1]
    assert [w for t, w, _f in faces["L22"] if t in ("HE", "EST")] == ["$361"] * 2
    # A slanted word in a code link keeps the code's font.
    ((_t, _w, family),) = [x for x in faces["L23"] if x[0] == "slant"]
    assert family == [f for t, _w, f in faces["L23"] if t == "mono slant"][0]
