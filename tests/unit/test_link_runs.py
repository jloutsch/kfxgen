"""Links in plain paragraphs: one link run, styles layered on it (#715).

Kindle Previewer 4.0.3, probe P715 (2026-11-06), paragraphs with no `dir`
and no inline `lang`: a link is one run over its whole text, carrying the
styles common to all of it. A style covering only part of the link is a
run of its own layered on it, with no link. A style around the link is
split at it, and the link run carries that style.

#713 already wrote links so in a paragraph with a direction or language
(tests/unit/test_inline_direction.py); every other paragraph split a link
into one link run per style change.

These run through the test shim, which reads no CSS, so styles here come
from tags. Sizes in a link are checked through calibre in
tests/integration/test_calibre_link_runs.py.
"""

import pytest

from tests.unit.test_direction import AR, _convert
from tests.unit.test_inline_direction import BOLD, ITAL, LINK, _runs, _shape

_A = "<a href='#t'>"


def _book(tmp_path, body):
    return _runs(
        _convert(
            tmp_path,
            body + "<p id='t'>T99 target</p>",
            html_attrs=' lang="en"',
            language="en",
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "markup, expected",
    [
        # L01 (QA's G58 on #713).
        (
            f"{_A}link <i>italic</i> end</a>",
            [("link italic end", LINK), ("italic", ITAL)],
        ),
        # L02: bold at the start of the link comes after the link run.
        (f"{_A}<b>bold</b> rest</a>", [("bold rest", LINK), ("bold", BOLD)]),
        # L03: bold around the link is split at it; the link carries it.
        (
            f"<b>before {_A}link</a> after</b>",
            [("before ", BOLD), ("link", {**BOLD, **LINK}), (" after", BOLD)],
        ),
        # L04: italic to the end of the link.
        (f"{_A}whole <i>tail</i></a>", [("whole tail", LINK), ("tail", ITAL)]),
        # L05: the whole link italic is one run with both.
        (f"{_A}<i>all italic</i></a>", [("all italic", {**ITAL, **LINK})]),
        # L07: a note mark inside the link has no link of its own.
        (f"see {_A}note mark<sup>3</sup></a>", [("note mark3", LINK), ("3", {})]),
        # L08 (QA's G87 on #713): the ruby's run comes after the link's.
        (
            f"{_A}<ruby>漢<rt>かん</rt></ruby>字</a>",
            [("漢字", LINK), ("漢", {})],
        ),
        # L11: two links to one target, side by side, are one run.
        (f"{_A}one</a>{_A}two</a>", [("onetwo", LINK)]),
        # L15: a link out of the book.
        (
            "<a href='https://example.com/'>ext <i>it</i></a>",
            [("ext it", LINK), ("it", ITAL)],
        ),
        # L16: code at the start of the link.
        (f"{_A}<code>code</code> text</a>", [("code text", LINK), ("code", {})]),
        # L17: bold inside italic inside the link.
        (
            f"{_A}a <i>b <b>c</b></i></a>",
            [("a b c", LINK), ("b ", ITAL), ("c", {**ITAL, **BOLD})],
        ),
        # L20: italic around the link, bold inside it. Previewer's "c" run
        # is bold only; kfxgen's also says italic, the slant of the link run
        # under it, as every kfxgen run names its face's weight and slant
        # (#50).
        (
            f"<i>a {_A}b <b>c</b></a> d</i>",
            [
                ("a ", ITAL),
                ("b c", {**ITAL, **LINK}),
                ("c", {**ITAL, **BOLD}),
                (" d", ITAL),
            ],
        ),
    ],
    ids=["L01", "L02", "L03", "L04", "L05", "L07", "L08", "L11"]
    + ["L15", "L16", "L17", "L20"],
)
def test_a_link_is_one_run_with_its_styles_layered_on_it(tmp_path, markup, expected):
    found = _book(tmp_path, f"<p>P01 {markup} after.</p>")
    assert _shape(found["P01"]) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "markup, expected",
    [
        # pg8492's contents page: a bold link's inner run kept the bold. It
        # was written normal weight, measured against the paragraph.
        (
            f"<b>{_A}x <i>y</i></a></b>",
            [("x y", {**BOLD, **LINK}), ("y", {**ITAL, **BOLD})],
        ),
        (
            f"<i>{_A}x <b>y</b></a></i>",
            [("x y", {**ITAL, **LINK}), ("y", {**ITAL, **BOLD})],
        ),
    ],
    ids=["bold-link", "italic-link"],
)
def test_a_run_in_a_link_keeps_the_links_weight_and_slant(tmp_path, markup, expected):
    found = _book(tmp_path, f"<p>P01 {markup} after.</p>")
    assert _shape(found["P01"]) == expected


@pytest.mark.unit
def test_a_run_in_a_bold_direction_run_stays_bold(tmp_path):
    # The same for a direction run (#713): the italic word inside a bold
    # direction span was written normal weight.
    found = _runs(
        _convert(
            tmp_path,
            f"<p>P01 {AR} <b>BO <span dir='ltr'>XOUT <i>YIN</i></span></b> {AR}</p>",
            html_attrs=' dir="rtl" lang="ar"',
        )
    )
    assert ("YIN", {"italic": True, "bold": True}) in _shape(found["P01"])


@pytest.mark.unit
def test_adjacent_links_to_one_target_are_one_layer(tmp_path):
    # L14: Previewer writes "both" (italic) and " plain" as two link runs,
    # one per <a>. kfxgen can't tell two adjacent links to one target from
    # one, so it writes one link run with the italic layered on "both":
    # the same words, styles and target.
    found = _book(tmp_path, f"<p>P01 <i>it {_A}both</a></i>{_A} plain</a> after.</p>")
    assert _shape(found["P01"]) == [
        ("it ", ITAL),
        ("both plain", LINK),
        ("both", ITAL),
    ]


@pytest.mark.unit
def test_links_in_headings_and_cells_are_one_run_too(tmp_path):
    # L09 and L10.
    found = _book(
        tmp_path,
        "<p>P00 first.</p>"
        f"<h2>H01 head {_A}go <i>there</i></a></h2>"
        f"<table><tr><td>C01 {_A}cell <b>bold</b> link</a></td><td>B</td></tr></table>",
    )
    assert _shape(found["H01"]) == [("go there", LINK), ("there", ITAL)]
    assert _shape(found["C01"]) == [("cell bold link", LINK), ("bold", BOLD)]
