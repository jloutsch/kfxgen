"""The book's own metadata, as Kindle Previewer 4.0.3 writes it (#454): its
dc:description, not "Book: <title>"; one author entry per dc:creator, not
one joined name; and no issue date the book doesn't give: a year alone,
which calibre fills out to a day, or no date at all (the 1970-01-01
placeholder, #89)."""

import types

import pytest

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val


def _title_metadata(tmp_path, **kw):
    out = tmp_path / "m.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author=kw.pop("author", "A"),
        chapters=[
            {
                "title": "One",
                "text": "Body.",
                "blocks": [{"text": "Body.", "spans": []}],
            }
        ],
        output_path=str(out),
        **kw,
    )
    (meta,) = by_type(load_fragments(out), "$490")
    for section in val(meta)["$491"]:
        if str(section.get("$495")) == "kindle_title_metadata":
            return [(str(f["$492"]), f["$307"]) for f in section["$258"]]
    return []


@pytest.mark.unit
def test_one_author_entry_per_creator_and_the_books_description(tmp_path):
    meta = _title_metadata(
        tmp_path, authors=["Author One", "Author Two"], description="A short one."
    )
    assert [v for k, v in meta if k == "author"] == ["Author One", "Author Two"]
    assert [str(v) for k, v in meta if k == "description"] == ["A short one."]


@pytest.mark.unit
def test_no_description_and_no_date_when_the_book_has_none(tmp_path):
    meta = _title_metadata(tmp_path)
    keys = [k for k, _v in meta]
    assert "description" not in keys
    assert "issue_date" not in keys
    assert [v for k, v in meta if k == "author"] == ["A"]


@pytest.mark.unit
def test_a_date_the_book_gives_is_written(tmp_path):
    meta = _title_metadata(tmp_path, issue_date="2021-03-04")
    assert [str(v) for k, v in meta if k == "issue_date"] == ["2021-03-04"]


def _oeb(dates, raw_opf_date):
    """A stand-in OEB book whose calibre metadata holds `dates` and whose own
    OPF says `raw_opf_date` (None: no OPF to read)."""
    md = types.SimpleNamespace(
        title=["T"],
        creator=["A"],
        language=["en"],
        publisher=[],
        date=dates,
        description=[],
    )
    book = types.SimpleNamespace(metadata=md)
    book._raw_date = raw_opf_date
    return book


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "calibre_date", "issue"),
    [
        ("1897", "1897-10-15T05:00:00+00:00", None),
        ("1897-03", "1897-03-15T05:00:00+00:00", None),
        ("2021-03-04", "2021-03-04T05:00:00+00:00", "2021-03-04"),
        ("2021-03-04T10:00:00Z", "2021-03-04T10:00:00+00:00", "2021-03-04"),
        (None, "2021-03-04T05:00:00+00:00", "2021-03-04"),
    ],
    ids=["year", "year-month", "day", "with-time", "no-opf"],
)
def test_only_a_full_date_is_an_issue_date(monkeypatch, raw, calibre_date, issue):
    monkeypatch.setattr(
        _conv, "_opf_raw_dates", lambda oeb: None if raw is None else [raw]
    )
    meta = _conv.extract_metadata(
        _oeb([calibre_date], raw), log=types.SimpleNamespace(error=print)
    )
    assert meta["issue_date"] == issue
