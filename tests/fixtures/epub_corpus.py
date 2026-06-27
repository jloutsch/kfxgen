"""
Per-fixture builder functions and Outcome contracts for the #49 integration
corpus. PR1 = 9 fixtures (structural + unicode). PR2 will add image and
adversarial fixtures using the same Outcome dataclass.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import pytest

from tests.fixtures.epub_builder import EpubBuilder


# Image bytes for PR2 cover fixtures. Constants live here rather than
# in EpubBuilder because they're per-fixture test inputs, not a
# capability the builder needs to provide.
_VALID_JPEG_SMALL = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 100

# 50MB of JFIF-headed bytes followed by incompressible random data.
# Large enough to exercise the cover pipeline's bandwidth handling
# without exceeding MAX_DECODE_SIZE (default 64MB; hard-ceiling 1GB
# per #80). secrets.token_bytes (not b"\x00" * N) so the payload is
# incompressible — a future regression that adds deflate wrapping
# can't shrink it below the size-band check below.
_VALID_JPEG_OVERSIZED = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + secrets.token_bytes(
    50 * 1024 * 1024
)

# Valid JPEG header followed by garbage. PR #65 N4 documented that
# the magic-byte gate accepts this — Kindle rejects at render time.
_MALFORMED_JPEG = b"\xff\xd8\xff\xe0" + b"\xde\xad\xbe\xef" * 200

# Zip magic, not an image. Magic-byte gate should reject.
_ZIP_AS_IMAGE = b"PK\x03\x04" + b"\x00" * 200


@dataclass(frozen=True)
class Outcome:
    """Expected outcome of running a fixture through the conversion pipeline."""

    kind: Literal["succeeds", "raises"]
    exception_type: type[BaseException] | None = None
    message_pattern: str | None = None  # regex, optional
    # Optional post-success assertion. The integration runner invokes this
    # after the standard succeed-path checks (#43 invariant subset, kfxlib
    # round-trip). Lets a fixture pin behavior beyond bare succeeds-vs-raises
    # so future drift flips the test red rather than passing silently.
    additional_check: Callable[[Path], None] | None = None

    @classmethod
    def succeeds(cls, *, check: Callable[[Path], None] | None = None) -> "Outcome":
        return cls(kind="succeeds", additional_check=check)

    @classmethod
    def raises(cls, exc: type[BaseException], pattern: str = "") -> "Outcome":
        return cls(kind="raises", exception_type=exc, message_pattern=pattern or None)


def make_single_chapter(out_dir: Path) -> tuple[Path, Outcome]:
    """Baseline happy case — smoke test for the entire pipeline."""
    path = (
        EpubBuilder()
        .set_metadata(title="Single", author="T")
        .add_chapter("Ch1", "Hello world.\n\nA second paragraph.")
        .build(out_dir, "single_chapter")
    )
    return path, Outcome.succeeds()


def make_zero_chapters(out_dir: Path) -> tuple[Path, Outcome]:
    """Empty spine. Pinned 2026-05-04 after #72 fix landed:
    extract_chapters_from_oeb now raises ValueError instead of returning
    the "No content extracted." sentinel chapter. The sentinel was a
    silent-success failure mode that bypassed generate_full_book's
    documented "Raises ValueError if empty or None" validation."""
    path = (
        EpubBuilder()
        .set_metadata(title="Zero", author="T")
        .build(out_dir, "zero_chapters")
    )
    return path, Outcome.raises(ValueError, "No spine items with extractable text")


def make_empty_chapter(out_dir: Path) -> tuple[Path, Outcome]:
    """Chapter exists with empty body (sometimes legitimate — separator pages).
    Empirical (2026-05-03, post oeb_shim lxml fix): heading "Ch1" survives
    extraction but body is empty; converter falls back to the sentinel
    chapter; KFX ~5KB."""
    path = (
        EpubBuilder()
        .set_metadata(title="Empty", author="T")
        .add_chapter("Ch1", "")
        .build(out_dir, "empty_chapter")
    )
    return path, Outcome.succeeds()


def make_whitespace_only(out_dir: Path) -> tuple[Path, Outcome]:
    """Chapter contains only whitespace — should normalize to same effect as empty.
    Empirical (2026-05-03, post oeb_shim lxml fix): same outcome as
    empty_chapter — sentinel chapter, ~5KB."""
    path = (
        EpubBuilder()
        .set_metadata(title="WS", author="T")
        .add_chapter("Ch1", "   \n\n   \t   \n  ")
        .build(out_dir, "whitespace_only")
    )
    return path, Outcome.succeeds()


def make_huge_chapter(out_dir: Path) -> tuple[Path, Outcome]:
    """1MB single paragraph — stresses the ~2000-char chunk splitter.

    Empirical (2026-05-03, post oeb_shim lxml fix): produces ~1MB KFX,
    chunker handles 1MB body without overflowing the 16000 position envelope
    and the #43 invariants hold."""
    big_paragraph = "Lorem ipsum dolor sit amet. " * (1024 * 1024 // 28)
    path = (
        EpubBuilder()
        .set_metadata(title="Huge", author="T")
        .add_chapter("Ch1", big_paragraph)
        .build(out_dir, "huge_chapter")
    )
    return path, Outcome.succeeds()


def make_many_chapters(out_dir: Path) -> tuple[Path, Outcome]:
    """300 chapters — stresses position-id envelope. May surface a real
    generator bug via TestPositionEnvelopeCeiling — that IS the signal.

    Empirical (2026-05-03, post oeb_shim lxml fix): 300 short chapters fit
    inside the 16000 position envelope; #43 invariants hold; KFX ~210KB."""
    builder = EpubBuilder().set_metadata(title="Many", author="T")
    for i in range(300):
        builder = builder.add_chapter(
            f"Ch{i + 1}", f"Chapter {i + 1} body. Para A.\n\nPara B."
        )
    path = builder.build(out_dir, "many_chapters")
    return path, Outcome.succeeds()


def make_emoji_only(out_dir: Path) -> tuple[Path, Outcome]:
    """2500 x 4-byte UTF-8 chars - stresses chunking on multi-byte boundaries."""
    body = "\U0001f600" * 2500
    path = (
        EpubBuilder()
        .set_metadata(title="Emoji", author="T")
        .add_chapter("Ch1", body)
        .build(out_dir, "emoji_only")
    )
    return path, Outcome.succeeds()


def make_rtl_combining(out_dir: Path) -> tuple[Path, Outcome]:
    """RTL Arabic + combining marks - pass-through verification."""
    body = (
        "السَّلامُ عَلَيْكُم. "
        "هَذَا نَصٌّ تَجْرِيبِيٌّ يَحْتَوِي عَلَى عَلَامَاتٍ مُرَكَّبَةٍ.\n\n"
        "Mixed: שָׁלוֹם and العَرَبيّة together."
    ) * 5
    path = (
        EpubBuilder()
        .set_metadata(title="RTL", author="T")
        .add_chapter("Ch1", body)
        .build(out_dir, "rtl_combining")
    )
    return path, Outcome.succeeds()


def make_non_utf8(out_dir: Path) -> tuple[Path, Outcome]:
    """Declared UTF-8 with embedded invalid byte sequences (\\xff\\xfe, \\xc0\\x80).

    Pinned 2026-05-04 after #73 fix landed: extract_chapters_from_oeb's
    spine loop now uses per-item try/except (instead of a whole-loop
    bare except) and warns + continues for parse failures. With only
    one bad spine item, the loop ends with spine_items_ordered empty,
    triggering the empty-spine ValueError. The XMLSyntaxError itself
    is logged as a warning and absorbed; the user-facing error is the
    cleaner ValueError."""
    invalid_xhtml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<!DOCTYPE html>"
        b'<html xmlns="http://www.w3.org/1999/xhtml">'
        b"<head><title>NU</title></head>"
        b"<body><h1>NU</h1><p>Valid prefix "
        b"\xff\xfe\x00invalid bytes here\xc0\x80"
        b" valid suffix.</p></body></html>"
    )
    path = (
        EpubBuilder()
        .set_metadata(title="NU", author="T")
        .add_chapter("Ch1", invalid_xhtml)
        .build(out_dir, "non_utf8")
    )
    return path, Outcome.raises(ValueError, "No spine items with extractable text")


def make_missing_cover(out_dir: Path) -> tuple[Path, Outcome]:
    """Cover declared in OPF, file missing from zip. extract_cover_image
    should return (None, None); pipeline produces coverless KFX.

    Empirical pinning: first run determines whether _find_manifest_item
    blows up on the dangling reference. If it raises, change to
    Outcome.raises and file follow-up. If it returns None gracefully,
    Outcome.succeeds() suffices."""
    path = (
        EpubBuilder()
        .set_metadata(title="MissingCover", author="T")
        .add_chapter("Ch1", "Body.")
        .set_cover(_VALID_JPEG_SMALL, declare_only=True)
        .build(out_dir, "missing_cover")
    )
    return path, Outcome.succeeds()


def _oversized_cover_check(kfx_path: Path) -> None:
    """Pin that the 50MB cover bytes actually flow through to KFX
    output. If MAX_DECODE_SIZE clamps or the cover gets dropped, KFX
    size won't match.

    Threshold (>=45MB) is 90% of the 50MB input — leaves headroom for
    Ion wrapping overhead but trips on a >10% silent drop. Combined
    with secrets.token_bytes payload (incompressible), this catches
    both clamp-style and compress-style regressions."""
    size = kfx_path.stat().st_size
    assert size >= 45 * 1024 * 1024, (
        f"oversized_cover KFX {size} bytes — expected >=45MB (90% of "
        f"50MB input); cover was dropped, clamped, or unexpectedly "
        f"compressed"
    )


def make_oversized_cover(out_dir: Path) -> tuple[Path, Outcome]:
    """50MB cover image. Stresses the cover pipeline's bandwidth
    handling without exceeding MAX_DECODE_SIZE (default 64MB)."""
    path = (
        EpubBuilder()
        .set_metadata(title="OversizedCover", author="T")
        .add_chapter("Ch1", "Body.")
        .set_cover(_VALID_JPEG_OVERSIZED)
        .build(out_dir, "oversized_cover")
    )
    return path, Outcome.succeeds(check=_oversized_cover_check)


def _malformed_image_check(kfx_path: Path) -> None:
    """Pin that magic-byte gate accepts the malformed payload and the
    bytes flow into KFX output. Without this, a future regression that
    silently rejects malformed-but-magic-valid JPEGs would flip output
    back to the ~5KB sentinel band and the test stays green.

    Empirical observation (2026-05-04): _MALFORMED_JPEG (804 bytes) +
    Ion BLOB framing → KFX ~6,800 bytes. The 6000-8000 band catches
    drift in either direction."""
    size = kfx_path.stat().st_size
    assert 6000 < size < 8000, (
        f"malformed_image KFX {size} bytes — expected 6000-8000 band "
        f"(magic-byte gate accepts + _MALFORMED_JPEG flows through). "
        f"Regression candidate: silent rejection (sentinel band) or "
        f"unexpected wrapping overhead."
    )


def make_malformed_image(out_dir: Path) -> tuple[Path, Outcome]:
    """JPEG header + garbage body. Magic-byte gate accepts (PR #65 N4
    documented this design choice — Kindle rejects at render time)."""
    path = (
        EpubBuilder()
        .set_metadata(title="MalformedImage", author="T")
        .add_chapter("Ch1", "Body.")
        .set_cover(_MALFORMED_JPEG)
        .build(out_dir, "malformed_image")
    )
    return path, Outcome.succeeds(check=_malformed_image_check)


def make_zip_as_image(out_dir: Path) -> tuple[Path, Outcome]:
    """Cover image bytes are actually a zip file (PK header). Magic-byte
    gate should reject; extract_cover_image returns (None, None);
    pipeline produces coverless KFX — same shape as missing_cover."""
    path = (
        EpubBuilder()
        .set_metadata(title="ZipAsImage", author="T")
        .add_chapter("Ch1", "Body.")
        .set_cover(_ZIP_AS_IMAGE)
        .build(out_dir, "zip_as_image")
    )
    return path, Outcome.succeeds()


def _path_traversal_check(kfx_path: Path) -> None:
    """Pin that the path-traversal href was rejected by _normalize_href
    and the chapter dropped. KFX should be sentinel-band size since the
    only chapter was rejected.

    Note: the security WARN log emission is verified separately by
    test_normalize_href.py at the unit-test layer; we don't have
    caplog access from inside additional_check, so we use KFX size as
    the proxy signal here."""
    size = kfx_path.stat().st_size
    assert size < 6000, (
        f"path_traversal_href KFX {size} bytes — expected sentinel "
        f"band; the only chapter should have been rejected by "
        f"_normalize_href"
    )


def make_path_traversal_href(out_dir: Path) -> tuple[Path, Outcome]:
    """Manifest href is `../../../etc/passwd`. _normalize_href rejects;
    chapter dropped; sentinel KFX produced."""
    path = (
        EpubBuilder()
        .set_metadata(title="PathTraversal", author="T")
        .add_manifest_item(
            item_id="evil",
            href="../../../etc/passwd",
            media_type="application/xhtml+xml",
            data=b"<html><body>evil content</body></html>",
            in_spine=True,
        )
        .build(out_dir, "path_traversal_href")
    )
    return path, Outcome.succeeds(check=_path_traversal_check)


def _duplicate_basename_check(kfx_path: Path) -> None:
    """Pin that BOTH chapters land as separate $260 sections in the KFX
    output. #82 originally hypothesized "one shadows the other" (the
    KFX size of 5,778 bytes was misread as sentinel-band, suggesting
    both chapters dropped). Investigation under #82 confirmed both
    chapters extract correctly through `extract_chapters_from_oeb` AND
    end up as 2 distinct `$260` sections in the generated KFX. This
    check pins the structural invariant — a regression to 1 or 0
    sections will trip the test. Bare size-band checks would have
    been regression-blind to either drop pattern."""
    from tests._kfx_introspect import by_type, load_fragments

    frags = load_fragments(kfx_path)
    sections = by_type(frags, "$260")
    assert len(sections) == 2, (
        f"duplicate_basename should produce 2 $260 sections (one per "
        f"manifest item), got {len(sections)}. Either one chapter is "
        f"shadowing the other, or both dropped — see #82 for the "
        f"expected behavior."
    )


def make_duplicate_basename(out_dir: Path) -> tuple[Path, Outcome]:
    """Two manifest items with same basename in different directories.

    Empirical (2026-05-04 investigation under #82): both chapters
    extract correctly and land as separate `$260` sections in the KFX
    output. KFX size (~5,778 bytes) was originally misread as sentinel
    band, but the small size simply reflects the tiny chapter content
    (~15 chars each) plus normal Ion overhead. Contract pinned via
    `_duplicate_basename_check` (asserts 2 `$260` sections) so a future
    regression that drops or shadows a chapter flips the test red."""
    path = (
        EpubBuilder()
        .set_metadata(title="DupBasename", author="T")
        .add_manifest_item(
            item_id="ch1",
            href="chapter1/intro.xhtml",
            media_type="application/xhtml+xml",
            data=b"<html><body>chapter 1 intro</body></html>",
            in_spine=True,
        )
        .add_manifest_item(
            item_id="ch2",
            href="chapter2/intro.xhtml",
            media_type="application/xhtml+xml",
            data=b"<html><body>chapter 2 intro</body></html>",
            in_spine=True,
        )
        .build(out_dir, "duplicate_basename")
    )
    return path, Outcome.succeeds(check=_duplicate_basename_check)


# Each entry is a pytest.param so we can attach per-fixture marks. All entries
# carry an explicit `id=` so the parametrize call doesn't need an ids= kwarg
# (mixing tuples with pytest.param breaks the simple `[n for n, _ in CORPUS]`
# unpacking the test file used previously).
CORPUS: list[pytest.param] = [
    pytest.param("single_chapter", make_single_chapter, id="single_chapter"),
    pytest.param("zero_chapters", make_zero_chapters, id="zero_chapters"),
    pytest.param("empty_chapter", make_empty_chapter, id="empty_chapter"),
    pytest.param("whitespace_only", make_whitespace_only, id="whitespace_only"),
    pytest.param(
        "huge_chapter",
        make_huge_chapter,
        marks=pytest.mark.slow,
        id="huge_chapter",
    ),
    pytest.param("many_chapters", make_many_chapters, id="many_chapters"),
    pytest.param("emoji_only", make_emoji_only, id="emoji_only"),
    pytest.param("rtl_combining", make_rtl_combining, id="rtl_combining"),
    pytest.param("non_utf8", make_non_utf8, id="non_utf8"),
    pytest.param("missing_cover", make_missing_cover, id="missing_cover"),
    pytest.param(
        "oversized_cover",
        make_oversized_cover,
        marks=pytest.mark.slow,
        id="oversized_cover",
    ),
    pytest.param("malformed_image", make_malformed_image, id="malformed_image"),
    pytest.param("zip_as_image", make_zip_as_image, id="zip_as_image"),
    pytest.param(
        "path_traversal_href",
        make_path_traversal_href,
        id="path_traversal_href",
    ),
    pytest.param(
        "duplicate_basename", make_duplicate_basename, id="duplicate_basename"
    ),
]
