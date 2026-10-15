"""Small caps, text-transform, letter-spacing and colour through calibre's
real Stylizer (#239).

calibre gives an inherited colour, small caps and transform as computed
values, but letter-spacing's computed value is in points (0.2em came back
3.2), so the resolver reads letter-spacing as written. The unit tests' fake
style can't show either.

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

from tests._kfx_introspect import (  # noqa: E402
    by_type,
    iter_entries,
    load_fragments,
    val,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _build_epub,
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """
p.sc { font-variant: small-caps; } span.sc { font-variant: small-caps; }
p.sc span.n { font-variant: normal; }
p.up { text-transform: uppercase; } span.low { text-transform: lowercase; }
p.ls { letter-spacing: 0.2em; } span.ls { letter-spacing: 3px; }
p.ls span.n { letter-spacing: normal; }
div.grey { color: #333; } span.k { color: black; }
span.red { color: #c00000; } span.white { color: #fff; }
a.blue { color: #00f; }
p.small { font-size: 0.8em; }
"""

MAIN = " ".join(["Main text of the book, at the reader's own size."] * 12)

#: (file, case id, markup).
CASES = [
    ("c1", "MAIN", f"<p>{MAIN}</p><p>{MAIN}</p>"),
    ("c1", "SCPARA", '<p class="sc">Small caps <span class="n">plain</span>.</p>'),
    ("c1", "SCSPAN", '<p>A <span class="sc">Caps</span> word.</p>'),
    ("c1", "UPPARA", '<p class="up">Upper <span class="low">LOW</span>.</p>'),
    ("c1", "LSPARA", '<p class="ls">Spaced <span class="n">plain</span>.</p>'),
    ("c1", "LSSPAN", '<p>A <span class="ls">wide</span> word.</p>'),
    ("c1", "GREY", '<div class="grey"><p>Grey <span class="k">black</span>.</p></div>'),
    ("c1", "COLOUR", '<p>A <span class="red">red</span> <span class="white">white</span>.</p>'),
    ("c1", "LINK", '<p id="t">A <a class="blue" href="#t">link</a>.</p>'),
    ("c1", "SMALL", '<p class="small">Small print, still sized.</p>'),
]  # fmt: skip


def _entries(kfx):
    """Every text entry in storyline order: (text, style, [(start, length,
    run style)])."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    out = []
    for story in by_type(frags, "$259"):
        for entry in iter_entries(val(story)["$146"]):
            ref = entry.get("$145")
            if ref is not None:
                text = str(content[str(ref["name"])][int(ref["$403"])])
                runs = [
                    (int(r["$143"]), int(r["$144"]), styles.get(str(r.get("$157"))))
                    for r in entry.get("$142") or []
                ]
                out.append((text, styles[str(entry["$157"])], runs))
    return out


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """{case id: {"rows": [(text, style)], "runs": the case's one paragraph's
    runs}}, and calibre's version."""
    tmp, _env, version = calibre
    epub, kfx = tmp / "typography.epub", tmp / "typography.kfx"
    _build_epub(epub, CASES, CSS, {}, ())
    _ebook_convert(calibre, epub, kfx, True)
    ids = {cid for _, cid, _ in CASES}
    by_case, current = {}, None
    for text, style, runs in _entries(kfx):
        if text in ids:
            current = text
            by_case[current] = {"rows": [], "runs": []}
        elif current and text in (f"END {current}", f"{current} control"):
            current = None
        elif current:
            by_case[current]["rows"].append((text, style))
            by_case[current]["runs"] = runs
    return by_case, version


def _props(style):
    style = {str(k): v for k, v in (style or {}).items()}
    out = {}
    for k in ("$583", "$41"):
        if k in style:
            out[k] = str(style[k])
    if "$32" in style:
        out["$32"] = (str(style["$32"]["$307"]), str(style["$32"]["$306"]))
    if "$19" in style:
        out["$19"] = int(style["$19"])
    return out


def _case(converted, cid):
    by_case, version = converted
    # The case's own paragraph is its last: calibre puts the LINK case's
    # link in its TOC, and kfxgen prints that first.
    text, style = by_case[cid]["rows"][-1]
    return text, _props(style), by_case[cid], version


@pytest.mark.parametrize(
    ("cid", "block"),
    [
        ("SCPARA", {"$583": "$369"}),
        ("UPPARA", {"$41": "$372"}),
        ("LSPARA", {"$32": ("0.2", "$308")}),
        ("GREY", {"$19": 0xFF333333}),
        ("SCSPAN", {}),
        ("COLOUR", {}),
        ("LINK", {}),
    ],
)
def test_a_paragraphs_typography(converted, cid, block):
    _text, got, _c, version = _case(converted, cid)
    assert got == block, f"calibre {version}"


@pytest.mark.parametrize(
    ("cid", "word", "run"),
    [
        ("SCPARA", "plain", {"$583": "$349"}),
        ("SCSPAN", "Caps", {"$583": "$369"}),
        ("UPPARA", "LOW", {"$41": "$373"}),
        ("LSPARA", "plain", {"$32": ("0", "$308")}),
        ("LSSPAN", "wide", {"$32": ("1.35", "$318")}),
        ("GREY", "black", {"$19": 0xFF000000}),
        ("COLOUR", "red", {"$19": 0xFFC00000}),
        ("COLOUR", "white", {"$19": 0xFF767676}),
    ],
)
def test_a_runs_typography(converted, cid, word, run):
    text, _got, case, version = _case(converted, cid)
    runs = {text[s : s + n]: _props(style) for s, n, style in case.get("runs") or []}
    assert runs.get(word) == run, f"calibre {version}: {runs}"


def test_a_links_colour_is_no_run(converted):
    text, _got, case, version = _case(converted, "LINK")
    for s, n, style in case.get("runs") or []:
        assert "$19" not in _props(style), f"calibre {version}"


def test_sizes_still_come_through(converted):
    """The resolver catches every error: one reading a property wrong
    loses the whole element's CSS, sizes with it (#240's lesson)."""
    by_case, version = converted
    ((_text, style),) = by_case["SMALL"]["rows"]
    style = {str(k): v for k, v in style.items()}
    assert str(style["$16"]["$307"]) == "0.8", f"calibre {version}"
