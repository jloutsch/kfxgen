"""Nested superscripts and subscripts through calibre, as Kindle Previewer
4.0.3 writes them (#536, my probe 2026-10-29): the outer shift on its text,
then a run layered over the inner text with the inner shift. Previewer's
layered text-top run has no size of its own, so it keeps the superscript's;
calibre's computed size for the span must not shrink it.

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from kfxgen.native_generator import SUPERSCRIPT_FONT_SIZE as S  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_part_headings import (  # noqa: E402
    _book_epub,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BODY = (
    "<h1>Nested</h1>"
    "<p>N1 x<sup>2<sub>i</sub></sup> end.</p>"
    "<p>N2 y<sub>j<sup>3</sup></sub> end.</p>"
    "<p>N3 a<sup>b<span style='vertical-align:text-top'>c</span>d</sup> end.</p>"
    "<p>N6 plain<sup>9</sup> and H<sub>2</sub>O.</p>"
    "<p>P7 a<sup>b<span style='vertical-align:baseline'>c</span>d</sup> end.</p>"
    "<p>P8 e<sup>f<sub style='font-size:100%'>g</sub>h</sup> end.</p>"
    "<p>P9 e<sup>f<sub style='font-size:100%'>g<sup>z</sup></sub></sup> end.</p>"
    "<p>PB note<sup><span style='vertical-align:baseline'>12</span></sup> end.</p>"
    "<p>PC x<sup><span style='vertical-align:baseline'>1</span></sup><sup>2</sup> end.</p>"
    "<p>PD x<sup>1</sup><sup><span style='vertical-align:baseline'>2</span></sup> end.</p>"
    "<p>PE m<sup><span style='vertical-align:baseline'>1</span> "
    "<span style='vertical-align:baseline'>2</span></sup> end.</p>"
    "<p>PF m<sup> <span style='vertical-align:baseline'>1</span></sup> end.</p>"
    "<p>PG m<sup><span style='vertical-align:baseline'>1</span> </sup> end.</p>"
)


def _plain(x):
    return getattr(x, "value", x)


def _runs(kfx):
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not (hasattr(e, "get") and e.get("$145")):
                continue
            t = str(texts[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            runs = []
            for r in e.get("$142") or ():
                r = _plain(r)
                a, n = int(r["$143"]), int(r["$144"])
                y = styles.get(str(r.get("$157")), {})
                size = y.get("$16")
                runs.append(
                    (
                        t[a : a + n],
                        str(y.get("$44")),
                        round(float(_plain(size)["$307"]), 4) if size else None,
                    )
                )
            got[t[:2]] = runs
    return got


def test_nested_shifts_are_layered_as_previewer_writes_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "n536.epub", tmp / "n536.kfx"
    _book_epub(
        epub,
        "N536",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Nested", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    runs = _runs(kfx)
    shifts = {k: [(t, v) for t, v, _s in r] for k, r in runs.items()}
    assert shifts["N1"] == [("2", "$370"), ("i", "$370"), ("i", "$371")], version
    assert shifts["N2"] == [("j", "$371"), ("3", "$371"), ("3", "$370")], version
    assert ("c", "$447") in shifts["N3"], version
    assert shifts["N6"] == [("9", "$370"), ("2", "$371")], version
    # The layered runs' sizes: the inner shift's on the outer's, and text-top
    # the superscript's own.
    assert runs["N1"][2][2] == round(S * S, 4), version
    assert [s for t, v, s in runs["N3"] if v == "$447"] == [round(S, 4)], version


def test_baseline_and_a_kept_size_are_layered_as_previewer_writes_them(calibre):  # noqa: F811
    """`vertical-align: baseline` inside a superscript is a layer at the
    baseline ($44 $350), and a subscript that keeps its superscript's size
    (font-size: 100%) is layered at that size, as Kindle Previewer 4.0.3
    writes them (#638, my probe 2026-10-29)."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "n638.epub", tmp / "n638.kfx"
    _book_epub(
        epub,
        "N638",
        BODY,
        [("c1.xhtml", "Next")],
        [("c0", "contents.xhtml", "Nested", []), ("c1", "c1.xhtml", "Next", [])],
    )
    _ebook_convert(calibre, epub, kfx, True)
    runs = _runs(kfx)
    assert ("c", "$350", round(S, 4)) in runs["P7"], f"calibre {version}: {runs['P7']}"
    assert ("g", "$371", round(S, 4)) in runs["P8"], f"calibre {version}: {runs['P8']}"
    # A baseline span that is all of the superscript: no layer, as Previewer
    # writes a note marker reset this way (QA on #641).
    assert [v for t, v, _s in runs["PB"]] == ["$370"], (
        f"calibre {version}: {runs['PB']}"
    )
    # A space in the <sup> counts as its text: the baseline spans are part of
    # it and layered, as Previewer writes them (QA's P34, P41, P42 on #641).
    for key in ("PE", "PF", "PG"):
        assert "$350" in {v for t, v, _s in runs[key]}, (
            f"calibre {version}: {runs[key]}"
        )
    # Each <sup> judged on its own, a superscript beside it apart (QA on #641).
    for key in ("PC", "PD"):
        assert {v for t, v, _s in runs[key]} == {"$370"}, (
            f"calibre {version}: {runs[key]}"
        )
    # Under a layer at its parent's size, the next one shrinks from that size.
    assert ("z", "$370", round(S * S, 4)) in runs["P9"], (
        f"calibre {version}: {runs['P9']}"
    )
