import pytest
from kfxgen import inline_style as ist
from kfxgen.inline_style import FLAG_ITALIC as I, FLAG_BOLD as B


@pytest.mark.unit
def test_plain_text_no_spans():
    text, spans = ist.normalize_runs([("hello world", frozenset())])
    assert text == "hello world"
    assert spans == []


@pytest.mark.unit
def test_single_italic_run():
    text, spans = ist.normalize_runs(
        [("a ", frozenset()), ("big", frozenset({I})), (" cat", frozenset())]
    )
    assert text == "a big cat"
    assert spans == [(2, 3, frozenset({I}))]


@pytest.mark.unit
def test_whitespace_collapsed_and_stripped():
    text, spans = ist.normalize_runs(
        [("  a\n\n", frozenset()), ("  b  ", frozenset({B}))]
    )
    assert text == "a b"
    assert spans == [(2, 1, frozenset({B}))]


@pytest.mark.unit
def test_bold_italic_combined_flags():
    text, spans = ist.normalize_runs([("x", frozenset({I, B}))])
    assert text == "x"
    assert spans == [(0, 1, frozenset({I, B}))]


@pytest.mark.unit
def test_adjacent_same_flags_merge():
    text, spans = ist.normalize_runs([("ab", frozenset({I})), ("cd", frozenset({I}))])
    assert text == "abcd"
    assert spans == [(0, 4, frozenset({I}))]


@pytest.mark.unit
def test_parse_css_length_units():
    assert ist.parse_css_length("1.5em") == ("1.5", "$308")
    assert ist.parse_css_length("2rem") == ("2", "$505")
    assert ist.parse_css_length("5%") == ("5", "$314")
    assert ist.parse_css_length("12pt") == ("12", "$318")
    assert ist.parse_css_length("3px") == ("3", "$319")
    assert ist.parse_css_length("4mm") == ("4", "$316")


@pytest.mark.unit
def test_parse_css_length_rejects():
    assert ist.parse_css_length("") is None
    assert ist.parse_css_length("auto") is None
    assert ist.parse_css_length("0") is None
    assert ist.parse_css_length("0em") is None
    assert ist.parse_css_length("2vw") is None
    assert ist.parse_css_length("3ch") is None
    assert ist.parse_css_length("inherit") is None


@pytest.mark.unit
def test_align_map_values():
    assert ist.ALIGN_MAP == {
        "left": "$59",
        "right": "$61",
        "center": "$320",
        "justify": "$321",
    }


@pytest.mark.unit
def test_compute_block_style_align():
    assert ist.compute_block_style({"text-align": "center"})["align"] == "center"
    assert ist.compute_block_style({"text-align": "left"})["align"] == "left"
    assert ist.compute_block_style({"text-align": "JUSTIFY"})["align"] == "justify"
    assert ist.compute_block_style({"text-align": "start"})["align"] is None
    assert ist.compute_block_style({})["align"] is None


@pytest.mark.unit
def test_compute_block_style_indent():
    assert ist.compute_block_style({"text-indent": "1.5em"})["indent"] == (
        "1.5",
        "$308",
    )
    assert ist.compute_block_style({"text-indent": "0"})["indent"] is None
    assert ist.compute_block_style({})["indent"] is None
