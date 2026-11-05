"""Lines standing loose in a container take its language (#718).

A paragraph takes its language (`$10`) and direction from `lang`/`dir` on
itself or an ancestor (#246). Text standing loose between blocks was made
into a line of its own with no style, so it had neither: pg21053's verse
line numbers (`<span class="linenum">` between `<p>`s), the page markers of
pg22120 and pg26849, a list item's own line before its nested list
(pg37106), text loose in a table outside its cells, and the words an SVG
draws. Kindle Previewer 4.0.3 writes the page's `$10`, `$682` and `$674`
on each of these lines (probe of this book's first page, `de` and `ar`).
"""

import pytest

from tests.unit.test_contents_pages import _convert, _texts

BODY = (
    "<h1 id='a'>Kapitel</h1>"
    '<div class="poem"><p>Erste Zeile.</p><span class="linenum">5</span>'
    "<p>Zweite Zeile.</p></div>"
    '<div><p>Text.</p><span class="pb">[pg 1-001]</span><hr/></div>'
    '<ul><li><span class="title">REIHE EINS</span><ul><li>Buch A</li></ul></li></ul>'
    "<div>Loser Text <em>kursiv</em><p>Absatz.</p>Nachher.</div>"
    "<table>Vor der Zeile<tr><td>Zelle</td></tr></table>"
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40">'
    '<text x="0" y="20">Bildwort</text></svg>'
)
LOOSE = (
    "5",
    "[pg 1-001]",
    "• REIHE EINS",
    "Loser Text kursiv",
    "Nachher.",
    "Vor der Zeile",
    "Bildwort",
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "html_attrs, expected",
    [
        (' xml:lang="de"', {"$10": "de"}),
        (' xml:lang="ar" dir="rtl"', {"$10": "ar", "$682": "$375"}),
    ],
)
def test_a_loose_line_has_its_pages_language(tmp_path, html_attrs, expected):
    lines = dict(
        _texts(
            _convert(
                tmp_path, html_attrs, {"a.xhtml": BODY}, [("Kapitel", "a.xhtml#a", 0)]
            )
        )
    )
    assert set(LOOSE) <= set(lines), sorted(lines)
    wrong = {t: lines[t] for t in LOOSE if lines[t] != expected}
    assert not wrong, wrong


@pytest.mark.unit
def test_a_loose_line_in_an_element_with_its_own_language_takes_that(tmp_path):
    body = (
        "<h1 id='a'>Kapitel</h1><div lang='fr'><p>Un.</p>"
        "<span>Ligne libre</span><p>Deux.</p></div>"
    )
    lines = dict(
        _texts(
            _convert(
                tmp_path,
                ' xml:lang="de"',
                {"a.xhtml": body},
                [("Kapitel", "a.xhtml#a", 0)],
            )
        )
    )
    assert lines["Ligne libre"] == {"$10": "fr"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "html_attrs, table_attrs, expected",
    [
        (' xml:lang="de"', " lang='fr'", {"$10": "de"}),
        (' xml:lang="en"', " lang='ar' dir='rtl'", {"$10": "en"}),
        (
            ' xml:lang="ar" dir="rtl"',
            " lang='en' dir='ltr'",
            {"$10": "ar", "$682": "$375"},
        ),
    ],
)
def test_text_loose_in_a_table_takes_the_language_around_the_table(
    tmp_path, html_attrs, table_attrs, expected
):
    # Kindle Previewer 4.0.3 gives it the page's language, not the table's
    # (QA on #720: probes F01, F04, L10).
    body = (
        f"<h1 id='a'>Kapitel</h1><p>Vorher.</p>"
        f"<table{table_attrs}>Vor der Zeile<tr><td>Zelle</td></tr></table>"
    )
    lines = dict(
        _texts(
            _convert(
                tmp_path, html_attrs, {"a.xhtml": body}, [("Kapitel", "a.xhtml#a", 0)]
            )
        )
    )
    assert lines["Vor der Zeile"] == expected
