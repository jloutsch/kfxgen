"""A heading on a contents page that the TOC lists, such as a part's, prints
on the rebuilt page as the heading: its size, weight and level, as Kindle
Previewer 4.0.3 prints the page (#595). kfxgen keeps its link, not
underlined. The end-to-end case, checked against Previewer's file, is in
tests/integration/test_calibre_contents_part_headings.py."""

import pytest

from kfxgen import converter as _conv
from kfxgen.inline_style import make_link_flag
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import NullLog
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

pytestmark = pytest.mark.unit

PART_STYLE = {"bold": True, "font_size": 24.0}
#: Body text at 16px, as calibre gives every block a size; 24px is 1.5 of it.
BODY = {"font_size": 16.0}


def _rebuilt():
    link = frozenset({make_link_flag("c1.xhtml")})
    contents = {
        "title": "Contents",
        "text": "",
        "blocks": [
            {"text": "Contents", "heading_level": 1},
            {"text": "Part One", "heading_level": 2, "block_style": PART_STYLE},
            {"text": "Chapter One", "spans": [(0, 11, link)]},
        ],
    }
    chapters = [
        contents,
        {"title": "Part One", "text": "Part One words."},
        {"title": "Chapter One", "text": "Chapter words."},
    ]
    _conv._rebuild_contents_page(contents, chapters, NullLog())
    return contents["toc_links"]


def test_a_listed_heading_takes_its_level_and_style():
    lines = {link["text"]: link for link in _rebuilt()}
    assert lines["Part One"]["heading_level"] == 2
    assert lines["Part One"]["block_style"] == PART_STYLE
    assert lines["Part One"]["target_chapter_idx"] == 1
    assert "heading_level" not in lines["Chapter One"]


def test_the_generator_prints_it_as_a_linked_heading(tmp_path):
    out = tmp_path / "c.kfx"
    chapters = [
        {
            "title": "Contents",
            "text": "",
            "toc_links": [
                {
                    "text": "Part One",
                    "target_chapter_idx": 1,
                    "level": 0,
                    "heading_level": 2,
                    "block_style": PART_STYLE,
                },
                {"text": "Chapter One", "target_chapter_idx": 2, "level": 0},
            ],
        },
        {
            "title": "Part One",
            "text": "Part words.",
            "blocks": [{"text": "Part words.", "spans": [], "block_style": BODY}],
        },
        {
            "title": "Chapter One",
            "text": "Chapter words.",
            "blocks": [{"text": "Chapter words.", "spans": [], "block_style": BODY}],
        },
    ]
    NativeKFXGenerator().generate_full_book("T", "A", chapters, output_path=str(out))
    frags = load_fragments(out)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    lines = {}
    for st in by_type(frags, "$259"):
        page = {}
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                t = str(text[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                page.setdefault(t, e)
        if "Chapter One" in page and "Part One" in page:
            lines = page  # the Contents page, not the part's own heading
    part, chapter = lines["Part One"], lines["Chapter One"]

    def run_style(entry):
        (run,) = [getattr(r, "value", r) for r in entry["$142"]]
        assert "$179" in [str(k) for k in run]
        return styles[str(run["$157"])]

    assert str(styles[str(part["$157"])]["$13"]) == "$361"
    assert float(styles[str(part["$157"])]["$16"]["$307"]) == 1.5
    assert str(run_style(part).get("$23")) != "$328"
    # A plain contents line is still underlined.
    assert str(run_style(chapter).get("$23")) == "$328"
