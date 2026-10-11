"""Scene breaks through calibre's real Stylizer (#230).

A break inside a chapter is an `<hr>`, a gap set by `margin-top` or
`margin-bottom`, or a spacer paragraph holding a non-breaking space. All
three left no trace, so a change of scene read as a continuation. An `<hr>`
is now a rule ($596), as Kindle Previewer writes it; one whose CSS draws no
border, a spacer, or a margin beyond kfxgen's own paragraph spacing is space
($47) above the next paragraph. The unit tests use a fake resolver; this
checks what calibre's own sheet and computed CSS give the plugin.

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

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _build_epub,
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """
p { margin: 0; text-indent: 1.5em; }
p.break { margin-top: 2em; }
p.gapbelow { margin-bottom: 2em; }
hr.orn { border: none; border-top: 1px solid black; width: 30%; margin: 1em auto; }
hr.space { border: none; margin: 1em 0; height: 0; }
div.sec { margin-top: 3em; }
"""

#: (file, case id, markup, what follows the case's heading up to its END
#: line: "RULE" for a rule, else (text, its space above in line heights or
#: None)). Each case opens with "<id> lead", an indented paragraph.
CASES = [
    ("c1", "PLAIN", "<p>{c} lead</p><hr/><p>after</p>", ["RULE", ("after", None)]),
    (
        "c1",
        "STYLED",
        '<p>{c} lead</p><hr class="orn"/><p>after</p>',
        ["RULE", ("after", None)],
    ),
    (
        "c1",
        "NOBORDER",
        '<p>{c} lead</p><hr class="space"/><p>after</p>',
        [("after", "0.833333")],
    ),
    (
        "c1",
        "MARGINTOP",
        '<p>{c} lead</p><p class="break">after</p>',
        [("after", "1.66667")],
    ),
    (
        "c1",
        "MARGINBOTTOM",
        '<p class="gapbelow">{c} lead</p><p>after</p>',
        [("after", "1.66667")],
    ),
    (
        "c1",
        "SPACER",
        "<p>{c} lead</p><p>&#160;</p><p>after</p>",
        [("after", "1")],
    ),
    ("c1", "EMPTY", "<p>{c} lead</p><p> </p><p>after</p>", [("after", None)]),
    (
        "c1",
        "SECTION",
        '<p>{c} lead</p><div class="sec"><p>after</p><p>more</p></div>',
        [("after", "2.5"), ("more", None)],
    ),
]


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    tmp, _, version = calibre
    epub = tmp / "breaks.epub"
    _build_epub(epub, [c[:3] for c in CASES], CSS, {})
    kfx = tmp / "breaks.kfx"
    _ebook_convert(calibre, epub, kfx, native=True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    by_case, current = {}, None
    for story in by_type(frags, "$259"):
        for entry in iter_entries(val(story)["$146"]):
            ref = entry.get("$145")
            if ref is None:
                if current and str(entry.get("$159")) == "$596":
                    by_case[current].append("RULE")
                continue
            text = str(content[str(ref["name"])][int(ref["$403"])])
            if text.endswith(" lead") and text[:-5] in {c[1] for c in CASES}:
                current = text[:-5]
                by_case[current] = []
            elif text == f"END {current}":
                current = None
            elif current:
                st = {str(k): v for k, v in styles[str(entry["$157"])].items()}
                above = st.get("$47")
                indent = st.get("$36")
                # An indented paragraph has no $47 of its own: only space
                # the book asked for is written there.
                assert indent is not None
                by_case[current].append(
                    (text, None if above is None else str(above["$307"]))
                )
    return by_case, version


@pytest.mark.parametrize(
    "cid,expected", [(c[1], c[3]) for c in CASES], ids=[c[1] for c in CASES]
)
def test_scene_breaks_through_the_real_stylizer(converted, cid, expected):
    by_case, calibre_version = converted
    assert by_case.get(cid) == expected, f"calibre {calibre_version}, case {cid}"
