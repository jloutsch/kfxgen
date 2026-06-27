"""
Position-map invariants required for Kindle nav-pane activation.

Per project memory and #9 test plan, these must hold across all generator
changes (especially #2 per-chapter content split):
- Z3 sentinel position 0 at end of $265
- $265 contains only content positions ($259 1000+ range), never section
  positions ($260 10000+ range)
- Position ranges don't overlap (no duplicate EIDs)
- TOC ($389) entries point to content positions, not section positions
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.native_generator import NativeKFXGenerator
from kfxgen.kfxlib_minimal.ion import IS

from tests._kfx_introspect import by_type as _by_type, val as _val


def _generate_book(chapters):
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    gen.generate_full_book(
        title="Test Book",
        author="Test Author",
        chapters=chapters,
        output_path=path,
    )
    return path


from tests._helpers import MINIMAL_JPEG as _MINIMAL_JPEG  # noqa: E402


def _img_token(href: str, alt: str = "alt") -> str:
    """Build the exact IMG sentinel token that converter.py emits."""
    return f"\x00IMG\x01{href}\x01{alt}\x00"


def _make_chapter_text(
    title: str, image_hrefs: list[str], paragraph_count: int = 3
) -> str:
    """Build chapter body text with N IMG tokens spread across paragraphs.

    Each IMG token gets a unique href so the generator emits a distinct
    $259 image entry (no resource dedupe).
    """
    paragraphs = [f"Paragraph {i} of {title}." for i in range(paragraph_count)]
    # Spread images evenly: image i lands between paragraphs at index
    # round(i * (paragraph_count + 1) / max(image_count, 1))
    parts: list[str] = []
    n_imgs = len(image_hrefs)
    if n_imgs == 0:
        parts = paragraphs
    else:
        # Build slot list: image positions interleaved among paragraphs.
        slots: list[str] = list(paragraphs)
        for i, href in enumerate(image_hrefs):
            insert_at = (i * (len(slots) + 1)) // max(n_imgs, 1)
            slots.insert(insert_at, _img_token(href))
        parts = slots
    body = "\n\n".join(parts)
    return f"{title}\n\n{body}"


def _generate_book_with_images(
    layouts: list[tuple[str, list[str]]],
) -> tuple[str, dict]:
    """Generate a KFX from chapter layouts.

    layouts: list of (chapter_title, list_of_image_hrefs).
             Empty href list = plain-text chapter.

    Returns (kfx_path, images_dict). Caller is responsible for unlinking the path.
    """
    chapters = []
    images: dict[str, bytes] = {}
    for title, hrefs in layouts:
        chapters.append({"title": title, "text": _make_chapter_text(title, hrefs)})
        for href in hrefs:
            images[href] = _MINIMAL_JPEG
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    try:
        gen.generate_full_book(
            title="Img Test",
            author="A",
            chapters=chapters,
            output_path=path,
            images=images,
        )
    except Exception:
        os.unlink(path)
        raise
    return path, images


@pytest.fixture
def sample_chapters():
    return [
        {"title": f"Ch{i}", "text": f"Ch{i}\n\nPara A.\n\nPara B.\n\nPara C."}
        for i in range(5)
    ]


class TestZ3Sentinel:
    """$265 (position-id map) must end with a sentinel entry having position 0."""

    def test_z3_sentinel_preserved(self, sample_chapters, load_kfx_fragments):
        path = _generate_book(sample_chapters)
        try:
            frags = load_kfx_fragments(path)
            map_265 = _by_type(frags, "$265")
            assert map_265, "Missing $265 position-id map fragment"
            v = _val(map_265[0])
            entries = v if isinstance(v, list) else v.get(IS("$181")) or []
            assert entries, "$265 has no entries"
            last = entries[-1]
            sentinel_pos = last.get(IS("$185")) if hasattr(last, "get") else None
            assert int(sentinel_pos) == 0, (
                f"$265 last entry $185 must be 0 (Z3 sentinel for nav pane); "
                f"got {sentinel_pos}"
            )
        finally:
            os.unlink(path)


class TestPositionRangeSeparation:
    """Section positions ($260, 10000+) must NEVER appear in $265 position map.

    Per project memory: 'section positions in $265 create boundary markers
    that cause Kindle TOC navigation to land one page past the target.'
    """

    def test_265_contains_only_content_positions(
        self, sample_chapters, load_kfx_fragments
    ):
        path = _generate_book(sample_chapters)
        try:
            frags = load_kfx_fragments(path)
            map_265 = _by_type(frags, "$265")
            v = _val(map_265[0])
            entries = v if isinstance(v, list) else v.get(IS("$181")) or []
            for e in entries:
                pos = e.get(IS("$185")) if hasattr(e, "get") else None
                pos_int = int(pos) if pos is not None else 0
                # Sentinel 0 is allowed; section range 10000+ is not.
                if pos_int == 0:
                    continue
                assert pos_int < NativeKFXGenerator.SECTION_POS_BASE, (
                    f"$265 contains section position {pos_int} "
                    f"(>= SECTION_POS_BASE={NativeKFXGenerator.SECTION_POS_BASE}); "
                    f"only content positions allowed"
                )
        finally:
            os.unlink(path)

    def test_position_ranges_no_overlap(self, sample_chapters, load_kfx_fragments):
        """Content positions stay <SECTION_POS_BASE; section positions stay >=."""
        path = _generate_book(sample_chapters)
        try:
            frags = load_kfx_fragments(path)
            content_positions = set()
            section_positions = set()
            for f in _by_type(frags, "$259"):
                v = _val(f)
                entries = v.get(IS("$146")) or v.get(IS("$181")) or []
                for entry in entries:
                    if hasattr(entry, "get"):
                        pos = entry.get(IS("$155"))
                        if pos is not None:
                            content_positions.add(int(pos))
            for f in _by_type(frags, "$260"):
                v = _val(f)
                pos = v.get(IS("$155")) if hasattr(v, "get") else None
                if pos is not None:
                    section_positions.add(int(pos))
            # No overlap between ranges
            overlap = content_positions & section_positions
            assert not overlap, (
                f"Position ID overlap between content and section ranges: {overlap}"
            )
            # Content stays low, section stays high
            if content_positions:
                assert max(content_positions) < NativeKFXGenerator.SECTION_POS_BASE
        finally:
            os.unlink(path)


def _content_positions_in_265(frags) -> set[int]:
    """Return the non-sentinel content positions present in $265."""
    map_265 = _by_type(frags, "$265")
    v = _val(map_265[0])
    entries_265 = v if isinstance(v, list) else v.get(IS("$181")) or []
    pos_in_265: set[int] = set()
    for e in entries_265:
        if hasattr(e, "get"):
            p = e.get(IS("$185"))
            if p is not None and int(p) != 0:
                pos_in_265.add(int(p))
    return pos_in_265


def _image_entry_positions(frags) -> set[int]:
    """Return positions of $259 entries that carry a $175 resource ref."""
    image_positions: set[int] = set()
    for f in _by_type(frags, "$259"):
        v = _val(f)
        outers = v.get(IS("$146")) or v.get(IS("$181")) or []
        for outer in outers:
            if not hasattr(outer, "get"):
                continue
            entries = [outer]
            nested = outer.get(IS("$146"))
            if nested:
                entries = nested
            for e in entries:
                if not hasattr(e, "get"):
                    continue
                if e.get(IS("$175")) is None:
                    continue
                p = e.get(IS("$155"))
                if p is not None:
                    image_positions.add(int(p))
    return image_positions


def _assert_image_positions_in_265(frags):
    """Image $259 entries' positions must all appear in $265."""
    pos_in_265 = _content_positions_in_265(frags)
    image_positions = _image_entry_positions(frags)
    assert image_positions, "Test setup error: no image entries emitted"
    missing = image_positions - pos_in_265
    assert not missing, (
        f"$259 image entry positions absent from $265: {missing}. "
        f"This breaks Kindle nav for image-heavy chapters."
    )


def _assert_264_and_550_subset_of_265(frags):
    """$264 and $550 content positions must be a subset of $265."""
    pos_in_265 = _content_positions_in_265(frags)
    section_base = NativeKFXGenerator.SECTION_POS_BASE

    f264 = _by_type(frags, "$264")[0]
    v264 = _val(f264)
    for sec in v264:
        pids = sec.get(IS("$181")) if hasattr(sec, "get") else []
        for pid in pids or []:
            pid_int = int(pid)
            if pid_int >= section_base:
                continue  # section positions are fine in $264
            assert pid_int in pos_in_265, (
                f"$264 references content position {pid_int} that is "
                f"not present in $265 — would break Kindle progress (#4)"
            )

    f550 = _by_type(frags, "$550")[0]
    v550 = _val(f550)
    entries_550 = v550 if isinstance(v550, list) else [v550]
    for item in entries_550:
        if not hasattr(item, "get"):
            continue
        positions = item.get(IS("$182")) or []
        for p in positions:
            pos = p.get(IS("$155")) if hasattr(p, "get") else None
            if pos is None:
                continue
            pos_int = int(pos)
            if pos_int >= section_base or pos_int == 0:
                continue
            assert pos_int in pos_in_265, (
                f"$550 references content position {pos_int} that is "
                f"not present in $265 — would break Kindle progress (#4)"
            )


def _layout_uniform(
    images_per_chapter: int, chapter_count: int
) -> list[tuple[str, list[str]]]:
    """Build chapter layouts where every chapter has the same image count.

    Each image gets a unique href so the generator emits one $259 image
    entry per token (no resource dedupe).
    """
    layouts: list[tuple[str, list[str]]] = []
    for c in range(chapter_count):
        hrefs = [f"images/c{c}_i{i}.jpg" for i in range(images_per_chapter)]
        layouts.append((f"Ch{c}", hrefs))
    return layouts


class TestImagePositionsConsistent:
    """Every content position appearing in $264, $550, or $259 image entries
    MUST also be present in $265. Position IDs that exist in $264/$550 but
    not in $265 break Kindle's progress walk (the original v5.3.x
    regression). Image entry positions absent from $265 break navigation
    for image-heavy chapters (Kindle can't resolve the
    TOC target's surrounding storyline and falls back to the start of
    the book)."""

    @pytest.mark.parametrize(
        "images_per_chapter,chapter_count",
        [(1, 1), (3, 1), (1, 5), (5, 5), (10, 3)],
    )
    def test_image_entry_positions_present_in_265(
        self, images_per_chapter, chapter_count, load_kfx_fragments
    ):
        """Image $259 entries (with $175 resource ref) must have their $155
        position recorded in $265 — otherwise Kindle nav drops the
        image-heavy chapter (regression observed during #4 work).
        """
        layouts = _layout_uniform(images_per_chapter, chapter_count)
        path, _ = _generate_book_with_images(layouts)
        try:
            frags = load_kfx_fragments(path)
            _assert_image_positions_in_265(frags)
        finally:
            os.unlink(path)

    @pytest.mark.parametrize(
        "images_per_chapter,chapter_count",
        [(1, 1), (3, 1), (1, 5), (5, 5), (10, 3)],
    )
    def test_264_and_550_positions_consistent_with_265(
        self, images_per_chapter, chapter_count, load_kfx_fragments
    ):
        """Every content position in $264 (positions per section) and $550
        (page-break list) must also appear in $265. Positions in
        $264/$550 with no $265 entry break Kindle's progress walk.
        """
        layouts = _layout_uniform(images_per_chapter, chapter_count)
        path, _ = _generate_book_with_images(layouts)
        try:
            frags = load_kfx_fragments(path)
            _assert_264_and_550_subset_of_265(frags)
        finally:
            os.unlink(path)


class TestImageBoundaryCases:
    """Boundary conditions for image placement that the parametrized sweep
    cannot express directly: image as the first body element, image as
    the last body element, and consecutive image-only chapters."""

    def _generate(
        self, title: str, chapters: list[dict], images: dict[str, bytes]
    ) -> str:
        """Generate a KFX inline (used by tests that need hand-crafted text shapes
        that the uniform `_generate_book_with_images` helper can't express)."""
        gen = NativeKFXGenerator()
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        try:
            gen.generate_full_book(
                title=title,
                author="A",
                chapters=chapters,
                output_path=path,
                images=images,
            )
        except Exception:
            os.unlink(path)
            raise
        return path

    def test_image_at_chapter_start(self, load_kfx_fragments):
        """Image is the very first paragraph after the heading."""
        text = "Ch0\n\n" + _img_token("images/start.jpg") + "\n\nFollowing paragraph."
        chapters = [{"title": "Ch0", "text": text}]
        path = self._generate(
            "Img Start", chapters, {"images/start.jpg": _MINIMAL_JPEG}
        )
        try:
            frags = load_kfx_fragments(path)
            _assert_image_positions_in_265(frags)
            _assert_264_and_550_subset_of_265(frags)
        finally:
            os.unlink(path)

    def test_image_at_chapter_end(self, load_kfx_fragments):
        """Image is the last paragraph in the chapter body."""
        text = "Ch0\n\nLeading paragraph.\n\n" + _img_token("images/end.jpg")
        chapters = [{"title": "Ch0", "text": text}]
        path = self._generate("Img End", chapters, {"images/end.jpg": _MINIMAL_JPEG})
        try:
            frags = load_kfx_fragments(path)
            _assert_image_positions_in_265(frags)
            _assert_264_and_550_subset_of_265(frags)
        finally:
            os.unlink(path)

    def test_consecutive_image_only_chapters(self, load_kfx_fragments):
        """Three back-to-back chapters whose body is only an image.
        This was the original v5.3.5 regression shape."""
        chapters = []
        images = {}
        for i in range(3):
            href = f"images/only_{i}.jpg"
            chapters.append(
                {"title": f"Ch{i}", "text": f"Ch{i}\n\n" + _img_token(href)}
            )
            images[href] = _MINIMAL_JPEG
        path = self._generate("Img Only", chapters, images)
        try:
            frags = load_kfx_fragments(path)
            _assert_image_positions_in_265(frags)
            _assert_264_and_550_subset_of_265(frags)
        finally:
            os.unlink(path)


class TestTOCPointsToContent:
    """$389 TOC entries must reference content positions ($259), not section ($260)."""

    def test_toc_positions_point_to_content_not_section(
        self, sample_chapters, load_kfx_fragments
    ):
        path = _generate_book(sample_chapters)
        try:
            frags = load_kfx_fragments(path)
            toc = _by_type(frags, "$389")
            assert toc, "Missing $389 TOC fragment"
            # Walk the structure looking for $246.$155 references
            v = _val(toc[0])

            def positions_in(o, found):
                # IonAnnotation wrapper — recurse into .value
                if hasattr(o, "annotations") and hasattr(o, "value"):
                    positions_in(o.value, found)
                    return
                if hasattr(o, "items"):
                    for k, val in o.items():
                        if str(k) == "$246":
                            inner = val.value if hasattr(val, "annotations") else val
                            if hasattr(inner, "items"):
                                for k2, v2 in inner.items():
                                    if str(k2) == "$155":
                                        found.append(int(v2))
                        else:
                            positions_in(val, found)
                elif isinstance(o, list):
                    for item in o:
                        positions_in(item, found)

            toc_positions = []
            positions_in(v, toc_positions)
            assert toc_positions, "No TOC position references found"
            for pos in toc_positions:
                assert pos < NativeKFXGenerator.SECTION_POS_BASE, (
                    f"TOC entry references section position {pos} "
                    f"(>= SECTION_POS_BASE); must reference content position"
                )
        finally:
            os.unlink(path)
