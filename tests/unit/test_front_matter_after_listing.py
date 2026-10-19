"""A dedication or epigraph after a rebuilt contents listing is kept (#393).

kfxgen rebuilds a book's contents listing as its own Contents page (#132),
replacing the chapter that held it. pg64317's listing is followed in the same
file by a dedication and an epigraph with no TOC entry of their own, so they
were replaced with it.

#389 kept every block after the listing, and printed contents leftovers in
about 30 books of its A/B. Only blocks the book marks as front matter are
kept now: an element whose class, `epub:type` or ARIA role names a
dedication, an epigraph, a copyright page or a colophon. They follow the
rebuilt page as a chapter of their own, out of the navigation and with no
heading. Anything else after the listing is replaced with it, as before.
"""

import pytest

from kfxgen.converter import extract_chapters_from_oeb
from tests.unit.test_converter import _OEBBook, _RawSpineItem, _silent_log, _TOCNode

META = {"title": "The Real Title", "author": "Jane Author"}
LISTING = (
    '<div class="toc"><p><a href="c1.xhtml">Chapter I</a></p>'
    '<p><a href="c2.xhtml">Chapter II</a></p></div>'
)
TOC = [
    ("Contents", "front.xhtml"),
    ("Chapter I", "c1.xhtml"),
    ("Chapter II", "c2.xhtml"),
]


def _chapters(front, toc=TOC):
    oeb = _OEBBook(
        [
            _RawSpineItem("front.xhtml", front),
            _RawSpineItem("c1.xhtml", "<p>First chapter prose.</p>"),
            _RawSpineItem("c2.xhtml", "<p>Second chapter prose.</p>"),
        ],
        [_TOCNode(t, h) for t, h in toc],
    )
    return extract_chapters_from_oeb(oeb, _silent_log(), metadata=META)


def _contents(chapters):
    (contents,) = [ch for ch in chapters if ch.get("toc_links")]
    return contents


def _kept_after(chapters):
    """The texts of the chapter after the rebuilt page when it is the kept
    front matter, else None."""
    i = chapters.index(_contents(chapters))
    after = chapters[i + 1] if i + 1 < len(chapters) else None
    if after is None or not after.get("_omit_from_toc"):
        return None
    assert after.get("_omit_title_heading")
    return [b["text"] for b in after["blocks"]]


@pytest.mark.unit
def test_pg64317s_dedication_and_epigraph_are_kept():
    front = (
        "<h1>Contents</h1>" + LISTING + '<div class="dedication"><p>Once again'
        '<br/>to<br/>Zelda</p></div><div class="epigraph"><blockquote>'
        "<p>Then wear the gold hat.</p></blockquote></div>"
    )
    chapters = _chapters(front)
    assert _kept_after(chapters) == ["Once again\nto\nZelda", "Then wear the gold hat."]
    # The rebuilt page's links still reach their chapters.
    assert [
        (link["text"], chapters[link["target_chapter_idx"]]["title"])
        for link in _contents(chapters)["toc_links"]
    ] == [("Chapter I", "Chapter I"), ("Chapter II", "Chapter II")]


@pytest.mark.unit
@pytest.mark.parametrize(
    "marked",
    [
        '<section epub:type="dedication"><p>For my family.</p></section>',
        '<section epub:type="copyright-page"><p>For my family.</p></section>',
        '<div role="doc-epigraph"><p>For my family.</p></div>',
        '<div role="doc-colophon"><p>For my family.</p></div>',
        '<p class="dedication">For my family.</p>',
    ],
)
def test_front_matter_marked_any_way_is_kept(marked):
    assert _kept_after(_chapters(LISTING + marked)) == ["For my family."]


@pytest.mark.unit
def test_unmarked_text_after_the_listing_is_still_replaced():
    """#389's leftovers: a second, unlinked contents list after the one
    kfxgen recognises."""
    front = LISTING + "<p>Chapter I</p><p>Chapter II</p><p>_152035053_</p>"
    chapters = _chapters(front)
    assert _kept_after(chapters) is None
    assert [ch["title"] for ch in chapters] == ["Contents", "Chapter I", "Chapter II"]


@pytest.mark.unit
def test_only_the_marked_blocks_after_the_listing_are_kept():
    front = (
        LISTING + "<p>Chapter I</p>"
        '<div class="dedication"><p>For my family.</p></div><p>Chapter II</p>'
    )
    assert _kept_after(_chapters(front)) == ["For my family."]


@pytest.mark.unit
def test_a_marked_page_in_a_chapter_of_its_own_is_untouched():
    """A dedication with its own TOC entry is its own chapter already."""
    toc = TOC[:1] + [("Dedication", "c1.xhtml")] + TOC[2:]
    oeb = _OEBBook(
        [
            _RawSpineItem("front.xhtml", LISTING),
            _RawSpineItem(
                "c1.xhtml", '<div class="dedication"><p>For my family.</p></div>'
            ),
            _RawSpineItem("c2.xhtml", "<p>Second chapter prose.</p>"),
        ],
        [_TOCNode(t, h) for t, h in toc],
    )
    chapters = extract_chapters_from_oeb(oeb, _silent_log(), metadata=META)
    assert [ch["title"] for ch in chapters] == ["Contents", "Dedication", "Chapter II"]


@pytest.mark.unit
def test_a_picture_after_the_listing_is_still_kept_by_the_rebuild():
    """The rebuild keeps the pictures of the chapter it replaces (#117); only
    the marked front matter moves to a chapter of its own."""
    front = LISTING + '<p><img src="plate.jpg" alt="plate"/></p>'
    front += '<div class="dedication"><p>For my family.</p></div>'
    chapters = _chapters(front)
    assert _kept_after(chapters) == ["For my family."]
    assert _contents(chapters).get("preserved_images")


@pytest.mark.unit
def test_a_picture_in_the_kept_front_matter_is_shown_once():
    """It moves with its dedication; the rebuilt page doesn't keep it as
    well (#396 review)."""
    front = LISTING + '<div class="dedication"><p>For my family.</p>'
    front += '<p><img src="d.jpg" alt="d"/></p></div>'
    chapters = _chapters(front)
    kept = _kept_after(chapters)
    assert kept[0] == "For my family." and "d.jpg" in kept[1]
    assert not _contents(chapters).get("preserved_images")
