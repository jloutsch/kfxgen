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
    ("with_cover", make_with_cover),
    ("multi_chapter", make_multi_chapter),
]
