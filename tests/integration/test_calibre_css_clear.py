"""CSS `clear`, through calibre's real Stylizer (#381).

calibre must report `clear` as declared, on a paragraph, an empty `<div>`,
a `<div>` around a picture and an `<hr>`; the unit tests' fake style can
only assume it. This converts a book of cleared elements with calibre and
checks the `$628` on each.

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

CSS = """img.fl { float: left; width: 30%; }
.cb { clear: both; } .cl { clear: left; } .cr { clear: right; }
div.clearer { clear: both; height: 0; }
hr.cb { border-top: 1px solid black; }
img.w20 { width: 20%; }"""

PICTURE = '<img class="fl" src="i.jpg" alt="fl"/>'
WORDS = "words after the picture. " * 4

#: Each case's text starts with its name.
CASES = [
    ("both", f'<p>{PICTURE}Short.</p><h3 class="cb">Both heading</h3><p>{WORDS}</p>'),
    ("left", f'<p>{PICTURE}Short.</p><p class="cl">Left {WORDS}</p>'),
    ("right", f'<p>{PICTURE}Short.</p><p class="cr">Right {WORDS}</p>'),
    ("empty", f'<p>{PICTURE}Short.</p><div class="clearer"></div><p>Empty {WORDS}</p>'),
    ("plain", f'<p>{PICTURE}Short.</p><p>Plain {WORDS}</p>'),
    ("figure", f'<p>Figure {WORDS}</p><div class="cb"><img class="w20" src="i.jpg" alt="figure"/></div>'),
    ("rule", f'<p>Rule {WORDS}</p><hr class="cb"/><p>{WORDS}</p>'),
]  # fmt: skip


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{first word of each text entry or alt of each picture: its $628 or
    None}, the rules' $628s, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "clear.epub", tmp / "clear.kfx"
    _build_floats(epub, CASES, CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out, rules = {}, []

    def clear(e):
        v = styles.get(str(e.get("$157")), {}).get("$628")
        return None if v is None else str(v)

    def walk(entries):
        for e in entries:
            if isinstance(e, str):
                continue
            if str(e.get("$159")) == "$271":
                out[str(e.get("$584"))] = clear(e)
            elif str(e.get("$159")) == "$596":
                rules.append(clear(e))
            elif e.get("$145"):
                r = e["$145"]
                word = str(texts[str(r["name"])][r["$403"]]).split()[0]
                out.setdefault(word, clear(e))
            walk(e.get("$146") or [])

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"])
    return out, rules, version


@pytest.mark.parametrize(
    ("word", "symbol"),
    [
        ("Both", "$421"),
        ("Left", "$59"),
        ("Right", "$61"),
        # An empty clearing div clears the next paragraph.
        ("Empty", "$421"),
        ("Plain", None),
        ("Short.", None),
        # A cleared div around a picture clears the picture.
        ("figure", "$421"),
        ("fl", None),
    ],
)
def test_clear_reaches_the_entry(converted, word, symbol):
    by_word, _rules, version = converted
    assert by_word[word] == symbol, f"calibre {version}"


def test_a_cleared_rule_has_628(converted):
    _by_word, rules, version = converted
    assert rules == ["$421"], f"calibre {version}"
