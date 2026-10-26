"""<q> is printed with the quotation marks a browser's own stylesheet adds,
as Kindle Previewer 4.0.3 prints them (#469). The marks come from the
nearest lang, matched as a whole tag; with none, or a tag Previewer has no
marks for (en-US among them), they are plain " and '. A <q> inside a <q>
takes the second pair, and so does one deeper still."""

import pytest
from lxml import etree

from kfxgen import converter as _conv


def _texts(body, html_attrs=""):
    xhtml = (
        f'<html xmlns="http://www.w3.org/1999/xhtml"{html_attrs}><body>{body}'
        "</body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )
    return [b["text"] for b in blocks]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "text"),
    [
        # No lang anywhere: plain marks, as Previewer writes them.
        ("<p>a <q>b</q> c</p>", 'a "b" c'),
        ("<p><q>a <q>b <q>c</q></q></q></p>", "\"a 'b 'c''\""),
        # The nearest lang, on the <q> or an ancestor.
        ('<p><q lang="en">a <q>b</q></q></p>', "“a ‘b’”"),
        ('<p lang="fr"><q>a <q>b</q></q></p>', "«a «b»»"),
        ('<p lang="de"><q>a <q>b</q></q></p>', "„a ‚b‘“"),
        ('<p><q lang="ja">a <q>b</q></q></p>', "「a 『b』」"),
        ('<p><q lang="fr-CH">a <q>b</q></q></p>', "«a ‹b›»"),
        # A whole tag Previewer has no marks for, not its language's.
        ('<p><q lang="en-US">a</q></p>', '"a"'),
        ('<p><q lang="pt-BR">a</q></p>', '"a"'),
        ('<p><q lang="xx">a</q></p>', '"a"'),
        # xml:lang counts as lang does; the closer one wins.
        (
            '<p xml:lang="en"><q lang="fr">a</q></p>',
            "«a»",
        ),
    ],
)
def test_q_takes_its_languages_marks(body, text):
    assert _texts(body) == [text]


@pytest.mark.unit
def test_the_documents_lang_reaches_q():
    assert _texts("<p><q>a</q></p>", ' xml:lang="en"') == ["“a”"]


@pytest.mark.unit
def test_the_marks_take_the_qs_own_formatting():
    """Inside an <i>, the marks are italic too; an <i> inside the <q>
    leaves them upright."""
    from kfxgen.inline_style import FLAG_ITALIC

    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<p><i><q>a</q></i> <q><i>b</i></q></p></body></html>"
    )
    (block,) = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )
    assert block["text"] == '"a" "b"'
    italic = [(s, n) for s, n, flags in block["spans"] if FLAG_ITALIC in flags]
    assert italic == [(0, 3), (5, 1)]
