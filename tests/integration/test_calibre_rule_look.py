"""A rule's width, alignment and border, through calibre (#722).

kfxgen wrote an <hr> with only its space above and below, so the Kindle drew
every rule at its default width and weight. On the Paperwhite (5.19.2) a 40%
1px rule and a 60% 3px one looked the same in kfxgen's file and different in
Kindle Previewer 4.0.3's. Each case below is an <hr> of the probe P722 with
the keys Previewer 4.0.3 wrote for it, space above and below aside.

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
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BLACK = "4278190080"
# (CSS for the rule, Previewer 4.0.3's keys for it.)
CASES = {
    "V01": ("", {}),
    "V02": ("width: 50%", {"$546": "$377", "$56": "50 $314"}),
    "V03": (
        "width: 40%; border: 0; border-top: 1px solid black",
        {
            "$546": "$377",
            "$56": "40 $314",
            "$84": BLACK,
            "$89": "$328",
            "$94": "0.45 $318",
            "$95": "0 $318",
            "$96": "0 $318",
            "$97": "0 $318",
        },
    ),
    "V04": (
        "width: 60%; border: 0; border-top: 3px solid black",
        {
            "$546": "$377",
            "$56": "60 $314",
            "$84": BLACK,
            "$89": "$328",
            "$94": "1.35 $318",
            "$95": "0 $318",
            "$96": "0 $318",
            "$97": "0 $318",
        },
    ),
    "V05": ("width: 200px", {"$546": "$377", "$56": "23.4375 $314"}),
    "V06": (
        "width: 10em",
        {"$546": "$377", "$56": "10 $308", "$65": "100 $314"},
    ),
    "V07": (
        "width: 30%; margin-left: 0",
        {"$546": "$377", "$56": "30 $314", "$580": "$59"},
    ),
    "V08": (
        "width: 30%; margin-left: auto; margin-right: 0",
        {"$546": "$377", "$56": "30 $314", "$580": "$61"},
    ),
    "V09": (
        "width: 50%; border: 2px dashed red",
        {
            "$546": "$377",
            "$56": "50 $314",
            "$83": "4294901760",
            "$88": "$330",
            "$93": "0.9 $318",
        },
    ),
    "V10": (
        "border: 0; border-bottom: 2px dotted blue",
        {
            "$86": "4278190335",
            "$91": "$331",
            "$94": "0 $318",
            "$95": "0 $318",
            "$96": "0.9 $318",
            "$97": "0 $318",
        },
    ),
    "V11": (
        "width: 50%; height: 4px; background-color: #888; border: 0",
        {
            "$546": "$377",
            "$56": "50 $314",
            "$62": "1.8 $318",
            "$70": "4287137928",
            "$93": "0 $318",
        },
    ),
    "V13": ("width: 100%", {"$546": "$377", "$56": "100 $314"}),
    "V15": (
        "width: 45%; margin-left: 27.5%",
        {"$48": "27.5 $314", "$546": "$377", "$56": "45 $314", "$580": "$59"},
    ),
    "V16": (
        "border-width: 3px; width: 20%",
        {"$546": "$377", "$56": "20 $314", "$93": "1.35 $318"},
    ),
    "V17": (
        "width: 50%; height: 0.5em; background-color: #888; border: 0",
        {
            "$546": "$377",
            "$56": "50 $314",
            "$62": "0.5 $308",
            "$70": "4287137928",
            "$93": "0 $318",
        },
    ),
    "V19": (
        "border: 0; border-top: 1px solid #000; border-bottom: 1px solid #000;"
        " width: 35%",
        {
            "$546": "$377",
            "$56": "35 $314",
            "$84": BLACK,
            "$86": BLACK,
            "$89": "$328",
            "$91": "$328",
            "$94": "0.45 $318",
            "$95": "0 $318",
            "$96": "0.45 $318",
            "$97": "0 $318",
        },
    ),
    "V20": (
        "width: 35%; margin: 1em 0",
        {"$546": "$377", "$56": "35 $314", "$580": "$59"},
    ),
    "V21": ("margin: 1em 10%", {"$48": "10 $314", "$50": "10 $314"}),
    "V22": (
        "width: 50%; height: 1px; background-color: black; border: 0",
        {
            "$546": "$377",
            "$56": "50 $314",
            "$62": "0.45 $318",
            "$70": BLACK,
            "$93": "0 $318",
        },
    ),
    "V23": (
        "border: 0; height: 1px; background: #444; width: 30%",
        {
            "$546": "$377",
            "$56": "30 $314",
            "$62": "0.45 $318",
            "$70": "4282664004",
            "$93": "0 $318",
        },
    ),
    "V24": (
        "width: 6em; border-top: 2px solid; border-bottom: 0; border-left: 0;"
        " border-right: 0",
        {
            "$546": "$377",
            "$56": "6 $308",
            "$65": "100 $314",
            "$89": "$328",
            "$94": "0.9 $318",
            "$95": "0 $318",
            "$96": "0 $318",
            "$97": "0 $318",
        },
    ),
    # A width and margins on both sides: placed from the left, only the left
    # margin is written (Gutenberg's commonest rule, 231 in the 90 books).
    "V25": (
        "width: 20%; margin-left: 40%; margin-right: 40%",
        {"$48": "40 $314", "$546": "$377", "$56": "20 $314", "$580": "$59"},
    ),
    "V26": (
        "width: 80%; margin: 1em 10%",
        {"$48": "10 $314", "$546": "$377", "$56": "80 $314", "$580": "$59"},
    ),
    "V27": (
        "width: 10%; margin-left: 45%; margin-right: 1em",
        {"$48": "45 $314", "$546": "$377", "$56": "10 $314", "$580": "$59"},
    ),
    "V28": (
        "width: 30%; margin-left: 10%; margin-right: auto",
        {"$48": "10 $314", "$546": "$377", "$56": "30 $314", "$580": "$59"},
    ),
    "V29": (
        "width: 30%; margin-left: auto; margin-right: 10%",
        {"$50": "10 $314", "$546": "$377", "$56": "30 $314", "$580": "$61"},
    ),
}
CSS = "p { margin: 0.4em 0 }" + "".join(
    f" hr.{v.lower()} {{ {css} }}" for v, (css, _) in CASES.items() if css
)
BODY = (
    "<h1 id='top'>Rules</h1>"
    + "".join(
        f"<p>{v} rule below.</p><hr{f' class={chr(39)}{v.lower()}{chr(39)}' if css else ''}/>"
        for v, (css, _) in CASES.items()
    )
    + "<p>END.</p>"
)


def _text(v):
    v = getattr(v, "value", v)
    if hasattr(v, "get") and "$307" in v:
        n = float(getattr(v["$307"], "value", v["$307"]))
        return f"{n:g} {v['$306']}"
    return str(v)


@pytest.fixture(scope="module")
def rules(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "p722.epub", tmp / "p722.kfx"
    write_epub(
        epub,
        pages={"c0.xhtml": BODY},
        toc=[("Rules", "c0.xhtml#top", 0)],
        css=CSS,
        ident="p722",
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out, last = {}, [None]

    def visit(nodes):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            ref = e.get("$145")
            if hasattr(ref, "get"):
                last[0] = str(texts[str(ref["name"])][int(ref["$403"])])[:3]
            if str(e.get("$159")) == "$596":
                s = dict(styles.get(str(e.get("$157")), {}))
                s.update(e)
                out[last[0]] = {
                    str(k): _text(v)
                    for k, v in s.items()
                    if str(k) not in ("$47", "$49", "$155", "$157", "$159", "$173")
                }
            visit(e.get("$146") or ())

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"])
    return out, version


@pytest.mark.parametrize("case", list(CASES))
def test_a_rule_is_drawn_as_the_book_styles_it(rules, case):
    got, version = rules
    assert got.get(case) == CASES[case][1], f"calibre {version}: {got.get(case)}"
