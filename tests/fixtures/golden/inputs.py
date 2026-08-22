"""
Synthetic EPUB builders for the #48 golden-file regression corpus.

Each builder returns the path to a freshly built EPUB that exercises one
historical regression class. The full pipeline
(EPUB → EpubAsOeb → converter → NativeKFXGenerator) is then run against
this input by both `regenerate.py` (to produce the committed golden KFX)
and `tests/integration/test_golden_corpus.py` (to diff a fresh build
against the golden).

Real-book device-verified KFX files in the maintainer's local
`research/` directory are NOT used as goldens — they're 8+ MB,
gitignored, and derived from copyrighted EPUBs. The synthetic fixtures
below cover the same regression shapes at a fraction of the size with
no copyright concern.

Adding a fixture:
1. Write a `make_<name>(out_dir) -> Path` builder below.
2. Append `("<name>", make_<name>)` to `GOLDEN_INPUTS`.
3. Run `python -m tests.fixtures.golden.regenerate` to produce
   `expected/<name>.kfx`.
4. Commit both the builder change and the new golden together.
"""

from __future__ import annotations

from pathlib import Path

from tests._helpers import MINIMAL_JPEG as _MINIMAL_JPEG
from tests.fixtures.epub_builder import EpubBuilder


def make_minimal(out_dir: Path) -> Path:
    """Three plain-text chapters. Sanity baseline — exercises the
    happy path with no images, no cover, no in-book links."""
    return (
        EpubBuilder()
        .set_metadata(title="Minimal Golden", author="Golden Author")
        .add_chapter("Chapter One", "First chapter body.\n\nSecond paragraph.")
        .add_chapter("Chapter Two", "Second chapter body.\n\nAnother paragraph.")
        .add_chapter("Chapter Three", "Third chapter body.\n\nFinal paragraph.")
        .build(out_dir, "minimal")
    )


def make_body_images(out_dir: Path) -> Path:
    """Two chapters, the first with two body `<img>` tags. Locks the v5.3.5
    body-image rendering path that #4 fixed (image $259 entries with
    dedicated $157 styles, image positions in $265, etc.)."""
    body_with_imgs = (
        "<p>Opening paragraph of chapter one.</p>\n"
        '<p><img src="img1.jpg" alt="first image"/></p>\n'
        "<p>Middle paragraph between two images.</p>\n"
        '<p><img src="img2.jpg" alt="second image"/></p>\n'
        "<p>Closing paragraph of chapter one.</p>"
    )
    builder = (
        EpubBuilder()
        .set_metadata(title="Body Images Golden", author="Golden Author")
        # add_chapter normally escapes its body, but we need raw XHTML for
        # the <img> tags to survive into converter.extract_chapters_from_oeb.
        # Pass bytes to bypass the str-path's escape+wrap logic.
        .add_chapter(
            "Image Chapter",
            _xhtml_page("Image Chapter", body_with_imgs).encode("utf-8"),
        )
        .add_chapter(
            "Plain Chapter",
            "This chapter has no images.\n\nJust two paragraphs.",
        )
        .add_manifest_item(
            item_id="img1",
            href="img1.jpg",
            media_type="image/jpeg",
            data=_MINIMAL_JPEG,
        )
        .add_manifest_item(
            item_id="img2",
            href="img2.jpg",
            media_type="image/jpeg",
            data=_MINIMAL_JPEG,
        )
    )
    return builder.build(out_dir, "body_images")


def make_captioned_images(out_dir: Path) -> Path:
    """Images that share a block container with block siblings (#113).

    `make_body_images` wraps each `<img>` in its own `<p>`, which is a leaf
    block — `_walk_inline` consumes the image and it survives. That is the
    shape that always worked, which is why the golden corpus passed for months
    while the shape below silently lost every image in it.

    A container with block children takes the other path in `_walk`, and an
    image dispatched that way used to be dropped without a warning. Both orders
    are covered because the corpus showed the caption position was incidental:
    any block sibling triggers it.
    """
    body = (
        "<p>Opening paragraph.</p>\n"
        '<div class="figure">'
        '<div class="caption">Fig. 1 — caption before the image.</div>'
        '<img src="cap1.jpg" alt="captioned first"/>'
        "</div>\n"
        '<div class="figure">'
        '<img src="cap2.jpg" alt="captioned second"/>'
        '<div class="caption">Fig. 2 — caption after the image.</div>'
        "</div>\n"
        "<p>Closing paragraph.</p>"
    )
    builder = (
        EpubBuilder()
        .set_metadata(title="Captioned Images Golden", author="Golden Author")
        .add_chapter(
            "Figures",
            _xhtml_page("Figures", body).encode("utf-8"),
        )
        .add_manifest_item(
            item_id="cap1",
            href="cap1.jpg",
            media_type="image/jpeg",
            data=_MINIMAL_JPEG,
        )
        .add_manifest_item(
            item_id="cap2",
            href="cap2.jpg",
            media_type="image/jpeg",
            data=_MINIMAL_JPEG,
        )
    )
    return builder.build(out_dir, "captioned_images")


def make_with_cover(out_dir: Path) -> Path:
    """Book with a cover image. Locks cover-image emission ($164/$417 with
    distinct fids, $490 cover_image metadata). #32 cover-in-flow context."""
    return (
        EpubBuilder()
        .set_metadata(title="Cover Golden", author="Golden Author")
        .set_cover(_MINIMAL_JPEG, media_type="image/jpeg", href="cover.jpg")
        .add_chapter("Chapter One", "First chapter body.\n\nSecond paragraph.")
        .add_chapter("Chapter Two", "Second chapter body.")
        .build(out_dir, "with_cover")
    )


def make_multi_chapter(out_dir: Path) -> Path:
    """Eight plain chapters. Exercises the larger-corpus path: more
    `$259` entries, more `$260` sections, larger `$265` position map.
    Catches regressions that only surface at scale (e.g. position
    envelope, EID uniqueness across many sections)."""
    builder = EpubBuilder().set_metadata(
        title="Multi Chapter Golden", author="Golden Author"
    )
    for i in range(1, 9):
        builder = builder.add_chapter(
            f"Chapter {i}",
            f"Body of chapter {i}.\n\nA second paragraph of chapter {i}.\n\nA third paragraph.",
        )
    return builder.build(out_dir, "multi_chapter")


def _xhtml_page(title: str, body_html: str) -> str:
    """Wrap raw inner-body HTML in a minimal valid XHTML document.

    Used by fixtures that need raw markup (img, a, h1) preserved into
    the OEB layer rather than EpubBuilder's default escape-and-wrap.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n{body_html}\n</body>\n"
        "</html>\n"
    )


def make_linked_toc(out_dir: Path) -> Path:
    """A Contents page linking to chapters, and a note marker linking into an
    endnotes page that links back.

    Exercises the anchor/link subsystem: `$266` anchors carrying `$180`,
    span-level `$179` link references, cross-file target resolution, and a
    superscript run. No previous fixture emitted a single `$266`, so the
    entire linking layer had zero golden coverage — every internal link
    kfxgen produced was dead and no committed fixture could see it (#51).
    """
    contents = _xhtml_page(
        "Contents",
        "<h1>Contents</h1>\n"
        "<ol>\n"
        '<li><a href="chapter_2.xhtml#c1">Part One</a>\n'
        '  <ol><li><a href="chapter_2.xhtml#s1">A Nested Entry</a></li></ol>\n'
        "</li>\n"
        '<li><a href="chapter_3.xhtml">Endnotes</a></li>\n'
        "</ol>",
    )
    chapter = _xhtml_page(
        "Part One",
        '<h1 id="c1">Part One</h1>\n'
        '<p id="s1">Body text with a marker'
        '<span style="vertical-align: super"><a href="chapter_3.xhtml#n1">1</a></span>'
        " and more prose after it.</p>",
    )
    notes = _xhtml_page(
        "Endnotes",
        "<h1>Endnotes</h1>\n"
        '<p id="n1"><a href="chapter_2.xhtml#s1">1.</a> The note text.</p>',
    )
    return (
        EpubBuilder()
        .set_metadata(title="Linked TOC Golden", author="Golden Author")
        .add_chapter("Contents", contents.encode())
        .add_chapter("Part One", chapter.encode())
        .add_chapter("Endnotes", notes.encode())
        .build(out_dir, "linked_toc")
    )


def make_marker_offsets(out_dir: Path) -> Path:
    """Notes whose markers sit at the END of long paragraphs — the shape real
    publishers use, and the one `linked_toc` cannot cover.

    In `linked_toc` the return target is an id on the `<p>` itself, so its
    offset is 0 and the fixture passes whether or not anchors carry `$143`.
    Here the id is on the marker's own `<a>`, hundreds of characters into the
    paragraph, which is where a return link used to land on the first line
    instead of the marker. One paragraph also runs past CHUNK_SIZE so the
    offset has to rebase into a later chunk. (#79)
    """
    prose = "Sentence that carries the argument forward. " * 12  # ~530 chars
    long_prose = "Filler that pushes this paragraph past the chunk size. " * 45
    chapter = _xhtml_page(
        "Part One",
        '<h1 id="c1">Part One</h1>\n'
        f"<p>{prose}"
        '<a id="ref1" href="chapter_2.xhtml#n1">'
        '<span style="vertical-align: super">1</span></a></p>\n'
        f"<p>{long_prose}"
        '<a id="ref2" href="chapter_2.xhtml#n2">'
        '<span style="vertical-align: super">2</span></a></p>',
    )
    notes = _xhtml_page(
        "Endnotes",
        "<h1>Endnotes</h1>\n"
        '<p id="n1"><a href="chapter_1.xhtml#ref1">1.</a> First note.</p>\n'
        '<p id="n2"><a href="chapter_1.xhtml#ref2">2.</a> Second note.</p>',
    )
    return (
        EpubBuilder()
        .set_metadata(title="Marker Offsets Golden", author="Golden Author")
        .add_chapter("Part One", chapter.encode())
        .add_chapter("Endnotes", notes.encode())
        .build(out_dir, "marker_offsets")
    )


def make_unused_manifest_image(out_dir: Path) -> Path:
    """A book whose manifest declares an image no `<img>` references.

    Publishers ship unused assets, and content kfxgen elides takes its image
    references with it. Either way the resource and its bytes were emitted and
    then pointed at by nothing, which upstream grades ERROR (#102).

    The cover is here deliberately: it is referenced through `$490` metadata
    rather than a `$259` entry, so a prune that only looks at `$259` would
    delete it. This fixture fails if that happens.
    """
    body = (
        "<p>Opening paragraph.</p>\n"
        '<p><img src="used.jpg" alt="a referenced image"/></p>\n'
        "<p>Closing paragraph.</p>"
    )
    return (
        EpubBuilder()
        .set_metadata(title="Unused Manifest Image", author="Golden Author")
        .set_cover(_MINIMAL_JPEG)
        .add_chapter("Images", _xhtml_page("Images", body).encode())
        .add_manifest_item(
            item_id="used",
            href="used.jpg",
            media_type="image/jpeg",
            data=_MINIMAL_JPEG,
        )
        .add_manifest_item(
            item_id="unused",
            href="unused.jpg",
            media_type="image/jpeg",
            data=_MINIMAL_JPEG,
        )
        .build(out_dir, "unused_manifest_image")
    )


def _epub3_page(title: str, body_html: str) -> str:
    """Like `_xhtml_page` but declaring the `epub:` namespace.

    Kept separate rather than added to `_xhtml_page` so the existing golden
    fixtures' bytes do not churn.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n{body_html}\n</body>\n"
        "</html>\n"
    )


def make_publisher_structure(out_dir: Path) -> Path:
    """The shapes commercial EPUBs have and Project Gutenberg does not.

    Every one of these was found by a device report rather than by a test,
    because no fixture and no book in the public-domain corpus exercised
    them — Gutenberg regenerates every title through one pipeline, so its
    output is uniformly flat: no nested navigation, no `page-list`, no
    `hidden` navs, one directory.

    Pins, in order:

    * nested ``<ol>`` inside ``<li>`` — was flattening a Part heading and
      every chapter under it into one paragraph (#58)
    * a container holding inline text *and* block children — the inline text
      was silently dropped, costing 44% of one book's body (#58)
    * ``hidden="hidden"`` ``page-list`` and ``landmarks`` navs — arrived as
      hundreds of blocks of bare page numbers (#60)
    * back matter whose heading equals its chapter title — that block is
      elided as redundant and used to take the id the TOC links to with it,
      killing the link (#62)
    * two documents sharing a basename in different folders — anchor keys
      were basenames, so the first one silently won every link (#69)
    * a superscripted note marker linking cross-folder, via `<sup>`
    * a chapter whose nav title is reconstructed by two body blocks (#64)
    * back matter whose heading equals its nav title, holding the id the
      navigation links to — the elided block's anchor must survive onto the
      chapter's first chunk or the link is dropped (#62)

    The navigation document is titled "Navigation", not "Contents", and that
    is load-bearing. `_rebuild_contents_page` discards the blocks of any
    chapter titled "contents"/"table of contents" and re-emits chapter-index
    links in their place, so a nav page under that title reaches neither the
    block extractor nor the body-link resolver: its nested `<ol>`, its hidden
    navs and its `<a href>` targets are all thrown away before the code they
    are meant to exercise ever sees them. Under that title three of the pins
    above passed vacuously and #62 was never reached at all (#76). Publisher
    nav documents carry titles other than "Contents" routinely; that shape is
    also where #60 was found. `linked_toc` covers the rebuild path.

    The CSS route to superscript (`vertical-align` on a span) is deliberately
    not used here: it resolves through Calibre's Stylizer, which golden
    generation runs without, so it would silently produce no span. That route
    is covered by unit tests with an injected stylizer. The #64 split-opener
    elision likewise needs a chapter title from the nav, which these
    manifest-only spine items do not have; it is unit-tested instead.
    """
    toc = _epub3_page(
        "Navigation",
        "<h1>Navigation</h1>\n"
        '<nav epub:type="toc">\n'
        "<ol>\n"
        '<li><a href="text/part1.xhtml#p1">PART I</a>\n'
        "  <ol>\n"
        '  <li><a href="text/part1.xhtml#c1">1. First Chapter</a></li>\n'
        '  <li><a href="back/notes.xhtml#n1">Notes</a></li>\n'
        "  </ol>\n"
        "</li>\n"
        '<li><a href="back/afterword.xhtml">Afterword</a></li>\n'
        '<li><a href="chapter_3.xhtml#ea1">Endnote Appendix</a></li>\n'
        "</ol>\n"
        "</nav>\n"
        '<nav epub:type="landmarks" hidden="hidden"><ol>\n'
        '<li><a href="text/part1.xhtml">Begin Reading</a></li></ol></nav>\n'
        '<nav epub:type="page-list" hidden="hidden"><h2>Page List</h2><ol>\n'
        '<li><a href="text/part1.xhtml#pg1">1</a></li>\n'
        '<li><a href="text/part1.xhtml#pg2">2</a></li>\n'
        '<li><a href="text/part1.xhtml#pg3">3</a></li></ol></nav>',
    )
    # Container with its own text alongside block children, plus a chapter
    # opener split into numeral and title.
    part = _xhtml_page(
        "PART I",
        '<h1 id="p1">PART I</h1>\n'
        "<div>Lead-in text that belongs to the div itself.<p>A nested paragraph.</p></div>\n"
        '<p id="c1" class="num">1</p>\n'
        "<p>First Chapter</p>\n"
        # Both note links are written relative to THIS document, which lives in
        # text/. The superscript one used to read "back/notes.xhtml#n1", which
        # resolves to text/back/notes.xhtml — a file that does not exist — so
        # the marker rendered superscripted but linked nowhere, and the pin on
        # <sup> link handling was inert (#76).
        '<p><span id="pg1"/>Body prose with a marker'
        '<sup><a href="../back/notes.xhtml#n1">1</a></sup>'
        " and a cross-folder link to "
        '<a href="../back/notes.xhtml#n1">the notes</a>.</p>',
    )
    # Same basename as text/notes.xhtml below — the collision case.
    back_notes = _xhtml_page(
        "Notes",
        '<h1>Notes</h1>\n<p id="n1">1. The note text, in back/notes.xhtml.</p>',
    )
    text_notes = _xhtml_page(
        "Front Notes",
        '<h1>Front Notes</h1>\n<p id="n1">A different n1, in text/notes.xhtml.</p>',
    )
    # Heading equal to the chapter title — the elision case.
    afterword = _xhtml_page("Afterword", "<h1>Afterword</h1>\n<p>Closing remarks.</p>")
    # A chapter whose nav title is "3. Split Opener" while the page prints the
    # numeral and the title as separate blocks. The synthesized heading used to
    # land on top of the book's own opener and the name rendered twice (#64);
    # this only exercises that path now that fixtures get nav-derived titles
    # (#74).
    split_opener = _xhtml_page(
        "3. Split Opener",
        '<p class="num">3</p>\n<p class="ttl">Split Opener</p>\n'
        "<p>Chapter body prose.</p>",
    )
    # Back matter whose <h1> equals its nav title. That block is elided as
    # redundant — and it carries the id the navigation links to, so the anchor
    # has to survive the elision or the link dies silently (#62). Confirmed
    # reached: deleting the anchor-carry in native_generator drops this link
    # and the expected-count assertion goes red.
    endnote_page = _xhtml_page(
        "Endnote Appendix",
        '<h1 id="ea1">Endnote Appendix</h1>\n<p>Appendix body.</p>',
    )
    builder = (
        EpubBuilder()
        .set_metadata(title="Publisher Structure Golden", author="Golden Author")
        .add_chapter("Navigation", toc.encode())
        .add_chapter("3. Split Opener", split_opener.encode())
        .add_chapter("Endnote Appendix", endnote_page.encode())
    )
    for item_id, href, page in (
        ("part1", "text/part1.xhtml", part),
        ("textnotes", "text/notes.xhtml", text_notes),
        ("backnotes", "back/notes.xhtml", back_notes),
        ("afterword", "back/afterword.xhtml", afterword),
    ):
        builder = builder.add_manifest_item(
            item_id=item_id,
            href=href,
            media_type="application/xhtml+xml",
            data=page.encode(),
            in_spine=True,
        )
    return builder.build(out_dir, "publisher_structure")


#: One paragraph of ordinary prose. Repeated to build a chapter whose text
#: exceeds the per-`$145` byte cap several times over.
_LONG_PARA = (
    "The quick brown fox jumps over the lazy dog, and then continues on past "
    "the hedgerow toward the river where the light falls in long bars across "
    "the water and the afternoon draws itself out into evening once more. "
)

#: The same, in a script that costs 3 bytes per character. A packer that
#: budgets on `len(str)` rather than encoded bytes passes the ASCII chapter
#: and still overflows on this one.
_LONG_PARA_CJK = "月明かりが川の上に長く伸びて、夕暮れがゆっくりと訪れる。" * 4


def make_long_chapter(out_dir: Path) -> Path:
    """Chapters whose text exceeds the per-`$145` content-fragment cap (#37).

    One `$145` per chapter overflowed the format's 8192-byte per-fragment
    maximum on ordinary trade books — upstream `kfxlib` reported single
    fragments of ~121 KB, 15x the ceiling. Every other fixture here is small
    enough to fit one fragment, so nothing in the corpus could see it.

    The cap is measured the way upstream measures it, which is not obvious:

        sum(len(s.encode("utf8")) for s in fragment["$146"][:-1]) >= 8192

    The **last** string is excluded and the comparison is `>=`, so a fragment
    totalling exactly 8192 is already a violation. Both chapters here are far
    enough over that an off-by-one in the budget still fails the assertion.

    The CJK chapter is not decoration: at 3 bytes/character a packer budgeting
    on character count clears the ASCII chapter and still emits fragments
    upstream rejects.
    """
    ascii_body = "".join(f"<p>{_LONG_PARA}</p>\n" for _ in range(120))
    cjk_body = "".join(f"<p>{_LONG_PARA_CJK}</p>\n" for _ in range(60))
    return (
        EpubBuilder()
        .set_metadata(title="Long Chapter Golden", author="Golden Author")
        .add_chapter(
            "Long Chapter",
            _xhtml_page(
                "Long Chapter", f"<h1>Long Chapter</h1>\n{ascii_body}"
            ).encode(),
        )
        .add_chapter(
            "Long Chapter CJK",
            _xhtml_page(
                "Long Chapter CJK", f"<h1>Long Chapter CJK</h1>\n{cjk_body}"
            ).encode(),
        )
        .build(out_dir, "long_chapter")
    )


# Registry consumed by both regenerate.py and test_golden_corpus.py.
#
# Each fixture is paired with a structural-fingerprint check in
# test_golden_corpus.py so the harness fails red if a fixture stops
# exercising its target shape (e.g. body_images no longer emits image
# resource refs). This catches "fixture rotted into the same shape as
# minimal" regressions that the byte/structural diff would silently miss.
GOLDEN_INPUTS: list[tuple[str, callable]] = [
    ("minimal", make_minimal),
    ("body_images", make_body_images),
    ("captioned_images", make_captioned_images),
    ("with_cover", make_with_cover),
    ("multi_chapter", make_multi_chapter),
    ("linked_toc", make_linked_toc),
    ("publisher_structure", make_publisher_structure),
    ("long_chapter", make_long_chapter),
    ("marker_offsets", make_marker_offsets),
    ("unused_manifest_image", make_unused_manifest_image),
]
