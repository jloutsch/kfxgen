"""A chapter whose page is only its heading, through calibre (#717).

The book of tests/unit/test_heading_only_page.py, with pg100's CSS. Each
title page prints its heading in the book's style, as Kindle Previewer
4.0.3 writes the same book: centred (`$34` `$320`), 1.5em, normal weight,
and its margins as space above and below. A rule under the heading is
kept, and a box holding only the heading is drawn round it.

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
from tests.unit.test_heading_only_page import CSS, PAGES, TOC, entries  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]


@pytest.fixture(scope="module")
def frags(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "h717.epub", tmp / "h717.kfx"
    write_epub(epub, pages=PAGES, toc=TOC, css=CSS, ident="h717")
    _ebook_convert(calibre, epub, kfx, True)
    return load_fragments(kfx), version


def _num(v):
    v = getattr(v, "value", v)
    return (
        round(float(getattr(v["$307"], "value", v["$307"])), 3)
        if v is not None
        else None
    )


def _sym(v):
    return str(getattr(v, "value", v)) if v is not None else None


def _section(frags, title):
    (section,) = [s for s in entries(frags) if any(t == title for t, _ in s)]
    return section


# Kindle Previewer 4.0.3's values for the same book: `$47` and `$49` in
# line heights, the heading's own size and line height.
@pytest.mark.parametrize(
    "title, above, below, after",
    [
        ("RULED TITLE", 2.222, None, ["$596"]),
        ("BARE TITLE", 2.222, 0.833, []),
        ("BOXED TITLE", 1.667, 0.833, ["$596"]),
        ("PLAIN TITLE", 1.667, 0.833, []),
    ],
)
def test_a_title_alone_prints_in_the_books_heading_style(
    frags, title, above, below, after
):
    frags, version = frags
    section = _section(frags, title)
    (style,) = [s for t, s in section if t == title]
    got = {
        "align": _sym(style.get("$34")),
        "size": _num(style.get("$16")),
        "weight": _sym(style.get("$13")),
        "above": _num(style.get("$47")),
        "below": _num(style.get("$49")),
    }
    want = {
        "align": "$320",
        "size": 1.5,
        "weight": "$350",
        "above": above,
        "below": below,
    }
    assert got == want, f"calibre {version}: {got}"
    labels = [t for t, _ in section]
    assert labels[labels.index(title) + 1 :] == after, f"calibre {version}: {labels}"


def test_a_box_holding_only_the_title_is_drawn_round_it(frags):
    frags, version = frags
    labels = [t for t, _ in _section(frags, "BOXED TITLE")]
    assert labels[:2] == ["$270", "BOXED TITLE"], f"calibre {version}: {labels}"


# QA on #720: the probe E720, with Kindle Previewer 4.0.3's values for it.
CSS_720 = (
    "h1, h2 { text-align: center; font-weight: bold; }"
    " h1 { font-size: 1.6em; margin: 2em 0 1em; }"
    " h2 { font-size: 1.3em; margin: 1.5em 0 0.5em; }"
    " p { margin: 0.4em 0; text-indent: 1em; }"
    " div.box { border: 2px solid black; padding: 0.5em; margin: 1em 2em; }"
    " hr { width: 40%; margin: 1em auto; border: 0; border-top: 1px solid black; }"
    " hr.thick { width: 60%; border-top: 3px solid black; }"
    " h1.small { font-size: 0.75em; font-weight: normal }"
    " span.big { font-size: 2em; font-weight: bold }"
    " h1.pad { font-size: 1em; font-style: italic; font-weight: normal;"
    " padding-top: 10em; padding-bottom: 10em; margin: 0 }"
    " hr.pbk { margin: 2em auto }"
    " p.mb { margin-bottom: 2em }"
    " h2.mb3 { margin-bottom: 3em }"
    " div.pad { padding: 1em 0 }"
    " div.padm { padding: 1em 0; margin-bottom: 3em }"
    " h2.small { font-size: 0.75em }"
    " span.huge { font-size: 3em }"
    " h2.light { font-weight: normal }"
    " h2.slant { font-weight: normal; font-style: italic }"
    " span.dbl { font-size: 2em }"
    " span.half { font-size: 0.5em }"
)
PAGES_720 = {
    "e00.xhtml": "<h1 id='w'>Front</h1><p>Front words.</p>",
    "e01.xhtml": "<h1 id='h1'>Act One</h1><hr id='r1'/>",
    "e02.xhtml": "<h2>Scene Two</h2><hr/><p>&#160;</p><hr class='thick'/>",
    "e03.xhtml": "<h2>Scene Four</h2><hr/><hr class='thick'/>",
    "e04.xhtml": "<div class='box'><h2>Boxed Two</h2><hr/></div>",
    "e05.xhtml": "<h2>Cleared</h2><hr style='clear: both'/>",
    "e06.xhtml": "<h1 class='small'><span class='big'>SIZED TITLE</span></h1>",
    "e07.xhtml": (
        "<h1 class='small'><span class='big'>SIZED CUT</span></h1><p>Words after.</p>"
    ),
    "e08.xhtml": "<div><h1 class='pad'>BOOK II</h1></div><hr class='pbk'/>",
    "e09.xhtml": (
        "<h2>General</h2><p class='mb'>Before rule.</p><hr/><p>After rule.</p>"
    ),
    "e10.xhtml": "<h2 class='mb3'>Wide Below</h2><hr/><p>Words ten.</p>",
    "e11.xhtml": "<h1>Act Cut</h1><hr/><p>Words eleven.</p>",
    "e12.xhtml": (
        "<h2>Padded</h2><div class='pad'><p>R03 in a padded div.</p></div><hr/>"
        "<div class='padm'><p>R04 with a margin.</p></div><hr/><p>R09 last.</p>"
    ),
    "e13.xhtml": (
        "<h2 class='small'><span class='big'>Mixed</span> "
        "<span class='huge'>Sizes</span></h2>"
    ),
    "e14.xhtml": (
        "<div class='box'><h2>Box Table</h2><table><tr><td></td></tr></table></div>"
    ),
    "e15.xhtml": "<p>Last words.</p>",
    "e16.xhtml": "<h2 class='small'>Partial <span class='big'>Span</span></h2>",
    "e17.xhtml": (
        "<h2 class='small'>Partial <span class='big'>Cut</span></h2>"
        "<p>Words seventeen.</p>"
    ),
    "e18.xhtml": (
        "<h2 class='light'>Light <span class='dbl'>Big</span> "
        "<span class='half'>Small</span></h2>"
    ),
    "e19.xhtml": "<h2 class='slant'>Slant <span class='dbl'>Wide</span></h2>",
    "e20.xhtml": "<h2 class='light'>Plain <i>Leaning</i></h2>",
}
TOC_720 = [
    (label, page, 0)
    for label, page in zip(
        (
            "Front",
            "Act One",
            "Scene Two",
            "Scene Four",
            "Boxed Two",
            "Cleared",
            "SIZED TITLE",
            "SIZED CUT",
            "BOOK II",
            "General",
            "Wide Below",
            "Act Cut",
            "Padded",
            "Mixed Sizes",
            "Box Table",
            "End",
            "Partial Span",
            "Partial Cut",
            "Light Big Small",
            "Slant Wide",
            "Plain Leaning",
        ),
        PAGES_720,
    )
]


@pytest.fixture(scope="module")
def frags720(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "e720.epub", tmp / "e720.kfx"
    write_epub(epub, pages=PAGES_720, toc=TOC_720, css=CSS_720, ident="e720")
    _ebook_convert(calibre, epub, kfx, True)
    return load_fragments(kfx), version


def _after(frags, title):
    """The entries after `title` in its section, as (label, style)."""
    section = _section(frags, title)
    return section[[t for t, _ in section].index(title) + 1 :]


@pytest.mark.parametrize(
    "title, above",
    [
        # The heading's margin-bottom (1em of 1.6em) meets the rule's 1em.
        ("Act One", 1.333),
        ("Act Cut", 1.333),
        ("Wide Below", 3.25),
        # A paragraph's 2em margin-bottom meets the rule's 1em.
        ("Before rule.", 1.667),
    ],
)
def test_the_margin_above_a_rule_is_the_larger_of_the_two(frags720, title, above):
    frags, version = frags720
    rule = _after(frags, title)[0]
    assert rule[0] == "$596", f"calibre {version}: {rule}"
    assert _num(rule[1].get("$47")) == above, f"calibre {version}: {rule}"


def test_a_headings_padding_below_stays_above_the_rule(frags720):
    # Previewer: the heading's padding-bottom 8.333 lh, then the rule's 1.667.
    frags, version = frags720
    (rule,) = _after(frags, "BOOK II")
    assert _num(rule[1].get("$47")) == 10.0, f"calibre {version}: {rule}"


@pytest.mark.parametrize("title", ["Scene Two", "Scene Four"])
def test_every_rule_under_a_title_alone_is_kept(frags720, title):
    frags, version = frags720
    labels = [t for t, _ in _after(frags, title)]
    assert labels == ["$596", "$596"], f"calibre {version}: {labels}"


def test_a_spacer_between_two_rules_adds_to_the_space_above_the_second(frags720):
    # Previewer writes 2.26 lh above the second rule of Scene Two, against
    # 0.8333 above the first. kfxgen adds the spacer's space to it: 1.627.
    frags, version = frags720
    first, second = (_num(s.get("$47")) for _, s in _after(frags, "Scene Two"))
    assert second > first, f"calibre {version}: {first}, {second}"


def test_a_rule_under_a_title_alone_keeps_its_clear(frags720):
    frags, version = frags720
    (rule,) = _after(frags, "Cleared")
    assert _sym(rule[1].get("$628")) == "$421", f"calibre {version}: {rule}"


def test_a_box_holding_the_title_and_a_rule_is_drawn_round_both(frags720):
    frags, version = frags720
    section = _section(frags, "Boxed Two")
    assert [t for t, _ in section] == ["$270", "Boxed Two", "$596"], (
        f"calibre {version}: {section}"
    )
    style = section[1][1]
    assert (_sym(style.get("$34")), _num(style.get("$16"))) == ("$320", 1.3), (
        f"calibre {version}: {style}"
    )


@pytest.mark.parametrize("title", ["SIZED TITLE", "SIZED CUT"])
def test_a_title_in_a_sized_span_prints_at_the_spans_size(frags720, title):
    # The heading is 0.75em and its words a 2em span: 1.5em, as Previewer.
    frags, version = frags720
    (style,) = [s for t, s in _section(frags, title) if t == title]
    assert _num(style.get("$16")) == 1.5, f"calibre {version}: {style}"


@pytest.mark.parametrize(
    "line, above",
    [
        # The div's padding-bottom (1em) holds the paragraph's 0.4em margin
        # inside it; the rule's 1em margin is outside: 2.4em, Previewer's
        # total (its container's `$54`, the paragraph's `$49`, the rule's).
        ("R03 in a padded div.", 2.0),
        # The same with the div's 3em margin outside, met by the rule's 1em.
        ("R04 with a margin.", 3.667),
    ],
)
def test_the_margin_inside_a_padded_block_stays_above_the_rule(frags720, line, above):
    frags, version = frags720
    rule = _after(frags, line)[0]
    assert (rule[0], _num(rule[1].get("$47"))) == ("$596", above), (
        f"calibre {version}: {rule}"
    )


def test_a_title_in_spans_of_different_sizes_keeps_the_headings_size(frags720):
    # 2em and 3em spans in a 0.75em heading: no one size, so the heading's
    # own, as Previewer writes the heading.
    frags, version = frags720
    (style,) = [s for t, s in _section(frags, "Mixed Sizes") if t == "Mixed Sizes"]
    assert _num(style.get("$16")) == 0.75, f"calibre {version}: {style}"


def test_a_box_holding_the_title_and_more_than_rules_is_not_drawn(frags720):
    frags, version = frags720
    labels = [t for t, _ in _section(frags, "Box Table")]
    assert labels[0] == "Box Table", f"calibre {version}: {labels}"


def _runs(frags, title):
    """The heading's runs as (offset, length, `$16`)."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (style,) = [s for t, s in _section(frags, title) if t == title]
    out = []
    for run in style.get("$142") or ():
        run = getattr(run, "value", run)
        size = styles.get(str(run.get("$157")), {}).get("$16")
        out.append((int(run["$143"]), int(run["$144"]), _num(size)))
    return out


# QA's probe H720e on #720 (#721): Kindle Previewer 4.0.3 writes the
# heading at its own 0.75em and each sized span as a run of its own size,
# against the main text: 2em of 0.75em is 1.5em, 3em 2.25em.
@pytest.mark.parametrize(
    "title, runs",
    [
        ("Partial Span", [(8, 4, 1.5)]),
        ("Partial Cut", [(8, 3, 1.5)]),
        ("Mixed Sizes", [(0, 5, 1.5), (6, 5, 2.25)]),
    ],
)
def test_sized_words_in_a_title_print_at_their_own_size(frags720, title, runs):
    frags, version = frags720
    (style,) = [s for t, s in _section(frags, title) if t == title]
    assert _num(style.get("$16")) == 0.75, f"calibre {version}: {style}"
    assert _runs(frags, title) == runs, f"calibre {version}"


@pytest.mark.parametrize("title", ["SIZED TITLE", "SIZED CUT"])
def test_a_title_wholly_in_one_size_has_no_size_run(frags720, title):
    frags, version = frags720
    assert all(size is None for *_, size in _runs(frags, title)), (
        f"calibre {version}: {_runs(frags, title)}"
    )


def _run_faces(frags, title):
    """The heading's runs as (offset, length, `$13`, `$12`)."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (style,) = [s for t, s in _section(frags, title) if t == title]
    out = []
    for run in style.get("$142") or ():
        run = getattr(run, "value", run)
        rs = styles.get(str(run.get("$157")), {})
        out.append(
            (
                int(run["$143"]),
                int(run["$144"]),
                _sym(rs.get("$13")),
                _sym(rs.get("$12")),
            )
        )
    return out


# QA on #725 (probe P725b): in a heading the book sets normal weight, or
# italic, a run takes the heading's weight and slant, as Kindle Previewer
# 4.0.3 writes it: no bold `$13` on a sized or italic word, and a sized
# word in an italic heading stays italic.
@pytest.mark.parametrize(
    "title, faces",
    [
        ("Light Big Small", [(6, 3, None, None), (10, 5, None, None)]),
        ("Slant Wide", [(6, 4, None, None)]),
        ("Plain Leaning", [(6, 7, None, "$382")]),
    ],
)
def test_a_run_takes_the_weight_and_slant_of_its_heading(frags720, title, faces):
    frags, version = frags720
    got = [
        (s, n, None if w in (None, "$350") else w, None if i in (None, "$350") else i)
        for s, n, w, i in _run_faces(frags, title)
    ]
    assert got == faces, f"calibre {version}: {_run_faces(frags, title)}"
