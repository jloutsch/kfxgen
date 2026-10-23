"""Where a link lands (#282).

Three mutations the #282 recheck found every test still passes: a cover
book's Contents links one chapter early, a paragraph's picture taking the
anchors of its later text, and a duplicate id landing on its last use
instead of its first. Each test follows a link the way the reader does:
the span's `$179` names a `$266` anchor, whose `$183` gives the entry
(`$155`) and the offset in it (`$143`).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.converter import _make_img_token  # noqa: E402
from kfxgen.inline_style import make_link_flag  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402
from tests._helpers import MINIMAL_JPEG, jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402

pytestmark = pytest.mark.unit


def _landings(kfx):
    """{link text: the text of the entry it lands on, from the offset it
    names} for every link span in the written book."""
    frags = load_fragments(kfx)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    anchors = {str(f.fid): val(f)["$183"] for f in by_type(frags, "$266")}
    entries, links = {}, []
    for story in by_type(frags, "$259"):
        for e in iter_entries(val(story)["$146"]):
            ref = e.get("$145")
            text = str(texts[str(ref["name"])][ref["$403"]]) if ref else ""
            entries[int(e["$155"])] = text
            for span in e.get("$142") or ():
                if "$179" in span:
                    start = int(span["$143"])
                    links.append(
                        (text[start : start + int(span["$144"])], str(span["$179"]))
                    )
    out = {}
    for label, target in links:
        pos = anchors[target]
        out[label] = entries[int(pos["$155"])][int(pos.get("$143") or 0) :]
    return out


def _para(text, **extra):
    return {"text": text, "spans": [], **extra}


def _link(label, key):
    return _para(label, spans=[(0, len(label), frozenset({make_link_flag(key)}))])


def test_a_cover_books_contents_links_open_their_own_chapters(tmp_path):
    gen = NativeKFXGenerator()
    gen.generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "Contents",
                "text": "",
                "toc_links": [
                    {"text": "To one", "target_chapter_idx": 1},
                    {"text": "To two", "target_chapter_idx": 2},
                ],
            },
            {"title": "Chapter 1", "text": "Chapter 1\n\nFirst chapter body."},
            {"title": "Chapter 2", "text": "Chapter 2\n\nSecond chapter body."},
        ],
        cover_image=MINIMAL_JPEG,
        output_path=str(tmp_path / "b.kfx"),
    )
    landings = _landings(tmp_path / "b.kfx")
    # Labels the chapters don't print, so a link landing on itself fails.
    assert landings["To one"] == "Chapter 1"
    assert landings["To two"] == "Chapter 2"


def test_a_picture_takes_only_the_anchors_at_its_paragraphs_start(tmp_path):
    # A note marker after the picture keeps its own place, so its return
    # link lands on the marker, not on the picture.
    pic = _make_img_token("p.jpg", "", None)
    text = pic + "A sentence with a marker."
    marker = text.index("marker")
    gen = NativeKFXGenerator()
    gen.generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "Chapter",
                "text": "x",
                "blocks": [
                    _para(
                        text,
                        anchor_keys=["c.xhtml#fig", "c.xhtml#ref1"],
                        anchor_offsets={"c.xhtml#fig": 0, "c.xhtml#ref1": marker},
                    ),
                    _link("Back to the marker", "c.xhtml#ref1"),
                ],
            }
        ],
        images={"p.jpg": jpeg_of(40, 30)},
        output_path=str(tmp_path / "b.kfx"),
    )
    assert _landings(tmp_path / "b.kfx")["Back to the marker"] == "marker."


def test_a_duplicate_id_lands_on_its_first_use(tmp_path):
    gen = NativeKFXGenerator()
    gen.generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "Chapter",
                "text": "x",
                "blocks": [
                    _link("To the place", "c.xhtml#dup"),
                    _para("First place.", anchor_keys=["c.xhtml#dup"]),
                    _para("Second place.", anchor_keys=["c.xhtml#dup"]),
                ],
            }
        ],
        output_path=str(tmp_path / "b.kfx"),
    )
    assert _landings(tmp_path / "b.kfx")["To the place"] == "First place."
