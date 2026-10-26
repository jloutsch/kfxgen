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
        # Only `lang` counts: Previewer ignores xml:lang (#503 QA).
        ('<p xml:lang="de"><q>a</q></p>', '"a"'),
        ('<p lang="en"><q xml:lang="de">a</q></p>', "“a”"),
    ],
)
def test_q_takes_its_languages_marks(body, text):
    assert _texts(body) == [text]


@pytest.mark.unit
def test_the_documents_lang_reaches_q():
    assert _texts("<p><q>a</q></p>", ' lang="en"') == ["“a”"]
    # xml:lang alone, as 173 of QA's library books set it: plain (#503 QA).
    assert _texts("<p><q>a</q></p>", ' xml:lang="fr"') == ['"a"']


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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lang", "text"),
    [
        # Region and script tags Previewer has marks of their own for (#503
        # QA; 4,422 tags measured).
        ("fr-CA", "«a»"),
        ("pt-PT", "«a»"),
        ("zh-Hant", "「a」"),
        ("de-AT", '"a"'),
    ],
)
def test_region_tags_previewer_knows(lang, text):
    assert _texts(f'<p><q lang="{lang}">a</q></p>') == [text]


@pytest.mark.unit
def test_an_empty_lang_means_unknown():
    """lang="" is an unknown language: plain marks, not the parent's (#503 QA)."""
    assert _texts('<p lang="fr"><q lang="">a</q></p>') == ['"a"']


def _styled(css_for_q, body='<p lang="en"><q>a <q>b</q></q></p>'):
    """Text of `body` with `css_for_q` as each <q>'s resolved CSS."""
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    root = etree.fromstring(xhtml.encode())

    def resolver(elem):
        return {"q-css": css_for_q} if _conv._local_tag(elem.tag) == "q" else {}

    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=resolver, base_href="c.xhtml"
    )
    return [b["text"] for b in blocks]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("css", "text"),
    [
        # A book typing its own marks sets quotes: none (#503 QA).
        ({"quotes": "none"}, "a b"),
        ({"quotes": '"<<" ">>" "<" ">"'}, "<<a <b>>>"),
        ({"quotes": '"\\201C" "\\201D"'}, "“a “b””"),
        # ::before/::after content replaces the marks.
        ({"before": '"[["', "after": '"]]"'}, "[[a [[b]]]]"),
        ({"before": "open-quote", "after": "close-quote"}, "“a ‘b’”"),
        ({"before": "none", "after": "none"}, "a b"),
    ],
    ids=["none", "strings", "escapes", "before-after", "open-close", "content-none"],
)
def test_the_books_css_for_q(css, text):
    assert _styled(css) == [text]
