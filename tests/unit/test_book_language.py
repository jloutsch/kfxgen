"""The book's language is written as the book gives it, as Kindle Previewer
4.0.3 writes it, not cut to two letters (#453)."""

from types import SimpleNamespace

import pytest

from kfxgen import converter as _conv


def _language(lang):
    meta = SimpleNamespace(
        title=[], creator=[], language=[lang] if lang is not None else []
    )
    return _conv.extract_metadata(SimpleNamespace(metadata=meta), None)["language"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lang", "written"),
    [
        # A region, a script and the book's own spelling are kept.
        ("en-GB", "en-GB"),
        ("EN-us", "EN-us"),
        ("pt_BR", "pt_BR"),
        ("zh-Hant-TW", "zh-Hant-TW"),
        ("en", "en"),
        # A code with no two-letter form isn't cut into another language's.
        ("haw", "haw"),
        ("grc", "grc"),
        # No language: calibre gives "und"; Previewer writes "en".
        ("und", "en"),
        (None, "en"),
        ("", "en"),
    ],
)
def test_the_language_is_written_as_the_book_gives_it(lang, written):
    assert _language(lang) == written


@pytest.mark.unit
def test_a_three_letter_code_takes_its_two_letter_form(monkeypatch):
    """As Previewer writes "fre" as "fr" and "eng" as "en", by calibre's own
    ISO 639 table (stubbed here: unit tests run without calibre)."""
    monkeypatch.setattr(
        _conv, "_iso639_1", lambda code: {"eng": "en", "fre": "fr"}.get(code.lower())
    )
    assert [_language(c) for c in ("eng", "fre", "eng-GB", "haw")] == [
        "en",
        "fr",
        "en-GB",
        "haw",
    ]
