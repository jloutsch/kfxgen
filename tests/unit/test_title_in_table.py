"""A chapter opening with a table that prints its title keeps the table as
written, with no heading of kfxgen's, as Kindle Previewer 4.0.3 prints it
(#470). The title cut left an empty cell where the title was, or ate the
rows that spelled it."""

import pytest

from tests.unit.test_native_generator import (
    _box_block,
    _chapter_texts,
    _para,
    _table_block,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("blocks", "title", "texts"),
    [
        # QA's shapes: a row holding the title and more, and a title spelled
        # over rows. Every cell stays, and kfxgen prints no heading.
        (
            lambda: [_table_block([["Chapter Four", "S4 page twelve"]])],
            "Chapter Four",
            ["Chapter Four", "S4 page twelve"],
        ),
        (
            lambda: [_table_block([["Chapter"], ["Eight"], ["S8 row three"]])],
            "Chapter Eight",
            ["Chapter", "Eight", "S8 row three"],
        ),
        # An empty spacer row above the title's row.
        (
            lambda: [_table_block([[""], ["Chapter Four", "S4 page twelve"]])],
            "Chapter Four",
            [" ", "Chapter Four", "S4 page twelve"],
        ),
        # A table that is the title alone stays too.
        (
            lambda: [_table_block([["Chapter Nine"]]), _para("Body.")],
            "Chapter Nine",
            ["Chapter Nine", "Body."],
        ),
        # A box opening with such a table, as a table alone: a row, a title
        # spelled over rows, and the title alone (#344's shapes).
        (
            lambda: [_box_block([_table_block([["Chapter Ten", "x"]])])],
            "Chapter Ten",
            ["Chapter Ten", "x"],
        ),
        (
            lambda: [
                _box_block([_table_block([["The"], ["Harbour"], ["A quiet morning."]])])
            ],
            "The Harbour",
            ["The", "Harbour", "A quiet morning."],
        ),
        (
            lambda: [_box_block([_table_block([["One"]]), _para("Text.")])],
            "One",
            ["One", "Text."],
        ),
    ],
    ids=[
        "row",
        "split-rows",
        "spacer-row",
        "title-alone",
        "boxed",
        "boxed-split",
        "boxed-alone",
    ],
)
def test_a_table_printing_the_title_is_kept_whole(tmp_path, blocks, title, texts):
    assert _chapter_texts(tmp_path, blocks(), title=title) == texts


@pytest.mark.parametrize(
    ("blocks", "texts"),
    [
        # A table that doesn't print the title: kfxgen's heading above it.
        (
            lambda: [_table_block([["Name", "Age"]])],
            ["H:The Harbour", "Name", "Age"],
        ),
        # A paragraph holding the title before a table: cut as before.
        (
            lambda: [_para("The Harbour"), _table_block([["a", "b"]])],
            ["H:The Harbour", "a", "b"],
        ),
    ],
    ids=["other-table", "paragraph-first"],
)
def test_other_openings_are_unchanged(tmp_path, blocks, texts):
    assert _chapter_texts(tmp_path, blocks()) == texts
