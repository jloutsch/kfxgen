"""A picture in a sized wrapper, through calibre's real Stylizer (#382).

calibre reports a wrapper's `width`, `float` and side margins as declared,
and a `<figure>`'s too; the unit tests' fake style can only assume it. This
converts a book of sized wrappers with calibre and checks each picture's
container.

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

CSS = """div.w40 { width: 40%; } div.w40 img { width: 100%; }
figure.w40 { width: 40%; margin: 0; } figure.w40 img { width: 100%; }
div.f40 { float: left; width: 40%; } div.f40 img { width: 100%; }
div.c40 { width: 40%; margin: 0 auto; } div.c40 img { width: 100%; }
div.w300 { width: 300px; } div.w300 img { width: 100%; }
div.open { width: 40%; }
figure.plain { margin: 0; } figure.plain img { width: 50%; }"""

WORDS = "words after the picture. " * 4

#: (alt, markup): each picture's alt names its case.
CASES = [
    ("div", f'<div class="w40"><img src="i.jpg" alt="div"/><p>Cap div.</p></div><p>{WORDS}</p>'),
    ("figure", f'<figure class="w40"><img src="i.jpg" alt="figure"/><figcaption>Cap fig.</figcaption></figure><p>{WORDS}</p>'),
    ("float", f'<div class="f40"><img src="i.jpg" alt="float"/><p>Cap float.</p></div><p>{WORDS}</p>'),
    ("linkfloat", f'<div class="f40"><a href="c.xhtml#end"><img src="i.jpg" alt="linkfloat"/></a><p>Cap link.</p></div><p id="end">{WORDS}</p>'),
    ("centre", f'<div class="c40"><img src="i.jpg" alt="centre"/><p>Cap centre.</p></div><p>{WORDS}</p>'),
    ("px", f'<div class="w300"><img src="i.jpg" alt="px"/><p>Cap px.</p></div><p>{WORDS}</p>'),
    ("nowidth", f'<div class="open"><img src="i.jpg" alt="nowidth"/><p>Cap open.</p></div><p>{WORDS}</p>'),
    ("plain", f'<figure class="plain"><img src="i.jpg" alt="plain"/><figcaption>Cap plain.</figcaption></figure><p>{WORDS}</p>'),
]  # fmt: skip


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{alt: (the nearest container type, its style, picture style)}, and
    calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "figures.epub", tmp / "figures.kfx"
    _build_floats(epub, CASES, CSS)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = {}

    def walk(entries, parent):
        for e in entries:
            if isinstance(e, str):
                continue
            if str(e.get("$159")) == "$271":
                p = parent or {}
                out[str(e.get("$584"))] = (
                    str(p.get("$159")) if parent is not None else None,
                    styles.get(str(p.get("$157")), {}),
                    styles.get(str(e.get("$157")), {}),
                )
            # The nearest container with a width: a linked picture's own
            # parent is its link's container.
            sized = "$56" in styles.get(str(e.get("$157")), {})
            walk(e.get("$146") or [], e if sized or parent is None else parent)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return out, version


def _pct(style):
    return round(float(style["$56"]["$307"]), 3)


@pytest.mark.parametrize(
    ("alt", "ftype", "width", "side", "centred"),
    [
        ("div", "$269", 40.0, None, False),
        ("figure", "$269", 40.0, None, False),
        ("float", "$270", 40.0, "$59", False),
        # The Voyage drops a link in a floated box (#384 device check).
        ("linkfloat", "$269", 40.0, None, False),
        ("centre", "$269", 40.0, None, True),
        ("px", "$269", 35.156, None, False),
    ],
)
def test_a_sized_wrapper_is_a_container_at_its_width(
    converted, alt, ftype, width, side, centred
):
    by_alt, version = converted
    container, style, picture = by_alt[alt]
    assert container == ftype, f"calibre {version}"
    assert _pct(style) == width, f"calibre {version}"
    assert style.get("$140") == side, f"calibre {version}"
    assert ("$580" in style) == centred, f"calibre {version}"
    assert _pct(picture) == 100.0, f"calibre {version}"


def test_a_picture_with_no_width_fills_its_container(converted):
    by_alt, version = converted
    _container, style, picture = by_alt["nowidth"]
    assert _pct(style) == 40.0 and _pct(picture) == 100.0, f"calibre {version}"


def test_a_figure_with_no_width_is_unchanged(converted):
    by_alt, version = converted
    container, _style, picture = by_alt["plain"]
    assert container is None, f"calibre {version}"
    assert _pct(picture) == 50.0, f"calibre {version}"
