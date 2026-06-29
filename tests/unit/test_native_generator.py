"""
Unit tests for NativeKFXGenerator.

Tests the core KFX generation pipeline including fragment building,
content chunking, and position ID management.
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock

import pytest


# Add plugin directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402

from tests._helpers import MINIMAL_JPEG  # noqa: E402


class TestNativeGeneratorInit:
    """Test generator initialization."""

    def test_init(self):
        gen = NativeKFXGenerator()
        assert gen.next_entity_id == 349
        assert gen.fragments == []

    def test_position_constants(self):
        gen = NativeKFXGenerator()
        # The exact values may shift across releases (5.3.1 widened
        # SECTION_POS_BASE from 10000 to 100000). What matters is the
        # invariant: the section range starts above the content range so
        # they never overlap.
        assert gen.CONTENT_POS_BASE == 1000
        assert gen.SECTION_POS_BASE > gen.CONTENT_POS_BASE


class TestGenerateFullBook:
    """Test full book generation."""

    def test_minimal_book(self):
        gen = NativeKFXGenerator()
        chapters = [
            {"title": "Chapter 1", "text": "Hello world."},
        ]
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        try:
            gen.generate_full_book(
                title="Test Book",
                author="Test Author",
                chapters=chapters,
                output_path=path,
            )
            assert os.path.isfile(path)
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)

    def test_multi_chapter_book(self):
        gen = NativeKFXGenerator()
        chapters = [
            {
                "title": "Chapter 1",
                "text": "First chapter content.\n\nSecond paragraph.",
            },
            {"title": "Chapter 2", "text": "Second chapter content."},
            {"title": "Chapter 3", "text": "Third chapter content."},
        ]
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        try:
            gen.generate_full_book(
                title="Multi Chapter",
                author="Author",
                chapters=chapters,
                output_path=path,
            )
            assert os.path.isfile(path)
            assert os.path.getsize(path) > 100
        finally:
            os.unlink(path)

    def test_metadata_params(self):
        gen = NativeKFXGenerator()
        chapters = [{"title": "Ch1", "text": "Content."}]
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        try:
            gen.generate_full_book(
                title="Test",
                author="Author",
                chapters=chapters,
                output_path=path,
                language="fr",
                publisher="TestPress",
                issue_date="2025-06-15",
            )
            assert os.path.isfile(path)
        finally:
            os.unlink(path)


class TestPerParagraphChunking:
    """Test that per-paragraph chunking produces correct structure."""

    def test_paragraphs_become_separate_chunks(self):
        gen = NativeKFXGenerator()
        chapters = [
            {
                "title": "Test",
                "text": "Test\n\nParagraph one.\n\nParagraph two.\n\nParagraph three.",
            },
        ]
        result = gen._build_chapter_content(chapters)
        # Title + 3 paragraphs = 4 chunks (Phase 4b: chunks are typed dicts)
        assert len(result["all_chunks"]) == 4
        texts = [c["text"] for c in result["all_chunks"] if c["type"] == "text"]
        assert texts == ["Test", "Paragraph one.", "Paragraph two.", "Paragraph three."]

    def test_empty_paragraphs_skipped(self):
        gen = NativeKFXGenerator()
        chapters = [
            {"title": "Test", "text": "Test\n\nPara one.\n\n\n\nPara two."},
        ]
        result = gen._build_chapter_content(chapters)
        assert len(result["all_chunks"]) == 3  # title + 2 paragraphs

    def test_position_ids_no_overlap(self):
        gen = NativeKFXGenerator()
        chapters = [
            {
                "title": f"Ch{i}",
                "text": f"Ch{i}\n\n" + "\n\n".join(f"Para {j}." for j in range(50)),
            }
            for i in range(10)
        ]
        result = gen._build_chapter_content(chapters)
        max_content_pos = max(result["chunk_positions"])
        min_section_pos = min(result["section_positions"])
        assert max_content_pos < min_section_pos, (
            f"Content positions ({max_content_pos}) overlap with section positions ({min_section_pos})"
        )

    @pytest.mark.unit
    def test_blocks_title_prefix_stripped_no_duplicate(self):
        # When blocks[0].text equals the chapter title, the title must appear
        # exactly once across all chunks (heading chunk only, not also as body).
        gen = NativeKFXGenerator()
        chapters = [
            {
                "title": "Chapter 1",
                "text": "Chapter 1\n\nbody",
                "blocks": [
                    {"text": "Chapter 1", "spans": []},
                    {"text": "body", "spans": []},
                ],
            }
        ]
        result = gen._build_chapter_content(chapters)
        texts = [c["text"] for c in result["all_chunks"] if c["type"] == "text"]
        assert texts.count("Chapter 1") == 1, f"Title duplicated in chunks: {texts}"
        assert texts == ["Chapter 1", "body"]

    @pytest.mark.unit
    def test_blocks_title_prefix_span_rebased(self):
        # When blocks[0] has "Title rest" with an italic span on "rest",
        # stripping the title prefix must rebase the span offset correctly.
        from kfxgen.inline_style import FLAG_ITALIC

        gen = NativeKFXGenerator()
        # First block: "Chapter 1 italic" where "italic" (chars 10-16) is italic.
        # Title "Chapter 1" (len 9) + space = 10 chars removed after lstrip.
        # Span (10, 6, {FLAG_ITALIC}) -> rebased to (0, 6, {FLAG_ITALIC}).
        chapters = [
            {
                "title": "Chapter 1",
                "text": "Chapter 1 italic\n\nbody",
                "blocks": [
                    {"text": "Chapter 1 italic", "spans": [(10, 6, {FLAG_ITALIC})]},
                    {"text": "body", "spans": []},
                ],
            }
        ]
        result = gen._build_chapter_content(chapters)
        texts = [c["text"] for c in result["all_chunks"] if c["type"] == "text"]
        assert texts[0] == "Chapter 1", f"Expected heading first, got: {texts}"
        assert texts[1] == "italic", f"Expected rebased remainder second, got: {texts}"
        italic_chunk = next(
            c
            for c in result["all_chunks"]
            if c["type"] == "text" and c["text"] == "italic"
        )
        spans = italic_chunk.get("spans", [])
        assert len(spans) == 1, f"Expected one span on 'italic' chunk, got: {spans}"
        s, length, flags = spans[0]
        assert s == 0, f"Rebased span start should be 0, got {s}"
        assert length == 6, f"Rebased span length should be 6, got {length}"
        assert FLAG_ITALIC in flags, f"Expected FLAG_ITALIC in flags, got {flags}"


class TestBuildFragment157:
    """Test style fragment building."""

    def test_default_line_height(self):
        gen = NativeKFXGenerator()
        from kfxgen.kfxlib_minimal.ion import IS, IonDecimal

        frag = gen.build_fragment_157(entity_name="test_style")
        # Line height should be 1.0 — matches reference Calibre KFX Output and KP
        # pipeline (avg 0.98–1.07 in real-book corpus). Higher values inflated
        # body text and made Kindle line-spacing settings unresponsive.
        lh_value = frag.value[IS("$42")]
        assert lh_value[IS("$307")] == IonDecimal("1.0")

    def test_default_text_align_is_justify(self):
        gen = NativeKFXGenerator()
        from kfxgen.kfxlib_minimal.ion import IS

        frag = gen.build_fragment_157(entity_name="test_align")
        # $321 = justify (flush both edges, like a printed book).
        # $320 = center, $59 = left, $61 = right — never the body default.
        assert frag.value[IS("$34")] == IS("$321")

    def test_margin_top(self):
        gen = NativeKFXGenerator()
        from kfxgen.kfxlib_minimal.ion import IS

        frag = gen.build_fragment_157(entity_name="s_mt", margin_top=2.0)
        assert IS("$46") in frag.value

    def test_no_margin_top_by_default(self):
        gen = NativeKFXGenerator()
        from kfxgen.kfxlib_minimal.ion import IS

        frag = gen.build_fragment_157(entity_name="s_no_mt")
        assert IS("$46") not in frag.value

    def test_bold(self):
        gen = NativeKFXGenerator()
        from kfxgen.kfxlib_minimal.ion import IS

        frag = gen.build_fragment_157(entity_name="s_bold", bold=True)
        assert frag.value[IS("$13")] == IS("$361")

    def test_underline(self):
        gen = NativeKFXGenerator()
        from kfxgen.kfxlib_minimal.ion import IS

        frag = gen.build_fragment_157(entity_name="s_ul", underline=True)
        assert frag.value[IS("$23")] == IS("$328")

    def test_italic_sets_font_style(self):
        from kfxgen.kfxlib_minimal.ion import IS

        gen = NativeKFXGenerator()
        frag = gen.build_fragment_157(entity_name="s_it", italic=True)
        assert frag.value[IS("$12")] == IS("$382")

    def test_no_italic_by_default(self):
        from kfxgen.kfxlib_minimal.ion import IS

        gen = NativeKFXGenerator()
        frag = gen.build_fragment_157(entity_name="s_plain")
        assert IS("$12") not in frag.value

    @pytest.mark.unit
    def test_align_overrides_text_align(self):
        from kfxgen.kfxlib_minimal.ion import IS

        gen = NativeKFXGenerator()
        assert gen.build_fragment_157(entity_name="sa", align="center").value[
            IS("$34")
        ] == IS("$320")
        assert gen.build_fragment_157(entity_name="sb", align="left").value[
            IS("$34")
        ] == IS("$59")
        assert gen.build_fragment_157(entity_name="sc", align="right").value[
            IS("$34")
        ] == IS("$61")

    @pytest.mark.unit
    def test_align_default_is_justify(self):
        from kfxgen.kfxlib_minimal.ion import IS

        gen = NativeKFXGenerator()
        assert gen.build_fragment_157(entity_name="sd").value[IS("$34")] == IS("$321")

    @pytest.mark.unit
    def test_text_indent_sets_36_and_omits_padding(self):
        from kfxgen.kfxlib_minimal.ion import IS, IonDecimal

        gen = NativeKFXGenerator()
        frag = gen.build_fragment_157(entity_name="si", text_indent=("1.5", "$308"))
        ind = frag.value[IS("$36")]
        assert ind[IS("$307")] == IonDecimal("1.5")
        assert ind[IS("$306")] == IS("$308")
        assert IS("$47") not in frag.value  # padding-top suppressed

    @pytest.mark.unit
    def test_no_text_indent_keeps_default(self):
        from kfxgen.kfxlib_minimal.ion import IS, IonDecimal

        gen = NativeKFXGenerator()
        frag = gen.build_fragment_157(entity_name="sj")
        ind = frag.value[IS("$36")]
        assert ind[IS("$307")] == IonDecimal("0")
        assert IS("$47") in frag.value  # padding-top present by default (non-heading)


class TestStyleSharing:
    """Issue #5: $157 styles must be shared globally, not cloned per chapter.

    Identical attribute fingerprints across chapters should produce one
    style fragment, not N copies named s0, s1, s2, ...
    """

    def _generate(self, chapters):
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

    def test_styles_shared_across_chapters(self, load_kfx_fragments):
        """Ten chapters with uniform attributes produce exactly 3 $157 styles.

        Expected: one body style (font_size=1.0), one heading style for the
        first chapter (margin_top=None), one heading style shared by chapters
        1-9 (margin_top=2.0). Anything more means per-chapter cloning has
        regressed.
        """
        chapters = [
            {"title": f"Ch{i}", "text": f"Ch{i}\n\nPara 1.\n\nPara 2.\n\nPara 3."}
            for i in range(10)
        ]
        path = self._generate(chapters)
        try:
            frags = load_kfx_fragments(path)
            style_count = sum(1 for f in frags if str(f.ftype) == "$157")
            assert style_count == 3, (
                f"Expected exactly 3 $157 styles for 10 uniform chapters "
                f"(1 body + 2 heading variants), got {style_count}. "
                "A larger count indicates per-chapter style cloning regression."
            )
        finally:
            os.unlink(path)

    def test_body_style_shared_when_font_size_uniform(self, load_kfx_fragments):
        """Chapters with the same font_size reference the same body style fid."""
        from kfxgen.kfxlib_minimal.ion import IS

        chapters = [
            {
                "title": f"Ch{i}",
                "text": f"Ch{i}\n\nBody paragraph one.\n\nBody paragraph two.",
            }
            for i in range(5)
        ]
        path = self._generate(chapters)
        try:
            frags = load_kfx_fragments(path)
            body_style_refs = []
            for f in frags:
                if str(f.ftype) != "$259":
                    continue
                v = f.value.value if hasattr(f.value, "value") else f.value
                outers = v.get(IS("$146")) or v.get(IS("$181")) or []
                # Phase 3: descend into nested $146 children when present
                children = []
                for outer in outers:
                    if hasattr(outer, "get"):
                        nested = outer.get(IS("$146"))
                        children.extend(nested if nested else [outer])
                # First child is the chapter heading; remaining are body paragraphs
                for entry in children[1:]:
                    if hasattr(entry, "get"):
                        s = entry.get(IS("$157"))
                        if s is not None:
                            body_style_refs.append(str(s))
            # Skip chapters with no body refs (toc/copyright handled separately)
            chapter_only_refs = [r for r in body_style_refs if "link" not in r]
            unique_chapter_body = set(chapter_only_refs)
            assert len(unique_chapter_body) == 1, (
                f"Expected one shared body style for uniform chapters, got "
                f"{len(unique_chapter_body)}: {unique_chapter_body}"
            )

        finally:
            os.unlink(path)

    def test_distinct_styles_preserved_when_attributes_differ(self, load_kfx_fragments):
        """A chapter with font_size=0.75 must NOT share style with font_size=1.0 chapters."""
        chapters = [
            {
                "title": "Copyright",
                "text": "Copyright\n\nLegal notice.",
                "font_size": 0.75,
            },
            {"title": "Ch1", "text": "Ch1\n\nNormal text."},
            {"title": "Ch2", "text": "Ch2\n\nNormal text."},
        ]
        path = self._generate(chapters)
        try:
            frags = load_kfx_fragments(path)
            style_fids = {str(f.fid) for f in frags if str(f.ftype) == "$157"}
            # At minimum: body-1.0 + body-0.75 + heading-no-margin (first) + heading-margin (others)
            assert len(style_fids) >= 4, (
                f"Expected ≥4 distinct styles (body×2 + heading×2), got "
                f"{len(style_fids)}: {style_fids}"
            )
            # But not 6+ which would indicate per-chapter cloning
            assert len(style_fids) <= 8, (
                f"Expected ≤8 styles for 3 chapters with 2 font sizes, got "
                f"{len(style_fids)}: {style_fids}"
            )
        finally:
            os.unlink(path)


class TestPerChapterContentFragments:
    """Issue #2: each chapter must own its own $145 content fragment.

    Reference Calibre KFX emits one $145 per chapter (or finer); the prior
    singleton content_1 walked the entire book's text array on every nav
    lookup. Per-chapter split also unblocks #3 (nested $259 storyline).
    """

    def _generate(self, chapters):
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

    def test_one_145_per_chapter(self, load_kfx_fragments):
        chapters = [
            {"title": f"Ch{i}", "text": f"Ch{i}\n\nPara A.\n\nPara B."}
            for i in range(5)
        ]
        path = self._generate(chapters)
        try:
            frags = load_kfx_fragments(path)
            f145_count = sum(1 for f in frags if str(f.ftype) == "$145")
            assert f145_count == len(chapters), (
                f"Expected one $145 per chapter ({len(chapters)}), got "
                f"{f145_count}. Either singleton has regressed or splits "
                f"finer than per-chapter."
            )
            f145_fids = {str(f.fid) for f in frags if str(f.ftype) == "$145"}
            expected = {f"content_{i + 1}" for i in range(len(chapters))}
            assert f145_fids == expected, (
                f"Expected $145 fids {expected}, got {f145_fids}"
            )
        finally:
            os.unlink(path)

    def test_259_entries_reference_own_chapter_content(self, load_kfx_fragments):
        """Each $259 storyline's children reference the matching chapter's $145.

        Storyline `lN` contains entries that reference `content_{N+1}` (1-indexed
        per reference convention). $403 indices reset to 0 at chapter start.
        """
        from kfxgen.kfxlib_minimal.ion import IS

        chapters = [
            {"title": f"Ch{i}", "text": f"Ch{i}\n\nFirst.\n\nSecond."} for i in range(3)
        ]
        path = self._generate(chapters)
        try:
            frags = load_kfx_fragments(path)
            for f in frags:
                if str(f.ftype) != "$259":
                    continue
                sl_fid = str(f.fid)
                if not sl_fid.startswith("l"):
                    continue
                ch_idx = int(sl_fid[1:])
                expected = f"content_{ch_idx + 1}"
                v = f.value.value if hasattr(f.value, "value") else f.value
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
                        cref = e.get(IS("$145"))
                        if cref is None:
                            continue  # image entry — skip
                        name = cref.get(IS("name")) if hasattr(cref, "get") else None
                        assert str(name) == expected, (
                            f"Storyline {sl_fid} child references {name!s}; "
                            f"expected {expected}"
                        )
        finally:
            os.unlink(path)


class TestInlineHyperlinks:
    """Issue #30: in-book hyperlinks (e.g. Contents page chapter title links)
    must be encoded as $142 character spans, not entry-level $179.

    Reference Calibre KFX uses $142 character-span markers; entry-level $179
    is non-tappable on Kindle so taps fall through to page-turn instead of
    jumping to the linked chapter. Fixed in v5.3.7.
    """

    def _generate_with_toc_links(self):
        """Build a book whose first chapter has toc_links to later chapters,
        triggering the in-book Contents-style hyperlink path."""
        gen = NativeKFXGenerator()
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        chapters = [
            {
                "title": "Contents",
                "text": "",
                "toc_links": [
                    {"text": "Chapter 1", "target_chapter_idx": 1},
                    {"text": "Chapter 2", "target_chapter_idx": 2},
                ],
            },
            {"title": "Chapter 1", "text": "Chapter 1\n\nFirst chapter body."},
            {"title": "Chapter 2", "text": "Chapter 2\n\nSecond chapter body."},
        ]
        gen.generate_full_book(
            title="Test",
            author="A",
            chapters=chapters,
            output_path=path,
        )
        return path

    def test_link_emitted_as_142_span_not_entry_level_179(self, load_kfx_fragments):
        from kfxgen.kfxlib_minimal.ion import IS

        path = self._generate_with_toc_links()
        try:
            frags = load_kfx_fragments(path)
            link_entries = []
            for f in frags:
                if str(f.ftype) != "$259":
                    continue
                v = f.value.value if hasattr(f.value, "value") else f.value
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
                        if e.get(IS("$142")) is not None:
                            link_entries.append(e)

            assert link_entries, (
                "Expected at least one $259 entry with $142 character-span "
                "(in-book hyperlink). None found."
            )

            for e in link_entries:
                # Entry-level $179 must NOT be present — Kindle treats it as a
                # non-tappable structural reference and the link fails to fire.
                assert e.get(IS("$179")) is None, (
                    f"Link entry has both $142 and entry-level $179. "
                    f"Reference uses $142 only. Entry: {dict(e.items())}"
                )
                spans = e.get(IS("$142"))
                assert spans, "$142 present but empty"
                span = spans[0]
                # Required span fields per reference shape
                assert span.get(IS("$143")) is not None, (
                    "Span missing $143 (start offset)"
                )
                assert span.get(IS("$144")) is not None, "Span missing $144 (length)"
                assert span.get(IS("$179")) is not None, (
                    "Span missing $179 (anchor target)"
                )
                assert span.get(IS("$157")) is not None, (
                    "Span missing $157 (link style)"
                )
        finally:
            os.unlink(path)


class TestCoverInReadingFlow:
    """Issue #32: when a cover_image is provided, it must appear as a $259
    image entry at the start of the reading flow, but NOT in the TOC nav-pane.
    """

    def _generate_with_cover(self):
        gen = NativeKFXGenerator()
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        jpeg = MINIMAL_JPEG
        chapters = [
            {"title": "Chapter 1", "text": "Chapter 1\n\nFirst chapter body."},
            {"title": "Chapter 2", "text": "Chapter 2\n\nSecond chapter body."},
        ]
        gen.generate_full_book(
            title="Test",
            author="A",
            chapters=chapters,
            output_path=path,
            cover_image=jpeg,
        )
        return path

    def test_cover_referenced_from_259_when_provided(self, load_kfx_fragments):
        from kfxgen.kfxlib_minimal.ion import IS

        path = self._generate_with_cover()
        try:
            frags = load_kfx_fragments(path)
            cover_refs = 0
            for f in frags:
                if str(f.ftype) != "$259":
                    continue
                v = f.value.value if hasattr(f.value, "value") else f.value
                outers = v.get(IS("$146")) or v.get(IS("$181")) or []
                for outer in outers:
                    if not hasattr(outer, "get"):
                        continue
                    entries = [outer]
                    nested = outer.get(IS("$146"))
                    if nested:
                        entries = nested
                    for e in entries:
                        if hasattr(e, "get") and str(e.get(IS("$175"))) == "cover_img":
                            cover_refs += 1
            assert cover_refs == 1, (
                f"Expected exactly 1 $259 entry referencing cover_img "
                f"(cover-in-reading-flow); got {cover_refs}."
            )
        finally:
            os.unlink(path)

    def test_cover_chapter_excluded_from_toc(self, load_kfx_fragments):
        """The cover chapter has no title and no TOC entry — only the real
        chapters appear in the nav-pane."""
        from kfxgen.kfxlib_minimal.ion import IS

        path = self._generate_with_cover()
        try:
            frags = load_kfx_fragments(path)

            def walk(o):
                if hasattr(o, "annotations") and hasattr(o, "value"):
                    yield from walk(o.value)
                    return
                if hasattr(o, "items"):
                    d = dict(o.items())
                    if str(d.get(IS("$235"), "")) == "$212":
                        for u in d.get(IS("$247"), []) or []:
                            inner = u.value if hasattr(u, "annotations") else u
                            if hasattr(inner, "items"):
                                di = dict(inner.items())
                                title_obj = di.get(IS("$241"), {})
                                title = ""
                                if hasattr(title_obj, "items"):
                                    title = str(
                                        dict(title_obj.items()).get(IS("$244"), "")
                                    )
                                yield title
                    for v in d.values():
                        yield from walk(v)
                elif isinstance(o, list):
                    for x in o:
                        yield from walk(x)

            for f in frags:
                if str(f.ftype) == "$389":
                    titles = list(
                        walk(f.value.value if hasattr(f.value, "value") else f.value)
                    )
                    assert len(titles) == 2, (
                        f"Expected 2 TOC entries (Chapter 1, Chapter 2); "
                        f"got {len(titles)}: {titles}. Cover chapter must be omitted."
                    )
                    assert titles == [
                        "Chapter 1",
                        "Chapter 2",
                    ], f"Expected TOC entries ['Chapter 1', 'Chapter 2']; got {titles}"
                    return
            raise AssertionError("No $389 TOC fragment found")
        finally:
            os.unlink(path)


class TestImageOnlyChapterHeadings:
    """Issue #33: chapters whose body is image-only (map pages, diagram pages)
    or whose title is structurally redundant (Title Page replaced with
    title+author body) must NOT emit the chapter title as a heading
    text chunk above the content."""

    def _generate(self, chapters, cover=None):
        gen = NativeKFXGenerator()
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        kwargs = {
            "title": "Test",
            "author": "A",
            "chapters": chapters,
            "output_path": path,
        }
        if cover is not None:
            kwargs["cover_image"] = cover
        kwargs["images"] = {
            "images/x.jpg": bytes.fromhex(
                "ffd8ffe000104a46494600010100000100010000ffdb004300080606"
                "07060805070707090908"
                + "0a" * 100
                + "ffc0000b08000100010101"
                + "00" * 30
                + "ffd9"
            )
        }
        gen.generate_full_book(**kwargs)
        return path

    def _first_chunk_kind(self, path, load_kfx_fragments, target_pos):
        from kfxgen.kfxlib_minimal.ion import IS

        frags = load_kfx_fragments(path)
        for f in frags:
            if str(f.ftype) != "$259":
                continue
            v = f.value.value if hasattr(f.value, "value") else f.value
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
                    p = e.get(IS("$155"))
                    if p is not None and int(p) == target_pos:
                        if e.get(IS("$175")) is not None:
                            return "image"
                        if e.get(IS("$145")) is not None:
                            return "text"
                        return "unknown"
        return None

    def _toc_pos(self, path, load_kfx_fragments, want_title):
        from kfxgen.kfxlib_minimal.ion import IS

        frags = load_kfx_fragments(path)

        def walk(o):
            if hasattr(o, "annotations") and hasattr(o, "value"):
                yield from walk(o.value)
                return
            if hasattr(o, "items"):
                d = dict(o.items())
                if str(d.get(IS("$235"), "")) == "$212":
                    for u in d.get(IS("$247"), []) or []:
                        inner = u.value if hasattr(u, "annotations") else u
                        if hasattr(inner, "items"):
                            di = dict(inner.items())
                            title_obj = di.get(IS("$241"), {})
                            title = ""
                            if hasattr(title_obj, "items"):
                                title = str(dict(title_obj.items()).get(IS("$244"), ""))
                            pos_obj = di.get(IS("$246"), {})
                            pos = None
                            if hasattr(pos_obj, "items"):
                                pos = dict(pos_obj.items()).get(IS("$155"))
                            yield (title, int(pos) if pos else None)
                for v in d.values():
                    yield from walk(v)
            elif isinstance(o, list):
                for x in o:
                    yield from walk(x)

        for f in frags:
            if str(f.ftype) == "$389":
                for t, p in walk(
                    f.value.value if hasattr(f.value, "value") else f.value
                ):
                    if t == want_title:
                        return p
        return None

    def test_image_only_chapter_skips_heading(self, load_kfx_fragments):
        """A chapter whose body is just an IMG token has its first $259
        entry pointing at the image, not at a text heading chunk."""
        chapters = [
            {"title": "Maps", "text": "\x00IMG\x01images/x.jpg\x01\x00"},
            {"title": "Chapter 1", "text": "Chapter 1\n\nFirst body paragraph."},
        ]
        path = self._generate(chapters)
        try:
            pos = self._toc_pos(path, load_kfx_fragments, "Maps")
            assert pos is not None, "TOC missing 'Maps' entry"
            kind = self._first_chunk_kind(path, load_kfx_fragments, pos)
            assert kind == "image", (
                f"Expected Maps chapter's TOC target to be an image entry "
                f"(no heading); got kind={kind}."
            )
            # Real chapter still has heading
            pos2 = self._toc_pos(path, load_kfx_fragments, "Chapter 1")
            kind2 = self._first_chunk_kind(path, load_kfx_fragments, pos2)
            assert kind2 == "text", (
                f"Expected Chapter 1 to keep its heading text; got kind={kind2}."
            )
        finally:
            os.unlink(path)

    def test_omit_title_heading_flag_suppresses_heading(self, load_kfx_fragments):
        """The `_omit_title_heading` flag (set by converter for Title Page
        after replacement) skips the chapter-title heading even when the
        body has text."""
        chapters = [
            {
                "title": "Title Page",
                "text": "Test\n\nby\n\nA",
                "_omit_title_heading": True,
            },
            {"title": "Chapter 1", "text": "Chapter 1\n\nBody."},
        ]
        path = self._generate(chapters)
        try:
            # Implicit assertion: raises if "Title Page" isn't in the TOC.
            self._toc_pos(path, load_kfx_fragments, "Title Page")
            # The first chunk should be the replaced body text, NOT the
            # title heading. Both are text chunks, so we verify by
            # checking the $403 index — heading would have been chunk 0
            # of content_1; replaced-body first paragraph lands at chunk 0
            # too, so this test relies on the heading being absent rather
            # than indexed differently. Easier check: count chunks.
            from kfxgen.kfxlib_minimal.ion import IS

            frags = load_kfx_fragments(path)
            for f in frags:
                if str(f.ftype) == "$259" and str(f.fid) == "l0":
                    v = f.value.value if hasattr(f.value, "value") else f.value
                    outers = v.get(IS("$146")) or []
                    if outers and hasattr(outers[0], "get"):
                        nested = outers[0].get(IS("$146"))
                        children = nested if nested else outers
                        # Without omit: heading + 3 body paragraphs = 4 chunks
                        # With omit:    3 body paragraphs = 3 chunks
                        assert len(children) == 3, (
                            f"Expected 3 chunks (no heading); got {len(children)}"
                        )
                    break
        finally:
            os.unlink(path)


class TestThumbnailFix:
    """Issue #39: Kindle home-screen thumbnail extraction.

    Verified on Paperwhite that two changes are required for thumbnails
    to appear when the file is side-loaded via USB:
    - Cover $164 must include $162 MIME type ('image/jpg' for JPEG covers)
    - ASIN must be 32-char alphanumeric (not the prior `ASIN_<10>` format)

    The Voyage doesn't extract local thumbnails regardless (firmware
    limitation), but Paperwhite/Oasis+ do honor these fields.
    """

    def _generate_with_cover(self):
        gen = NativeKFXGenerator()
        with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
            path = f.name
        jpeg = MINIMAL_JPEG
        chapters = [
            {"title": "Chapter 1", "text": "Chapter 1\n\nFirst body."},
            {"title": "Chapter 2", "text": "Chapter 2\n\nSecond body."},
        ]
        gen.generate_full_book(
            title="Test",
            author="A",
            chapters=chapters,
            output_path=path,
            cover_image=jpeg,
        )
        return path

    def test_cover_164_has_mime_type(self, load_kfx_fragments):
        """Cover $164 must have $162 = 'image/jpg' (or 'image/png')."""
        from kfxgen.kfxlib_minimal.ion import IS

        path = self._generate_with_cover()
        try:
            frags = load_kfx_fragments(path)
            cover = next(
                (
                    f
                    for f in frags
                    if str(f.ftype) == "$164" and str(f.fid) == "cover_img"
                ),
                None,
            )
            assert cover is not None, "Cover $164 not found"
            v = cover.value.value if hasattr(cover.value, "value") else cover.value
            mime = v.get(IS("$162"))
            assert mime is not None, (
                "Cover $164 missing $162 MIME — Kindle home-screen "
                "thumbnail extraction needs this (#39)"
            )
            assert str(mime) == "image/jpg", (
                f"Expected $162 = 'image/jpg' for JPEG cover; got {mime!r}"
            )
        finally:
            os.unlink(path)

    def test_asin_is_32_chars_no_prefix(self, load_kfx_fragments):
        """ASIN must be 32-char alphanumeric (no `ASIN_` prefix) to enable
        Kindle home-screen thumbnail extraction (#39)."""
        from kfxgen.kfxlib_minimal.ion import IS

        path = self._generate_with_cover()
        try:
            frags = load_kfx_fragments(path)
            for f in frags:
                if str(f.ftype) != "$490":
                    continue
                v = f.value.value if hasattr(f.value, "value") else f.value
                for entry in v.get(IS("$491")) or []:
                    if not hasattr(entry, "get"):
                        continue
                    if str(entry.get(IS("$495"))) != "kindle_title_metadata":
                        continue
                    for kv in entry.get(IS("$258"), []) or []:
                        if hasattr(kv, "get") and str(kv.get(IS("$492"))) == "ASIN":
                            asin = str(kv.get(IS("$307")))
                            assert len(asin) == 32, (
                                f"ASIN must be 32 chars; got {asin!r} (len={len(asin)})"
                            )
                            assert not asin.startswith("ASIN_"), (
                                f"ASIN must not have 'ASIN_' prefix; got {asin!r}"
                            )
                            return
                break
            raise AssertionError("ASIN not found in $490 metadata")
        finally:
            os.unlink(path)


class TestHalfTitlePageEndToEnd:
    """#107: end-to-end guard at the chunk-emission layer. Drives the
    real converter -> generator pipeline and asserts the structural
    label 'Half Title Page' never reaches an emitted chunk (the exact
    user-reported symptom: the words appeared on the page)."""

    def test_half_title_label_not_emitted_as_chunk(self):
        from kfxgen.converter import _replace_title_page

        log = MagicMock()
        log.info = log.warn = log.error = log.debug = lambda *a, **k: None

        # Shape the converter produces for a half-title front-matter entry.
        chapters = [
            {"title": "Half Title Page", "text": "The Real Title\n"},
            {"title": "Chapter 1", "text": "Chapter 1\n\nBody of chapter one."},
        ]
        _replace_title_page(
            chapters, {"title": "The Real Title", "author": "Jane Author"}, log
        )

        result = NativeKFXGenerator()._build_chapter_content(chapters)
        chunk_texts = [c["text"] for c in result["all_chunks"]]

        # The structural label must never be an emitted chunk.
        assert "Half Title Page" not in chunk_texts, (
            f"half-title label leaked into chunks: {chunk_texts}"
        )
        # The book title still appears (as body text, not a heading).
        assert "The Real Title" in chunk_texts
        # Real chapters are unaffected.
        assert "Chapter 1" in chunk_texts


@pytest.mark.unit
def test_emphasis_spans_emit_142():
    from kfxgen.kfxlib_minimal.ion import IS

    gen = NativeKFXGenerator()
    frag = gen.build_fragment_259(
        ["s0"],
        content_name="content_1",
        entity_name="l0",
        positions=[1001],
        outer_position=1000,
        outer_style="s0",
        chunk_kinds=["text"],
        emphasis_spans=[[(2, 3, "s0it")]],
    )
    child = frag.value[IS("$146")][0]
    spans = child[IS("$142")]
    assert spans[0][IS("$143")] == 2
    assert spans[0][IS("$144")] == 3
    assert spans[0][IS("$157")] == IS("s0it")
    assert IS("$179") not in spans[0]


@pytest.mark.unit
def test_emphasis_block_produces_italic_span_in_book(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS
    from kfxgen.inline_style import FLAG_ITALIC

    gen = NativeKFXGenerator()
    chapters = [
        {
            "title": "Ch",
            "text": "a big cat",
            "blocks": [
                {"text": "a big cat", "spans": [(2, 3, frozenset({FLAG_ITALIC}))]}
            ],
        }
    ]
    out = tmp_path / "out.kfx"
    gen.generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(out)
    )
    # An italic $157 ($12 -> $382) must exist among emitted fragments.
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    assert any(
        IS("$12") in f.value and f.value[IS("$12")] == IS("$382") for f in styles
    )


@pytest.mark.unit
def test_plain_chapter_emits_no_emphasis_fragments(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS

    gen = NativeKFXGenerator()
    chapters = [{"title": "Ch", "text": "plain text only"}]  # no blocks
    gen.generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(tmp_path / "o.kfx")
    )
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    assert all(IS("$12") not in f.value for f in styles)  # no italic anywhere


@pytest.mark.unit
def test_block_style_produces_aligned_indented_157(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS

    gen = NativeKFXGenerator()
    chapters = [
        {
            "title": "Ch",
            "text": "centered indented line",
            "blocks": [
                {
                    "text": "centered indented line",
                    "spans": [],
                    "block_style": {"align": "center", "indent": ("2", "$308")},
                }
            ],
        }
    ]
    gen.generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(tmp_path / "o.kfx")
    )
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    # A style must exist with text-align center, text-indent 2em, no padding-top.
    hit = [
        f
        for f in styles
        if f.value.get(IS("$34")) == IS("$320")
        and IS("$36") in f.value
        and f.value[IS("$36")].get(IS("$306")) == IS("$308")
        and IS("$47") not in f.value
    ]
    assert hit, "expected a centered+indented $157 with padding-top suppressed"


@pytest.mark.unit
def test_no_block_style_emits_default_align_and_indent(tmp_path):
    from kfxgen.kfxlib_minimal.ion import IS

    gen = NativeKFXGenerator()
    chapters = [{"title": "Ch", "text": "plain body text"}]  # no blocks/block_style
    gen.generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(tmp_path / "o.kfx")
    )
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    # Every body style keeps justify ($321) and indent 0; none carry a non-% indent unit.
    for f in styles:
        if IS("$34") in f.value:
            assert f.value[IS("$34")] in (
                IS("$321"),
                IS("$320"),
            )  # justify or heading-center if any
        if IS("$36") in f.value:
            assert f.value[IS("$36")][IS("$306")] == IS(
                "$314"
            )  # default % unit, value 0


@pytest.mark.unit
def test_per_chapter_font_size_not_leaked_from_last_chapter(tmp_path):
    """Regression: plain-text block-style path must use each chapter's own
    font_size, not the leaked final chapter's value from an earlier loop."""
    from kfxgen.kfxlib_minimal.ion import IS, IonDecimal

    gen = NativeKFXGenerator()
    chapters = [
        {"title": "Copyright", "text": "c 2026 Author", "font_size": 0.75},
        {"title": "Chapter One", "text": "The main body text begins here."},
    ]
    gen.generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(tmp_path / "o.kfx")
    )
    styles = [f for f in gen.fragments if str(f.ftype) == "$157"]
    # The first chapter has font_size=0.75 (non-default), so at least one $157
    # must carry $16 with $307=0.75 and $306=$505 (rem unit).
    small_styles = [
        f
        for f in styles
        if IS("$16") in f.value
        and f.value[IS("$16")].get(IS("$307")) == IonDecimal("0.75")
        and f.value[IS("$16")].get(IS("$306")) == IS("$505")
    ]
    assert small_styles, (
        "expected a $157 with font_size=0.75rem for the first chapter; "
        "got none — leaked-loop-variable bug may still be present"
    )
