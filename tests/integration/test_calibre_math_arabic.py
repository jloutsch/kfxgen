"""Arabic MathML and right-to-left equations, through calibre (#708).

calibre reaches `<math>` before kfxgen does; `dir="rtl"` must survive it.
Probe P708's M01 and M04: each Arabic character is text in the drawing,
in the mirrored order Kindle Previewer 4.0.3 lays it out, a word as one
text (tests/unit/test_math_arabic.py has the rest).

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'
BODY = (
    "<h1>رياضيات</h1>"
    f"<p>M01 <math {M} dir='rtl'><mi>س</mi><mo>+</mo><mi>ص</mi><mo>=</mo>"
    "<mi>ع</mi></math> نهاية.</p>"
    f"<p>M04 <math {M} dir='rtl'><mi>ق</mi><mo>(</mo><mi>س</mi><mo>)</mo>"
    "<mo>=</mo><mtext>دالة</mtext></math> نهاية.</p>"
)


@pytest.fixture(scope="module")
def shapes(calibre):  # noqa: F811
    """[[text of each child, in order] for each drawing] and its paths."""
    tmp, _env, _version = calibre
    epub, kfx = tmp / "m708.epub", tmp / "m708.kfx"
    write_epub(
        epub,
        html_attrs=' xml:lang="ar" lang="ar" dir="rtl"',
        pages={"c.xhtml": BODY},
        toc=[("رياضيات", "c.xhtml", 1)],
        ident="m708",
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    texts = {
        str(f.fid): [str(x) for x in val(f)["$146"]] for f in by_type(frags, "$145")
    }
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and str(e.get("$159")) == "$272":
                kids = {
                    int(k["$155"]): texts[str(k["$145"]["name"])][
                        int(k["$145"]["$403"])
                    ]
                    for k in e.get("$146") or ()
                }
                order = [
                    kids[int(p["$474"])] if "$474" in p else "path" for p in e["$250"]
                ]
                out.append(order)
    return out


def test_arabic_is_text_in_the_mirrored_order(shapes):
    # M01 س + ص = ع laid out ع = ص + س, as Previewer lays it out.
    assert shapes[0] == ["ع", "path", "ص", "path", "س"]


def test_a_word_is_one_text_and_brackets_are_mirrored(shapes):
    # M04 ق(س) = دالة: دالة = (س) ق, the word one text.
    assert shapes[1] == ["دالة", "path", "path", "س", "path", "ق"]
