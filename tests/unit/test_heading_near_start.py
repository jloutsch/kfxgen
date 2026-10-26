"""A book heading among a section's first blocks, not only its first, means
kfxgen prints no TOC label of its own there (#492): an epigraph, then the
heading, reads as the book has it, as Kindle Previewer 4.0.3 prints it. So
does a heading in a table cell. A section with no heading near its start
keeps its label: it gives the section a visible start."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import load_fragments
from tests.unit.test_book_heading_prints import _book, _lines


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "lines"),
    [
        (
            "<p>An epigraph line.</p><h1>Chapter 1</h1><p>Body.</p>",
            ["An epigraph line.", "Chapter 1", "Body."],
        ),
        (
            "<p>Line one.</p><p>Line two.</p><h2>Chapter 1</h2><p>Body.</p>",
            ["Line one.", "Line two.", "Chapter 1", "Body."],
        ),
        # Past the first 3 blocks, the heading isn't the section's opening.
        (
            "<p>A.</p><p>B.</p><p>C.</p><h1>Chapter 1</h1><p>Body.</p>",
            ["Chapter One", "A.", "B.", "C.", "Chapter 1", "Body."],
        ),
        # No heading at all: the label stays.
        ("<p>Body.</p><p>More.</p>", ["Chapter One", "Body.", "More."]),
    ],
    ids=["epigraph", "two-lines", "too-far", "no-heading"],
)
def test_a_heading_near_the_start_takes_the_labels_place(tmp_path, body, lines):
    assert _lines(_book(tmp_path, "Chapter One", body))[: len(lines)] == lines


@pytest.mark.unit
def test_a_heading_in_a_table_cell(tmp_path):
    """Native tables, as a conversion writes them by default."""
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><table><tr><td>'
        "<h1>Chapter 1</h1></td></tr></table><p>Body.</p></body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml", native_tables=True
    )
    out = tmp_path / "t.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "Chapter One", "text": "x", "blocks": blocks},
            {
                "title": "Next",
                "text": "x",
                "blocks": _conv.extract_blocks_from_html(
                    etree.fromstring(
                        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>N.</p></body></html>'
                    ),
                    base_href="n.xhtml",
                ),
            },
        ],
        output_path=str(out),
    )
    assert _lines(load_fragments(out))[:2] == ["Chapter 1", "Body."]
