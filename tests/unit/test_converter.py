"""
Unit tests for converter.py — TOC and spine extraction.

Issue #6: TOC entries whose href isn't in the spine should fall back to
matching against the manifest, instead of being silently dropped.
"""

import logging
import os
import re
import sys
from unittest.mock import MagicMock

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen import converter as _conv
from kfxgen._img_tokens import IMG_TOKEN_RE
from kfxgen.converter import (
    CONTENTS_SKIP_TITLES,
    HALF_TITLE_TITLES,
    TITLE_PAGE_TITLES,
    _anchor_block_index,
    _assemble_chapters_by_coordinate,
    _href_fragment,
    _leading_chapter_title,
    _normalize_title,
    _replace_title_page,
    extract_blocks_from_html,
    extract_chapters_from_oeb,
    extract_cover_image,
    extract_images_from_oeb,
)


def _xhtml(body_text):
    """Build a minimal XHTML element whose body contains body_text."""
    src = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"<p>{body_text}</p>"
        "</body></html>"
    )
    return etree.fromstring(src)


class _SpineItem:
    def __init__(self, href, body_text):
        self.href = href
        self.data = _xhtml(body_text)
        self.media_type = "application/xhtml+xml"


class _ManifestItem:
    def __init__(
        self, item_id, href, body_text=None, media_type="application/xhtml+xml"
    ):
        self.id = item_id
        self.href = href
        self.media_type = media_type
        self.data = _xhtml(body_text) if body_text is not None else None


class _Manifest:
    """Iterable manifest with .hrefs dict, mimicking Calibre's manifest API."""

    def __init__(self, items):
        self._items = items
        self.hrefs = {it.href: it for it in items}

    def __iter__(self):
        return iter(self._items)


class _TOCNode:
    def __init__(self, title, href, children=()):
        self.title = title
        self.href = href
        self._children = list(children)

    def __iter__(self):
        return iter(self._children)


class _OEBBook:
    def __init__(self, spine, toc, manifest=None):
        self.spine = spine
        self.toc = toc
        self.manifest = manifest or _Manifest([])
        # Provide a metadata stub that mimics the bits convert_oeb_to_kfx uses
        self.metadata = MagicMock()
        self.metadata.cover = None


def _silent_log():
    """A logger stub matching Calibre's log API (info/warn/error/debug)."""
    log = MagicMock()
    log.info = lambda *a, **k: None
    log.warn = lambda *a, **k: None
    log.error = lambda *a, **k: None
    log.debug = lambda *a, **k: None
    return log


class TestTOCBasenameMatch:
    """TOC hrefs with paths should match spine items by basename."""

    def test_toc_with_path_matches_spine_basename(self):
        spine = [
            _SpineItem("chapter1.xhtml", "First chapter content."),
            _SpineItem("chapter2.xhtml", "Second chapter content."),
        ]
        toc = [
            _TOCNode("Chapter 1", "OEBPS/text/chapter1.xhtml"),
            _TOCNode("Chapter 2", "OEBPS/text/chapter2.xhtml"),
        ]
        oeb = _OEBBook(spine=spine, toc=toc)
        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        titles = [c["title"] for c in chapters]
        assert "Chapter 1" in titles
        assert "Chapter 2" in titles


class TestTOCManifestFallbackEdgeCases:
    """Defensive behavior when manifest is missing or holds non-XHTML items."""

    def test_no_manifest_does_not_crash(self):
        """A book with `manifest=None` must skip the fallback gracefully."""
        spine = [_SpineItem("chapter1.xhtml", "Body.")]
        toc = [
            _TOCNode("Chapter 1", "chapter1.xhtml"),
            _TOCNode("Ghost", "ghost.xhtml"),
        ]
        oeb = _OEBBook(spine=spine, toc=toc)
        oeb.manifest = None  # explicitly clear

        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        titles = [c["title"] for c in chapters]
        assert titles == ["Chapter 1"]

    def test_manifest_image_item_with_empty_media_type_is_skipped(self):
        """An image item with no media_type set must not be parsed as XHTML."""
        spine = [_SpineItem("chapter1.xhtml", "Body.")]
        toc = [
            _TOCNode("Chapter 1", "chapter1.xhtml"),
            _TOCNode("Cover", "cover.jpg"),
        ]
        # Manifest item for cover.jpg has bytes data but no media_type
        cover = _ManifestItem("cover", "cover.jpg", media_type="")
        cover.data = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
        manifest = _Manifest(
            [
                _ManifestItem("ch1", "chapter1.xhtml"),
                cover,
            ]
        )
        oeb = _OEBBook(spine=spine, toc=toc, manifest=manifest)

        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        titles = [c["title"] for c in chapters]
        assert "Cover" not in titles, (
            "Manifest items with non-XHTML / empty media_type must not be "
            "fed into the XHTML text extractor"
        )


JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\x00" * 200
    + b"\xff\xd9"
)


class _GuideRef:
    def __init__(self, type_, href):
        self.type = type_
        self.href = href


class TestCoverImageReturnsHref:
    """extract_cover_image must return (bytes, href) for every discovery
    method so the body-image pipeline can exclude the cover regardless of
    where it was found (regression guard for double-emit bug found in PR #20)."""

    def test_method_1_metadata_cover_returns_href(self):
        cover = _ManifestItem("cover_id", "images/cover.jpg", media_type="image/jpeg")
        cover.data = JPEG_BYTES
        manifest = _Manifest([cover])
        oeb = _OEBBook(spine=[], toc=[], manifest=manifest)
        oeb.metadata.cover = ["cover_id"]

        data, href = extract_cover_image(oeb, _silent_log())
        assert data == JPEG_BYTES
        assert href == "images/cover.jpg"

    def test_method_2_guide_returns_href(self):
        cover = _ManifestItem(
            "img_cover", "images/cover_guide.jpg", media_type="image/jpeg"
        )
        cover.data = JPEG_BYTES
        manifest = _Manifest([cover])
        oeb = _OEBBook(spine=[], toc=[], manifest=manifest)
        oeb.metadata.cover = None
        oeb.guide = [_GuideRef("cover", "images/cover_guide.jpg")]

        data, href = extract_cover_image(oeb, _silent_log())
        assert data == JPEG_BYTES
        assert href == "images/cover_guide.jpg", (
            "Method 2 (guide) must return the href so the cover isn't "
            "double-emitted as a body image"
        )

    def test_method_3_manifest_scan_returns_href(self):
        cover = _ManifestItem(
            "cover_image", "images/cover_scan.jpg", media_type="image/jpeg"
        )
        cover.data = JPEG_BYTES
        manifest = _Manifest([cover])
        oeb = _OEBBook(spine=[], toc=[], manifest=manifest)
        oeb.metadata.cover = None
        oeb.guide = []

        data, href = extract_cover_image(oeb, _silent_log())
        assert data == JPEG_BYTES
        assert href == "images/cover_scan.jpg", (
            "Method 3 (manifest scan) must return the href so the cover "
            "isn't double-emitted as a body image"
        )

    def test_no_cover_returns_none_tuple(self):
        oeb = _OEBBook(spine=[], toc=[], manifest=_Manifest([]))
        oeb.metadata.cover = None
        oeb.guide = []

        data, href = extract_cover_image(oeb, _silent_log())
        assert data is None
        assert href is None


class TestImagesExcludeCover:
    """Body-image extraction must skip the cover href."""

    def test_cover_excluded_from_body_images(self):
        cover = _ManifestItem("cover", "images/cover.jpg", media_type="image/jpeg")
        cover.data = JPEG_BYTES
        body = _ManifestItem("fig1", "images/figure1.jpg", media_type="image/jpeg")
        body.data = JPEG_BYTES
        manifest = _Manifest([cover, body])
        oeb = _OEBBook(spine=[], toc=[], manifest=manifest)

        result = extract_images_from_oeb(
            oeb, _silent_log(), exclude_hrefs=["images/cover.jpg"]
        )
        hrefs = list(result.keys())
        assert "images/cover.jpg" not in hrefs
        assert "images/figure1.jpg" in hrefs

    def test_unsupported_format_skipped_with_warning(self):
        body = _ManifestItem("gif1", "images/animated.gif", media_type="image/gif")
        body.data = b"GIF89a" + b"\x00" * 200
        manifest = _Manifest([body])
        oeb = _OEBBook(spine=[], toc=[], manifest=manifest)

        log_mock = MagicMock()
        result = extract_images_from_oeb(oeb, log_mock)
        assert "images/animated.gif" not in result
        warn_calls = [str(c) for c in log_mock.warn.call_args_list]
        assert any(
            "animated.gif" in c and "unsupported" in c.lower() for c in warn_calls
        ), (
            f"Expected an 'unsupported format' warn call mentioning the file, "
            f"got: {warn_calls}"
        )


class TestTOCMappingPreservesContent:
    """Existing TOC-to-spine mapping must keep working (regression guard)."""

    def test_normal_toc_to_spine_mapping_unchanged(self):
        spine = [
            _SpineItem("chapter1.xhtml", "Chapter 1 body."),
            _SpineItem("chapter2.xhtml", "Chapter 2 body."),
            _SpineItem("chapter3.xhtml", "Chapter 3 body."),
        ]
        toc = [
            _TOCNode("Chapter 1", "chapter1.xhtml"),
            _TOCNode("Chapter 2", "chapter2.xhtml"),
            _TOCNode("Chapter 3", "chapter3.xhtml"),
        ]
        oeb = _OEBBook(spine=spine, toc=toc)

        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        titles = [c["title"] for c in chapters]
        assert titles == ["Chapter 1", "Chapter 2", "Chapter 3"]


class TestImageOnlyOrphanSkipped:
    """Orphan recovery must skip spine items that have no real text once
    IMG tokens are removed — the common case is the EPUB's own cover.xhtml
    (just an <img> for the cover, which is emitted separately, #32).

    Recovering it appended a junk trailing chapter that emitted zero content
    chunks and crashed the native generator with an IndexError
    (native_generator.py:2283). A text-bearing orphan must still recover.
    """

    def test_image_only_cover_orphan_not_recovered(self):
        # cover.xhtml is last and not referenced by the TOC -> orphan.
        spine = [
            _SpineItem("chapter1.xhtml", "Chapter 1 body."),
            _SpineItem("chapter2.xhtml", "Chapter 2 body."),
            _SpineItem("cover.xhtml", '<img src="cover.jpg" alt="Cover"/>'),
        ]
        toc = [
            _TOCNode("Chapter 1", "chapter1.xhtml"),
            _TOCNode("Chapter 2", "chapter2.xhtml"),
        ]
        oeb = _OEBBook(spine=spine, toc=toc)

        chapters = extract_chapters_from_oeb(
            oeb, _silent_log(), cover_href="images/cover.jpg"
        )
        titles = [c["title"] for c in chapters]
        assert titles == ["Chapter 1", "Chapter 2"]

    def test_text_bearing_orphan_still_recovered(self):
        # A real back-matter page the TOC missed must NOT be dropped.
        spine = [
            _SpineItem("chapter1.xhtml", "Chapter 1 body."),
            _SpineItem("appendix.xhtml", "Appendix with real prose."),
        ]
        toc = [_TOCNode("Chapter 1", "chapter1.xhtml")]
        oeb = _OEBBook(spine=spine, toc=toc)

        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        texts = "\n".join(c["text"] for c in chapters)
        assert "Appendix with real prose." in texts


class TestHalfTitlePage:
    """#107: a chapter whose TOC label is 'Half Title Page' (or a
    variant) must not leak that structural label onto the page. Half-
    title convention is book-title-only, no author."""

    META = {"title": "The Real Title", "author": "Jane Author"}

    def test_half_title_replaced_with_title_only_no_author(self):
        chapters = [{"title": "Half Title Page", "text": "the title\n"}]
        _replace_title_page(chapters, self.META, _silent_log())
        ch = chapters[0]
        # Title only — no author, no "by" (distinct from the full title page).
        assert ch["text"] == "The Real Title"
        assert "Jane Author" not in ch["text"]
        assert "by" not in ch["text"]
        # The structural label must be suppressed as a heading.
        assert ch["_omit_title_heading"] is True

    def test_variants_recognized(self):
        for label in [
            "Half Title",
            "Half-Title",
            "half title page",
            "HALFTITLE",
            "Halftitle Page",
            "Bastard Title",
        ]:
            chapters = [{"title": label, "text": "x"}]
            _replace_title_page(chapters, self.META, _silent_log())
            ch = chapters[0]
            assert ch["text"] == "The Real Title", f"{label!r} not recognized"
            assert ch["_omit_title_heading"] is True, f"{label!r} heading not omitted"

    def test_full_title_page_still_includes_author(self):
        # Regression guard: the full title page path is unchanged.
        chapters = [{"title": "Title Page", "text": "old"}]
        _replace_title_page(chapters, self.META, _silent_log())
        ch = chapters[0]
        assert ch["text"] == "The Real Title\n\nby\n\nJane Author"
        assert ch["_omit_title_heading"] is True

    def test_half_title_excluded_from_rebuilt_contents(self):
        chapters = [
            {"title": "Contents", "text": "old toc"},
            {"title": "Half Title Page", "text": "t"},
            {"title": "Chapter 1", "text": "body one"},
        ]
        _replace_title_page(chapters, self.META, _silent_log())
        contents = chapters[0]
        assert "Half Title Page" not in contents["text"]
        listed = [link["text"] for link in contents.get("toc_links", [])]
        assert "Half Title Page" not in listed
        assert "Chapter 1" in listed

    def test_skip_sets_stay_in_sync(self):
        # CONTENTS_SKIP_TITLES is built from the shared sets; guard the
        # DRY union so a future edit can't desync them (#107).
        assert HALF_TITLE_TITLES <= CONTENTS_SKIP_TITLES
        assert TITLE_PAGE_TITLES <= CONTENTS_SKIP_TITLES


@pytest.mark.unit
def test_replace_title_page_clears_stale_blocks():
    """Chapters whose text is synthesised must not retain stale blocks (#9)."""
    dummy_blocks = [{"spans": [("old text", "old text", frozenset())]}]
    chapters = [
        # Title page — blocks must be cleared after text replacement.
        {"title": "Title Page", "text": "old", "blocks": list(dummy_blocks)},
        # Half-title page — same invariant.
        {"title": "Half Title", "text": "old", "blocks": list(dummy_blocks)},
        # Contents page — _rebuild_contents_page replaces text, blocks must go.
        {"title": "Contents", "text": "old", "blocks": list(dummy_blocks)},
        # Normal chapter — blocks must be left untouched.
        {"title": "Chapter 1", "text": "body", "blocks": list(dummy_blocks)},
    ]
    meta = {"title": "MyBook", "author": "A. Author"}
    _replace_title_page(chapters, meta, _silent_log())

    assert "blocks" not in chapters[0], "title page blocks not cleared"
    assert "blocks" not in chapters[1], "half-title page blocks not cleared"
    assert "blocks" not in chapters[2], "contents page blocks not cleared"
    assert "blocks" in chapters[3], "normal chapter blocks wrongly cleared"


class _OptsStub:
    def __init__(self, embed):
        self.kfxgen_embed_original_images = embed


class _Log2:
    def info(self, *a):
        pass

    def warn(self, *a):
        pass

    def debug(self, *a):
        pass

    def error(self, *a):
        pass


def _patch_pipeline(monkeypatch, captured):
    monkeypatch.setattr(
        _conv,
        "extract_metadata",
        lambda *a, **k: {
            "title": "T",
            "author": "A",
            "language": "en",
            "publisher": "P",
            "issue_date": None,
        },
    )
    monkeypatch.setattr(
        _conv, "extract_cover_image", lambda *a, **k: (b"COVER", "c.jpg")
    )
    monkeypatch.setattr(
        _conv, "extract_images_from_oeb", lambda *a, **k: {"x.jpg": b"XX"}
    )
    monkeypatch.setattr(
        _conv, "extract_chapters_from_oeb", lambda *a, **k: [{"text": "hi"}]
    )

    class _Gen:
        def generate_full_book(self, **kw):
            captured["images"] = kw["images"]
            captured["cover"] = kw["cover_image"]
            # create the output file so the success branch passes
            with open(kw["output_path"], "wb") as f:
                f.write(b"KFX")

    monkeypatch.setattr(_conv, "NativeKFXGenerator", lambda: _Gen())


@pytest.mark.unit
def test_optimization_runs_by_default(monkeypatch, tmp_path):
    captured = {}
    _patch_pipeline(monkeypatch, captured)
    called = {}
    monkeypatch.setattr(
        _conv,
        "optimize_images",
        lambda cover, images, log: (
            called.setdefault("yes", True),
            (b"C2", {"x.jpg": b"Y"}),
        )[1],
        raising=False,
    )
    out = tmp_path / "o.kfx"
    _conv.convert_oeb_to_kfx(object(), str(out), _OptsStub(False), _Log2())
    assert called.get("yes") is True
    assert captured["cover"] == b"C2"
    assert captured["images"] == {"x.jpg": b"Y"}


@pytest.mark.unit
def test_optimization_skipped_when_embed_originals(monkeypatch, tmp_path):
    captured = {}
    _patch_pipeline(monkeypatch, captured)
    monkeypatch.setattr(
        _conv,
        "optimize_images",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
        raising=False,
    )
    out = tmp_path / "o.kfx"
    _conv.convert_oeb_to_kfx(object(), str(out), _OptsStub(True), _Log2())
    assert captured["images"] == {"x.jpg": b"XX"}  # originals untouched
    assert captured["cover"] == b"COVER"


# ── Task 2: extract_blocks_from_html ─────────────────────────────────────────

from kfxgen.inline_style import FLAG_BOLD as Bf  # noqa: E402
from kfxgen.inline_style import FLAG_ITALIC as I  # noqa: E402, N816


def _doc(body_inner):
    return etree.fromstring(
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>{body_inner}</body></html>'.encode()
    )


@pytest.mark.unit
def test_blocks_capture_italic_span():
    blocks = _conv.extract_blocks_from_html(_doc("<p>a <em>big</em> cat</p>"))
    assert len(blocks) == 1
    assert blocks[0]["text"] == "a big cat"
    assert blocks[0]["spans"] == [(2, 3, frozenset({I}))]


@pytest.mark.unit
def test_blocks_capture_bold_and_nested():
    blocks = _conv.extract_blocks_from_html(
        _doc("<p><strong>x <em>y</em></strong></p>")
    )
    assert blocks[0]["text"] == "x y"
    assert blocks[0]["spans"] == [
        (0, 2, frozenset({Bf})),
        (2, 1, frozenset({Bf, I})),
    ]


@pytest.mark.unit
def test_blocks_capture_b_tag():
    # <b> maps to bold the same as <strong> (the <i>/<b> counterparts of
    # <em>/<strong>).
    blocks = _conv.extract_blocks_from_html(_doc("<p>a <b>bee</b> c</p>"))
    assert blocks[0]["text"] == "a bee c"
    assert blocks[0]["spans"] == [(2, 3, frozenset({Bf}))]


@pytest.mark.unit
def test_extract_text_unchanged_delegates_to_blocks():
    doc = _doc("<p>one</p><p>two <i>three</i></p>")
    assert _conv.extract_text_from_html(doc) == "one\n\ntwo three"


# ── Task 3: Thread emphasis blocks onto chapters ──────────────────────────────


@pytest.fixture
def simple_oeb_with_italic():
    item = _SpineItem("chap.xhtml", "see <em>this</em>")
    toc = [_TOCNode("Chapter 1", "chap.xhtml")]
    return _OEBBook(spine=[item], toc=toc)


@pytest.mark.unit
def test_chapter_carries_emphasis_blocks(simple_oeb_with_italic):
    chapters = extract_chapters_from_oeb(simple_oeb_with_italic, _silent_log())
    blocks = chapters[0]["blocks"]
    assert any(b["spans"] and b["spans"][0][2] == frozenset({I}) for b in blocks)


# ── Task 3 (plan B/9): block_style via style_resolver ────────────────────────


@pytest.mark.unit
def test_blocks_block_style_from_resolver():
    doc = _doc("<p>centered</p><p>plain</p>")  # _doc helper exists from Plan A

    def resolver(elem):
        # first <p> centered + indented, second has nothing
        txt = "".join(elem.itertext())
        if "centered" in txt:
            return {"text-align": "center", "text-indent": "2em"}
        return {}

    blocks = _conv.extract_blocks_from_html(doc, style_resolver=resolver)
    assert blocks[0]["block_style"] == {
        "align": "center",
        "indent": ("2", "$308"),
        "margin_left": None,
        "margin_right": None,
        "font_family": [],
        "bold": False,
        "italic": False,
    }
    assert blocks[1]["block_style"] == {
        "align": None,
        "indent": None,
        "margin_left": None,
        "margin_right": None,
        "font_family": [],
        "bold": False,
        "italic": False,
    }


@pytest.mark.unit
def test_blocks_block_style_none_without_resolver():
    doc = _doc("<p>x</p>")
    blocks = _conv.extract_blocks_from_html(doc)
    assert blocks[0]["block_style"] is None


@pytest.mark.unit
def test_blocks_block_style_margins_from_resolver():
    doc = _doc("<blockquote>quoted</blockquote><p>plain</p>")

    def resolver(elem):
        txt = "".join(elem.itertext())
        if "quoted" in txt:
            return {"margin-left": "2em", "margin-right": "1em"}
        return {}

    blocks = _conv.extract_blocks_from_html(doc, style_resolver=resolver)
    assert blocks[0]["block_style"]["margin_left"] == ("2", "$308")
    assert blocks[0]["block_style"]["margin_right"] == ("1", "$308")
    assert blocks[1]["block_style"]["margin_left"] is None
    assert blocks[1]["block_style"]["margin_right"] is None


# ── Task 4: Stylizer-backed style_resolver ───────────────────────────────────


@pytest.fixture
def simple_oeb_centered():
    """OEB book with one spine item containing a centered and a plain paragraph."""
    data = _doc('<p class="c">Title</p><p>body</p>')

    class _Item:
        href = "chap.xhtml"
        media_type = "application/xhtml+xml"

    item = _Item()
    item.data = data
    toc = [_TOCNode("Chapter 1", "chap.xhtml")]
    return _OEBBook(spine=[item], toc=toc)


@pytest.mark.unit
def test_style_resolver_none_outside_calibre():
    # calibre.ebooks.oeb.stylizer is absent in CI -> resolver is None
    import logging

    r = _conv._build_style_resolver(object(), object(), logging.getLogger("t"))
    assert r is None


@pytest.mark.unit
def test_chapters_carry_block_style_with_fake_stylizer(
    monkeypatch, simple_oeb_centered
):
    # Monkeypatch _build_style_resolver to a fake so the test needs no Calibre.
    def fake_builder(oeb, item, log):
        def resolver(elem):
            cls = elem.get("class") or ""
            return {"text-align": "center"} if "c" in cls.split() else {}

        return resolver

    monkeypatch.setattr(_conv, "_build_style_resolver", fake_builder)
    import logging

    chapters = _conv.extract_chapters_from_oeb(
        simple_oeb_centered, logging.getLogger("t")
    )
    blocks = chapters[0].get("blocks", [])
    assert any((b.get("block_style") or {}).get("align") == "center" for b in blocks)


# ── Task 2: Coordinate helpers ──────────────────────────────────────────────


class TestCoordinateHelpers:
    def test_href_fragment(self):
        assert _href_fragment("ch.xhtml#c2") == "c2"
        assert _href_fragment("ch.xhtml") == ""
        assert _href_fragment("") == ""

    def test_anchor_block_index_first_wins(self):
        blocks = [
            {"anchor_ids": ["a"]},
            {"anchor_ids": ["b", "a"]},
            {"anchor_ids": []},
        ]
        assert _anchor_block_index(blocks) == {"a": 0, "b": 1}


# ── Task 1: per-block anchor_ids ─────────────────────────────────────────────


def _xhtml_raw(body_inner):
    src = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body_inner}</body></html>'
    return etree.fromstring(src)


class TestBlockAnchorIds:
    def test_id_on_block_element(self):
        blocks = extract_blocks_from_html(_xhtml_raw('<h2 id="c1">One</h2>'))
        assert blocks[0]["anchor_ids"] == ["c1"]

    def test_id_on_container_attaches_to_first_leaf(self):
        el = _xhtml_raw('<div id="c1"><p>First</p><p>Second</p></div>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["text"] == "First"
        assert blocks[0]["anchor_ids"] == ["c1"]
        assert blocks[1]["anchor_ids"] == []

    def test_standalone_anchor_between_blocks(self):
        el = _xhtml_raw('<p>Before</p><a id="c2"></a><p>After</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["anchor_ids"] == []
        assert blocks[1]["anchor_ids"] == ["c2"]

    def test_legacy_a_name_anchor(self):
        el = _xhtml_raw('<a name="c3"></a><p>Body</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["anchor_ids"] == ["c3"]

    def test_inline_anchor_snaps_to_containing_block(self):
        el = _xhtml_raw('<p>Mid <a id="c4">word</a> here</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["anchor_ids"] == ["c4"]

    def test_empty_id_block_carries_forward(self):
        el = _xhtml_raw('<p id="c5"></p><p>Real</p>')
        blocks = extract_blocks_from_html(el)
        assert blocks[0]["text"] == "Real"
        assert blocks[0]["anchor_ids"] == ["c5"]

    def test_block_without_anchor_has_empty_list(self):
        blocks = extract_blocks_from_html(_xhtml_raw("<p>Plain</p>"))
        assert blocks[0]["anchor_ids"] == []

    def test_trailing_anchor_snaps_to_last_block(self):
        # A standalone anchor AFTER the last leaf block must attach to the last
        # block's anchor_ids, not be silently dropped (FIX 7).
        el = _xhtml_raw('<p>Last</p><a id="eof"></a>')
        blocks = extract_blocks_from_html(el)
        assert blocks[-1]["anchor_ids"] == ["eof"]


# ---------------------------------------------------------------------------
# Coordinate-based chapter assembly
# ---------------------------------------------------------------------------


def _spine_item(href, blocks):
    """blocks: list of (text, anchor_ids) tuples."""
    return {
        "href": href,
        "text": "\n\n".join(t for t, _ in blocks),
        "blocks": [
            {"text": t, "spans": [], "block_style": None, "anchor_ids": list(a)}
            for t, a in blocks
        ],
    }


class TestCoordinateAssembly:
    def test_multi_anchor_split_within_one_file(self):
        spine = [
            _spine_item(
                "book.xhtml",
                [("I", ["c1"]), ("Body one", []), ("II", ["c2"]), ("Body two", [])],
            )
        ]
        toc = [
            {"title": "I", "href": "book.xhtml#c1"},
            {"title": "II", "href": "book.xhtml#c2"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["I", "II"]
        assert chapters[0]["text"] == "I\n\nBody one"
        assert chapters[1]["text"] == "II\n\nBody two"

    def test_one_file_per_chapter(self):
        spine = [
            _spine_item("a.xhtml", [("Alpha", [])]),
            _spine_item("b.xhtml", [("Beta", [])]),
        ]
        toc = [
            {"title": "Alpha", "href": "a.xhtml"},
            {"title": "Beta", "href": "b.xhtml"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["Alpha", "Beta"]

    def test_split_sibling_spans_files(self):
        # chap.xhtml is in the TOC; chap_split_001.xhtml is an orphan sibling
        # between two TOC anchors -> absorbed into the first chapter.
        spine = [
            _spine_item("chap.xhtml", [("One", ["c1"])]),
            _spine_item("chap_split_001.xhtml", [("One continued", [])]),
            _spine_item("chap2.xhtml", [("Two", ["c2"])]),
        ]
        toc = [
            {"title": "One", "href": "chap.xhtml#c1"},
            {"title": "Two", "href": "chap2.xhtml#c2"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["One", "Two"]
        assert "One continued" in chapters[0]["text"]

    def test_returns_none_when_no_toc_entry_in_spine(self):
        spine = [_spine_item("a.xhtml", [("Alpha", [])])]
        toc = [{"title": "Ghost", "href": "missing.xhtml"}]
        assert _assemble_chapters_by_coordinate(spine, toc, _silent_log()) is None


class TestCoordinateAssemblyEdges:
    def test_front_matter_becomes_leading_chapter(self):
        spine = [
            _spine_item(
                "book.xhtml",
                [("Copyright 2026", []), ("I", ["c1"]), ("Body", [])],
            )
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["Copyright 2026", "I"]
        # Front matter is NOT merged into Chapter I
        assert "Copyright" not in chapters[1]["text"]

    def test_missing_anchor_snaps_after_previous(self):
        spine = [
            _spine_item(
                "book.xhtml",
                [("I", ["c1"]), ("Mid", []), ("II body", [])],
            )
        ]
        toc = [
            {"title": "I", "href": "book.xhtml#c1"},
            {"title": "II", "href": "book.xhtml#ghost"},  # missing -> block 1
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["I", "II"]
        assert chapters[0]["text"] == "I"
        assert chapters[1]["text"] == "Mid\n\nII body"

    def test_non_monotonic_toc_skips_split(self):
        spine = [_spine_item("book.xhtml", [("I", ["c1"]), ("II", ["c2"])])]
        toc = [
            {"title": "II", "href": "book.xhtml#c2"},  # block 1 first
            {"title": "I", "href": "book.xhtml#c1"},  # block 0 -> backward, skipped
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["II"]

    def test_tail_orphan_recovered_as_separate_chapter(self):
        spine = [
            _spine_item("ch.xhtml", [("Nine", ["c9"])]),
            _spine_item("license.xhtml", [("Project Gutenberg License text", [])]),
        ]
        toc = [{"title": "IX", "href": "ch.xhtml#c9"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert chapters[0]["title"] == "IX"
        assert chapters[1]["title"] == "license"
        assert "License text" in chapters[1]["text"]

    def test_image_only_head_not_emitted_as_leading_chapter(self):
        # A spine file whose content before the first TOC anchor is only an IMG
        # token (e.g. an inline cover image) must NOT produce a leading chapter.
        # This is consistent with how tail orphans skip image-only content (FIX 1).
        img_token = _conv._make_img_token("cover.jpg", "")
        spine = [
            _spine_item(
                "book.xhtml",
                [(img_token, []), ("Chapter I", ["c1"]), ("Body text", [])],
            )
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(
            spine, toc, _silent_log(), cover_href="images/cover.jpg"
        )
        # Only chapter I: the head showed nothing but the cover, which the
        # cover chapter already shows. Any other image would make it a page.
        assert [c["title"] for c in chapters] == ["I"]

    def test_head_leading_with_an_image_is_not_titled_with_the_image(self):
        # A head that is an image token FOLLOWED BY REAL TEXT does produce a
        # leading chapter — unlike the image-only case above, which produces
        # none. `_leading_chapter_title` took block[0] as the title whenever it
        # was short and newline-free, and an IMG token is both, so the chapter
        # ended up titled with a picture. That title then reached
        # `_rebuild_contents_page`, which matches literal strings and so could
        # not skip it, and the cover was re-rendered as a contents entry. Every
        # corpus book that builds a contents page had one. (#133)
        img_token = _conv._make_img_token("cover.jpg", "")
        spine = [
            _spine_item(
                "book.xhtml",
                [
                    (img_token, []),
                    ("The Project Gutenberg eBook of A Book", []),
                    ("Chapter I", ["c1"]),
                    ("Body text", []),
                ],
            )
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["Front Matter", "I"]
        # The image itself is content and stays in the body; only the *title*
        # must be free of it.
        assert img_token in chapters[0]["text"]

    def test_head_is_folded_when_it_carries_toc_anchor(self):
        # When the head (blocks before the first coordinate) carries an anchor
        # that appears in the TOC (even as a non-monotonic/skipped entry),
        # the head must fold into the first chapter rather than being emitted
        # as a separate "Front Matter" chapter (FIX 3: head_has_toc_anchor branch).
        spine = [
            _spine_item(
                "book.xhtml",
                [
                    ("Pre-chapter text", ["intro"]),  # block 0: anchor in TOC
                    ("Chapter I", ["c1"]),  # block 1: first coord
                    ("Body text", []),
                ],
            )
        ]
        toc = [
            {"title": "I", "href": "book.xhtml#c1"},  # block 1 -> first valid coord
            {
                "title": "Intro",
                "href": "book.xhtml#intro",
            },  # block 0 -> non-monotonic, skipped
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        # head_has_toc_anchor = True -> no separate Front Matter chapter
        assert [c["title"] for c in chapters] == ["I"]
        assert "Pre-chapter text" in chapters[0]["text"]


# ── Task 5: Gatsby-shaped integration test ───────────────────────────────────


def _multi_block_spine(href, blocks):
    """Build a real XHTML spine item from (tag, id, text) tuples so the live
    extract_blocks path (not a hand-built block list) is exercised."""
    parts = []
    for tag, anchor_id, text in blocks:
        idattr = f' id="{anchor_id}"' if anchor_id else ""
        parts.append(f"<{tag}{idattr}>{text}</{tag}>")
    body = "".join(parts)

    class _Item:
        def __init__(self):
            self.href = href
            self.data = _xhtml_raw(body)
            self.media_type = "application/xhtml+xml"

    return _Item()


class TestGatsbyShapedSplit:
    def test_within_file_anchors_split_into_chapters(self):
        # h-0 holds title + chapters I..III via within-file anchors
        spine = [
            _multi_block_spine(
                "h-0.xhtml",
                [
                    ("h1", "title", "The Great Gatsby"),
                    ("div", "chapter-1", "Chapter one prose."),
                    ("div", "chapter-2", "Chapter two prose."),
                    ("div", "chapter-3", "Chapter three prose."),
                ],
            ),
            _multi_block_spine(
                "h-1.xhtml", [("div", "chapter-4", "Chapter four prose.")]
            ),
        ]
        toc = [
            _TOCNode("Title", "h-0.xhtml#title"),
            _TOCNode("I", "h-0.xhtml#chapter-1"),
            _TOCNode("II", "h-0.xhtml#chapter-2"),
            _TOCNode("III", "h-0.xhtml#chapter-3"),
            _TOCNode("IV", "h-1.xhtml#chapter-4"),
        ]
        oeb = _OEBBook(spine=spine, toc=toc)
        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        titles = [c["title"] for c in chapters]
        assert titles == ["Title", "I", "II", "III", "IV"]
        assert "Chapter two prose." in chapters[2]["text"]
        assert "Chapter two prose." not in chapters[1]["text"]


# ── Task 6: High-chapter-count scale gate (#23, measure-first) ───────────────


class TestHighChapterCountScale:
    """A large book (1200 chapters × several paragraphs) must keep content and
    section eid ranges disjoint by construction (#30) — no reliance on the
    old fixed 10000 boundary, which this book's content overflows."""

    def test_1200_chapter_book_has_disjoint_eid_ranges(self, tmp_path):
        from kfxgen.native_generator import NativeKFXGenerator
        from kfxgen.kfxlib_minimal.ion import IS
        from tests._kfx_introspect import by_type, val, load_fragments

        chapters = [
            {
                "title": f"Chapter {i}",
                "text": (
                    f"Chapter {i}\n\n"
                    f"First sentence of chapter {i}.\n\n"
                    f"Second sentence of chapter {i}.\n\n"
                    f"Third sentence of chapter {i}.\n\n"
                    f"Fourth sentence of chapter {i}.\n\n"
                    f"Fifth sentence of chapter {i}."
                ),
            }
            for i in range(1200)
        ]
        out = tmp_path / "scale.kfx"
        NativeKFXGenerator().generate_full_book(
            title="Scale", author="T", chapters=chapters, output_path=str(out)
        )
        frags = load_fragments(out)

        assert len(by_type(frags, "$260")) == 1200

        content_eids = set()
        for f in by_type(frags, "$259"):
            v = val(f)
            for e in v.get(IS("$146")) or v.get(IS("$181")) or []:
                if hasattr(e, "get") and e.get(IS("$155")) is not None:
                    content_eids.add(int(e.get(IS("$155"))))

        section_eids = set()
        for f in by_type(frags, "$260"):
            v = val(f)
            for e in v.get(IS("$141")) or []:
                if hasattr(e, "get") and e.get(IS("$155")) is not None:
                    section_eids.add(int(e.get(IS("$155"))))

        # Content pushes past the old 10000 floor at this scale...
        assert max(content_eids) >= NativeKFXGenerator.SECTION_POS_BASE
        # ...but content and section eid sets are still disjoint by construction.
        assert content_eids.isdisjoint(section_eids), (
            f"content/section eid overlap: {sorted(content_eids & section_eids)[:10]}"
        )

        # #23 invariant still holds: every section eid is present in $265.
        pos_265 = set()
        for f in by_type(frags, "$265"):
            v = val(f)
            entries = v if isinstance(v, list) else v.get(IS("$181")) or []
            for e in entries:
                if hasattr(e, "get") and e.get(IS("$185")) is not None:
                    pos_265.add(int(e.get(IS("$185"))))
        missing = section_eids - pos_265
        assert not missing, f"section eids absent from $265: {sorted(missing)[:10]}"


# ── Task 1: Dynamic section base (#30) ────────────────────────────────────────


class TestSectionBase:
    def test_normal_book_keeps_default_base(self):
        from kfxgen.native_generator import NativeKFXGenerator

        # content well under the floor -> sections stay at SECTION_POS_BASE
        assert NativeKFXGenerator._section_base(3398) == 10000
        assert NativeKFXGenerator._section_base(9998) == 10000

    def test_overflow_relocates_above_content(self):
        from kfxgen.native_generator import NativeKFXGenerator

        # content at/above the floor -> section base moves just above content_max
        assert NativeKFXGenerator._section_base(10000) == 10002
        assert NativeKFXGenerator._section_base(17798) == 17800

    def test_result_is_even_aligned(self):
        from kfxgen.native_generator import NativeKFXGenerator

        # content eids are always even; the relocated base stays even
        for cm in (10000, 10002, 12344, 17798):
            assert NativeKFXGenerator._section_base(cm) % 2 == 0


# --- #15/#9: attach conversion opts to the OEB so Stylizer can construct ---
# Calibre's OutputFormatPlugin.convert() passes `opts` as a separate arg; the
# OEBBook has no `.opts` on this pipeline, so both the per-element style
# resolver and @font-face extraction (which build a Stylizer needing opts)
# silently degraded until this shim.


@pytest.mark.unit
def test_ensure_oeb_opts_attaches_when_missing():
    class _Oeb:
        pass

    oeb = _Oeb()
    opts = object()
    _conv._ensure_oeb_opts(oeb, opts)
    assert oeb.opts is opts


@pytest.mark.unit
def test_ensure_oeb_opts_preserves_existing():
    class _Oeb:
        pass

    oeb = _Oeb()
    existing = object()
    oeb.opts = existing
    _conv._ensure_oeb_opts(oeb, object())
    assert oeb.opts is existing


@pytest.mark.unit
def test_ensure_oeb_opts_tolerates_unsettable_object():
    class _Frozen:
        __slots__ = ()

    # Must not raise even if the OEB rejects attribute assignment.
    _conv._ensure_oeb_opts(_Frozen(), object())


# --- #15: computed CSS value must include inheritance (font-family on <body>) ---


class _FakeStyle:
    """Mimics Calibre's Style: .get() returns element-local only (None when
    inherited); [prop] returns the fully computed value."""

    def __init__(self, own, computed):
        self._own = own
        self._computed = computed

    def get(self, k, default=None):
        return self._own.get(k, default)

    def __getitem__(self, k):
        if k in self._computed:
            return self._computed[k]
        raise KeyError(k)


@pytest.mark.unit
def test_computed_value_prefers_getitem_for_inherited():
    # font-family inherited from <body>: .get() is None, getitem has the value.
    st = _FakeStyle(own={}, computed={"font-family": '"Charis SIL", serif'})
    assert _conv._computed_value(st, "font-family") == '"Charis SIL", serif'


@pytest.mark.unit
def test_computed_value_falls_back_to_get_when_getitem_missing():
    st = _FakeStyle(own={"font-family": "Georgia"}, computed={})
    assert _conv._computed_value(st, "font-family") == "Georgia"


# --- #33: text-align inherited from <body>/<div> must not be dropped ---


class _FakeCalibreStyle:
    """Mimics Calibre's Style: .get() is element-local only (None when
    inherited); [prop] returns the computed value (incl. inheritance)."""

    def __init__(self, own, computed):
        self._own, self._computed = own, computed

    def get(self, k, default=None):
        return self._own.get(k, default)

    def __getitem__(self, k):
        if k in self._computed:
            return self._computed[k]
        raise KeyError(k)


class _FakeStylizer:
    def __init__(self, style):
        self._style = style

    def style(self, elem):
        return self._style


@pytest.mark.unit
def test_style_resolver_reads_inherited_text_align_via_getitem():
    # Regression for #33: text-align set on <body>/<div> is inherited; Calibre's
    # Style.get() returns None for it, Style[prop] returns 'center'. The resolver
    # must use getitem so inherited alignment isn't silently dropped.
    st = _FakeCalibreStyle(own={}, computed={"text-align": "center"})
    resolver = _conv._build_style_resolver(
        None,
        None,
        _silent_log(),
        stylizer_factory=lambda oeb, item: _FakeStylizer(st),
    )
    assert resolver(object())["text-align"] == "center"


@pytest.mark.unit
def test_style_resolver_unstyled_align_is_auto_then_ignored():
    # A truly unstyled paragraph: getitem returns 'auto', which compute_block_style
    # must ignore (so getitem does not over-apply alignment).
    st = _FakeCalibreStyle(own={}, computed={"text-align": "auto"})
    resolver = _conv._build_style_resolver(
        None, None, _silent_log(), stylizer_factory=lambda oeb, item: _FakeStylizer(st)
    )
    assert resolver(object())["text-align"] == "auto"
    from kfxgen.inline_style import compute_block_style

    assert compute_block_style({"text-align": "auto"})["align"] is None


# --- font-embedding toggle: opt-out via kfxgen_disable_font_embedding (#15) ---


@pytest.mark.unit
def test_font_table_for_disabled_returns_empty_without_building(monkeypatch):
    import kfxgen.font_table as _ft

    called = []
    monkeypatch.setattr(_ft, "build_font_table", lambda *a, **k: called.append(1))

    class _Opts:
        kfxgen_disable_font_embedding = True

    ft = _conv._font_table_for(object(), _Opts(), _silent_log())
    assert isinstance(ft, _ft.FontTable) and ft.faces == []
    assert not called, "build_font_table must not run when embedding is disabled"


@pytest.mark.unit
def test_font_table_for_default_embeds_delegating_to_build(monkeypatch):
    import kfxgen.font_table as _ft

    sentinel = _ft.FontTable([])
    monkeypatch.setattr(_ft, "build_font_table", lambda oeb, log: sentinel)

    class _NotDisabled:  # explicit opt-out = False -> embed
        kfxgen_disable_font_embedding = False

    class _Absent:  # option missing -> default is to embed
        pass

    assert _conv._font_table_for(object(), _NotDisabled(), _silent_log()) is sentinel
    assert _conv._font_table_for(object(), _Absent(), _silent_log()) is sentinel
    assert _conv._font_table_for(object(), None, _silent_log()) is sentinel


# --- native-table toggle: opt-out via kfxgen_disable_native_tables (#219) ---


def _table_oeb(directory):
    from tests.fixtures.epub_builder import EpubBuilder
    from tests.fixtures.golden.inputs import _xhtml_page
    from tests.fixtures.oeb_shim import EpubAsOeb

    body = f"<p>Before.</p>{_ISSUE_219_TABLE}<p>After.</p>"
    epub = (
        EpubBuilder()
        .set_metadata(title="Table Book", author="Table Author")
        .add_chapter("Chapter", _xhtml_page("Chapter", body).encode("utf-8"))
        .build(directory, "t")
    )
    return EpubAsOeb(str(epub))


def _convert_table_book(directory, opts):
    out = directory / "t.kfx"
    _conv.convert_oeb_to_kfx(
        _table_oeb(directory), str(out), opts=opts, log=_silent_log()
    )
    return out


def _storyline_node_types(path):
    from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

    frags = load_fragments(path)
    return {
        str(e.get("$159"))
        for f in by_type(frags, "$259")
        for e in iter_entries(val(f).get("$146"))
    }


@pytest.mark.unit
def test_native_tables_are_on_by_default(tmp_path):
    kfx = _convert_table_book(tmp_path, opts=None)
    assert "$278" in _storyline_node_types(kfx)


@pytest.mark.unit
def test_disabling_native_tables_restores_rows(tmp_path):
    class Opts:
        kfxgen_disable_native_tables = True

    assert "$278" not in _storyline_node_types(
        _convert_table_book(tmp_path, opts=Opts())
    )


@pytest.mark.unit
def test_disabled_output_is_byte_identical_to_a_book_built_without_native_support(
    tmp_path, monkeypatch
):
    class Opts:
        kfxgen_disable_native_tables = True

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _convert_table_book(tmp_path / "a", opts=Opts()).read_bytes()

    # The same conversion with the flag never passed at all: the extractor's
    # own default, which is what 5.8.8 did.
    real = _conv.extract_chapters_from_oeb

    def without_native_support(oeb, log, **kwargs):
        kwargs.pop("native_tables", None)
        return real(oeb, log, **kwargs)

    monkeypatch.setattr(_conv, "extract_chapters_from_oeb", without_native_support)
    b = _convert_table_book(tmp_path / "b", opts=None).read_bytes()
    assert a == b


# ── #52: superscript / subscript inline runs ─────────────────────────────────

from kfxgen.inline_style import FLAG_SUB as Sb  # noqa: E402
from kfxgen.inline_style import FLAG_SUPER as Sp  # noqa: E402


@pytest.mark.unit
def test_blocks_capture_sup_tag():
    blocks = _conv.extract_blocks_from_html(_doc("<p>note<sup>1</sup></p>"))
    assert blocks[0]["text"] == "note1"
    assert blocks[0]["spans"] == [(4, 1, frozenset({Sp}))]


@pytest.mark.unit
def test_blocks_capture_sub_tag():
    blocks = _conv.extract_blocks_from_html(_doc("<p>H<sub>2</sub>O</p>"))
    assert blocks[0]["text"] == "H2O"
    assert blocks[0]["spans"] == [(1, 1, frozenset({Sb}))]


@pytest.mark.unit
def test_sup_composes_with_emphasis():
    blocks = _conv.extract_blocks_from_html(_doc("<p><em>a<sup>2</sup></em></p>"))
    assert blocks[0]["text"] == "a2"
    assert blocks[0]["spans"] == [
        (0, 1, frozenset({I})),
        (1, 1, frozenset({I, Sp})),
    ]


@pytest.mark.unit
def test_css_vertical_align_super_marks_run():
    """Publisher EPUBs get superscript from CSS, not <sup>: a noteref is
    `<span class="EN_REF"><a ...>1</a></span>` with
    `span.EN_REF { vertical-align: super }`. (#52)
    """
    doc = _doc('<p>text<span class="EN_REF"><a href="n.xhtml#n1">1</a></span></p>')

    def resolver(elem):
        if elem.get("class") == "EN_REF":
            return {"vertical-align": "super"}
        return {}

    blocks = _conv.extract_blocks_from_html(doc, style_resolver=resolver)
    assert blocks[0]["text"] == "text1"
    # One run covering the marker, carrying superscript. It also carries a
    # link flag once base_href is supplied (#53) — asserted separately in
    # test_link_composes_with_superscript.
    assert len(blocks[0]["spans"]) == 1
    start, length, flags = blocks[0]["spans"][0]
    assert (start, length) == (4, 1)
    assert Sp in flags


@pytest.mark.unit
def test_css_vertical_align_on_block_does_not_mark_whole_paragraph():
    """vertical-align resolved on the block element itself must not turn the
    entire paragraph into a superscript run."""
    doc = _doc("<p>whole paragraph</p>")

    def resolver(elem):
        return {"vertical-align": "super"}

    blocks = _conv.extract_blocks_from_html(doc, style_resolver=resolver)
    assert blocks[0]["spans"] == []


@pytest.mark.unit
def test_style_resolver_reports_vertical_align():
    """The Stylizer-backed resolver must expose vertical-align so inline runs
    can see it. It does not inherit, so .get() is the correct accessor."""
    import logging

    class _Style:
        def get(self, prop):
            return "super" if prop == "vertical-align" else None

        def __getitem__(self, prop):
            return "auto"

    class _Stylizer:
        def style(self, elem):
            return _Style()

    r = _conv._build_style_resolver(
        object(), object(), logging.getLogger("t"), lambda o, i: _Stylizer()
    )
    assert r(_doc("<p>x</p>"))["vertical-align"] == "super"


# ── #53: in-body <a href> links and their anchor targets ─────────────────────

from kfxgen.inline_style import link_target  # noqa: E402


def _spans_link_targets(block):
    return [link_target(flags) for _, _, flags in block["spans"]]


@pytest.mark.unit
def test_anchor_href_becomes_link_span():
    blocks = _conv.extract_blocks_from_html(
        _doc('<p>text<a href="endnotes.xhtml#note1">1</a></p>'),
        base_href="chapter_001.xhtml",
    )
    assert blocks[0]["text"] == "text1"
    assert len(blocks[0]["spans"]) == 1
    start, length, flags = blocks[0]["spans"][0]
    assert (start, length) == (4, 1)
    assert link_target(flags) == "endnotes.xhtml#note1"


@pytest.mark.unit
def test_bare_fragment_href_resolves_against_base_file():
    blocks = _conv.extract_blocks_from_html(
        _doc('<p>see <a href="#later">this</a></p>'), base_href="chapter_001.xhtml"
    )
    assert _spans_link_targets(blocks[0]) == ["chapter_001.xhtml#later"]


@pytest.mark.unit
def test_link_composes_with_superscript():
    """The real noteref shape: a CSS-superscripted span wrapping a link."""
    doc = _doc('<p>x<span class="EN_REF"><a href="endnotes.xhtml#n1">1</a></span></p>')

    def resolver(elem):
        if elem.get("class") == "EN_REF":
            return {"vertical-align": "super"}
        return {}

    blocks = _conv.extract_blocks_from_html(
        doc, style_resolver=resolver, base_href="chapter_001.xhtml"
    )
    _, _, flags = blocks[0]["spans"][0]
    assert Sp in flags
    assert link_target(flags) == "endnotes.xhtml#n1"


@pytest.mark.unit
@pytest.mark.parametrize(
    "href",
    [
        "http://example.com/x",
        "https://example.com/x",
        "mailto:a@b.c",
        "../../../etc/passwd",
        "/absolute/path.xhtml",
    ],
)
def test_external_and_unsafe_hrefs_produce_no_link(href):
    blocks = _conv.extract_blocks_from_html(
        _doc(f'<p>a<a href="{href}">b</a></p>'), base_href="chapter_001.xhtml"
    )
    assert _spans_link_targets(blocks[0]) in ([], [None])


@pytest.mark.unit
def test_href_without_fragment_targets_the_file():
    blocks = _conv.extract_blocks_from_html(
        _doc('<p><a href="chapter_002.xhtml">Next</a></p>'),
        base_href="chapter_001.xhtml",
    )
    assert _spans_link_targets(blocks[0]) == ["chapter_002.xhtml"]


@pytest.mark.unit
def test_blocks_carry_file_qualified_anchor_keys():
    blocks = _conv.extract_blocks_from_html(
        _doc('<p id="note1">A note</p>'), base_href="endnotes.xhtml"
    )
    # The bare filename is prepended to the first block so whole-file links
    # resolve (#62); the id-qualified key is what matters here.
    assert "endnotes.xhtml#note1" in blocks[0]["anchor_keys"]
    assert blocks[0]["anchor_keys"] == ["endnotes.xhtml", "endnotes.xhtml#note1"]


@pytest.mark.unit
def test_anchor_keys_absent_base_href_falls_back_to_bare_ids():
    blocks = _conv.extract_blocks_from_html(_doc('<p id="note1">A note</p>'))
    assert blocks[0]["anchor_ids"] == ["note1"]
    assert blocks[0]["anchor_keys"] == []


# ── #58/#59/#60: TOC extraction defects ──────────────────────────────────────


@pytest.mark.unit
def test_nested_list_inside_li_is_not_flattened():
    """#58: <li> whose child is a nested <ol> must not be treated as a leaf.
    A Part heading and its chapters collapsed into one run-on paragraph."""
    blocks = _conv.extract_blocks_from_html(
        _doc("<ol><li>Part<ol><li>Ch1</li><li>Ch2</li></ol></li></ol>")
    )
    # Markers since #201: "Part" is item 1 of the outer list, the chapters
    # items 1 and 2 of the inner one.
    assert [b["text"] for b in blocks] == ["1. Part", "1. Ch1", "2. Ch2"]


@pytest.mark.unit
def test_nested_ul_inside_li_is_not_flattened():
    blocks = _conv.extract_blocks_from_html(
        _doc("<ul><li>Top<ul><li>Sub</li></ul></li></ul>")
    )
    assert [b["text"] for b in blocks] == ["• Top", "• Sub"]


@pytest.mark.unit
def test_tail_after_nested_tag_keeps_enclosing_italic():
    """#59: ' gamma' sits inside <em> and must stay italic. It was getting the
    *incoming* flags instead of the enclosing element's."""
    blocks = _conv.extract_blocks_from_html(
        _doc("<p><em>alpha <b>beta</b> gamma</em></p>")
    )
    text = blocks[0]["text"]
    assert text == "alpha beta gamma"
    covered = {}
    for s, length, flags in blocks[0]["spans"]:
        for i in range(s, s + length):
            covered[i] = flags
    tail_start = text.index("gamma")
    assert all(
        I in covered.get(i, frozenset()) for i in range(tail_start, len(text))
    ), "tail text inside <em> lost its italic"


@pytest.mark.unit
def test_tail_after_nested_tag_keeps_enclosing_link():
    """#59, link form: the real TOC shape — <a> wrapping styled spans with bare
    text between them. Every character of the anchor must carry the link."""
    doc = _doc(
        '<p><a href="c.xhtml#t"><span>PART I</span> T<span>he</span> End</a></p>'
    )
    blocks = _conv.extract_blocks_from_html(doc, base_href="toc.xhtml")
    text = blocks[0]["text"]
    covered = {}
    for s, length, flags in blocks[0]["spans"]:
        for i in range(s, s + length):
            covered[i] = flags
    assert all(
        link_target(covered.get(i, frozenset())) == "c.xhtml#t"
        for i in range(len(text))
    ), (
        f"anchor text only partly linked: "
        f"{[(i, text[i], link_target(covered.get(i, frozenset()))) for i in range(len(text))]}"
    )


@pytest.mark.unit
def test_hidden_attribute_subtree_is_skipped():
    """#60: hidden='hidden' content is not rendered content."""
    blocks = _conv.extract_blocks_from_html(
        _doc('<p>visible</p><nav hidden="hidden"><p>SHOULD NOT APPEAR</p></nav>')
    )
    assert [b["text"] for b in blocks] == ["visible"]


@pytest.mark.unit
def test_page_list_nav_is_skipped_even_without_hidden():
    """#60: page-list/landmarks are navigation, never body text — some
    producers omit the hidden attribute."""
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<p>real</p>"
            '<nav epub:type="page-list"><ol><li><a href="a.xhtml#p1">1</a></li></ol></nav>'
            '<nav epub:type="landmarks"><ol><li><a href="a.xhtml">Begin</a></li></ol></nav>'
        )
    )
    assert [b["text"] for b in blocks] == ["real"]


@pytest.mark.unit
def test_hidden_does_not_swallow_normal_content():
    blocks = _conv.extract_blocks_from_html(
        _doc('<p hidden="hidden">gone</p><p>kept</p>')
    )
    assert [b["text"] for b in blocks] == ["kept"]


@pytest.mark.unit
def test_first_block_carries_bare_filename_anchor_key():
    """#62: a TOC entry may link to a whole file with no fragment. If that file
    declares no ids anywhere, nothing anchors it and the link is dropped."""
    blocks = _conv.extract_blocks_from_html(
        _doc("<p>About the author.</p><p>More.</p>"), base_href="038_BM_006.xhtml"
    )
    assert "038_BM_006.xhtml" in blocks[0]["anchor_keys"]
    assert "038_BM_006.xhtml" not in blocks[1]["anchor_keys"]


@pytest.mark.unit
def test_bare_filename_key_absent_without_base_href():
    blocks = _conv.extract_blocks_from_html(_doc("<p>x</p>"))
    assert blocks[0]["anchor_keys"] == []


# ── #69: anchor keys must be directory-aware ─────────────────────────────────


@pytest.mark.unit
def test_anchor_keys_keep_the_documents_directory():
    """#69: two files sharing a basename in different folders must not collide."""
    a = _conv.extract_blocks_from_html(
        _doc('<p id="n1">front</p>'), base_href="front/notes.xhtml"
    )
    b = _conv.extract_blocks_from_html(
        _doc('<p id="n1">back</p>'), base_href="back/notes.xhtml"
    )
    assert a[0]["anchor_keys"] != b[0]["anchor_keys"], (
        f"same-basename files collided: {a[0]['anchor_keys']}"
    )


@pytest.mark.unit
def test_link_target_resolves_relative_to_its_own_document():
    """A sibling href resolves within the linking document's directory."""
    blocks = _conv.extract_blocks_from_html(
        _doc('<p><a href="notes.xhtml#n1">x</a></p>'), base_href="text/ch1.xhtml"
    )
    assert link_target(blocks[0]["spans"][0][2]) == "text/notes.xhtml#n1"


@pytest.mark.unit
def test_link_target_resolves_parent_directory_reference():
    """`../back/notes.xhtml` from `text/ch1.xhtml` is a normal cross-folder
    link and must resolve, not be discarded as traversal."""
    blocks = _conv.extract_blocks_from_html(
        _doc('<p><a href="../back/notes.xhtml#n1">x</a></p>'),
        base_href="text/ch1.xhtml",
    )
    assert link_target(blocks[0]["spans"][0][2]) == "back/notes.xhtml#n1"


@pytest.mark.unit
def test_link_target_escaping_the_book_root_is_rejected():
    """Traversal above the book root stays rejected (SECURITY.md, #44/#60)."""
    blocks = _conv.extract_blocks_from_html(
        _doc('<p><a href="../../../etc/passwd">x</a></p>'), base_href="text/ch1.xhtml"
    )
    assert (
        link_target(blocks[0]["spans"][0][2] if blocks[0]["spans"] else frozenset())
        is None
    )


@pytest.mark.unit
def test_same_document_fragment_still_resolves():
    blocks = _conv.extract_blocks_from_html(
        _doc('<p><a href="#later">x</a></p>'), base_href="text/ch1.xhtml"
    )
    assert link_target(blocks[0]["spans"][0][2]) == "text/ch1.xhtml#later"


# ── #79: where in its block each anchor sits ─────────────────────────────────


class TestBlockAnchorOffsets:
    BASE = "text/ch1.xhtml"

    def _offsets(self, body_inner):
        blocks = extract_blocks_from_html(_xhtml_raw(body_inner), base_href=self.BASE)
        return blocks, blocks[0].get("anchor_offsets")

    def test_marker_at_end_of_paragraph_records_its_offset(self):
        blocks, offsets = self._offsets('<p>Some prose here.<a id="c9">7</a></p>')
        assert blocks[0]["text"] == "Some prose here.7"
        # The first block also carries the bare-filename key from #62.
        assert offsets == {self.BASE: 0, f"{self.BASE}#c9": len("Some prose here.")}

    def test_id_on_the_block_itself_is_offset_zero(self):
        _blocks, offsets = self._offsets('<h2 id="c1">One</h2>')
        assert offsets == {self.BASE: 0, f"{self.BASE}#c1": 0}

    def test_two_markers_in_one_paragraph_get_distinct_offsets(self):
        _blocks, offsets = self._offsets(
            '<p>First<a id="m1">1</a> then more<a id="m2">2</a></p>'
        )
        assert offsets[f"{self.BASE}#m1"] == len("First")
        assert offsets[f"{self.BASE}#m2"] == len("First1 then more")

    def test_id_from_a_container_lands_at_the_following_block_start(self):
        blocks = extract_blocks_from_html(
            _xhtml_raw('<div id="c1"><p>First</p><p>Second</p></div>'),
            base_href=self.BASE,
        )
        assert blocks[0]["anchor_offsets"] == {self.BASE: 0, f"{self.BASE}#c1": 0}

    def test_whole_file_key_is_offset_zero(self):
        blocks = extract_blocks_from_html(
            _xhtml_raw("<p>Body</p>"), base_href=self.BASE
        )
        assert blocks[0]["anchor_offsets"] == {self.BASE: 0}


# ── #113: an <img> sharing a container with block siblings ───────────────────
#
# `_walk` has two ways to reach an image. A block with no block children is a
# leaf: `_walk_inline` runs over it and picks up any `<img>` inside. A block
# that *does* have block children takes the container path instead, which never
# calls `_walk_inline` and dispatches each child to `_walk`. Images arriving
# that second way were dropped, because the img branch required
# `not parent_is_block` and the container passed its own blockness down.
#
# The corpus surfaced this as "images after a caption div vanish", but the
# caption is incidental — any block sibling triggers it, in either order.


@pytest.mark.unit
@pytest.mark.parametrize(
    "label,html",
    [
        (
            "caption div before img",
            '<div><div class="caption">Fig. 1.</div><img src="a.png"/></div>',
        ),
        (
            "caption div after img",
            '<div><img src="a.png"/><div class="caption">Fig. 1.</div></div>',
        ),
        ("paragraph sibling", '<div><p>Caption text</p><img src="a.png"/></div>'),
        ("heading sibling", '<div><h2>Plate I</h2><img src="a.png"/></div>'),
        (
            "img between two blocks",
            '<div><p>before</p><img src="a.png"/><p>after</p></div>',
        ),
        (
            "nested one level deeper",
            '<div><section><p>x</p><img src="a.png"/></section></div>',
        ),
    ],
)
def test_img_survives_block_siblings(label, html):
    """An image must not depend on being the only child of its container."""
    blocks = _conv.extract_blocks_from_html(_doc(html))
    token = _conv._make_img_token("a.png", "")
    assert any(b["text"] == token for b in blocks), (
        f"{label}: image dropped — blocks were {[b['text'] for b in blocks]}"
    )


@pytest.mark.unit
def test_img_emitted_exactly_once_per_occurrence():
    """The fix must not double-emit: the leaf-block path already consumes an
    image via `_walk_inline`, so an image whose container has no block children
    must still appear exactly once."""
    token = _conv._make_img_token("a.png", "")
    for html in (
        '<div><img src="a.png"/></div>',
        '<img src="a.png"/>',
        '<div><p>x</p><img src="a.png"/></div>',
    ):
        blocks = _conv.extract_blocks_from_html(_doc(html))
        assert sum(1 for b in blocks if b["text"] == token) == 1, (
            f"{html}: expected exactly one image block, got "
            f"{[b['text'] for b in blocks]}"
        )


@pytest.mark.unit
def test_block_siblings_keep_their_text_and_order():
    """Recovering the image must not cost the surrounding text or reorder it."""
    blocks = _conv.extract_blocks_from_html(
        _doc('<div><p>before</p><img src="a.png"/><p>after</p></div>')
    )
    texts = [b["text"] for b in blocks]
    token = _conv._make_img_token("a.png", "")
    assert texts == ["before", token, "after"]


# ── #113: cover discovery via <meta name="cover"> ────────────────────────────


def _epub_with_cover(tmp_path, *, meta_cover=True, epub3_properties=False):
    """Minimal EPUB whose cover is named so the filename heuristic cannot find it.

    Neither the item id (`plate`) nor the href (`title-page.jpg`) contains the
    substring "cover", so `extract_cover_image` Method 3 must miss it. That is
    the point: it isolates the metadata path. Fixtures built with
    `EpubBuilder.set_cover` cannot test this — that helper hardcodes
    `id="cover-image"`, which the heuristic matches regardless.
    """
    import zipfile

    from tests._helpers import MINIMAL_JPEG

    meta = '<meta name="cover" content="plate"/>' if meta_cover else ""
    props = ' properties="cover-image"' if epub3_properties else ""
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="i">x</dc:identifier><dc:title>T</dc:title>'
        f"<dc:language>en</dc:language>{meta}</metadata>"
        "<manifest>"
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        f'<item id="plate" href="title-page.jpg" media-type="image/jpeg"{props}/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )
    path = tmp_path / "cover_by_meta.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("content.opf", opf)
        zf.writestr(
            "c1.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            "<body><p>Body text.</p></body></html>",
        )
        zf.writestr("title-page.jpg", MINIMAL_JPEG)
    return path


@pytest.mark.unit
def test_shim_exposes_meta_name_cover(tmp_path):
    """`EpubAsOeb.metadata.cover` must carry the manifest id Calibre exposes."""
    from tests.fixtures.oeb_shim import EpubAsOeb

    oeb = EpubAsOeb(str(_epub_with_cover(tmp_path)))
    assert [str(c) for c in oeb.metadata.cover] == ["plate"]


@pytest.mark.unit
def test_cover_found_when_filename_heuristic_cannot_match(tmp_path):
    """The regression #113 hit: a cover not named `cover.*` was skipped as a
    cover page and then never emitted as one."""
    from tests.fixtures.oeb_shim import EpubAsOeb

    log = logging.getLogger("cover-test")
    log.warn = log.warning
    data, href = _conv.extract_cover_image(
        EpubAsOeb(str(_epub_with_cover(tmp_path))), log
    )
    assert data, "cover not found — metadata.cover path is broken"
    assert href.split("/")[-1] == "title-page.jpg"


@pytest.mark.unit
def test_cover_found_via_epub3_cover_image_property(tmp_path):
    """EPUB 3 declares the cover with `properties="cover-image"` and often no
    legacy `<meta name="cover">`. Same failure mode as above for any producer
    that omits the EPUB 2 form."""
    from tests.fixtures.oeb_shim import EpubAsOeb

    log = logging.getLogger("cover-test-3")
    log.warn = log.warning
    epub = _epub_with_cover(tmp_path, meta_cover=False, epub3_properties=True)
    data, href = _conv.extract_cover_image(EpubAsOeb(str(epub)), log)
    assert data, "EPUB 3 cover-image property not honoured"
    assert href.split("/")[-1] == "title-page.jpg"


# --- table cell boundaries (#128) -------------------------------------------
#
# kfxgen has no table structure: a <table> is walked as an ordinary container
# and every cell lands in one paragraph. Before the fix the only thing between
# two cells was whatever whitespace the author happened to leave between the
# tags, so `</td><td>` with nothing between it fused the values. That is silent
# corruption rather than bad layout — `18018,893` cannot be read back apart
# into `1801` and `8,893`.
#
# The pair of tests that matters is adjacent vs. whitespace-separated: they
# must produce the *same* text. One proves the separator is emitted, the other
# proves it is not doubled where whitespace already did the job.


@pytest.mark.unit
def test_adjacent_table_cells_do_not_fuse():
    blocks = _conv.extract_blocks_from_html(
        _doc("<table><tr><td>1801</td><td>8,893</td></tr></table>")
    )
    assert blocks[0]["text"] == "1801 8,893"


@pytest.mark.unit
def test_adjacent_and_spaced_cells_agree():
    adjacent = _conv.extract_blocks_from_html(
        _doc("<table><tr><td>1801</td><td>8,893</td></tr></table>")
    )
    spaced = _conv.extract_blocks_from_html(
        _doc("<table><tr>\n<td>1801</td>\n<td>8,893</td>\n</tr></table>")
    )
    assert adjacent[0]["text"] == spaced[0]["text"] == "1801 8,893"


@pytest.mark.unit
def test_adjacent_header_cells_do_not_fuse():
    blocks = _conv.extract_blocks_from_html(
        _doc("<table><tr><th>Year</th><th>Population</th></tr></table>")
    )
    assert blocks[0]["text"] == "Year Population"


@pytest.mark.unit
def test_cells_separate_across_row_boundary():
    # The last cell of one row and the first of the next are adjacent too:
    # `</td></tr><tr><td>`. Each row is its own block (#219), so the boundary
    # is a paragraph break rather than a space.
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
        )
    )
    assert [b["text"] for b in blocks] == ["a b", "c d"]


@pytest.mark.unit
def test_empty_cell_does_not_produce_double_space():
    blocks = _conv.extract_blocks_from_html(
        _doc("<table><tr><td>a</td><td></td><td>b</td></tr></table>")
    )
    assert blocks[0]["text"] == "a b"


@pytest.mark.unit
def test_cell_separator_preserves_span_offsets():
    # The separator lengthens the text, so every span offset after it shifts.
    # Emphasis inside a later cell has to still cover its own word.
    blocks = _conv.extract_blocks_from_html(
        _doc("<table><tr><td>ab</td><td><em>cd</em></td></tr></table>")
    )
    assert blocks[0]["text"] == "ab cd"
    assert blocks[0]["spans"] == [(3, 2, frozenset({I}))]


@pytest.mark.unit
def test_non_table_markup_is_unaffected():
    # The rule keys off td/th only; ordinary inline nesting keeps its spacing.
    blocks = _conv.extract_blocks_from_html(_doc("<p>Hello <em>there</em> world.</p>"))
    assert blocks[0]["text"] == "Hello there world."


@pytest.mark.unit
def test_cells_separate_without_tr_wrapper():
    # Cells do not always reach the parent's child loop through a cell-aware
    # step. `extract_blocks_from_html._walk`'s container branch calls
    # `_walk_inline` on a child directly, so markup omitting <tr>/<tbody>
    # bypassed a boundary emitted from the loop and fused anyway. Malformed,
    # and an html5 parser would repair it — but the OEB shim parses with plain
    # `etree.fromstring`, which does not. (#128)
    blocks = _conv.extract_blocks_from_html(
        _doc("<table><td>1801</td><td>8,893</td></table>")
    )
    assert blocks[0]["text"] == "1801 8,893"


@pytest.mark.unit
def test_cells_separate_with_bare_tr_under_body():
    blocks = _conv.extract_blocks_from_html(
        _doc("<tr><td>1801</td><td>8,893</td></tr>")
    )
    assert blocks[0]["text"] == "1801 8,893"


@pytest.mark.unit
def test_cell_does_not_fuse_onto_preceding_text():
    # Closing a cell is not enough on its own: a cell can follow ordinary text
    # rather than another cell. A nested table inside a cell that already held
    # text ran `x` and `i` together as `xi`, so the boundary is emitted on both
    # ends of a cell. (#128)
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<table><tr><td>x<table><tr><td>i</td><td>j</td></tr></table></td>"
            "<td>y</td></tr></table>"
        )
    )
    assert blocks[0]["text"] == "x i j y"


@pytest.mark.unit
def test_cells_separate_through_thead_and_tbody():
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
            "<tbody><tr><td>c</td><td>d</td></tr></tbody></table>"
        )
    )
    assert [b["text"] for b in blocks] == ["A B", "c d"]


@pytest.mark.unit
def test_cell_does_not_fuse_onto_following_text():
    # The mirror of the preceding-text case, and the reason the boundary is
    # emitted on both ends rather than one. Removing either half leaves a
    # distinct fusion here: opening-only gives 'atail b', closing-only gives
    # 'a tailb'. (#128)
    blocks = _conv.extract_blocks_from_html(
        _doc("<table><tr><td>a</td>tail<td>b</td></tr></table>")
    )
    assert blocks[0]["text"] == "a tail b"


# --- one block per table row (#219) -----------------------------------------
#
# A table that is not laid out natively (#251) falls back to rows: each row
# becomes its own paragraph, so a reader can still tell which values belong
# together. Before #221, a whole table was one paragraph:
# "Year A B 1 100 200 2 110 220".

_ISSUE_219_TABLE = (
    "<table>"
    "<tr><th>Year</th><th>A</th><th>B</th></tr>"
    "<tr><td>1</td><td>100</td><td>200</td></tr>"
    "<tr><td>2</td><td>110</td><td>220</td></tr>"
    "</table>"
)


@pytest.mark.unit
def test_each_table_row_is_its_own_block():
    blocks = _conv.extract_blocks_from_html(_doc(_ISSUE_219_TABLE))
    assert [b["text"] for b in blocks] == ["Year A B", "1 100 200", "2 110 220"]


@pytest.mark.unit
def test_table_inside_a_div_is_split_by_row():
    # A <div> whose only child is a table looked like a leaf block, and the
    # leaf branch walks its whole subtree inline — so the table has to count
    # as a block child, not just be handled when it is walked directly.
    blocks = _conv.extract_blocks_from_html(_doc(f"<div>{_ISSUE_219_TABLE}</div>"))
    assert [b["text"] for b in blocks] == ["Year A B", "1 100 200", "2 110 220"]


@pytest.mark.unit
def test_text_around_a_table_stays_in_its_own_blocks():
    blocks = _conv.extract_blocks_from_html(
        _doc("<p>Before.</p><table><tr><td>a</td><td>b</td></tr></table><p>After.</p>")
    )
    assert [b["text"] for b in blocks] == ["Before.", "a b", "After."]


@pytest.mark.unit
def test_table_caption_is_its_own_block():
    blocks = _conv.extract_blocks_from_html(
        _doc("<table><caption>Census</caption><tr><td>a</td><td>b</td></tr></table>")
    )
    assert [b["text"] for b in blocks] == ["Census", "a b"]


@pytest.mark.unit
def test_anchor_on_a_cell_lands_in_its_row():
    # Rows are separate blocks now, so a link into a cell resolves to the
    # row holding it, at the cell's offset within that row (#130).
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<table><tr><td>a</td><td>b</td></tr>"
            '<tr><td>c</td><td id="x">d</td></tr></table>'
        ),
        base_href="ch.xhtml",
    )
    assert blocks[0]["anchor_ids"] == []
    assert blocks[1]["anchor_ids"] == ["x"]
    assert blocks[1]["anchor_offsets"] == {"ch.xhtml#x": 2}


@pytest.mark.unit
def test_a_nested_table_marked_as_contents_is_discarded_like_any_listing():
    # Inside a container, a table was walked inline, so the #132
    # contents-listing check never saw it and a `class="toc"` table printed a
    # duplicate listing. (Directly under <body> it was already checked.) As a
    # block it goes through the same check as a <div> or <ul>.
    blocks = _conv.extract_blocks_from_html(
        _doc(
            '<div><table class="toc"><tr><td>Chapter I</td><td>1</td></tr></table>'
            "<p>Body.</p></div>"
        )
    )
    assert [b["text"] for b in blocks] == ["Body."]


@pytest.mark.unit
def test_tables_seen_counts_each_extracted_table():
    # Extraction time only: a table kept here can still be discarded later
    # with a contents page, which the warning tests below cover.
    seen = []
    _conv.extract_blocks_from_html(
        _doc(
            f"{_ISSUE_219_TABLE}<p>x</p>{_ISSUE_219_TABLE}"
            '<table class="toc"><tr><td>Chapter I</td></tr></table>'
            '<table hidden="hidden"><tr><td>gone</td></tr></table>'
        ),
        tables_seen=seen,
    )
    assert len(seen) == 2


def _table_book(*bodies):
    spine = []
    for i, body in enumerate(bodies):
        item = _SpineItem(f"ch{i}.xhtml", "")
        item.data = _xhtml_raw(body)
        spine.append(item)
    return _OEBBook(spine=spine, toc=[])


@pytest.mark.unit
def test_flattened_tables_are_warned_about_once_per_book():
    log = _silent_log()
    log.warn = MagicMock()
    oeb = _table_book(
        f"<p>One.</p>{_ISSUE_219_TABLE}{_ISSUE_219_TABLE}",
        f"<p>Two.</p>{_ISSUE_219_TABLE}",
    )
    extract_chapters_from_oeb(oeb, log)
    warnings = [str(c) for c in log.warn.call_args_list]
    table_warnings = [w for w in warnings if "table" in w.lower()]
    assert len(table_warnings) == 1, warnings
    assert "3 tables" in table_warnings[0]
    assert "2 files" in table_warnings[0]


def _table_warnings(log):
    return [str(c) for c in log.warn.call_args_list if "table" in str(c).lower()]


def _contents_book(*chapters):
    """A book whose TOC names each (title, body) chapter, one file each."""
    oeb = _table_book(*(body for _, body in chapters))
    oeb.toc = [_TOCNode(title, f"ch{i}.xhtml") for i, (title, _) in enumerate(chapters)]
    return oeb


_METADATA = {"title": "Table Book", "author": "Table Author"}


@pytest.mark.unit
def test_a_table_on_a_discarded_contents_page_is_not_counted():
    # Many Gutenberg books print their contents listing as a <table>. The
    # page titled "Contents" is rebuilt from the chapter titles after
    # extraction and its blocks dropped, so the table is never written. It
    # was counted anyway: in 28 of the 63 corpus books that warned, every
    # counted table had been discarded.
    log = _silent_log()
    log.warn = MagicMock()
    oeb = _contents_book(
        ("Contents", f"<p>Listing.</p>{_ISSUE_219_TABLE}"),
        ("Chapter One", f"<p>One.</p>{_ISSUE_219_TABLE}"),
    )
    extract_chapters_from_oeb(oeb, log, metadata=_METADATA)
    warnings = _table_warnings(log)
    assert len(warnings) == 1, warnings
    assert "1 table in 1 file" in warnings[0]


@pytest.mark.unit
def test_no_table_warning_when_every_table_is_discarded():
    log = _silent_log()
    log.warn = MagicMock()
    oeb = _contents_book(
        ("Contents", f"<p>Listing.</p>{_ISSUE_219_TABLE}"),
        ("Chapter One", "<p>One.</p>"),
    )
    extract_chapters_from_oeb(oeb, log, metadata=_METADATA)
    assert _table_warnings(log) == []


@pytest.mark.unit
def test_no_table_warning_without_tables():
    log = _silent_log()
    log.warn = MagicMock()
    extract_chapters_from_oeb(_table_book("<p>One.</p>"), log)
    assert not [c for c in log.warn.call_args_list if "table" in str(c).lower()]


_WIDE_TABLE = (
    "<table><tr>" + "".join(f"<td>c{i}</td>" for i in range(25)) + "</tr></table>"
)


@pytest.mark.unit
def test_a_table_that_falls_back_is_warned_as_a_fallback():
    # Since #251 most tables are laid out natively, so the warning names only
    # the ones that fell back; "KFX output has no table layout" was 5.8.8's.
    log = _silent_log()
    log.warn = MagicMock()
    oeb = _table_book(f"<p>One.</p>{_WIDE_TABLE}{_ISSUE_219_TABLE}")
    extract_chapters_from_oeb(oeb, log, native_tables=True)
    warnings = _table_warnings(log)
    assert len(warnings) == 1, warnings
    assert "1 table in 1 file" in warnings[0]
    assert "could not be laid out as a Kindle table" in warnings[0]
    assert "no table layout" not in warnings[0]


@pytest.mark.unit
def test_with_native_tables_off_the_warning_says_so():
    log = _silent_log()
    log.warn = MagicMock()
    oeb = _table_book(f"<p>One.</p>{_ISSUE_219_TABLE}")
    extract_chapters_from_oeb(oeb, log, native_tables=False)
    warnings = _table_warnings(log)
    assert len(warnings) == 1, warnings
    assert "native tables are turned off" in warnings[0]
    assert "no table layout" not in warnings[0]


# --- note anchors between table rows (#221, #223) ---------------------------
#
# Calibre's MOBI→EPUB output lays out notes as a table and puts each note's
# link target *after* its row: `<tr>note 1</tr><a id="n1"/><tr>note 2</tr>…`,
# with nothing before the first row and an anchor after the last. An anchor
# with no text of its own carries forward to the next block, so every note link
# landed on the next note — 859 of 871 in the book behind #223, and on a
# Paperwhite reference 2 opened the page starting at note 3. Before #221 the
# whole table was one paragraph, which hid it.


def _row_anchors(blocks):
    return [(b["text"], b["anchor_ids"]) for b in blocks]


@pytest.mark.unit
def test_an_anchor_after_each_row_belongs_to_that_row():
    blocks = _conv.extract_blocks_from_html(
        _doc(
            '<table><tr><td>1.</td><td>First.</td></tr><a id="n1"></a>'
            '<tr><td>2.</td><td>Second.</td></tr><a id="n2"></a></table>'
            "<p>After.</p>"
        ),
        base_href="notes.xhtml",
    )
    assert _row_anchors(blocks) == [
        ("1. First.", ["n1"]),
        ("2. Second.", ["n2"]),
        ("After.", []),
    ]
    # At the row's start, so a link lands on the note's own first line.
    assert blocks[1]["anchor_offsets"] == {"notes.xhtml#n2": 0}


@pytest.mark.unit
def test_an_anchor_after_each_row_belongs_to_that_row_inside_tbody():
    blocks = _conv.extract_blocks_from_html(
        _doc(
            '<table><tbody><tr><td>1.</td><td>First.</td></tr><a id="n1"></a>'
            '<tr><td>2.</td><td>Second.</td></tr><a id="n2"></a></tbody></table>'
        )
    )
    assert _row_anchors(blocks) == [("1. First.", ["n1"]), ("2. Second.", ["n2"])]


@pytest.mark.unit
def test_an_anchor_before_each_row_still_belongs_to_the_next_row():
    # The other layout (#225's reproduction): each anchor precedes its row.
    # Carrying forward is already right for it and must stay so.
    blocks = _conv.extract_blocks_from_html(
        _doc(
            '<table><a id="n1"></a><tr><td>1.</td><td>First.</td></tr>'
            '<a id="n2"></a><tr><td>2.</td><td>Second.</td></tr></table>'
        )
    )
    assert _row_anchors(blocks) == [("1. First.", ["n1"]), ("2. Second.", ["n2"])]


@pytest.mark.unit
def test_an_anchor_only_after_the_last_row_still_carries_forward():
    # A table's only anchor, just inside </table>, usually names what comes
    # next — here the next chapter. Moving it onto the last row put that row in
    # the next chapter and printed the chapter heading twice (#221 review).
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<p>Some text.</p><table><tr><td>Year</td><td>Pop</td></tr>"
            '<tr><td>1811</td><td>12,289</td></tr><a id="ch2"></a></table>'
            "<h2>Chapter 2</h2>"
        )
    )
    assert _row_anchors(blocks) == [
        ("Some text.", []),
        ("Year Pop", []),
        ("1811 12,289", []),
        ("Chapter 2", ["ch2"]),
    ]


@pytest.mark.unit
def test_two_anchors_only_after_the_last_row_still_carry_forward():
    # "At least two anchors" is not the signal: both of these sit past the
    # table. What marks the notes layout is an anchor *between* two rows.
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<table><tr><td>a</td></tr><tr><td>b</td></tr>"
            '<a id="x"></a><a id="y"></a></table><h2>Next</h2>'
        )
    )
    assert _row_anchors(blocks) == [("a", []), ("b", []), ("Next", ["x", "y"])]


@pytest.mark.unit
def test_a_lone_anchor_between_rows_still_carries_forward():
    # Nothing before the first row or after the last says which way the
    # table runs, so the long-standing rule holds.
    blocks = _conv.extract_blocks_from_html(
        _doc('<table><tr><td>a</td></tr><a id="x"></a><tr><td>b</td></tr></table>')
    )
    assert _row_anchors(blocks) == [("a", []), ("b", ["x"])]


# --- a row's alignment from its cells (#224) ---------------------------------


def _inherited_align(elem):
    # Stands in for calibre's computed text-align: the nearest class naming
    # an alignment, on the element or an ancestor, as inheritance would give.
    for e in [elem, *elem.iterancestors()]:
        if e.get("class") in ("left", "center", "right", "justify"):
            return {"text-align": e.get("class")}
    return {}


def _row_aligns(html):
    blocks = _conv.extract_blocks_from_html(_doc(html), style_resolver=_inherited_align)
    return [(b["text"], b["block_style"]["align"]) for b in blocks]


@pytest.mark.unit
@pytest.mark.parametrize(
    "html, expected",
    [
        # Every cell centred in a left table: the row is centred (pg22210's
        # picture tables).
        (
            '<table class="left"><tr><td class="center">a</td>'
            '<td class="center">b</td></tr></table>',
            [("a b", "center")],
        ),
        # Left cells in a centred table: the row follows the cells.
        (
            '<table class="center"><tr><td class="left">a</td>'
            '<td class="left">b</td></tr></table>',
            [("a b", "left")],
        ),
        # Cells that inherit agree with the row, so nothing changes.
        (
            '<table class="right"><tr><td>a</td><td>b</td></tr></table>',
            [("a b", "right")],
        ),
        # Mixed cells keep the row's alignment.
        (
            '<table class="center"><tr><td class="left">a</td>'
            '<td class="right">b</td></tr></table>',
            [("a b", "center")],
        ),
        # An empty spacer cell has nothing to align, so it doesn't vote.
        (
            '<table class="left"><tr><td class="center">a</td><td> </td>'
            '<td class="center">b</td></tr></table>',
            [("a b", "center")],
        ),
        # Each row decides on its own.
        (
            '<table class="left"><tr><td class="center">a</td></tr>'
            '<tr><td class="right">b</td><td class="left">c</td></tr></table>',
            [("a", "center"), ("b c", "left")],
        ),
        # No alignment anywhere: still none.
        ("<table><tr><td>a</td><td>b</td></tr></table>", [("a b", None)]),
    ],
    ids=[
        "all-center",
        "all-left-in-center",
        "inherited",
        "mixed",
        "empty-spacer",
        "per-row",
        "unset",
    ],
)
def test_a_row_takes_its_cells_alignment_when_they_agree(html, expected):
    assert _row_aligns(html) == expected


@pytest.mark.unit
def test_a_cell_holding_only_an_image_votes():
    # pg22210's picture rows: a centred cell with an image is not a spacer.
    blocks = _conv.extract_blocks_from_html(
        _doc(
            '<table class="left"><tr><td class="center"><img src="a.png"/></td>'
            '<td class="right">name</td></tr></table>'
        ),
        style_resolver=_inherited_align,
    )
    rows = [b for b in blocks if b.get("text")]
    assert [b["block_style"]["align"] for b in rows] == ["left"]


# --- native table eligibility (#219) ----------------------------------------


def _first_table(html):
    return next(e for e in _doc(html).iter() if _conv._local_tag(e.tag) == "table")


@pytest.mark.unit
def test_a_plain_table_goes_native():
    assert _conv._table_is_native(_first_table(_ISSUE_219_TABLE))


@pytest.mark.unit
def test_thead_tbody_tfoot_colspan_rowspan_go_native():
    assert _conv._table_is_native(
        _first_table(
            "<table><thead><tr><th colspan='2'>H</th></tr></thead>"
            "<tbody><tr><td rowspan='2'>a</td><td>b</td></tr><tr><td>c</td></tr></tbody>"
            "<tfoot><tr><td>f</td><td>g</td></tr></tfoot></table>"
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        "<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>",
        '<table><tr><td><video src="a.mp4"></video></td></tr></table>',
        "<table><tr><td><svg/></td></tr></table>",
        "<table><caption>only a caption</caption></table>",
        "<table><td>cell with no row</td></table>",
        "<table><tr></tr></table>",
    ],
    ids=[
        "nested",
        "video",
        "svg",
        "no-rows",
        "cell-outside-row",
        "no-cells",
    ],
)
def test_tables_that_fall_back_to_rows(html):
    assert not _conv._table_is_native(_first_table(html))


@pytest.mark.unit
def test_a_cell_over_the_chunk_size_falls_back():
    # The generator cuts text at CHUNK_SIZE (2,000); inside a table that cut
    # would turn one cell into two and shift every later column (#226).
    long_cell = "x" * (_conv._MAX_NATIVE_CELL_CHARS + 1)
    assert not _conv._table_is_native(
        _first_table(f"<table><tr><td>{long_cell}</td></tr></table>")
    )


@pytest.mark.unit
def test_max_native_cell_chars_matches_the_generator_chunk_size():
    from kfxgen.native_generator import NativeKFXGenerator

    assert _conv._MAX_NATIVE_CELL_CHARS == NativeKFXGenerator.CHUNK_SIZE


@pytest.mark.unit
def test_a_cell_at_exactly_the_chunk_size_stays_native():
    # Boundary case: exactly at the limit stays native.
    cell_at_limit = "x" * _conv._MAX_NATIVE_CELL_CHARS
    assert _conv._table_is_native(
        _first_table(f"<table><tr><td>{cell_at_limit}</td></tr></table>")
    )


@pytest.mark.unit
def test_a_cell_with_line_breaks_counts_them_in_length():
    # itertext() doesn't include <br/>, but the converter turns each into a
    # newline. A cell of (MAX - 10) chars + 20 <br/> would normalize to 2010
    # chars and must fall back (#226).
    text = "x" * (_conv._MAX_NATIVE_CELL_CHARS - 10)
    br_tags = "".join("<br/>" for _ in range(20))
    assert not _conv._table_is_native(
        _first_table(f"<table><tr><td>{text}{br_tags}</td></tr></table>")
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "cell, native",
    [
        ("<td><p>a</p><p>b</p></td>", True),
        ("<td><h5>1</h5><p>a</p><span>5</span><p>b</p></td>", True),
        ("<td><div><p>a</p></div></td>", True),
        ("<td><ul><li>a</li><li>b</li></ul></td>", True),
        (f"<td><p>{'x' * 2001}</p><p>b</p></td>", False),
        (f"<td><p>a</p>{'x' * 2001}</td>", False),
        (f"<td><p>{'x' * 1500}</p><p>{'y' * 1500}</p></td>", True),
    ],
    ids=[
        "two-p",
        "heading-loose",
        "wrapped-p",
        "list",
        "long-p",
        "long-loose",
        "long-cell-short-p",
    ],
)
def test_a_cell_holding_several_blocks_stays_native(cell, native):
    # #261: 131 Gutenberg tables fell back for this alone, and the rows build
    # ran each poem in them into one paragraph. The generator's 2,000-character
    # cut applies to each paragraph of such a cell, not to the whole cell.
    html = f"<table><tr>{cell}<td>z</td></tr></table>"
    assert _conv._table_is_native(_first_table(html)) is native


def _native_table(body):
    blocks = extract_blocks_from_html(_doc(body), native_tables=True)
    (table,) = [b for b in blocks if b.get("type") == "table"]
    return table, blocks


def _cell(table, r, c):
    return table["table"]["rows"][r]["cells"][c]


@pytest.mark.unit
def test_a_multi_block_cell_keeps_each_block_as_a_paragraph():
    # pg21053's poem tables: a heading, verse lines, line numbers between
    # them. The rows build ran all of it into one paragraph (#261).
    table, _ = _native_table(
        "<table><tr><td><h5>1</h5><h5>Mein.</h5><p>Du bist mein,</p>"
        "<span>5</span><p>ich bin dein.</p></td></tr></table>"
    )
    cell = _cell(table, 0, 0)
    assert [p["text"] for p in cell["paragraphs"]] == [
        "1",
        "Mein.",
        "Du bist mein,",
        "5",
        "ich bin dein.",
    ]
    assert cell["text"] == "1 Mein. Du bist mein, 5 ich bin dein."
    assert table["text"] == "1 Mein. Du bist mein, 5 ich bin dein."


@pytest.mark.unit
def test_a_one_block_cell_is_unchanged():
    table, _ = _native_table("<table><tr><td><p>only</p></td></tr></table>")
    assert "paragraphs" not in _cell(table, 0, 0)
    assert _cell(table, 0, 0)["text"] == "only"


@pytest.mark.unit
def test_loose_text_around_blocks_in_a_cell_is_kept_in_order():
    table, _ = _native_table(
        "<table><tr><td>Lead <p>x</p> tail<p>y</p></td></tr></table>"
    )
    assert [p["text"] for p in _cell(table, 0, 0)["paragraphs"]] == [
        "Lead",
        "x",
        "tail",
        "y",
    ]


@pytest.mark.unit
def test_a_list_in_a_cell_keeps_its_markers_and_does_not_leak():
    table, blocks = _native_table(
        "<table><tr><td><ol><li>a</li><li>b</li></ol></td><td>c</td></tr></table>"
        "<p>after</p>"
    )
    assert [p["text"] for p in _cell(table, 0, 0)["paragraphs"]] == ["1. a", "2. b"]
    assert _cell(table, 0, 1)["text"] == "c"
    assert blocks[-1]["text"] == "after"


@pytest.mark.unit
def test_ids_in_a_paragraph_cell_land_on_their_paragraph():
    table, _ = _native_table(
        '<table><tr><td id="cell"><p>a</p><p id="two">b</p></td></tr></table>'
    )
    paras = _cell(table, 0, 0)["paragraphs"]
    assert "cell" in paras[0]["anchor_ids"]
    assert "two" in paras[1]["anchor_ids"]
    assert {"cell", "two"} <= set(table["anchor_ids"])
    assert "cell" in _conv._table_start_ids(table)


@pytest.mark.unit
def test_a_background_picture_in_a_cell_is_its_own_paragraph():
    # #267 review: the body walker draws an empty no-repeat background as a
    # picture (#168). In a cell it once came back as a "paragraph" holding the
    # raw image token beside text. The table stays native and the picture is
    # a paragraph of its own, which the generator writes as an image (#262).
    def resolver(elem):
        if elem.get("class") == "pic":
            return {
                "background-image": "url(img.png)",
                "background-repeat": "no-repeat",
            }
        return {}

    blocks = extract_blocks_from_html(
        _doc(
            '<table><tr><td><div class="pic"></div><p>Caption.</p></td>'
            "<td>side</td></tr></table>"
        ),
        style_resolver=resolver,
        native_tables=True,
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    paras = [p["text"] for p in table["table"]["rows"][0]["cells"][0]["paragraphs"]]
    assert [IMG_TOKEN_RE.sub("[img]", t) for t in paras] == ["[img]", "Caption."]


@pytest.mark.unit
def test_an_anchor_pending_before_a_table_stays_out_of_its_cells():
    table, _ = _native_table(
        '<a id="pre"></a><table><tr><td><p>a</p><p>b</p></td></tr></table>'
    )
    assert "pre" not in _cell(table, 0, 0)["paragraphs"][0]["anchor_ids"]
    assert "pre" in table["table"]["anchor_ids"]


@pytest.mark.unit
def test_a_list_marker_pending_around_a_table_stays_out_of_its_cells():
    blocks = extract_blocks_from_html(
        _doc("<ol><li><table><tr><td><p>a</p><p>b</p></td></tr></table></li></ol>"),
        native_tables=True,
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    assert [p["text"] for p in _cell(table, 0, 0)["paragraphs"]] == ["a", "b"]


@pytest.mark.unit
def test_a_contents_listing_in_a_cell_is_recorded_at_the_table():
    at = []
    blocks = extract_blocks_from_html(
        _doc(
            "<p>Intro</p><table><tr><td><p>a</p>"
            '<div class="toc"><p>Chapter 1</p></div></td></tr></table><p>After</p>'
        ),
        native_tables=True,
        nav_listing_at=at,
    )
    table_index = next(i for i, b in enumerate(blocks) if b.get("type") == "table")
    assert [index for index, _ in at] == [table_index]


@pytest.mark.unit
def test_paragraphs_in_a_cell_get_anchor_keys():
    table, blocks = _native_table(
        '<table><tr><td><p>a</p><p id="two">b</p></td></tr></table>'
    )
    _conv._attach_anchor_keys(blocks, "ch.xhtml")
    assert _cell(table, 0, 0)["paragraphs"][1]["anchor_keys"] == ["ch.xhtml#two"]


@pytest.mark.unit
def test_a_cell_holding_exactly_one_block_stays_native():
    # A cell with one block-level child is fine; only two or more trigger
    # fallback.
    assert _conv._table_is_native(
        _first_table("<table><tr><td><p>x</p></td></tr></table>")
    )


def _row(n, cell="<td>x</td>"):
    return "<tr>" + cell * n + "</tr>"


@pytest.mark.unit
@pytest.mark.parametrize(
    "html, native",
    [
        (f"<table>{_row(24)}</table>", True),
        (f"<table>{_row(25)}</table>", False),
        # One wide row is enough.
        (f"<table>{_row(3)}{_row(25)}{_row(3)}</table>", False),
        # colspan counts: 23 cells, one spanning 2, is 24 columns; 3 is 25.
        (f"<table>{_row(22)[:-5]}<td colspan='2'>x</td></tr></table>", True),
        (f"<table>{_row(22)[:-5]}<td colspan='3'>x</td></tr></table>", False),
        (f"<table>{_row(1, '<th colspan="25">H</th>')}{_row(3)}</table>", False),
        # A cell carried down by rowspan takes a column in the next row.
        (
            f"<table><tr><td rowspan='2'>a</td>{'<td>x</td>' * 23}</tr>"
            f"{_row(24)}</table>",
            False,
        ),
        (
            f"<table><tr><td rowspan='2'>a</td>{'<td>x</td>' * 23}</tr>"
            f"{_row(23)}</table>",
            True,
        ),
        # rowspan ends with its row group.
        (
            f"<table><thead><tr><td rowspan='5'>a</td>{'<td>x</td>' * 23}</tr>"
            f"</thead><tbody>{_row(24)}</tbody></table>",
            True,
        ),
    ],
    ids=[
        "24-columns",
        "25-columns",
        "one-wide-row",
        "colspan-to-24",
        "colspan-to-25",
        "header-colspan-25",
        "rowspan-carry-to-25",
        "rowspan-carry-to-24",
        "rowspan-ends-at-group",
    ],
)
def test_a_table_wider_than_24_columns_falls_back(html, native):
    # #251's gate found 24 columns unreadable on the Voyage (5.13.6) and the
    # Oasis (5.18.2) without the table-viewer properties. With them (#254),
    # both read every table up to 24 columns; wider ones are untested and
    # their text would be too small to read, so they keep rows.
    assert _conv._table_is_native(_first_table(html)) is native


def _block(html, **kw):
    captions, table, trailing = _conv._table_block(_first_table(html), **kw)
    return captions, table, trailing


def _cells(table):
    return [[c["text"] for c in r["cells"]] for r in table["table"]["rows"]]


@pytest.mark.unit
def test_table_block_keeps_rows_and_cells():
    _, table, _ = _block(_ISSUE_219_TABLE)
    assert table["type"] == "table"
    assert _cells(table) == [
        ["Year", "A", "B"],
        ["1", "100", "200"],
        ["2", "110", "220"],
    ]
    assert table["text"] == "Year A B\n1 100 200\n2 110 220"
    assert [r["group"] for r in table["table"]["rows"]] == ["body"] * 3


@pytest.mark.unit
def test_table_block_row_groups_header_cells_and_spans():
    _, table, _ = _block(
        "<table><thead><tr><th colspan='2'>H</th></tr></thead>"
        "<tbody><tr><td rowspan='2'>a</td><td>b</td></tr><tr><td>c</td></tr></tbody>"
        "<tfoot><tr><td>f</td><td>g</td></tr></tfoot></table>"
    )
    rows = table["table"]["rows"]
    assert [r["group"] for r in rows] == ["head", "body", "body", "foot"]
    head = rows[0]["cells"][0]
    assert (head["header"], head["colspan"], head["rowspan"]) == (True, 2, 1)
    assert rows[1]["cells"][0]["rowspan"] == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw, expected", [("0", 1), ("-3", 1), ("x", 1), ("5000", 1000), (" 3 ", 3)]
)
def test_span_attributes_are_clamped(raw, expected):
    _, table, _ = _block(f"<table><tr><td colspan='{raw}'>a</td></tr></table>")
    assert table["table"]["rows"][0]["cells"][0]["colspan"] == expected
    # A rowspan also stops at its row group's last row (QA-3).
    _, table, _ = _block(
        f"<table><tr><td rowspan='{raw}'>a</td></tr><tr><td>b</td></tr>"
        "<tr><td>c</td></tr></table>"
    )
    assert table["table"]["rows"][0]["cells"][0]["rowspan"] == min(expected, 3)


@pytest.mark.unit
def test_rowspan_clamps_to_the_rows_left_in_its_row_group():
    # QA-3: a rowspan past its group's last row reached the Kindle as is
    # (up to 1,000). HTML ends a rowspan at its row group's end.
    _, table, _ = _block(
        "<table><thead><tr><th rowspan='5'>h</th></tr></thead><tbody>"
        "<tr><td rowspan='50'>a</td><td>x</td></tr>"
        "<tr><td rowspan='50'>b</td></tr>"
        "<tr><td>c</td><td rowspan='2'>d</td></tr></tbody>"
        "<tfoot><tr><td rowspan='9'>f</td></tr></tfoot></table>"
    )
    spans = [[c["rowspan"] for c in r["cells"]] for r in table["table"]["rows"]]
    assert spans == [[1], [3, 1], [2], [1, 1], [1]]


@pytest.mark.unit
def test_rows_directly_in_the_table_count_as_their_own_group():
    _, table, _ = _block(
        "<table><tr><td rowspan='9'>a</td></tr><tr><td>b</td></tr>"
        "<tbody><tr><td rowspan='9'>c</td></tr></tbody></table>"
    )
    spans = [[c["rowspan"] for c in r["cells"]] for r in table["table"]["rows"]]
    assert spans == [[2], [1], [1]]


@pytest.mark.unit
def test_cell_emphasis_and_anchor_offsets_are_cell_relative():
    _, table, _ = _block(
        '<table><tr><td>ab</td><td>x <em>cd</em> <a id="k"></a>e</td></tr></table>'
    )
    cell = table["table"]["rows"][0]["cells"][1]
    assert cell["text"] == "x cd e"
    assert cell["spans"] == [(2, 2, frozenset({I}))]
    assert cell["anchor_offsets"]["k"] == 5
    assert "k" in table["anchor_ids"]  # block level too, for chapter assembly


@pytest.mark.unit
def test_notes_layout_anchor_after_row_stays_with_row():
    _, table, _ = _block(
        '<table><tr><td>1.</td><td>First.</td></tr><a id="n1"></a>'
        '<tr><td>2.</td><td>Second.</td></tr><a id="n2"></a></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [["n1"], ["n2"]]


@pytest.mark.unit
def test_anchor_before_each_row_belongs_to_the_next_row():
    _, table, trailing = _block(
        '<table><a id="n1"></a><tr><td>1.</td></tr><a id="n2"></a><tr><td>2.</td></tr></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [["n1"], ["n2"]]
    assert trailing == []


@pytest.mark.unit
def test_an_anchor_only_after_the_last_row_carries_past_the_table():
    _, table, trailing = _block(
        '<table><tr><td>a</td></tr><tr><td>b</td></tr><a id="ch2"></a></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [[], []]
    assert trailing == ["ch2"]


@pytest.mark.unit
def test_caption_becomes_its_own_paragraph():
    captions, table = _caption_and_table(
        "<table><caption>Census</caption><tr><td>a</td></tr></table>"
    )
    assert [b["text"] for b in captions] == ["Census"]
    assert "Census" not in table["text"]


@pytest.mark.unit
def test_table_own_id_is_kept_separately():
    _, table, _ = _block('<table id="t"><tr id="r"><td id="c">a</td></tr></table>')
    assert table["table"]["anchor_ids"] == ["t"]
    assert table["table"]["rows"][0]["anchor_ids"] == ["r"]
    assert table["table"]["rows"][0]["cells"][0]["anchor_ids"] == ["c"]
    assert table["anchor_ids"] == ["t", "r", "c"]


@pytest.mark.unit
@pytest.mark.parametrize("group", ["thead", "tbody", "tfoot"])
def test_row_group_own_id_lands_on_its_first_row(group):
    _, table, _ = _block(
        f'<table><{group} id="g"><tr><td>a</td></tr><tr><td>b</td></tr></{group}></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [["g"], []]
    assert "g" in table["anchor_ids"]


@pytest.mark.unit
def test_caption_own_id_is_on_the_caption_block():
    captions, table = _caption_and_table(
        '<table><caption id="cp">Census <a id="in"></a>now</caption>'
        "<tr><td>a</td></tr></table>",
        base_href="ch.xhtml",
    )
    assert captions[0]["anchor_ids"] == ["cp", "in"]
    assert captions[0]["anchor_offsets"] == {
        "ch.xhtml": 0,
        "ch.xhtml#cp": 0,
        "ch.xhtml#in": 7,
    }


@pytest.mark.unit
def test_empty_caption_keeps_its_id_at_the_table_start():
    # The table's own ids go on its first row in the generator.
    captions, table = _caption_and_table(
        '<table><caption id="cp"></caption><tr><td>a</td></tr></table>'
    )
    assert captions == []
    assert table["table"]["anchor_ids"] == ["cp"]


@pytest.mark.unit
def test_anchor_inside_a_row_but_outside_any_cell_stays_with_the_row():
    _, table, _ = _block('<table><tr><td>1</td><a id="mid"></a><td>2</td></tr></table>')
    assert table["table"]["rows"][0]["anchor_ids"] == ["mid"]
    assert _cells(table) == [["1", "2"]]
    assert "mid" in table["anchor_ids"]


@pytest.mark.unit
def test_anchors_across_two_row_groups_each_stay_with_their_row():
    _, table, trailing = _block(
        '<table><tbody><tr><td>1</td></tr><a id="n1"></a><tr><td>2</td></tr><a id="n2"></a></tbody>'
        '<tbody><tr><td>3</td></tr><a id="n3"></a><tr><td>4</td></tr><a id="n4"></a></tbody></table>'
    )
    assert [r["anchor_ids"] for r in table["table"]["rows"]] == [
        ["n1"],
        ["n2"],
        ["n3"],
        ["n4"],
    ]
    assert trailing == []


@pytest.mark.unit
def test_an_anchor_after_the_last_row_group_carries_past_the_table():
    _, table, trailing = _block(
        '<table><tbody><tr><td>a</td></tr></tbody><a id="x"></a></table>'
    )
    assert table["table"]["rows"][0]["anchor_ids"] == []
    assert trailing == ["x"]


@pytest.mark.unit
def test_rows_with_only_empty_cells_add_no_blank_line_to_the_text():
    _, table, _ = _block(
        "<table><tr><td>a</td></tr><tr><td></td><td></td></tr><tr><td>b</td></tr></table>"
    )
    assert table["text"] == "a\nb"
    assert len(table["table"]["rows"]) == 3


# --- illustrations inside a discarded contents section (#117) ---------------
#
# The source contents section is replaced because its *text* duplicates the
# navigation KFX carries itself. That reasoning does not extend to pictures
# printed in the same region, and two corpus books lost decorative plates to
# it: pg1400 emitted 31 image resources for 32 inline refs, pg37106 204 for
# 206. Both are whole after the fix.


def _img(href, alt=""):
    return _conv._make_img_token(href, alt)


@pytest.mark.unit
def test_contents_section_illustrations_are_kept():
    chapters = [
        {
            "title": "Contents",
            "text": "old toc",
            "blocks": [
                {"text": "Contents"},
                {"text": _img("plate.png", "[Illustration]")},
                {"text": "I. First Chapter    1"},
            ],
        },
        {"title": "Chapter 1", "text": "body one"},
    ]
    _replace_title_page(chapters, {"title": "B", "author": "A"}, _silent_log())
    assert chapters[0]["preserved_images"] == [_img("plate.png", "[Illustration]")]


@pytest.mark.unit
def test_contents_section_text_is_still_discarded():
    # The images survive; the listing text they sat in does not. Losing this
    # distinction would reintroduce the duplicated table of contents that
    # replacing the page exists to remove.
    chapters = [
        {
            "title": "Contents",
            "text": "old toc",
            "blocks": [
                {"text": "I. First Chapter    1"},
                {"text": _img("plate.png")},
            ],
        },
        {"title": "Chapter 1", "text": "body one"},
    ]
    _replace_title_page(chapters, {"title": "B", "author": "A"}, _silent_log())
    contents = chapters[0]
    assert "blocks" not in contents
    assert "I. First Chapter" not in contents["text"]
    assert contents["preserved_images"] == [_img("plate.png")]


@pytest.mark.unit
def test_contents_without_illustrations_sets_no_key():
    # The common case must not grow an empty key, so the generator branch
    # stays untaken for books that never had the problem.
    chapters = [
        {"title": "Contents", "text": "old", "blocks": [{"text": "I. One    1"}]},
        {"title": "Chapter 1", "text": "body"},
    ]
    _replace_title_page(chapters, {"title": "B", "author": "A"}, _silent_log())
    assert "preserved_images" not in chapters[0]


class TestTitleNormalisation:
    """#135: every title lookup in converter.py is `in <frozenset>` against
    `title.lower().strip()`, so a trailing period defeats it. One corpus book
    titles its contents chapter "CONTENTS." and printed all 65 listing blocks
    into the body because `"contents."` is not `"contents"`.

    Normalise once, edge-of-string only — interior punctuation is part of the
    label and must survive."""

    META = {"title": "The Real Title", "author": "Jane Author"}

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("CONTENTS.", "contents"),
            ("Contents:", "contents"),
            ("  Table of Contents  ", "table of contents"),
            ("[Contents]", "contents"),
            ("*Contents*", "contents"),
            ("Table   of\tContents", "table of contents"),
            ("Half-Title Page.", "half-title page"),
            ("Chapter I. The Beginning", "chapter i. the beginning"),
            ("", ""),
            ("...", ""),
        ],
    )
    def test_normalises_edges_only(self, raw, expected):
        assert _normalize_title(raw) == expected

    def test_contents_with_trailing_period_is_rebuilt(self):
        """The pg1998 case: `_replace_title_page` must recognise "CONTENTS."
        and hand it to the rebuild, replacing the source listing."""
        chapters = [
            {"title": "CONTENTS.", "text": "old listing", "blocks": [{"text": "x"}]},
            {"title": "Chapter 1", "text": "body one"},
        ]
        _replace_title_page(chapters, self.META, _silent_log())
        contents = chapters[0]
        assert "toc_links" in contents, "contents chapter was never rebuilt"
        assert [link["text"] for link in contents["toc_links"]] == ["Chapter 1"]
        assert "blocks" not in contents

    def test_punctuated_title_page_is_replaced(self):
        chapters = [{"title": "Title Page:", "text": "old"}]
        _replace_title_page(chapters, self.META, _silent_log())
        assert chapters[0]["text"] == "The Real Title\n\nby\n\nJane Author"

    def test_punctuated_half_title_is_replaced(self):
        chapters = [{"title": "Half Title Page.", "text": "old"}]
        _replace_title_page(chapters, self.META, _silent_log())
        assert chapters[0]["text"] == "The Real Title"
        assert chapters[0]["_omit_title_heading"] is True

    def test_punctuated_small_text_chapter_gets_small_font(self):
        chapters = [{"title": "DEDICATION.", "text": "for someone"}]
        _replace_title_page(chapters, self.META, _silent_log())
        assert chapters[0]["font_size"] == _conv.SMALL_FONT_SIZE

    def test_punctuated_skip_title_excluded_from_listing(self):
        chapters = [
            {"title": "Contents", "text": "old"},
            {"title": "COVER.", "text": "c"},
            {"title": "Chapter 1", "text": "body"},
        ]
        _replace_title_page(chapters, self.META, _silent_log())
        listed = [link["text"] for link in chapters[0]["toc_links"]]
        assert "COVER." not in listed
        assert listed == ["Chapter 1"]


class TestLeadingChapterTitleRejectsImageToken:
    """#133: `_leading_chapter_title` titles front matter from its first
    block, guarding only on length and absence of a newline. An IMG token is
    short and has no newline, so a leading cover image became the chapter's
    title — and every one of the 39 corpus books that builds a contents page
    then listed that token as its first entry. Tokens are stripped from the
    candidate before the guards run, so an image beside real words keeps the
    words (matching PR #138)."""

    META = {"title": "The Real Title", "author": "Jane Author"}

    def test_bare_image_token_head_falls_back_to_front_matter(self):
        token = _conv._make_img_token("cover.jpg", "")
        assert _leading_chapter_title([{"text": token}]) == "Front Matter"

    def test_image_token_with_alt_text_falls_back(self):
        token = _conv._make_img_token("cover.jpg", "Cover")
        assert _leading_chapter_title([{"text": token}]) == "Front Matter"

    def test_token_beside_words_keeps_the_words(self):
        """Strip the token, don't reject the block. An image sits beside real
        words often enough that rejecting on sight would discard good titles:
        `<h2><img/>Preface</h2>` is one block, and the chapter is Preface."""
        token = _conv._make_img_token("cover.jpg", "")
        assert (
            _leading_chapter_title([{"text": f"{token} Frontispiece"}])
            == "Frontispiece"
        )

    def test_plain_short_text_is_still_used_as_title(self):
        assert _leading_chapter_title([{"text": "Copyright 2026"}]) == "Copyright 2026"

    def test_cover_plus_title_line_head_is_titled_front_matter(self):
        """The corpus shape: a front-matter page that opens with the cover
        image and continues with the title/author line. The head survives as a
        chapter (it is not image-only), so its title must not be the token."""
        token = _conv._make_img_token("cover.jpg", "")
        spine = [
            _spine_item(
                "book.xhtml",
                [
                    (token, []),
                    ("The Real Title, by Jane Author", []),
                    ("Chapter I", ["c1"]),
                    ("Body text", []),
                ],
            )
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert chapters[0]["title"] == "Front Matter"
        assert IMG_TOKEN_RE.search(chapters[0]["title"]) is None

    def test_no_contents_entry_carries_an_image_token(self):
        """End to end: the token must not reach `toc_links`, because the
        generator emits `link["text"]` as a chunk and the token is processed
        back into an image reference downstream."""
        token = _conv._make_img_token("cover.jpg", "")
        spine = [
            _spine_item(
                "book.xhtml",
                [
                    (token, []),
                    ("The Real Title, by Jane Author", []),
                    ("Contents", ["toc"]),
                    ("Chapter I", ["c1"]),
                    ("Body text", []),
                ],
            )
        ]
        toc = [
            {"title": "Contents", "href": "book.xhtml#toc"},
            {"title": "I", "href": "book.xhtml#c1"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        _replace_title_page(chapters, self.META, _silent_log())
        contents = next(c for c in chapters if c.get("toc_links"))
        for link in contents["toc_links"]:
            assert IMG_TOKEN_RE.search(link["text"]) is None, (
                f"contents entry carries an image token: {link['text']!r}"
            )


class TestSyntheticFrontMatterLabel:
    """#133 follow-on. "Front Matter" is a label kfxgen invents when a
    front-matter page has no usable heading of its own — it is never text the
    book contains. Rejecting image-token titles makes it the label for every
    book that opens with a cover image, so it must not reach the page.

    Same failure mode as #107, where the structural label "Half Title Page"
    was printed as visible text."""

    META = {"title": "The Real Title", "author": "Jane Author"}

    def _front_matter_book(self):
        token = _conv._make_img_token("cover.jpg", "")
        spine = [
            _spine_item(
                "book.xhtml",
                [
                    (token, []),
                    ("The Real Title, by Jane Author", []),
                    ("Contents", ["toc"]),
                    ("Chapter I", ["c1"]),
                    ("Body text", []),
                ],
            )
        ]
        toc = [
            {"title": "Contents", "href": "book.xhtml#toc"},
            {"title": "I", "href": "book.xhtml#c1"},
        ]
        return _assemble_chapters_by_coordinate(spine, toc, _silent_log())

    def test_heading_is_suppressed(self):
        chapters = self._front_matter_book()
        head = chapters[0]
        assert head["title"] == _conv.LEADING_TITLE_FALLBACK
        assert head.get("_omit_title_heading") is True, (
            "the invented label would render as visible text on the page"
        )

    def test_real_leading_title_still_renders_as_heading(self):
        """Control: a genuine heading taken from the book keeps its heading."""
        spine = [
            _spine_item(
                "book.xhtml",
                [("Copyright 2026", []), ("Chapter I", ["c1"]), ("Body", [])],
            )
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert chapters[0]["title"] == "Copyright 2026"
        assert not chapters[0].get("_omit_title_heading")

    def test_not_listed_as_a_contents_entry(self):
        chapters = self._front_matter_book()
        _replace_title_page(chapters, self.META, _silent_log())
        contents = next(c for c in chapters if c.get("toc_links"))
        listed = [link["text"] for link in contents["toc_links"]]
        assert _conv.LEADING_TITLE_FALLBACK not in listed
        assert listed == ["I"]


def _body_markup(markup):
    """Build an XHTML element from raw body markup."""
    src = (
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        f"{markup}"
        "</body></html>"
    )
    return etree.fromstring(src)


class TestNavigationListingDiscardedByMarkup:
    """#132: the contents discard was keyed on a chapter's *title*, so a
    listing that is not its own chapter — a `<div class="toc">` inside the
    front-matter page — was never recognised and printed in full. Seven corpus
    books ship a visible duplicate listing for this reason.

    Recognise the listing from its markup instead. KFX carries navigation
    itself and kfxgen builds its own contents page from chapter titles, so the
    source listing is redundant wherever it appears."""

    def _texts(self, markup):
        return [b["text"] for b in extract_blocks_from_html(_body_markup(markup))]

    def test_div_toc_listing_is_discarded(self):
        """The pg64317 shape: a toc div inside a larger front-matter page."""
        texts = self._texts(
            "<h1>The Real Title</h1>"
            '<div class="toc"><h2>Table of Contents</h2>'
            '<ol><li><a href="#c1">Chapter I</a></li>'
            '<li><a href="#c2">Chapter II</a></li></ol></div>'
            "<p>Real front matter prose.</p>"
        )
        assert "Real front matter prose." in texts
        assert "The Real Title" in texts
        assert not any("Chapter I" in t for t in texts)
        assert not any("Table of Contents" in t for t in texts)

    def test_sibling_p_toc_entries_are_discarded(self):
        """The dominant Gutenberg shape: one `<p class="toc">` per entry."""
        texts = self._texts(
            '<p class="toc">CONTENTS</p>'
            '<p class="toc"><a href="#c1">Chapter I. The Start</a></p>'
            '<p class="toc"><a href="#c2">Chapter II. The Middle</a></p>'
            "<p>Real prose follows.</p>"
        )
        assert texts == ["Real prose follows."]

    def test_table_toc_listing_is_discarded(self):
        """A table listing fuses into one block, so it never shows as a run —
        it has to be caught structurally or not at all."""
        texts = self._texts(
            '<table class="toc"><tr><td><a href="#c1">Chapter I</a></td>'
            "<td>1</td></tr>"
            '<tr><td><a href="#c2">Chapter II</a></td><td>17</td></tr></table>'
            "<p>Real prose follows.</p>"
        )
        assert texts == ["Real prose follows."]

    def test_epub_type_toc_is_discarded(self):
        texts = self._texts(
            '<nav epub:type="toc"><ol><li><a href="#c1">Chapter I</a></li></ol></nav>'
            "<p>Real prose follows.</p>"
        )
        assert texts == ["Real prose follows."]

    def test_role_doc_toc_is_discarded(self):
        texts = self._texts(
            '<div role="doc-toc"><p><a href="#c1">Chapter I</a></p></div>'
            "<p>Real prose follows.</p>"
        )
        assert texts == ["Real prose follows."]

    def test_class_is_matched_as_a_token_not_a_substring(self):
        """A class name like "tocsin" is a word, and a book using it must keep
        its text. Match whitespace-separated class tokens only."""
        texts = self._texts(
            '<p class="tocsin">Ring the alarm.</p>'
            '<p class="nottoc">Still real text.</p>'
            '<p class="toc-entry">Also real.</p>'
        )
        assert texts == ["Ring the alarm.", "Still real text.", "Also real."]

    def test_multi_class_token_still_matches(self):
        texts = self._texts(
            '<p class="indent toc small"><a href="#c1">Chapter I</a></p>'
            "<p>Real prose.</p>"
        )
        assert texts == ["Real prose."]

    def test_image_inside_a_toc_container_is_preserved(self):
        """#117's rule: the reason to discard a listing is that its *text*
        duplicates navigation KFX already carries. That says nothing about a
        plate printed there."""
        texts = self._texts(
            '<div class="toc"><p><a href="#c1">Chapter I</a></p>'
            '<img src="plate.jpg" alt="A plate"/>'
            '<p><a href="#c2">Chapter II</a></p></div>'
            "<p>Real prose.</p>"
        )
        assert any(IMG_TOKEN_RE.search(t) for t in texts), "the plate was discarded"
        assert not any("Chapter I" in t for t in texts)
        assert "Real prose." in texts

    def test_anchor_on_a_discarded_block_carries_forward(self):
        """A TOC entry may point at the listing container itself. Dropping the
        block must not drop the anchor, or that link dies (#51/#53)."""
        blocks = extract_blocks_from_html(
            _body_markup(
                '<div class="toc" id="tocanchor">'
                '<p><a href="#c1">Chapter I</a></p></div>'
                "<p>Real prose.</p>"
            )
        )
        assert [b["text"] for b in blocks] == ["Real prose."]
        assert "tocanchor" in blocks[0]["anchor_ids"]


class _RawSpineItem:
    """Spine item whose body is raw markup, not wrapped in a <p>."""

    def __init__(self, href, markup):
        self.href = href
        self.data = _body_markup(markup)
        self.media_type = "application/xhtml+xml"


_NAV_LISTING = (
    "<h1>Navigation</h1>"
    '<nav epub:type="toc"><ol>'
    '<li><a href="c1.xhtml">Chapter I</a></li>'
    '<li><a href="c2.xhtml">Chapter II</a></li>'
    "</ol></nav>"
)


class TestDiscardedListingBecomesGeneratedContents:
    """#132, second half. Recognising a listing structurally is only half the
    fix: for seven corpus books the listing *was* the contents page, so
    discarding it alone left a near-empty stub and the book lost its contents
    entirely. A source listing is redundant because kfxgen builds its own from
    the real chapter titles — so the discard has to hand off to that rebuild,
    not just delete.

    The same shape reaches kfxgen as a publisher's EPUB 3 navigation document
    sitting in the spine under a title like "Navigation"."""

    META = {"title": "The Real Title", "author": "Jane Author"}

    def _chapters(self, spine, toc_nodes):
        oeb = _OEBBook(spine, [_TOCNode(t, h) for t, h in toc_nodes])
        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        _replace_title_page(chapters, self.META, _silent_log())
        return chapters

    def _body_chapters(self):
        return [
            _RawSpineItem("c1.xhtml", "<p>First chapter prose.</p>"),
            _RawSpineItem("c2.xhtml", "<p>Second chapter prose.</p>"),
        ]

    def test_standalone_nav_document_is_rebuilt_as_contents(self):
        chapters = self._chapters(
            [_RawSpineItem("nav.xhtml", _NAV_LISTING)] + self._body_chapters(),
            [
                ("Navigation", "nav.xhtml"),
                ("Chapter I", "c1.xhtml"),
                ("Chapter II", "c2.xhtml"),
            ],
        )
        nav = chapters[0]
        assert nav.get("toc_links"), (
            "the nav document was discarded and nothing replaced it — "
            "the book has no contents page at all"
        )
        assert [link["text"] for link in nav["toc_links"]] == [
            "Chapter I",
            "Chapter II",
        ]
        # The source listing's own entry text must not survive alongside it.
        assert "Navigation" not in nav.get("text", "")
        # The page needs a heading of its own. "Navigation" named the listing
        # that was replaced and is a structural label, so it must not print
        # (#60/#107) — but suppressing it and adding nothing leaves a bare list
        # of links under no header, while the title-keyed path shows one. Name
        # the rebuilt page for what it now is.
        assert nav["title"] == "Contents"
        assert not nav.get("_omit_title_heading"), (
            "the rebuilt contents page would render with no heading at all"
        )

    def test_book_does_not_get_two_contents_pages(self):
        """pg64317's shape: an inline listing in the front matter *and* a real
        Contents chapter. The rebuild belongs to the Contents chapter; the
        front matter keeps its own prose and gains nothing."""
        front = (
            "<h1>The Real Title</h1>"
            '<div class="toc"><p><a href="c1.xhtml">Chapter I</a></p></div>'
            "<p>Front matter prose that is real content.</p>"
        )
        chapters = self._chapters(
            [
                _RawSpineItem("front.xhtml", front),
                _RawSpineItem("contents.xhtml", "<h1>Contents</h1>"),
            ]
            + self._body_chapters(),
            [
                ("Front Matter", "front.xhtml"),
                ("Contents", "contents.xhtml"),
                ("Chapter I", "c1.xhtml"),
                ("Chapter II", "c2.xhtml"),
            ],
        )
        with_links = [c for c in chapters if c.get("toc_links")]
        assert len(with_links) == 1, (
            f"expected exactly one contents page, got {len(with_links)}"
        )
        assert _normalize_title(with_links[0]["title"]) == "contents"
        front_ch = chapters[0]
        assert "Front matter prose that is real content." in front_ch["text"]
        assert "Chapter I" not in front_ch["text"]

    def test_listing_at_a_file_end_does_not_flag_the_next_chapter(self):
        """A listing records the block index it would have occupied. When it
        sits at the end of a file that index equals the *next* chapter's first
        block, so a naive range test flags both — and since the guard picks the
        first heading-sized flagged chapter, the short chapter after the
        listing gets its content replaced by a contents page."""
        front = (
            "<p>A long stretch of genuine front matter prose that the reader "
            "is meant to see, well beyond a heading in length.</p>"
            '<div class="toc"><p><a href="c1.xhtml">Chapter I</a></p>'
            '<p><a href="c2.xhtml">Chapter II</a></p></div>'
        )
        chapters = self._chapters(
            [
                _RawSpineItem("front.xhtml", front),
                _RawSpineItem("short.xhtml", "<h1>Short</h1>"),
            ]
            + self._body_chapters(),
            [
                ("Preface", "front.xhtml"),
                ("Short", "short.xhtml"),
                ("Chapter I", "c1.xhtml"),
                ("Chapter II", "c2.xhtml"),
            ],
        )
        short = next(c for c in chapters if c["title"] == "Short")
        assert not short.get("toc_links"), (
            "a chapter after the listing was rebuilt as the contents page"
        )
        assert "Short" in short["text"]

    def test_listing_beside_real_prose_does_not_replace_that_prose(self):
        """A page that carries a listing *and* substantial content is not a
        contents page. Drop the listing; never overwrite the content."""
        front = (
            '<div class="toc"><p><a href="c1.xhtml">Chapter I</a></p></div>'
            "<p>A long stretch of genuine front matter prose that the reader "
            "is meant to see, well beyond a heading in length.</p>"
        )
        chapters = self._chapters(
            [_RawSpineItem("front.xhtml", front)] + self._body_chapters(),
            [
                ("Preface", "front.xhtml"),
                ("Chapter I", "c1.xhtml"),
                ("Chapter II", "c2.xhtml"),
            ],
        )
        front_ch = chapters[0]
        assert not front_ch.get("toc_links")
        assert "genuine front matter prose" in front_ch["text"]
        assert "Chapter I" not in front_ch["text"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "first_block, expected",
    [
        ("A Short Heading", "A Short Heading"),
        ("", "Front Matter"),
        ("x" * 61, "Front Matter"),
        ("line one\nline two", "Front Matter"),
    ],
)
def test_leading_chapter_title_keeps_its_existing_rules(first_block, expected):
    """The length, emptiness and newline guards must survive the #133 fix."""
    blocks = [{"text": first_block}]
    assert _leading_chapter_title(blocks) == expected


@pytest.mark.unit
def test_leading_chapter_title_rejects_an_image_token():
    """An image is not a title, however short it is (#133).

    `_make_img_token` builds a single whitespace-free run, so the old
    `len(t) <= 60 and "\\n" not in t` guard admitted it.
    """
    token = _conv._make_img_token("cover.jpg", "")
    assert _leading_chapter_title([{"text": token}]) == "Front Matter"


@pytest.mark.unit
def test_leading_chapter_title_keeps_a_heading_that_carries_an_ornament():
    """An image beside real words must not cost the chapter its title.

    `<h2><img/>Preface</h2>` is one block — an ornament or drop-cap glyph
    followed by the heading text. Rejecting the whole block on sight would
    answer #133 by throwing away a perfectly good title, so the token is
    stripped and the ordinary guards then judge what is left.
    """
    token = _conv._make_img_token("ornament.png", "")
    assert _leading_chapter_title([{"text": f"{token}Preface"}]) == "Preface"


@pytest.mark.unit
def test_leading_chapter_title_keeps_a_caption_beside_a_plate():
    """Same rule with the words after a space, e.g. a captioned frontispiece."""
    token = _conv._make_img_token("plate.jpg", "Frontispiece")
    assert _leading_chapter_title([{"text": f"{token} Frontispiece"}]) == "Frontispiece"


@pytest.mark.unit
def test_leading_chapter_title_rejects_a_block_of_only_images():
    """Stripping must not resurrect the bug: images alone leave no title."""
    a = _conv._make_img_token("one.png", "")
    b = _conv._make_img_token("two.png", "")
    assert _leading_chapter_title([{"text": f"{a}{b}"}]) == "Front Matter"


@pytest.mark.unit
def test_leading_chapter_title_measures_length_after_stripping():
    """The 60-char bound applies to the words, not to the token's overhead."""
    token = _conv._make_img_token("x" * 200, "")
    assert _leading_chapter_title([{"text": f"{token}Preface"}]) == "Preface"


@pytest.mark.unit
def test_generated_contents_never_labels_an_entry_with_an_image():
    """The reader-visible symptom of #133, one level above the cause.

    A contents entry whose label is an image token renders the picture inside
    the listing. Asserted against `_replace_title_page` rather than the helper
    so the fix has to hold along the path that actually produces the page.
    """
    token = _conv._make_img_token("cover.jpg", "")
    chapters = [
        {"title": _leading_chapter_title([{"text": token}]), "text": token},
        {"title": "Contents", "text": "old"},
        {"title": "Chapter 1", "text": "body"},
    ]
    _replace_title_page(chapters, {"title": "B", "author": "A"}, _silent_log())

    entries = [link["text"] for link in chapters[1]["toc_links"]]
    assert not [e for e in entries if _conv._IMG_TOKEN_RE.search(e)], (
        f"an image token reached the contents listing: {entries}"
    )


class TestTableCellAnchors:
    """#130: an `id` on a table cell was discarded while the same id on any
    other element was kept.

    `table`/`tr`/`td` are not in `block_tags`, so a whole table is walked by
    the container branch, and that branch flushed its inline run through
    `normalize_runs` — which drops anchor marks — where the leaf-block branch
    uses `normalize_runs_with_anchors`. A link into a cell therefore resolved
    to nothing, which per the anchor model (#50) is a dead link rather than a
    mislanded one.

    No corpus book has ids on cells, so this shipped without symptom. The
    inconsistency is the defect.
    """

    BASE = "text/ch1.xhtml"

    def _blocks(self, markup):
        return extract_blocks_from_html(_body_markup(markup), base_href=self.BASE)

    def test_id_on_a_table_cell_is_kept(self):
        blocks = self._blocks('<table><tr><td id="c1">1801</td></tr></table>')
        assert blocks[0]["anchor_ids"] == ["c1"]

    def test_id_on_a_header_cell_is_kept(self):
        blocks = self._blocks('<table><tr><th id="h1">Year</th></tr></table>')
        assert blocks[0]["anchor_ids"] == ["h1"]

    def test_every_cell_id_in_a_fused_row_survives(self):
        """Cells fuse into one block (#128), so all their ids land on it."""
        blocks = self._blocks(
            '<table><tr><td id="c1">1801</td><td id="c2">8,893</td></tr></table>'
        )
        assert blocks[0]["anchor_ids"] == ["c1", "c2"]

    def test_a_fused_cell_id_records_where_its_text_starts(self):
        """Because the row is one block, an offset is what makes a link land
        on the right cell rather than at the start of the row (#79)."""
        blocks = self._blocks(
            '<table><tr><td id="c1">1801</td><td id="c2">8,893</td></tr></table>'
        )
        # `anchor_offsets` is re-keyed to "<file>#<id>" once `base_href` is
        # known, so links can be matched across files (#53).
        offsets = blocks[0]["anchor_offsets"]
        text = blocks[0]["text"]
        assert offsets[f"{self.BASE}#c1"] == 0
        assert offsets[f"{self.BASE}#c2"] == text.index("8,893"), (
            f"expected the second cell's offset to point at its own text in "
            f"{text!r}, got {offsets[f'{self.BASE}#c2']}"
        )

    def test_a_link_into_a_cell_has_a_key_to_resolve_against(self):
        blocks = self._blocks('<table><tr><td id="c1">1801</td></tr></table>')
        assert f"{self.BASE}#c1" in blocks[0]["anchor_keys"]

    def test_an_id_on_the_row_is_kept_too(self):
        blocks = self._blocks('<table><tr id="r1"><td>1801</td></tr></table>')
        assert "r1" in blocks[0]["anchor_ids"]

    def test_inline_anchor_in_a_container_lead_in_survives(self):
        """The same branch handles a container's own inline text alongside
        block children (#58) — an anchor there was dropped for the same
        reason."""
        blocks = self._blocks(
            '<div><span id="s1">Lead-in text.</span><p>A paragraph.</p></div>'
        )
        assert blocks[0]["text"] == "Lead-in text."
        assert blocks[0]["anchor_ids"] == ["s1"]

    def test_ids_carried_in_from_an_earlier_empty_anchor_still_arrive(self):
        """Guard: ids waiting in `pending_ids` must not be lost when the
        flushed run now contributes ids of its own."""
        blocks = self._blocks(
            '<a id="before"></a><table><tr><td id="c1">1801</td></tr></table>'
        )
        assert blocks[0]["anchor_ids"] == ["before", "c1"]
        assert blocks[0]["anchor_offsets"][f"{self.BASE}#before"] == 0

    def test_an_anchor_inside_a_cell_is_kept(self):
        """The shape that actually occurs. #130 measured ids *on* `<td>`/`<th>`
        and found none in the corpus, concluding the defect had no instances.
        Real books put the id on an inline element *inside* the cell — one
        corpus book has 443 of them — and those take the same dropped path."""
        blocks = self._blocks('<table><tr><td><a id="p1"></a>1801</td></tr></table>')
        assert "p1" in blocks[0]["anchor_ids"]

    def test_a_span_id_inside_a_cell_is_kept(self):
        blocks = self._blocks(
            '<table><tr><td><span id="s1">1801</span></td></tr></table>'
        )
        assert "s1" in blocks[0]["anchor_ids"]

    def test_a_cell_without_an_id_adds_nothing(self):
        blocks = self._blocks("<table><tr><td>1801</td></tr></table>")
        assert blocks[0]["anchor_ids"] == []


class TestUntocedChaptersAreNotListed:
    """#143: kfxgen invented navigation the source never had.

    Both paths that create a chapter without a TOC entry gave it a made-up
    label — "Front Matter" for content before the first TOC anchor, and the
    spine filename for orphans after the last one. Reviewing source EPUBs, the
    publisher's own TOC ends at the last real section ("End Notes", "Index");
    the back matter after it — a note to the reader, a "stay in touch" page —
    is deliberately unlisted. Inventing entries for those adds navigation the
    publisher chose not to provide.

    Verified across the corpus: the chapters carrying invented labels are
    exactly the chapters whose title is not a source TOC label (pg22210
    836 = 1 + 835, pg12082 238 = 1 + 237, pg120 44 = 1 + 43).

    Omitting from the TOC is not dropping content: `_omit_from_toc` is read
    only where the nav pane is built, so the pages still ship in the reading
    flow and still page through.
    """

    def _spine(self, href, text):
        return _spine_item(href, [(text, [])])

    def _head_book(self, *head_blocks):
        """`head_blocks` are separate blocks, which matters: the title comes
        from the *first* one alone, so a token sharing a block with prose
        would leave the prose as a perfectly good title."""
        blocks = [(t, []) for t in head_blocks]
        spine = [
            _spine_item("book.xhtml", blocks + [("Chapter I", ["c1"]), ("Body", [])])
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        return _assemble_chapters_by_coordinate(spine, toc, _silent_log())

    def test_invented_front_matter_label_is_not_listed(self):
        """A head with no usable heading of its own gets the invented label,
        and an invented label is not navigation."""
        token = _conv._make_img_token("cover.jpg", "")
        chapters = self._head_book(token, "more front matter")
        assert chapters[0]["title"] == _conv.LEADING_TITLE_FALLBACK
        assert chapters[0].get("_omit_from_toc") is True

    def test_a_head_with_its_own_heading_keeps_its_entry(self):
        """Narrow on purpose. This label is the book's own words, so it stays
        a usable destination even though the source TOC never named it —
        #143 is about invented labels, not about untoced content."""
        chapters = self._head_book("Copyright 2026")
        assert chapters[0]["title"] == "Copyright 2026"
        assert not chapters[0].get("_omit_from_toc")

    def test_the_front_matter_content_still_ships(self):
        """Omitted from the nav, still in the book."""
        token = _conv._make_img_token("cover.jpg", "")
        chapters = self._head_book(token, "A cover line")
        assert "A cover line" in chapters[0]["text"]

    def test_tail_orphans_are_not_listed(self):
        spine = [
            _spine_item("book.xhtml", [("Chapter I", ["c1"]), ("Body", [])]),
            self._spine("bm_001.xhtml", "A note from the author. back"),
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        orphan = chapters[-1]
        assert orphan["title"] == "bm_001"
        assert orphan.get("_omit_from_toc") is True
        assert "A note from the author" in orphan["text"]

    def _tail_book(self):
        spine = [
            _spine_item("book.xhtml", [("Chapter I", ["c1"]), ("Body", [])]),
            self._spine("bm_001.xhtml", "A note from the author. back"),
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        return _assemble_chapters_by_coordinate(spine, toc, _silent_log())

    def test_a_tail_orphan_does_not_print_its_file_name(self):
        # #275: #143 took the file-name label out of the nav pane, but it was
        # still printed as the page's heading: 43 in pg120, 3,471 pages in a
        # library sample. The label is ours, not the book's, as for the head.
        assert self._tail_book()[-1].get("_omit_title_heading") is True

    def test_no_heading_chunk_holds_the_file_name(self):
        from kfxgen.native_generator import NativeKFXGenerator

        chapters = self._tail_book()
        chunks = NativeKFXGenerator()._build_chapter_content(chapters)["all_chunks"]
        texts = [c["text"] for c in chunks if c.get("type") == "text"]
        assert "bm_001" not in texts
        assert any("A note from the author" in t for t in texts)
        assert "I" in texts  # the TOC-named chapter keeps its heading

    def test_a_tail_orphan_keeps_its_own_opening_words(self):
        # #284 review: back/notes.xhtml opening "Notes" lost it. The title
        # cut still ran with the heading suppressed, so the book's own words,
        # which match the file-name title, went with nothing in their place.
        # 20 pages in 7 library books.
        from kfxgen.native_generator import NativeKFXGenerator

        spine = [
            _spine_item("book.xhtml", [("Chapter I", ["c1"]), ("Body", [])]),
            _spine_item("notes.xhtml", [("Notes", []), ("1. The note text.", [])]),
        ]
        toc = [{"title": "I", "href": "book.xhtml#c1"}]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert chapters[-1]["title"] == "notes"
        chunks = NativeKFXGenerator()._build_chapter_content(chapters)["all_chunks"]
        texts = [c["text"] for c in chunks if c.get("type") == "text"]
        assert texts[-2:] == ["Notes", "1. The note text."]

    def test_the_contents_page_does_not_list_unlisted_pages(self):
        # #284 review: the rebuilt Contents page listed every chapter, so the
        # pages the nav pane leaves out were still linked there by file name:
        # 941 entries in 10 Gutenberg books, 835 in pg22210 alone.
        contents = {"title": "Contents", "text": "Contents"}
        chapters = [
            contents,
            {"title": "Chapter I", "text": "One."},
            {"title": "bm_001", "text": "Back.", "_omit_from_toc": True},
        ]
        _conv._rebuild_contents_page(contents, chapters, _silent_log())
        assert [link["text"] for link in contents["toc_links"]] == ["Chapter I"]

    def test_chapters_the_toc_names_are_still_listed(self):
        """The control. Omitting must not reach real entries — the nav pane is
        the project's headline feature."""
        spine = [
            _spine_item(
                "book.xhtml",
                [("Chapter I", ["c1"]), ("Body", []), ("Chapter II", ["c2"])],
            )
        ]
        toc = [
            {"title": "I", "href": "book.xhtml#c1"},
            {"title": "II", "href": "book.xhtml#c2"},
        ]
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        assert [c["title"] for c in chapters] == ["I", "II"]
        assert not any(c.get("_omit_from_toc") for c in chapters)

    def test_a_book_with_no_toc_mapping_keeps_every_entry(self):
        """Guard against emptying the pane entirely. When nothing resolves,
        every chapter is 'untoced' — omitting them all would leave a book with
        no navigation at all, which is worse than a machine label."""
        oeb = _OEBBook(
            [
                _SpineItem("a.xhtml", "First section body."),
                _SpineItem("b.xhtml", "Second section body."),
            ],
            [_TOCNode("Nowhere", "missing.xhtml")],
        )
        chapters = extract_chapters_from_oeb(oeb, _silent_log())
        assert chapters
        assert not any(c.get("_omit_from_toc") for c in chapters)


class TestImageHrefsResolveAgainstTheirDocument:
    """`<img src>` must be resolved the way `<a href>` already is.

    A manifest href is container-relative (`images/pic.jpg`); an `<img src>`
    is relative to the document holding it (`../images/pic.jpg`). They are
    different namespaces, which is why the generator matched them by basename
    — and why two images sharing a basename collapsed onto one resource.

    `_resolve_doc_path` has bridged exactly this gap for link targets since
    #51. These pin that image tokens now carry the resolved href, so the
    generator can match the manifest key exactly and only fall back to the
    basename when resolution finds nothing.
    """

    def _token_hrefs(self, html, base_href):
        tree = etree.fromstring(html, etree.HTMLParser())
        blocks = _conv.extract_blocks_from_html(tree, base_href=base_href)
        return [
            m.group(1)
            for b in blocks
            for m in IMG_TOKEN_RE.finditer(b.get("text") or "")
        ]

    def test_parent_relative_src_resolves_to_the_manifest_href(self):
        html = (
            b'<html><body><p><img src="../images/pic.jpg" alt="x"/></p>'
            b"<p>Body text.</p></body></html>"
        )
        assert self._token_hrefs(html, "text/chapter1.xhtml") == ["images/pic.jpg"]

    def test_sibling_relative_src_resolves_against_the_document_directory(self):
        html = (
            b'<html><body><p><img src="pic.jpg" alt="x"/></p>'
            b"<p>Body text.</p></body></html>"
        )
        assert self._token_hrefs(html, "OEBPS/text/chapter1.xhtml") == [
            "OEBPS/text/pic.jpg"
        ]

    def test_two_images_with_the_same_basename_stay_distinct(self):
        """The collision, at the layer where it is actually created."""
        html = (
            b'<html><body><p><img src="../a/pic.jpg" alt="x"/></p>'
            b'<p><img src="../b/pic.jpg" alt="y"/></p>'
            b"<p>Body text.</p></body></html>"
        )
        assert self._token_hrefs(html, "text/chapter1.xhtml") == [
            "a/pic.jpg",
            "b/pic.jpg",
        ]

    def test_no_base_href_leaves_the_src_untouched(self):
        """Nothing to resolve against — the raw src must survive unchanged.

        `extract_blocks_from_html` is called without a base in places, and
        mangling the href there would lose the image entirely rather than
        merely mis-key it.
        """
        html = (
            b'<html><body><p><img src="../images/pic.jpg" alt="x"/></p>'
            b"<p>Body text.</p></body></html>"
        )
        assert self._token_hrefs(html, "") == ["../images/pic.jpg"]

    @pytest.mark.parametrize(
        "src",
        [
            "http://example.com/remote.png",
            "/abs/pic.png",
            "data:image/png;base64,AAAA",
            "C:/a/pic.png",
        ],
    )
    def test_an_unsafe_src_is_left_untouched_without_a_security_warning(
        self, src, caplog
    ):
        """Remote, absolute and inline sources are not book-internal paths.

        `_resolve_doc_path` logs every href it rejects as a security event, so
        passing these through it turned each remote image into a false alarm,
        and an inline `data:` image into a log line carrying its whole payload.
        """
        with caplog.at_level(logging.DEBUG, logger="kfxgen.converter.security"):
            assert self._token_hrefs(
                f'<html><body><p><img src="{src}" alt="x"/></p>'
                "<p>Body text.</p></body></html>".encode(),
                "text/chapter1.xhtml",
            ) == [src]
        assert not [
            r for r in caplog.records if "rejected unsafe href" in r.getMessage()
        ]


def test_a_missing_image_does_not_borrow_a_same_named_one(tmp_path):
    """End to end through convert_oeb_to_kfx (#195).

    Calibre keeps an `<img>` whose file is missing ("Referenced file not
    found"). Converter resolves it to `a/pic.jpg`; the book also holds a
    different `b/pic.jpg`. Resolved references must not fall back by
    filename, or the reader sees b's picture where a's was meant.
    """
    from tests._helpers import MINIMAL_JPEG
    from tests._kfx_introspect import by_type, load_fragments, val, walk_for_key
    from tests.fixtures.epub_builder import EpubBuilder
    from tests.fixtures.oeb_shim import EpubAsOeb

    body = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>C</title></head>'
        b"<body><h1>C</h1><p>Missing picture:</p>"
        b'<div><img src="a/pic.jpg" alt="a"/></div>'
        b'<p>Present picture:</p><div><img src="b/pic.jpg" alt="b"/></div>'
        b"</body></html>"
    )
    epub = (
        EpubBuilder()
        .set_metadata(title="MissingImage", author="T")
        .add_chapter("C", body)
        .add_manifest_item(
            item_id="pb", href="b/pic.jpg", media_type="image/jpeg", data=MINIMAL_JPEG
        )
        .build(tmp_path, "missing-image")
    )
    out = tmp_path / "out.kfx"
    _conv.convert_oeb_to_kfx(EpubAsOeb(str(epub)), str(out), None, _silent_log())

    frags = load_fragments(out)
    refs = [
        str(r) for s in by_type(frags, "$259") for r in walk_for_key(val(s), "$175")
    ]
    assert refs == ["img_0"], (
        "the missing a/pic.jpg must show nothing, and b/pic.jpg must show once"
    )


def _epub_cover_and_title_share_a_document(tmp_path, nav):
    """A front document holding the cover image and the title page, with TOC
    entries `nav` [(label, src), ...] pointing into it, then two chapters.

    Children's-book EPUBs often put both on one page and give the TOC a
    "Cover" and a "Title Page" entry into it (#182).
    """
    import zipfile

    from tests._helpers import jpeg_of

    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{label}</text>'
        f'</navLabel><content src="{src}"/></navPoint>'
        for i, (label, src) in enumerate(
            nav + [("Chapter 1", "c1.xhtml"), ("Chapter 2", "c2.xhtml")], 1
        )
    )
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>C</title></head><body><h1>Chapter {n}</h1>"
        "<p>Body {n}.</p></body></html>"
    )
    path = tmp_path / "shared_front.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x'
            "</dc:identifier><dc:title>A Title</dc:title><dc:creator>An Author"
            "</dc:creator><dc:language>en</dc:language></metadata><manifest>"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '<item id="front" href="front.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ci" href="cover.jpg" media-type="image/jpeg"/>'
            '<item id="ti" href="title.jpg" media-type="image/jpeg"/>'
            '</manifest><spine toc="ncx"><itemref idref="front"/>'
            '<itemref idref="c1"/><itemref idref="c2"/></spine></package>',
        )
        zf.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>A Title</text></docTitle>'
            f"<navMap>{points}</navMap></ncx>",
        )
        zf.writestr(
            "front.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            "<head><title>Front</title></head><body>"
            '<div id="cover"><img src="cover.jpg" alt="cover"/></div>'
            '<div id="title"><img src="title.jpg" alt=""/><h1>A Title</h1>'
            "<p>An Author</p></div></body></html>",
        )
        zf.writestr("c1.xhtml", chapter.format(n=1))
        zf.writestr("c2.xhtml", chapter.format(n=2))
        zf.writestr("cover.jpg", jpeg_of(600, 800))
        zf.writestr("title.jpg", jpeg_of(400, 300))
    return path


@pytest.mark.unit
@pytest.mark.parametrize(
    "nav",
    [
        [("Title Page", "front.xhtml"), ("Cover", "front.xhtml")],
        [("Title Page", "front.xhtml"), ("Cover", "front.xhtml#cover")],
        [("Title Page", "front.xhtml#title"), ("Cover", "front.xhtml#cover")],
        [("Cover", "front.xhtml"), ("Title Page", "front.xhtml#title")],
        [("Cover", "front.xhtml#cover"), ("Title Page", "front.xhtml#title")],
        [("Cover", "front.xhtml"), ("Title Page", "front.xhtml")],
    ],
    ids=[
        "title-then-cover-same-doc",
        "title-doc-then-cover-fragment",
        "title-fragment-then-cover-fragment",
        "cover-doc-then-title-fragment",
        "cover-fragment-then-title-fragment",
        "cover-then-title-same-doc",
    ],
)
def test_the_cover_page_keeps_its_picture_when_it_shares_the_title_pages_document(
    tmp_path, nav
):
    """#182: the pictures printed on the page the TOC calls "Cover".

    When the Title Page entry comes first, or both land on one position, the
    Cover entry does not get a chapter of its own: its page joins the Title
    Page chapter. Before #181, `_replace_title_page` then overwrote that
    chapter's body and took the cover picture with it, which is how images
    printed on a "Cover" page were lost even though no chapter titled Cover
    was ever rewritten. Counted as the original measurement did: chapters
    with and without metadata, `preserved_images` as kept.
    """
    from tests.fixtures.oeb_shim import EpubAsOeb

    epub = _epub_cover_and_title_share_a_document(tmp_path, nav)
    meta = {"title": "A Title", "author": "An Author"}
    with_md = _conv.extract_chapters_from_oeb(
        EpubAsOeb(str(epub)), _silent_log(), metadata=meta
    )
    without_md = _conv.extract_chapters_from_oeb(
        EpubAsOeb(str(epub)), _silent_log(), metadata=None
    )

    def kept(chapters):
        return sorted(
            m.group(1)
            for c in chapters
            for t in [c.get("text") or ""] + list(c.get("preserved_images") or [])
            for m in IMG_TOKEN_RE.finditer(t)
        )

    assert kept(without_md) == ["cover.jpg", "title.jpg"]
    assert kept(with_md) == kept(without_md), (
        "the metadata rewrite dropped a picture printed on the cover or title page"
    )


def _epub_with_toc(tmp_path, body, nav, name="toc_targets"):
    """One spine document `body` (inner <body> markup) and an NCX of
    `nav` [(label, src)] entries pointing into it."""
    import zipfile

    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{label}</text>'
        f'</navLabel><content src="{src}"/></navPoint>'
        for i, (label, src) in enumerate(nav, 1)
    )
    path = tmp_path / f"{name}.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x'
            "</dc:identifier><dc:title>T</dc:title><dc:language>en</dc:language>"
            '</metadata><manifest><item id="ncx" href="toc.ncx" '
            'media-type="application/x-dtbncx+xml"/><item id="d" href="index.xhtml" '
            'media-type="application/xhtml+xml"/></manifest><spine toc="ncx">'
            '<itemref idref="d"/></spine></package>',
        )
        zf.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>T</text></docTitle>'
            f"<navMap>{points}</navMap></ncx>",
        )
        zf.writestr(
            "index.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>T</title></head>'
            f"<body>{body}</body></html>",
        )
    return path


# The shape Python-Markdown's footnotes extension writes, which is what calibre
# hands kfxgen for a Markdown book (#192, #203).
_MD_FOOTNOTE_BODY = (
    '<h1 id="one">Chapter One</h1><p>Text with a note.'
    '<sup id="fnref:1"><a class="footnote-ref" href="#fn:1">1</a></sup></p>'
    '<h1 id="two">Chapter Two</h1><p>More text.</p>'
    '<div class="footnote"><hr/><ol><li id="fn:1"><p>The note. '
    '<a class="footnote-backref" href="#fnref:1">&#8617;</a></p></li></ol></div>'
)


def _chapter_titles(epub):
    from tests.fixtures.oeb_shim import EpubAsOeb

    return [
        c["title"]
        for c in _conv.extract_chapters_from_oeb(EpubAsOeb(str(epub)), _silent_log())
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "encode", [False, True], ids=["literal-colon", "calibre-encoded-colon"]
)
def test_calibre_toc_entries_for_a_footnote_do_not_become_chapters(tmp_path, encode):
    """#203: calibre's generated TOC lists the note marker and its back-link.

    Neither is a chapter. As chapters they split the text and print `1` and `↩`
    as headings. calibre hands the fragments percent-encoded (`fn%3a1`), so
    both spellings must be recognised.
    """
    c = "%3a" if encode else ":"
    epub = _epub_with_toc(
        tmp_path,
        _MD_FOOTNOTE_BODY,
        [
            ("Chapter One", "index.xhtml#one"),
            ("Chapter Two", "index.xhtml#two"),
            ("1", f"index.xhtml#fn{c}1"),
            ("↩", f"index.xhtml#fnref{c}1"),
        ],
    )
    assert _chapter_titles(epub) == ["Chapter One", "Chapter Two"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "body,target",
    [
        (
            '<p>x<a epub:type="noteref" id="m1" href="#n1">1</a></p>'
            '<aside epub:type="footnote" id="n1"><p>Note.</p></aside>',
            "m1",
        ),
        (
            '<p>x<a epub:type="noteref" id="m1" href="#n1">1</a></p>'
            '<aside epub:type="footnote" id="n1"><p>Note.</p></aside>',
            "n1",
        ),
        (
            '<p>x<a role="doc-noteref" id="m1" href="#n1">1</a></p>'
            '<aside role="doc-endnote" id="n1"><p>Note.'
            '<a role="doc-backlink" id="b1" href="#m1">back</a></p></aside>',
            "b1",
        ),
        (
            '<p>x<a role="doc-noteref" id="m1" href="#n1">1</a></p>'
            '<aside role="doc-endnote" id="n1"><p>Note.</p></aside>',
            "n1",
        ),
    ],
    ids=["epub-noteref", "epub-footnote", "aria-backlink", "aria-endnote"],
)
def test_toc_entries_for_semantic_notes_do_not_become_chapters(tmp_path, body, target):
    epub = _epub_with_toc(
        tmp_path,
        '<h1 id="one">Chapter One</h1>'
        + body
        + '<h1 id="two">Chapter Two</h1><p>y</p>',
        [
            ("Chapter One", "index.xhtml#one"),
            ("Note", f"index.xhtml#{target}"),
            ("Chapter Two", "index.xhtml#two"),
        ],
    )
    assert _chapter_titles(epub) == ["Chapter One", "Chapter Two"]


@pytest.mark.unit
def test_a_notes_section_heading_is_still_a_chapter(tmp_path):
    """Adversarial: the container of the notes is a real TOC target.

    A "Notes" chapter whose heading sits inside the footnotes section must
    keep its entry. Only individual notes and markers are dropped.
    """
    epub = _epub_with_toc(
        tmp_path,
        '<h1 id="one">Chapter One</h1><p>x<a epub:type="noteref" href="#n1">1</a></p>'
        '<section epub:type="endnotes" id="notes"><h1 id="notes-h">Notes</h1>'
        '<aside epub:type="endnote" id="n1"><p>Note.</p></aside></section>',
        [("Chapter One", "index.xhtml#one"), ("Notes", "index.xhtml#notes-h")],
    )
    assert _chapter_titles(epub) == ["Chapter One", "Notes"]


@pytest.mark.unit
def test_a_notes_section_itself_is_still_a_chapter(tmp_path):
    epub = _epub_with_toc(
        tmp_path,
        '<h1 id="one">Chapter One</h1><p>x</p>'
        '<div class="footnotes" id="notes"><h2>Footnotes</h2>'
        '<p id="n1">1. Note.</p></div>',
        [("Chapter One", "index.xhtml#one"), ("Footnotes", "index.xhtml#notes")],
    )
    assert _chapter_titles(epub) == ["Chapter One", "Footnotes"]


@pytest.mark.unit
def test_chapters_titled_with_bare_numbers_are_kept(tmp_path):
    """Adversarial: "1", "2", "3" are ordinary chapter titles, not note markers.

    The fix must key on what the target *is*, never on how its label reads.
    """
    epub = _epub_with_toc(
        tmp_path,
        '<h2 id="c1">1</h2><p>a</p><h2 id="c2">2</h2><p>b</p><h2 id="c3">3</h2><p>c</p>',
        [("1", "index.xhtml#c1"), ("2", "index.xhtml#c2"), ("3", "index.xhtml#c3")],
    )
    assert _chapter_titles(epub) == ["1", "2", "3"]


@pytest.mark.unit
def test_an_encoded_toc_fragment_lands_on_its_own_heading(tmp_path):
    """calibre percent-encodes TOC fragments; the anchor map holds raw ids.

    `#part%3aii` must find `id="part:ii"`, not snap to a guessed block.
    """
    from tests.fixtures.oeb_shim import EpubAsOeb

    epub = _epub_with_toc(
        tmp_path,
        '<h1 id="part:i">Part I</h1><p>first</p><p>more first</p>'
        '<h1 id="part:ii">Part II</h1><p>second</p>',
        [("Part I", "index.xhtml#part%3ai"), ("Part II", "index.xhtml#part%3aii")],
    )
    chapters = _conv.extract_chapters_from_oeb(EpubAsOeb(str(epub)), _silent_log())
    assert [c["title"] for c in chapters] == ["Part I", "Part II"]
    assert "second" in chapters[1]["text"] and "first" not in chapters[1]["text"]


@pytest.mark.unit
def test_a_marker_calibre_stripped_of_its_class_is_still_recognised(tmp_path):
    """Adversarial: the shape calibre actually hands kfxgen.

    calibre rewrites classes during conversion, and the footnote-ref and
    footnote-backref classes do not survive: the marker arrives as a bare
    `<sup id="fnref:1"><a href="#fn:1">`. It is recognised by what it links
    to. The back-link entry is listed *before* the note here, so the older
    "out of document order" skip cannot hide a miss.
    """
    body = (
        '<h1 id="one">Chapter One</h1><p>Text with a note.'
        '<sup id="fnref:1" class="calibre10"><a href="#fn:1">1</a></sup></p>'
        '<h1 id="two">Chapter Two</h1><p>More text.</p>'
        '<div class="footnote"><hr/><ol class="calibre16">'
        '<li id="fn:1" class="calibre13"><p>The note. '
        '<a href="#fnref:1">&#8617;</a></p></li></ol></div>'
    )
    epub = _epub_with_toc(
        tmp_path,
        body,
        [
            ("Chapter One", "index.xhtml#one"),
            ("↩", "index.xhtml#fnref%3a1"),
            ("Chapter Two", "index.xhtml#two"),
            ("1", "index.xhtml#fn%3a1"),
        ],
    )
    assert _chapter_titles(epub) == ["Chapter One", "Chapter Two"]


@pytest.mark.unit
def test_a_paragraph_that_contains_a_note_marker_can_still_start_a_chapter(tmp_path):
    """Adversarial: marker detection must not swallow the marker's paragraph.

    A TOC entry may point at a paragraph with an id, and that paragraph may
    hold a note marker. The paragraph is a chapter start; only the marker is
    not.
    """
    body = (
        '<h1 id="one">Chapter One</h1><p>a</p>'
        '<p id="start2">Chapter two opens here, with a note.'
        '<sup><a href="#n1">1</a></sup></p><p>more</p>'
        '<aside epub:type="footnote" id="n1"><p>Note.</p></aside>'
    )
    epub = _epub_with_toc(
        tmp_path,
        body,
        [("Chapter One", "index.xhtml#one"), ("Chapter Two", "index.xhtml#start2")],
    )
    assert _chapter_titles(epub) == ["Chapter One", "Chapter Two"]


# ── native tables in the block stream (#219) ─────────────────────────────────


@pytest.mark.unit
def test_native_tables_become_one_block_between_paragraphs():
    blocks = _conv.extract_blocks_from_html(
        _doc(f"<p>Before.</p>{_ISSUE_219_TABLE}<p>After.</p>"), native_tables=True
    )
    assert [b.get("type", "text") for b in blocks] == ["text", "table", "text"]


@pytest.mark.unit
def test_native_tables_off_keeps_rows_as_paragraphs():
    blocks = _conv.extract_blocks_from_html(_doc(_ISSUE_219_TABLE))
    assert [b["text"] for b in blocks] == ["Year A B", "1 100 200", "2 110 220"]


@pytest.mark.unit
def test_an_ineligible_table_falls_back_to_rows_with_native_tables_on():
    blocks = _conv.extract_blocks_from_html(
        _doc('<table><tr><td>a</td><td><video src="v.mp4"></video></td></tr></table>'),
        native_tables=True,
    )
    assert all(b.get("type", "text") != "table" for b in blocks)


@pytest.mark.unit
def test_only_fallback_tables_count_for_the_warning():
    seen = []
    _conv.extract_blocks_from_html(
        _doc(
            f"{_ISSUE_219_TABLE}<table><tr><td>a</td>"
            '<td><video src="v.mp4"></video></td></tr></table>'
        ),
        native_tables=True,
        tables_seen=seen,
    )
    assert len(seen) == 1


@pytest.mark.unit
def test_anchor_keys_reach_rows_and_cells():
    blocks = _conv.extract_blocks_from_html(
        _doc('<table id="t"><tr id="r"><td id="c">a</td></tr></table>'),
        native_tables=True,
        base_href="ch.xhtml",
    )
    tbl = blocks[0]["table"]
    assert tbl["anchor_keys"][-1] == "ch.xhtml#t"
    assert tbl["rows"][0]["anchor_keys"] == ["ch.xhtml#r"]
    assert tbl["rows"][0]["cells"][0]["anchor_keys"] == ["ch.xhtml#c"]
    assert tbl["rows"][0]["cells"][0]["anchor_offsets"] == {"ch.xhtml#c": 0}


@pytest.mark.unit
def test_toc_entries_into_one_table_do_not_make_empty_chapters():
    # Review Focus 1: several TOC entries pointing at rows of one table must
    # not produce empty chapters. Entries past the table's start make it fall
    # back to rows (I4), so each entry keeps a chapter of its own.
    notes = (
        "<p>Notes intro.</p><table>"
        + "".join(
            f'<tr><td>{n}.</td><td>Note {n}.</td></tr><a id="n{n}"></a>'
            for n in (1, 2, 3)
        )
        + "</table>"
    )
    oeb = _contents_book(("Chapter One", "<p>One.</p>"), ("Notes", notes))
    oeb.toc = oeb.toc + [_TOCNode(f"{n}", f"ch1.xhtml#n{n}") for n in (1, 2, 3)]
    chapters = extract_chapters_from_oeb(oeb, _silent_log(), native_tables=True)
    assert all(c.get("blocks") or c.get("text", "").strip() for c in chapters)
    assert [c["title"] for c in chapters] == ["Chapter One", "Notes", "1", "2", "3"]
    tables = [
        b for c in chapters for b in c.get("blocks") or () if b.get("type") == "table"
    ]
    assert tables == []


@pytest.mark.unit
def test_toc_entries_at_one_tables_start_collapse_onto_it():
    # Entries naming the table, its first row and its first cell all resolve
    # to the table's block: one chapter, no empty ones, and the table native.
    oeb = _notes_book(["t", "n1", "c1"])
    chapters = extract_chapters_from_oeb(oeb, _silent_log(), native_tables=True)
    assert all(c.get("blocks") or c.get("text", "").strip() for c in chapters)
    assert [c["title"] for c in chapters] == ["Chapter One", "Notes", "Note t"]
    tables = [
        b for c in chapters for b in c.get("blocks") or () if b.get("type") == "table"
    ]
    assert len(tables) == 1


@pytest.mark.unit
def test_native_tables_are_counted_in_the_log():
    log = _silent_log()
    log.info = MagicMock()
    extract_chapters_from_oeb(
        _table_book(f"<p>One.</p>{_ISSUE_219_TABLE}"), log, native_tables=True
    )
    assert any("1 table written as native" in str(c) for c in log.info.call_args_list)


# ── final-review fixes for native tables (#219) ──────────────────────────────


def _book_link_target_kinds(oeb, tmp_path):
    """Convert `oeb` with native tables on and return the kind (`$159`) of the
    storyline entry each body `$266` targets."""
    from kfxgen.native_generator import NativeKFXGenerator
    from tests._kfx_introspect import iter_entries, load_fragments, val

    chapters = extract_chapters_from_oeb(oeb, _silent_log(), native_tables=True)
    out = tmp_path / "links.kfx"
    NativeKFXGenerator().generate_full_book("T", "A", chapters, output_path=str(out))
    frags = load_fragments(out)
    by_eid = {
        e["$155"]: e
        for f in frags
        if str(f.ftype) == "$259"
        for e in iter_entries(val(f)["$146"])
    }
    return [
        str(by_eid[val(f)["$183"]["$155"]]["$159"])
        for f in frags
        if str(f.ftype) == "$266" and str(val(f)["$180"]).startswith("body_anchor")
    ]


@pytest.mark.unit
def test_an_anchor_after_a_files_last_native_table_lands_on_its_last_row():
    # I1: a trailing anchor after the file's last block is snapped onto that
    # block. When the block is a native table, the id must reach a row, as it
    # did in 5.8.8, or nothing in the table declares it.
    blocks = _conv.extract_blocks_from_html(
        _doc(f'{_ISSUE_219_TABLE}<a id="eof"></a>'),
        native_tables=True,
        base_href="ch.xhtml",
    )
    assert blocks[-1]["type"] == "table"
    assert "ch.xhtml#eof" in blocks[-1]["table"]["rows"][-1]["anchor_keys"]


@pytest.mark.unit
def test_a_link_to_an_anchor_after_a_files_last_native_table_resolves(tmp_path):
    oeb = _contents_book(
        ("One", '<p>See <a href="ch1.xhtml#eof">the end</a>.</p>'),
        ("Two", f'<p>Table.</p>{_ISSUE_219_TABLE}<a id="eof"></a>'),
    )
    assert _book_link_target_kinds(oeb, tmp_path) == ["$279"]


@pytest.mark.unit
def test_a_caption_keeps_its_css_block_style():
    # I2: the caption is an ordinary paragraph and takes its style from CSS
    # like any other, or a centred caption comes out justified.
    from kfxgen.inline_style import compute_block_style

    css = {"text-align": "center"}
    captions, _ = _caption_and_table(
        f"<table><caption>Harbour lamps</caption>{_ISSUE_219_TABLE[7:]}",
        style_resolver=lambda e: css,
    )
    assert captions[0]["block_style"] == compute_block_style(css)
    assert captions[0]["block_style"]["align"] == "center"


@pytest.mark.unit
def test_a_caption_without_a_resolver_has_no_block_style():
    captions, _ = _caption_and_table(
        f"<table><caption>Harbour lamps</caption>{_ISSUE_219_TABLE[7:]}"
    )
    assert captions[0]["block_style"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        '<table><tr><td>a</td></tr><tr hidden="hidden"><td>SECRET</td></tr></table>',
        '<table><tbody hidden="hidden"><tr><td>SECRET</td></tr></tbody>'
        "<tbody><tr><td>a</td></tr></tbody></table>",
        '<table><tr><td>a</td><td hidden="hidden">SECRET</td></tr></table>',
        '<table><tr epub:type="page-list"><td>1</td></tr><tr><td>a</td></tr></table>',
        '<table><tr><td>a</td></tr><tr class="toc"><td>Listing</td></tr></table>',
        "<table><tr>LOOSE<td>a</td></tr></table>",
        "<table><tr><td>a</td>between<td>b</td></tr></table>",
        "<table><form><tr><td>a</td></tr></form><tr><td>b</td></tr></table>",
        "<table><tbody>stray<tr><td>a</td></tr></tbody></table>",
        "<table>loose<tr><td>a</td></tr></table>",
        "<table><tr><td>a</td></tr>after a row<tr><td>b</td></tr></table>",
        '<table><tr><td>a</td></tr><a id="x"></a>after an anchor<tr><td>b</td></tr>'
        "</table>",
    ],
    ids=[
        "hidden-tr",
        "hidden-tbody",
        "hidden-td",
        "page-list-tr",
        "toc-class-tr",
        "text-in-tr",
        "text-between-cells",
        "tr-under-form",
        "stray-text-in-tbody",
        "text-in-table",
        "text-after-a-row",
        "text-after-an-anchor",
    ],
)
def test_markup_only_the_row_path_honours_falls_back(html):
    # I3: the native path walks rows and cells directly, so it would show
    # what the row path hides and drop text the row path keeps.
    assert not _conv._table_is_native(_first_table(html))


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        "<table>\n  <tr><td>a</td></tr>\n  <tr><td>b</td></tr>\n</table>",
        "<table>\n<tbody>\n<tr>\n<td>a</td>\n<td>b</td>\n</tr>\n</tbody>\n</table>",
        '<table><tr><td>a</td></tr>\n<a id="n1"></a>\n<tr><td>b</td></tr>'
        '<a id="n2"></a></table>',
        "<table><caption>Lamps</caption>\n<tr><td>a</td></tr></table>",
        '<table><tr><td>a <span hidden="hidden">x</span></td></tr></table>',
    ],
    ids=[
        "whitespace-between-rows",
        "whitespace-everywhere",
        "anchors-between-rows",
        "caption",
        "hidden-inline-in-a-cell",
    ],
)
def test_ordinary_whitespace_and_anchors_stay_native(html):
    assert _conv._table_is_native(_first_table(html))


def _notes_book(toc_ids):
    """A chapter, then a notes table with a TOC entry per id in `toc_ids`."""
    rows = "".join(
        f'<tr id="n{n}"><td id="c{n}">{n}.</td><td>Note {n}.</td></tr>'
        for n in (1, 2, 3)
    )
    notes = f'<p>Notes intro.</p><a id="before"></a><table id="t">{rows}</table>'
    oeb = _contents_book(("Chapter One", "<p>One.</p>"), ("Notes", notes))
    oeb.toc = oeb.toc + [_TOCNode(f"Note {i}", f"ch1.xhtml#{i}") for i in toc_ids]
    return oeb


def _titles_and_tables(oeb, native):
    chapters = extract_chapters_from_oeb(oeb, _silent_log(), native_tables=native)
    tables = sum(
        1 for c in chapters for b in c.get("blocks") or () if b.get("type") == "table"
    )
    return [c["title"] for c in chapters], tables


@pytest.mark.unit
def test_a_table_with_toc_entries_inside_falls_back_to_rows():
    # I4: a chapter is a block range and a native table is one block, so a
    # second TOC entry into it was dropped and the rows before it moved under
    # its title. Such a table keeps rows, and the TOC 5.8.8 gave.
    oeb = _notes_book(["n2", "n3"])
    off = _titles_and_tables(oeb, native=False)
    on = _titles_and_tables(oeb, native=True)
    assert off == (["Chapter One", "Notes", "Note n2", "Note n3"], 0)
    assert on == off


@pytest.mark.unit
@pytest.mark.parametrize("target", ["t", "n1", "c1", "before"])
def test_a_toc_entry_at_the_tables_start_keeps_it_native(target):
    # Its start: the table's own id, its first row's or first cell's, or an
    # anchor just before it that carries into it.
    titles, tables = _titles_and_tables(_notes_book([target]), native=True)
    assert titles == ["Chapter One", "Notes", f"Note {target}"]
    assert tables == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "targets, native",
    [(None, True), ({"t"}, True), ({"r1", "c1"}, True), ({"r2"}, False)],
)
def test_toc_targets_past_a_tables_start_keep_rows(targets, native):
    html = (
        '<table id="t"><tr id="r1"><td id="c1">a</td><td id="c2">b</td></tr>'
        '<tr id="r2"><td>c</td><td>d</td></tr></table>'
    )
    blocks = _conv.extract_blocks_from_html(
        _doc(html), native_tables=True, toc_targets=targets
    )
    assert any(b.get("type") == "table" for b in blocks) is native


@pytest.mark.unit
def test_links_to_a_table_its_preceding_anchor_or_its_file_target_a_row(tmp_path):
    # I5: a link to the table's own id, to an anchor just before it, or to a
    # whole file that opens with a table must target the first row (`$279`,
    # a kind Amazon uses as a target), never the `$278` container.
    oeb = _contents_book(
        (
            "One",
            '<p><a href="ch1.xhtml#t">table</a>, <a href="ch1.xhtml#before">before'
            '</a> and <a href="ch2.xhtml">file</a>.</p>',
        ),
        (
            "Two",
            f'<p>Intro.</p><a id="before"></a>{_ISSUE_219_TABLE[:6]} id="t"'
            f"{_ISSUE_219_TABLE[6:]}",
        ),
        ("Three", f"{_ISSUE_219_TABLE}<p>After.</p>"),
    )
    assert _book_link_target_kinds(oeb, tmp_path) == ["$279", "$279", "$279"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "html, native",
    [
        ("<table><p>x</p><tr><td>a</td></tr></table>", False),
        ("<table><tbody><p>x</p><tr><td>a</td></tr></tbody></table>", False),
        ("<table><div>x</div><tr><td>a</td></tr></table>", False),
        ("<table><colgroup><col/></colgroup><tr><td>a</td></tr></table>", True),
        ("<table><!-- note --><tr><td>a</td></tr></table>", True),
    ],
    ids=["p-in-table", "p-in-tbody", "div-in-table", "colgroup", "comment"],
)
def test_a_non_row_element_in_a_table_or_row_group_falls_back(html, native):
    # The native walk reads only rows, row groups, the caption and empty
    # anchors there; anything else would lose its text. (#219)
    assert _conv._table_is_native(_first_table(html)) is native


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        "<table><tr><p>lost</p><td>a</td></tr></table>",
        "<table><tr><span>lost</span><td>a</td></tr></table>",
        '<table><caption hidden="hidden">SECRET</caption><tr><td>a</td></tr></table>',
        "<table><caption>One</caption><caption>Two</caption><tr><td>a</td></tr>"
        "</table>",
    ],
    ids=["p-in-tr", "span-in-tr", "hidden-caption", "two-captions"],
)
def test_row_children_and_captions_the_native_walk_misreads_fall_back(html):
    # A non-cell element in a row loses its text natively; a hidden caption
    # would be shown; only the first caption is kept. The row path handles
    # all three. (#219)
    assert not _conv._table_is_native(_first_table(html))


@pytest.mark.unit
def test_one_visible_caption_and_an_anchor_between_cells_stay_native():
    assert _conv._table_is_native(
        _first_table(
            "<table><caption>Lamps</caption><!-- note -->"
            '<tr><td>a</td><a id="x"></a><td>b</td></tr></table>'
        )
    )


# ── QA review fixes for native tables (#219, PR #251) ────────────────────────


def _chapter_texts(title, body, native):
    """The visible text chunks of a one-chapter book titled `title`, in order,
    and the chunk list itself."""
    from kfxgen.native_generator import NativeKFXGenerator

    chapters = extract_chapters_from_oeb(
        _contents_book((title, body)), _silent_log(), native_tables=native
    )
    chunks = NativeKFXGenerator()._build_chapter_content(chapters)["all_chunks"]
    return [c["text"] for c in chunks if c["type"] == "text"], chunks


def _words(texts):
    return " ".join(texts).split()


def _cell_rows(chunks):
    """Each emitted row as the list of its cells' texts."""
    rows, row = [], None
    for c in chunks:
        if c.get("node") == "row":
            row = []
            rows.append(row)
        elif "cell" in c:
            row.append(c["text"])
    return rows


_TITLE_TABLE_CASES = [
    (
        "CHAPTER I",
        "<table><tr><td>CHAPTER I</td></tr>"
        "<tr><td>The beginning</td><td>p. 1</td></tr></table>",
        [["The beginning", "p. 1"]],
    ),
    (
        "CHAPTER I. The Title",
        "<table><tr><td>CHAPTER I.</td><td>The Title</td></tr>"
        "<tr><td>a</td><td>b</td></tr></table>",
        [["a", "b"]],
    ),
    (
        "CHAPTER I",
        "<table><tr><td>CHAPTER I The Start of</td><td>x</td></tr>"
        "<tr><td>a</td><td>b</td></tr></table>",
        [["The Start of", "x"], ["a", "b"]],
    ),
    (
        "CHAPTER I",
        "<table><tr><td>CHAPTER</td><td>I</td><td>y</td></tr>"
        "<tr><td>a</td><td>b</td><td>c</td></tr></table>",
        [[" ", " ", "y"], ["a", "b", "c"]],
    ),
    (
        "CHAPTER I. The Title",
        "<table><tr><td>CHAPTER I.</td></tr><tr><td>The Title</td></tr>"
        "<tr><td>a</td><td>b</td></tr></table>",
        [["a", "b"]],
    ),
    (
        "CHAPTER I. The Title",
        "<p>CHAPTER I.</p><table><tr><td>The Title</td></tr>"
        "<tr><td>a</td><td>b</td></tr></table>",
        [["a", "b"]],
    ),
    (
        "CHAPTER I",
        "<table><tr><td>a</td><td>b</td></tr></table>",
        [["a", "b"]],
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "title, body, rows",
    _TITLE_TABLE_CASES,
    ids=[
        "row-is-title",
        "cells-make-title",
        "cell-starts-with-title",
        "title-spans-cells",
        "title-split-over-rows",
        "title-split-paragraph-then-row",
        "no-cut",
    ],
)
def test_a_chapter_title_in_a_leading_table_is_shown_once(title, body, rows):
    # QA-1: the title dedupe cut the title from the table block's text but
    # wrote cells from its rows, so the heading and the first cell both
    # showed it. The cut now reaches the cells, and the words a reader sees
    # match the rows build (5.8.8's bytes).
    native, chunks = _chapter_texts(title, body, native=True)
    rows_build, _ = _chapter_texts(title, body, native=False)
    assert native[0] == title
    assert _cell_rows(chunks) == rows
    assert _words(native) == _words(rows_build)


_POEM = "<h5>Mein.</h5><p>Du bist mein,</p><p>ich bin dein.</p>"


@pytest.mark.unit
@pytest.mark.parametrize(
    "title, texts",
    [
        ("Mein.", ["Mein.", "Du bist mein,", "ich bin dein.", "x"]),
        ("Mein. Du", ["Mein. Du", "bist mein,", "ich bin dein.", "x"]),
        ("Elsewhere", ["Elsewhere", "Mein.", "Du bist mein,", "ich bin dein.", "x"]),
    ],
    ids=["whole-paragraph", "into-a-paragraph", "no-cut"],
)
def test_the_title_cut_reaches_into_a_paragraph_cell(title, texts):
    # #261: a cell holding blocks is written as paragraphs; the title the
    # heading already shows is cut from them as the rows build cuts it.
    # Not compared with the rows build: it joins a cell's blocks with no
    # space when the source has none between the tags ("Mein.Du").
    body = f"<table><tr><td>{_POEM}</td><td>x</td></tr></table>"
    native, _ = _chapter_texts(title, body, native=True)
    assert native == texts


@pytest.mark.unit
def test_a_title_cell_of_paragraphs_drops_its_row_and_keeps_its_ids():
    texts, chunks = _chapter_texts(
        "CHAPTER I",
        '<table><tr><td><p id="p1">CHAPTER</p><p>I</p></td></tr>'
        '<tr id="r2"><td>a</td></tr></table>',
        native=True,
    )
    assert texts == ["CHAPTER I", "a"]
    row_keys = [c["anchor_keys"] for c in chunks if c.get("node") == "row"]
    assert len(row_keys) == 1
    assert {"ch0.xhtml#p1", "ch0.xhtml#r2"} <= set(row_keys[0])


@pytest.mark.unit
def test_a_dropped_title_row_moves_its_ids_to_the_next_row():
    _, chunks = _chapter_texts(
        "CHAPTER I",
        '<table><tr id="r1"><td id="c1">CHAPTER I</td></tr>'
        '<tr id="r2"><td>a</td></tr></table>',
        native=True,
    )
    row_keys = [c["anchor_keys"] for c in chunks if c.get("node") == "row"]
    assert len(row_keys) == 1
    assert {"ch0.xhtml#r1", "ch0.xhtml#c1", "ch0.xhtml#r2"} <= set(row_keys[0])


@pytest.mark.unit
def test_a_table_that_is_only_the_title_is_dropped_and_keeps_its_ids():
    texts, chunks = _chapter_texts(
        "CHAPTER I",
        '<table id="t"><tr><td id="c1">CHAPTER I</td></tr></table><p>Body.</p>',
        native=True,
    )
    assert texts == ["CHAPTER I", "Body."]
    assert not any(c.get("node") == "table" for c in chunks)
    assert "ch0.xhtml#t" in chunks[0]["anchor_keys"]
    assert "ch0.xhtml#c1" in chunks[0]["anchor_keys"]


@pytest.mark.unit
def test_a_trimmed_title_cell_keeps_its_spans_and_anchor_offsets():
    _, chunks = _chapter_texts(
        "CHAPTER I",
        "<table><tr><td>CHAPTER I The <em>Start</em> <a id='m'></a>of</td>"
        "<td>x</td></tr></table>",
        native=True,
    )
    cell = next(c for c in chunks if "cell" in c)
    assert cell["text"] == "The Start of"
    assert [(s, n) for s, n, _ in cell["spans"]] == [(4, 5)]
    assert cell["anchor_offsets"] == {"ch0.xhtml#m": 10}


def _caption_and_table(html, **kw):
    """A native table's blocks: the caption paragraphs before it, and it."""
    blocks = _conv.extract_blocks_from_html(_doc(html), native_tables=True, **kw)
    assert blocks[-1].get("type") == "table"
    return blocks[:-1], blocks[-1]


@pytest.mark.unit
def test_a_caption_of_several_blocks_stays_several_paragraphs():
    # QA-2: the caption was read inline as one run, so two paragraphs fused
    # into "Table 1-1Monthly totals". It is walked like any block now.
    captions, _ = _caption_and_table(
        "<table><caption><p>Table 1-1</p><p>Monthly totals</p></caption>"
        "<tr><td>a</td></tr></table>"
    )
    assert [b["text"] for b in captions] == ["Table 1-1", "Monthly totals"]


_CAPTION_SHAPES = {
    "plain": "<caption>Census</caption>",
    "two-p": "<caption><p>Table 1-1</p><p>Monthly totals</p></caption>",
    "two-div": "<caption><div>Table 2-1</div><div>Yearly totals</div></caption>",
    "br": "<caption>Table 3-1<br/>With break</caption>",
    "ids": '<caption id="cp">Census <a id="in"></a><em>now</em></caption>',
    "ids-in-blocks": '<caption id="cp"><p id="p1">One</p><p>Two <a id="in"></a>x</p>'
    "</caption>",
    "ids-before-table": '<caption id="cp">Census</caption>',
    "table-id": '<caption id="cp">Census</caption>',
    "table-id-two-p": "<caption><p>Table 1-1</p><p>Monthly totals</p></caption>",
}


@pytest.mark.unit
@pytest.mark.parametrize("shape", list(_CAPTION_SHAPES), ids=list(_CAPTION_SHAPES))
def test_caption_paragraphs_match_the_rows_build(shape):
    # Same paragraphs, styles, ids and offsets as 5.8.8's rows build writes.
    before = '<a id="pre"></a>' if shape in ("ids-before-table", "table-id") else ""
    table = '<table id="t">' if shape.startswith("table-id") else "<table>"
    html = (
        f"{before}{table}{_CAPTION_SHAPES[shape]}<tr><td>a</td><td>b</td></tr></table>"
    )
    css = {"text-align": "center"}
    kw = {"style_resolver": lambda e: css, "base_href": "ch.xhtml"}
    captions, _ = _caption_and_table(html, **kw)
    rows_build = _conv.extract_blocks_from_html(_doc(html), **kw)
    assert captions
    assert captions == rows_build[: len(captions)]
    assert rows_build[len(captions)]["text"] == "a b"


@pytest.mark.unit
def test_ids_before_a_table_go_on_its_caption():
    captions, table = _caption_and_table(
        '<a id="pre"></a><table><caption id="cp">Census</caption>'
        "<tr><td>a</td></tr></table>"
    )
    assert captions[0]["anchor_ids"] == ["pre", "cp"]
    assert table["table"]["anchor_ids"] == []


@pytest.mark.unit
def test_ids_before_a_table_with_an_empty_caption_go_on_the_table():
    captions, table = _caption_and_table(
        '<a id="pre"></a><table><caption id="cp"></caption><tr><td>a</td></tr></table>'
    )
    assert captions == []
    assert table["table"]["anchor_ids"] == ["pre", "cp"]


@pytest.mark.unit
def test_row_groups_are_written_head_body_foot_whatever_the_source_order():
    # QA-4: an HTML4-style <tfoot> before <tbody> came out between the head
    # and the body. Browsers draw it last; so does the Kindle now.
    _, chunks = _chapter_texts(
        "Tides",
        "<table><thead><tr><th>H</th></tr></thead>"
        "<tfoot><tr><td>F</td></tr></tfoot>"
        "<tbody><tr><td>B1</td></tr></tbody>"
        "<tbody><tr><td>B2</td></tr></tbody></table>",
        native=True,
    )
    groups = [c["node"] for c in chunks if c.get("node") in ("head", "body", "foot")]
    assert groups == ["head", "body", "foot"]
    assert _cell_rows(chunks) == [["H"], ["B1"], ["B2"], ["F"]]


@pytest.mark.unit
def test_a_moved_footer_keeps_its_anchors():
    blocks = _conv.extract_blocks_from_html(
        _doc(
            '<table><tfoot id="f"><tr><td id="fc">F</td></tr></tfoot><a id="n"></a>'
            '<tbody><tr id="b"><td>B</td></tr></tbody></table>'
        ),
        native_tables=True,
    )
    rows = blocks[0]["table"]["rows"]
    assert [r["group"] for r in rows] == ["body", "foot"]
    assert rows[0]["anchor_ids"] == ["n", "b"]
    assert rows[1]["anchor_ids"] == ["f"]
    assert rows[1]["cells"][0]["anchor_ids"] == ["fc"]
    assert blocks[0]["text"] == "B\nF"


@pytest.mark.unit
def test_a_captioned_tables_own_id_goes_on_its_caption():
    # Round 1, I1: in the rows build the table's id is pending when the
    # caption is walked, so it names the caption's first paragraph. It
    # stayed on the table's first row natively.
    captions, table = _caption_and_table(
        '<a id="pre"></a><table id="t"><caption>Census</caption>'
        "<tr><td>a</td></tr></table>"
    )
    assert captions[0]["anchor_ids"] == ["pre", "t"]
    assert table["table"]["anchor_ids"] == []
    assert "t" not in table["anchor_ids"]


@pytest.mark.unit
def test_an_empty_captions_table_keeps_its_own_id():
    captions, table = _caption_and_table(
        '<a id="pre"></a><table id="t"><caption id="cp"></caption>'
        "<tr><td>a</td></tr></table>"
    )
    assert captions == []
    assert table["table"]["anchor_ids"] == ["pre", "t", "cp"]


_CAPTIONED_TABLE_BOOK = (
    '<p>Intro text. <a href="ch0.xhtml#t">See the table.</a></p>'
    '<table id="t"><caption>Table 1 caption</caption>'
    "<tr><td>a</td><td>b</td></tr></table><p>After.</p>"
)


def _toc_book_chapters(body, toc, native):
    from kfxgen.native_generator import NativeKFXGenerator

    oeb = _table_book(body)
    oeb.toc = [_TOCNode(title, href) for title, href in toc]
    chapters = extract_chapters_from_oeb(oeb, _silent_log(), native_tables=native)
    content = NativeKFXGenerator()._build_chapter_content(chapters)
    return chapters, content["all_chunks"]


@pytest.mark.unit
@pytest.mark.parametrize("native", [True, False], ids=["native", "rows"])
def test_a_toc_entry_and_a_link_to_a_captioned_table_land_on_its_caption(native):
    chapters, chunks = _toc_book_chapters(
        _CAPTIONED_TABLE_BOOK,
        [("Start", "ch0.xhtml"), ("The Table", "ch0.xhtml#t")],
        native,
    )
    words = [
        (c["title"], " ".join(b["text"] for b in c["blocks"]).split()) for c in chapters
    ]
    assert words == [
        ("Start", ["Intro", "text.", "See", "the", "table."]),
        ("The Table", ["Table", "1", "caption", "a", "b", "After."]),
    ]
    target = [c for c in chunks if "ch0.xhtml#t" in (c.get("anchor_keys") or [])]
    assert [c.get("text") for c in target] == ["Table 1 caption"]


@pytest.mark.unit
def test_a_title_row_in_a_leading_tfoot_is_cut_as_the_rows_build_cuts_it():
    # Round 1, M1: rows are written head, body, foot, but the title cut
    # follows source order, as the rows build saw the rows, so a footer
    # written first that holds the title is still cut.
    body = (
        '<table><tfoot><tr id="f"><td>CHAPTER I</td></tr></tfoot>'
        "<tbody><tr><td>a</td></tr><tr><td>b</td></tr></tbody></table>"
    )
    native, chunks = _chapter_texts("CHAPTER I", body, native=True)
    rows_build, _ = _chapter_texts("CHAPTER I", body, native=False)
    assert _words(native) == _words(rows_build) == ["CHAPTER", "I", "a", "b"]
    assert _cell_rows(chunks) == [["a"], ["b"]]
    first_row = next(c for c in chunks if c.get("node") == "row")
    assert "ch0.xhtml#f" in first_row["anchor_keys"]


@pytest.mark.unit
def test_a_title_split_over_a_leading_tfoot_and_the_body_is_eaten():
    body = (
        "<table><tfoot><tr><td>CHAPTER I.</td></tr></tfoot>"
        "<tbody><tr><td>The Title</td></tr><tr><td>a</td></tr></tbody></table>"
    )
    native, chunks = _chapter_texts("CHAPTER I. The Title", body, native=True)
    rows_build, _ = _chapter_texts("CHAPTER I. The Title", body, native=False)
    assert _words(native) == _words(rows_build)
    assert _cell_rows(chunks) == [["a"]]


@pytest.mark.unit
@pytest.mark.parametrize(
    "middle", ["<tr></tr><tr></tr>", '<tr><a id="x"></a></tr>'], ids=["empty", "anchor"]
)
def test_rowspan_clamp_counts_only_rows_the_generator_writes(middle):
    # Round 1, M2: a row with no cells is never written, so it cannot hold a
    # spanned cell; counting it left a span past the last row written.
    _, table, _ = _block(
        f"<table><tbody><tr><td rowspan='5'>a</td><td>b</td></tr>{middle}"
        "</tbody></table>"
    )
    assert table["table"]["rows"][0]["cells"][0]["rowspan"] == 1
    _, table, _ = _block(
        f"<table><tbody><tr><td rowspan='5'>a</td></tr>{middle}<tr><td>c</td></tr>"
        "</tbody></table>"
    )
    assert table["table"]["rows"][0]["cells"][0]["rowspan"] == 2


# --- notes laid out as a table are written as paragraphs (#268) -------------
#
# A notes section laid out as a two-column table (marker | note) reads better
# as paragraphs, one per note: in a Kindle table the marker sits beside the
# middle of a long note (maintainer's Paperwhite comparison on the #223 book).
# What marks such a table is that the text links into it: at least 80% of its
# rows are link targets from elsewhere in the book. A contents table looks the
# same but its links point out (pg6133), and nothing links into a data table.


def _note_rows(markers, anchor_after=True, backlink=True):
    rows = []
    for n, m in enumerate(markers, 1):
        first = f'<a href="ch.xhtml#r{n}">{m}</a>' if backlink else m
        row = f"<tr><td>{first}</td><td>Note {n} text.</td></tr>"
        anchor = f'<a id="n{n}"></a>'
        rows.append(row + anchor if anchor_after else anchor + row)
    return "".join(rows)


def _targets(*ns, file="notes.xhtml"):
    return {f"{file}#n{n}" for n in ns}


def _is_notes(html, targets):
    return _conv._is_notes_table(_first_table(html), "notes.xhtml", targets)


@pytest.mark.unit
@pytest.mark.parametrize(
    "markers",
    [["1.", "2.", "3."], ["i", "ii", "iii"], ["*", "†", "‡"], ["[1]", "[2]", "[3]"]],
    ids=["numbers", "roman", "symbols", "bracketed"],
)
def test_a_notes_table_is_recognised_by_its_incoming_links(markers):
    html = f"<table>{_note_rows(markers)}</table>"
    assert _is_notes(html, _targets(1, 2, 3))


@pytest.mark.unit
def test_anchors_before_rows_count_too():
    html = f"<table>{_note_rows(['1.', '2.', '3.'], anchor_after=False)}</table>"
    assert _is_notes(html, _targets(1, 2, 3))


@pytest.mark.unit
def test_notes_without_back_links_are_still_notes():
    # One library book: real notes, every row referenced, no link in the
    # first cell. The issue's first rule (a link in every first cell) missed it.
    html = f"<table>{_note_rows(['1.', '2.', '3.'], backlink=False)}</table>"
    assert _is_notes(html, _targets(1, 2, 3))


@pytest.mark.unit
def test_most_rows_referenced_is_enough():
    # One library notes table has a note nothing links to.
    html = f"<table>{_note_rows(['1.', '2.', '3.', '4.', '5.'])}</table>"
    assert _is_notes(html, _targets(1, 2, 3, 4))
    assert not _is_notes(html, _targets(1, 2, 3))


@pytest.mark.unit
@pytest.mark.parametrize(
    "html, targets",
    [
        # pg6133's contents: roman numerals linking out, nothing linking in.
        # The book links elsewhere: with no links at all the check stops
        # before the incoming-links test this case is about (#273 review).
        (
            f"<table>{_note_rows(['I.', 'II.', 'III.'])}</table>",
            {"notes.xhtml#elsewhere"},
        ),
        # Three columns.
        (
            "<table>"
            + "".join(
                f'<tr><td>{n}.</td><td>a</td><td>b</td></tr><a id="n{n}"></a>'
                for n in (1, 2, 3)
            )
            + "</table>",
            _targets(1, 2, 3),
        ),
        # Two rows.
        (f"<table>{_note_rows(['1.', '2.'])}</table>", _targets(1, 2)),
        # A first cell that is a word, not a marker.
        (f"<table>{_note_rows(['1.', 'Apples', '3.'])}</table>", _targets(1, 2, 3)),
        # A row missing its second cell.
        (
            f"<table>{_note_rows(['1.', '2.'])}"
            '<tr><td>3.</td></tr><a id="n3"></a></table>',
            _targets(1, 2, 3),
        ),
        # Links into another file's anchors of the same names don't count.
        (
            f"<table>{_note_rows(['1.', '2.', '3.'])}</table>",
            _targets(1, 2, 3, file="other.xhtml"),
        ),
    ],
    ids=["contents", "three-columns", "two-rows", "word", "missing-cell", "other-file"],
)
def test_tables_that_are_not_notes_stay_native(html, targets):
    assert not _is_notes(html, targets)


@pytest.mark.unit
def test_the_row_layout_is_read_once_per_row_group(monkeypatch):
    # #273 review: reading it for every row made a 5,000-row table take about
    # 9 seconds, the work growing with rows times rows.
    calls = []
    real = _conv._anchors_follow_rows
    monkeypatch.setattr(
        _conv, "_anchors_follow_rows", lambda e: calls.append(e) or real(e)
    )
    markers = [f"{n}." for n in range(1, 301)]
    html = f"<table><tbody>{_note_rows(markers)}</tbody></table>"
    assert _is_notes(html, _targets(*range(1, 301)))
    assert len(calls) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "markers, notes",
    [
        (["xiv", "XV", "mcm"], True),
        (["mild", "civil", "dim"], False),
        (["mix", "lid", "vi"], False),
    ],
    ids=["numerals", "words", "words-and-numeral"],
)
def test_only_real_roman_numerals_count_as_markers(markers, notes):
    # #273 review: [ivxlcdm]+ also matched words such as "mild" and "civil".
    html = f"<table>{_note_rows(markers)}</table>"
    assert _is_notes(html, _targets(1, 2, 3)) is notes


@pytest.mark.unit
def test_an_id_matches_its_percent_encoded_link():
    # #273 review: a link to id "n:1" is written "#n%3A1"; it names that row.
    refs = "".join(f'<p>Claim<a href="#n%3A{n}">{n}</a>.</p>' for n in (1, 2, 3))
    rows = "".join(
        f'<tr><td>{n}.</td><td>Note {n}.</td></tr><a id="n:{n}"></a>' for n in (1, 2, 3)
    )
    oeb = _table_book(f"{refs}<table>{rows}</table>")
    targets = _conv._book_link_targets(oeb)
    table = next(
        e for e in oeb.spine[0].data.iter() if _conv._local_tag(e.tag) == "table"
    )
    assert _conv._is_notes_table(table, "ch0.xhtml", targets)


@pytest.mark.unit
def test_a_notes_table_is_written_as_one_paragraph_per_note():
    html = f"<table>{_note_rows(['1.', '2.', '3.'])}</table><p>After.</p>"
    blocks = extract_blocks_from_html(
        _doc(html),
        native_tables=True,
        base_href="notes.xhtml",
        link_targets=_targets(1, 2, 3),
    )
    assert not any(b.get("type") == "table" for b in blocks)
    assert [b["text"] for b in blocks] == [
        "1. Note 1 text.",
        "2. Note 2 text.",
        "3. Note 3 text.",
        "After.",
    ]
    # calibre's layout: each note's anchor follows its own row (#223).
    assert [b["anchor_ids"] for b in blocks[:3]] == [["n1"], ["n2"], ["n3"]]


@pytest.mark.unit
def test_links_from_another_file_make_a_notes_table():
    # The book-wide pass: the chapter links into the notes file.
    log = _silent_log()
    log.warn = MagicMock()
    infos = []
    log.info = lambda msg, *a, **k: infos.append(str(msg))
    chapter = "".join(
        f'<p>Claim {n}<a id="r{n}" href="ch1.xhtml#n{n}"><sup>{n}</sup></a>.</p>'
        for n in (1, 2, 3)
    )
    notes = (
        "<table>"
        + "".join(
            f'<tr><td><a href="ch0.xhtml#r{n}">{n}.</a></td><td>Note {n} text.</td></tr>'
            f'<a id="n{n}"></a>'
            for n in (1, 2, 3)
        )
        + "</table>"
    )
    chapters = extract_chapters_from_oeb(
        _table_book(chapter, notes), log, native_tables=True
    )
    blocks = [b for c in chapters for b in c.get("blocks") or []]
    assert not any(b.get("type") == "table" for b in blocks)
    assert "1. Note 1 text." in [b["text"] for b in blocks]
    # Written as notes on purpose: logged, not warned about.
    assert not [c for c in log.warn.call_args_list if "table" in str(c).lower()]
    assert any("1 notes table" in m and "#268" in m for m in infos), infos


@pytest.mark.unit
def test_without_incoming_links_the_same_table_stays_native():
    notes = (
        "<table>"
        + "".join(
            f'<tr><td><a href="ch0.xhtml#r{n}">{n}.</a></td><td>Note {n} text.</td></tr>'
            f'<a id="n{n}"></a>'
            for n in (1, 2, 3)
        )
        + "</table>"
    )
    chapters = extract_chapters_from_oeb(
        _table_book("<p>No links here.</p>", notes), _silent_log(), native_tables=True
    )
    blocks = [b for c in chapters for b in c.get("blocks") or []]
    assert any(b.get("type") == "table" for b in blocks)


# --- comments and processing instructions are not text (#252) --------------
#
# lxml gives a comment or a processing instruction a `.text`, which the
# walkers read as content: pg1998 printed "H2 anchor" 142 times, and a
# publisher's <?dp n="12" folio="ix"?> page markers printed as
# 'n="12" folio="ix"'. Their tails are real text and stay.


def _texts(body, **kw):
    return [b["text"] for b in extract_blocks_from_html(_doc(body), **kw)]


@pytest.mark.unit
@pytest.mark.parametrize(
    "body, texts",
    [
        ("<p>x</p><!-- three --><p>y</p>", ["x", "y"]),
        ("<p>alpha<!-- one -->beta</p>", ["alphabeta"]),
        ("<div>lead<!-- two --><p>para</p></div>", ["lead", "para"]),
        ("<p><em>a<!-- four -->b</em></p>", ["ab"]),
        ("<!-- H2 anchor --><h2>Chapter</h2>", ["Chapter"]),
        (
            '<p>The road went on.<?dp n="12" folio="ix" ?> It was late.</p>',
            ["The road went on. It was late."],
        ),
        ('<p>x</p><?dp n="13"?><p>y</p>', ["x", "y"]),
    ],
    ids=[
        "between-paragraphs",
        "inside-a-paragraph",
        "in-a-container",
        "inside-emphasis",
        "before-a-heading",
        "page-marker-inside",
        "page-marker-between",
    ],
)
def test_comments_and_processing_instructions_are_not_text(body, texts):
    assert _texts(body) == texts


@pytest.mark.unit
def test_a_comment_keeps_the_emphasis_of_its_tail():
    (block,) = extract_blocks_from_html(_doc("<p><em>a<!-- c -->b</em> c</p>"))
    assert block["text"] == "ab c"
    assert block["spans"] == [(0, 2, frozenset({I}))]


@pytest.mark.unit
@pytest.mark.parametrize("native", [True, False], ids=["native", "rows"])
def test_comments_in_a_table_are_not_text(native):
    body = (
        "<table><tr><td>x<!-- c -->y</td><td>z</td></tr>"
        "<!-- between rows --><tr><td>p<?dp n='2'?>q</td><td>r</td></tr></table>"
    )
    blocks = extract_blocks_from_html(_doc(body), native_tables=native)
    if native:
        (table,) = blocks
        assert [[c["text"] for c in r["cells"]] for r in table["table"]["rows"]] == [
            ["xy", "z"],
            ["pq", "r"],
        ]
    else:
        assert [b["text"] for b in blocks] == ["xy z", "pq r"]


@pytest.mark.unit
def test_a_comment_in_a_paragraph_cell_is_not_a_paragraph():
    blocks = extract_blocks_from_html(
        _doc("<table><tr><td><p>a</p><!-- gap --><p>b</p></td></tr></table>"),
        native_tables=True,
    )
    (table,) = blocks
    cell = table["table"]["rows"][0]["cells"][0]
    assert [p["text"] for p in cell["paragraphs"]] == ["a", "b"]


# --- a listing belongs to the chapter that held it (#276) -------------------
#
# A discarded contents listing records the index of the block after it. When
# that block starts the next chapter, the listing was credited to that chapter,
# whose content the contents rebuild then replaced (pg45130's part page; and,
# once #252 removed comment blocks that happened to sit in between, pg2160's
# letter "To Mr HENRY DAVIS"). It belongs to the next chapter only when that
# chapter's TOC entry points at the listing itself or an id inside it
# (`<div id="toc" class="toc">`).


def _listing_chapters(files, toc):
    spine = []
    for href, body in files:
        at = []
        blocks = extract_blocks_from_html(_doc(body), base_href=href, nav_listing_at=at)
        spine.append(
            {
                "href": href,
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": _conv._attach_anchor_keys(blocks, href),
                "nav_listing_at": at,
                "note_ids": set(),
            }
        )
    toc = [{"title": t, "href": h} for t, h in toc]
    return _assemble_chapters_by_coordinate(spine, toc, _silent_log())


_LISTING = (
    '<div class="toc"><p><a href="front.xhtml#p1">Part One</a></p>'
    '<p><a href="c1.xhtml">Chapter 1</a></p></div>'
)


@pytest.mark.unit
def test_a_listing_ending_where_the_next_chapter_starts_belongs_to_the_one_before():
    chapters = _listing_chapters(
        [
            (
                "front.xhtml",
                f'<h1 id="nav">Navigation</h1>{_LISTING}'
                '<h1 id="p1">Part One</h1><p>Part text.</p>',
            ),
            ("c1.xhtml", "<h1>Chapter 1</h1><p>One.</p>"),
        ],
        [
            ("Navigation", "front.xhtml#nav"),
            ("Part One", "front.xhtml#p1"),
            ("Chapter 1", "c1.xhtml"),
        ],
    )
    flagged = {c["title"]: bool(c.get("_had_nav_listing")) for c in chapters}
    assert flagged == {"Navigation": True, "Part One": False, "Chapter 1": False}


@pytest.mark.unit
def test_a_toc_entry_on_the_listing_itself_owns_it():
    listing = _LISTING.replace('<div class="toc">', '<div id="toc" class="toc">')
    chapters = _listing_chapters(
        [
            (
                "front.xhtml",
                f"<h1>Title</h1><p>By someone.</p>{listing}"
                '<p>After the listing.</p><h1 id="p1">Part One</h1><p>Part text.</p>',
            ),
            ("c1.xhtml", "<h1>Chapter 1</h1><p>One.</p>"),
        ],
        [
            ("Title", "front.xhtml"),
            ("Contents", "front.xhtml#toc"),
            ("Part One", "front.xhtml#p1"),
            ("Chapter 1", "c1.xhtml"),
        ],
    )
    flagged = {c["title"]: bool(c.get("_had_nav_listing")) for c in chapters}
    assert flagged == {
        "Title": False,
        "Contents": True,
        "Part One": False,
        "Chapter 1": False,
    }


@pytest.mark.unit
def test_a_toc_entry_on_an_id_inside_the_listing_owns_it():
    # #286 review: the TOC may name a heading inside the listing rather than
    # the listing element itself.
    listing = _LISTING.replace(
        '<div class="toc">', '<div class="toc"><h2 id="toc-head">Contents</h2>'
    )
    chapters = _listing_chapters(
        [
            (
                "front.xhtml",
                f"<h1>Title</h1><p>By someone.</p>{listing}"
                '<p>After the listing.</p><h1 id="p1">Part One</h1><p>Part text.</p>',
            ),
            ("c1.xhtml", "<h1>Chapter 1</h1><p>One.</p>"),
        ],
        [
            ("Title", "front.xhtml"),
            ("Contents", "front.xhtml#toc-head"),
            ("Part One", "front.xhtml#p1"),
            ("Chapter 1", "c1.xhtml"),
        ],
    )
    flagged = {c["title"]: bool(c.get("_had_nav_listing")) for c in chapters}
    assert flagged == {
        "Title": False,
        "Contents": True,
        "Part One": False,
        "Chapter 1": False,
    }


@pytest.mark.unit
def test_a_listing_at_the_start_of_a_file_stays_with_that_file():
    chapters = _listing_chapters(
        [
            ("a.xhtml", "<h1>Opening</h1><p>Start.</p>"),
            ("toc.xhtml", f"{_LISTING}<p>Notes on the contents.</p>"),
            ("c1.xhtml", "<h1>Chapter 1</h1><p>One.</p>"),
        ],
        [
            ("Opening", "a.xhtml"),
            ("Contents", "toc.xhtml"),
            ("Chapter 1", "c1.xhtml"),
        ],
    )
    flagged = {c["title"]: bool(c.get("_had_nav_listing")) for c in chapters}
    assert flagged == {"Opening": False, "Contents": True, "Chapter 1": False}


@pytest.mark.unit
def test_a_title_page_holding_a_listing_is_not_replaced_by_the_contents_page():
    # pg2160 and pg2701 print their contents listing on the title page. Once
    # the listing is credited to the page that held it (#276), the rebuild
    # took the short title-and-byline page for the contents page and replaced
    # it. A contents page keeps at most one block of its own: its heading.
    chapters = [
        {
            "title": "THE EXPEDITION",
            "text": "THE EXPEDITION\n\nby Tobias Smollett",
            "blocks": [{"text": "THE EXPEDITION"}, {"text": "by Tobias Smollett"}],
            "_had_nav_listing": True,
        },
        {"title": "Letter", "text": "Dear sir, a long letter."},
    ]
    assert _conv._nav_listing_contents_chapter(chapters) is None


@pytest.mark.unit
def test_a_listing_page_with_just_its_heading_becomes_the_contents_page():
    chapters = [
        {
            "title": "Navigation",
            "text": "Navigation",
            "blocks": [{"text": "Navigation"}],
            "_had_nav_listing": True,
        },
        {"title": "Part One", "text": "Part text."},
    ]
    assert _conv._nav_listing_contents_chapter(chapters) is chapters[0]


# --- blocks inside a cell are separated (#279) -------------------------------
#
# A cell's blocks were joined with nothing between them when the HTML had no
# whitespace there: real calibre wrote <td>cellone<p>celltwo</p></td> as
# "cellonecelltwo". Native paragraph cells (#261) walk each block apart; every
# other path walks the cell inline and now puts a space at block boundaries.


def _cell_texts(body, native):
    blocks = extract_blocks_from_html(_doc(body), native_tables=native)
    out = []
    for b in blocks:
        if b.get("type") == "table":
            for r in b["table"]["rows"]:
                for c in r["cells"]:
                    out.append(
                        [p["text"] for p in c["paragraphs"]]
                        if c.get("paragraphs")
                        else c["text"]
                    )
        else:
            out.append(b["text"])
    return out


@pytest.mark.unit
@pytest.mark.parametrize(
    "cells, native, expected",
    [
        ("<td><p>one two</p><p>three</p></td>", False, ["one two three"]),
        ("<td><p>one two</p><p>three</p></td>", True, [["one two", "three"]]),
        ("<td>one<p>two</p></td>", True, ["one two"]),
        ("<td><p>one</p>two</td>", True, ["one two"]),
        (
            '<td><img src="a.png"/><p>one</p><p>two</p></td>',
            True,
            [["[img]", "one", "two"]],
        ),
        (
            '<td><img src="a.png"/></td><td><p>one</p><p>two</p></td>',
            True,
            [["[img]"], ["one", "two"]],
        ),
        ("<td>cellone<p>celltwo</p></td>", False, ["cellone celltwo"]),
    ],
    ids=[
        "rows-two-p",
        "native-two-p",
        "native-lead-text",
        "native-tail-text",
        "image-same-cell",
        "image-other-cell",
        "rows-lead-text",
    ],
)
def test_blocks_inside_a_cell_do_not_run_together(cells, native, expected):
    # A picture is its own paragraph in a cell (#262); "[img]" stands for it.
    texts = _cell_texts(f"<table><tr>{cells}</tr></table>", native)
    pic = lambda t: IMG_TOKEN_RE.sub("[img]", t)  # noqa: E731
    texts = [[pic(x) for x in t] if isinstance(t, list) else pic(t) for t in texts]
    assert texts == expected


@pytest.mark.unit
def test_a_notes_table_keeps_a_notes_paragraphs_apart():
    # The notes path (#268) writes each row as one paragraph through the rows
    # path: "para apara b" before #279.
    rows = "".join(
        f'<tr><td><a href="ch.xhtml#r{n}">{n}.</a></td>'
        f"<td><p>para {n}a</p><p>para {n}b</p></td></tr>"
        f'<a id="n{n}"></a>'
        for n in (1, 2, 3)
    )
    blocks = extract_blocks_from_html(
        _doc(f"<table>{rows}</table>"),
        native_tables=True,
        base_href="notes.xhtml",
        link_targets={f"notes.xhtml#n{n}" for n in (1, 2, 3)},
    )
    assert [b["text"] for b in blocks] == [
        "1. para 1a para 1b",
        "2. para 2a para 2b",
        "3. para 3a para 3b",
    ]


@pytest.mark.unit
def test_inline_text_inside_a_cell_is_unchanged():
    # Only block boundaries gain a space; runs inside one block stay as written.
    texts = _cell_texts("<table><tr><td>a<em>b</em>c</td></tr></table>", False)
    assert texts == ["abc"]


# --- a cell's vertical alignment (#269) --------------------------------------
#
# A native cell carries its computed vertical-align: its own, else its row's,
# else its row group's. calibre's Stylizer computes the CSS but ignores the
# HTML `valign` attribute, so the converter reads that itself. calibre's UA
# sheet gives td/tr `inherit` and row groups (and `table > tr`) `middle`.


def _declared_valign(elem):
    # Stands in for the resolver's `vertical-align` (Stylizer.get): the
    # element's own inline rule, else the UA sheet's value.
    m = re.search(r"vertical-align:\s*([\w-]+)", elem.get("style") or "")
    if m:
        return {"vertical-align": m.group(1)}
    tag = _conv._local_tag(elem.tag)
    parent = elem.getparent()
    if tag in ("tbody", "thead", "tfoot") or (
        tag == "tr" and parent is not None and _conv._local_tag(parent.tag) == "table"
    ):
        return {"vertical-align": "middle"}
    if tag in ("td", "th", "tr"):
        return {"vertical-align": "inherit"}
    return {}


def _first_cell_valign(table_html, resolver=_declared_valign):
    blocks = extract_blocks_from_html(
        _doc(table_html), style_resolver=resolver, native_tables=True
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    return table["table"]["rows"][0]["cells"][0]["valign"]


_LONG = "<td>a long neighbouring cell</td>"


@pytest.mark.unit
@pytest.mark.parametrize(
    "table_html, expected",
    [
        (f"<table><tbody><tr><td>x</td>{_LONG}</tr></tbody></table>", "middle"),
        (f"<table><tr><td>x</td>{_LONG}</tr></table>", "middle"),
        (
            f'<table><tr><td style="vertical-align: top">x</td>{_LONG}</tr></table>',
            "top",
        ),
        (
            f'<table><tr><td style="vertical-align: bottom">x</td>{_LONG}</tr></table>',
            "bottom",
        ),
        (
            f'<table><tr><td style="vertical-align: baseline">x</td>{_LONG}</tr></table>',
            "baseline",
        ),
        (
            f'<table><tbody><tr style="vertical-align: top"><td>x</td>{_LONG}</tr></tbody></table>',
            "top",
        ),
        (
            f'<table><tbody style="vertical-align: bottom"><tr><td>x</td>{_LONG}</tr></tbody></table>',
            "bottom",
        ),
        (f'<table><tr><td valign="top">x</td>{_LONG}</tr></table>', "top"),
        (f'<table><tr><td valign="BOTTOM">x</td>{_LONG}</tr></table>', "bottom"),
        (
            f'<table><tbody><tr valign="top"><td>x</td>{_LONG}</tr></tbody></table>',
            "top",
        ),
        (f'<table><tr valign="bottom"><td>x</td>{_LONG}</tr></table>', "bottom"),
        (
            f'<table><tbody valign="top"><tr><td>x</td>{_LONG}</tr></tbody></table>',
            "top",
        ),
        (
            f'<table><tr><td valign="top" style="vertical-align: middle">x</td>{_LONG}</tr></table>',
            "middle",
        ),
        (
            f'<table><tbody style="vertical-align: bottom"><tr valign="top"><td>x</td>{_LONG}</tr></tbody></table>',
            "top",
        ),
        (
            f'<table><tbody><tr style="vertical-align: top"><td style="vertical-align: inherit">x</td>{_LONG}</tr></tbody></table>',
            "top",
        ),
        (f'<table><tr><td valign="sideways">x</td>{_LONG}</tr></table>', "middle"),
    ],
    ids=[
        "unset-tbody",
        "unset-bare-tr",
        "css-top",
        "css-bottom",
        "css-baseline",
        "row-css-top",
        "group-css-bottom",
        "attr-top",
        "attr-uppercase",
        "row-attr-top",
        "bare-row-attr-bottom",
        "group-attr-beats-ua-middle",
        "cell-css-beats-cell-attr",
        "row-attr-beats-group-css",
        "explicit-inherit",
        "attr-invalid",
    ],
)
def test_a_cell_carries_its_computed_vertical_align(table_html, expected):
    assert _first_cell_valign(table_html) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "table_html, expected",
    [
        (f"<table><tr><td>x</td>{_LONG}</tr></table>", None),
        (f'<table><tr><td valign="top">x</td>{_LONG}</tr></table>', "top"),
        (
            f'<table><tbody valign="bottom"><tr><td>x</td>{_LONG}</tr></tbody></table>',
            "bottom",
        ),
    ],
    ids=["unset", "attr", "group-attr"],
)
def test_without_a_stylizer_only_valign_counts(table_html, expected):
    assert _first_cell_valign(table_html, resolver=None) == expected


@pytest.mark.unit
def test_a_paragraph_cell_carries_its_vertical_align():
    html = f'<table><tr><td valign="top"><p>one</p><p>two</p></td>{_LONG}</tr></table>'
    blocks = extract_blocks_from_html(
        _doc(html), style_resolver=_declared_valign, native_tables=True
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    cell = table["table"]["rows"][0]["cells"][0]
    assert cell.get("paragraphs") and cell["valign"] == "top"


# --- pictures in table cells (#262) ------------------------------------------
#
# A table with an <img> in a cell stays native; the cell becomes a container of
# paragraphs in which each picture is a paragraph of its own (an image token),
# as Kindle Previewer writes a cell's picture as its own $271 entry.


def _picture_cells(table_html):
    blocks = extract_blocks_from_html(_doc(table_html), native_tables=True)
    tables = [b for b in blocks if b.get("type") == "table"]
    if not tables:
        return None
    return [
        [IMG_TOKEN_RE.sub("[img]", p["text"]) for p in c.get("paragraphs") or [c]]
        for r in tables[0]["table"]["rows"]
        for c in r["cells"]
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "cells, expected",
    [
        ('<td><img src="a.png"/></td><td>text</td>', [["[img]"], ["text"]]),
        (
            '<td><img src="a.png"/><br/>Captain Smith</td><td>x</td>',
            [["[img]", "Captain Smith"], ["x"]],
        ),
        (
            '<td><p><img src="a.png"/></p><p>Captain Smith</p></td><td>x</td>',
            [["[img]", "Captain Smith"], ["x"]],
        ),
        (
            '<td>Captain Smith<br/><img src="a.png"/></td><td>x</td>',
            [["Captain Smith", "[img]"], ["x"]],
        ),
        (
            '<td><img src="a.png"/><img src="b.png"/></td><td>x</td>',
            [["[img]", "[img]"], ["x"]],
        ),
    ],
    ids=[
        "picture-only",
        "caption-after-br",
        "caption-paragraph",
        "caption-above",
        "two-pictures",
    ],
)
def test_a_picture_in_a_cell_keeps_the_table_native(cells, expected):
    assert (
        _picture_cells(f"<table><tr>{cells}</tr><tr><td>c</td><td>d</td></tr></table>")[
            :2
        ]
        == expected
    )


@pytest.mark.unit
def test_a_picture_inside_a_line_of_text_is_split_out_of_it():
    # kfxgen writes a picture as its own entry outside tables too; the text on
    # either side stays, in order.
    cells = _picture_cells(
        '<table><tr><td>Press <img src="i.png"/> to stop.</td><td>x</td></tr></table>'
    )
    joined = " ".join(cells[0])
    assert "[img]" in joined and joined.index("Press") < joined.index(
        "[img]"
    ) < joined.index("to stop.")


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["svg", "video", "object", "math"])
def test_other_objects_in_a_cell_still_send_the_table_to_rows(tag):
    assert (
        _picture_cells(f"<table><tr><td><{tag}></{tag}></td><td>x</td></tr></table>")
        is None
    )


# --- table and cell borders (#264) -------------------------------------------
#
# A native table and each cell carry their borders, as Kindle Previewer 4.0.1
# writes them: per side (style, width in Previewer's points, colour as ARGB or
# None for the default). calibre computes a CSS width in points at 0.72 per
# pixel; Previewer writes 0.45 per pixel. The `border` attribute, which the
# Stylizer ignores, draws an outset grey frame of N x 0.45pt on the table and
# an inset 0.45pt border on each cell; a cell's own CSS border wins.

_GREY = 0xFF808080
_SIDES = ("top", "right", "bottom", "left")


def _border_resolver(elem):
    # Stands in for the resolver's computed `border-sides`: class "b" draws
    # 1px solid black all round, "red" 2px dotted red, "under" a bottom rule.
    classes = (elem.get("class") or "").split()
    if "b" in classes:
        return {"border-sides": (("solid", 0.72, "black"),) * 4}
    if "red" in classes:
        return {"border-sides": (("dotted", 1.44, "red"),) * 4}
    if "under" in classes:
        none = ("none", "medium", "currentColor")
        return {"border-sides": (none, none, ("solid", 0.72, "#336699"), none)}
    return {}


def _bordered(table_html, resolver=_border_resolver):
    blocks = extract_blocks_from_html(
        _doc(table_html), style_resolver=resolver, native_tables=True
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    first_cell = table["table"]["rows"][0]["cells"][0]
    return table["table"].get("border"), first_cell.get("border")


def _all(style, width, colour):
    return dict.fromkeys(_SIDES, (style, width, colour))


_TWO = "<tr><td{c}>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr>"


@pytest.mark.unit
@pytest.mark.parametrize(
    "table_html, table_border, cell_border",
    [
        (f"<table>{_TWO.format(c='')}</table>", None, None),
        (
            f'<table class="b">{_TWO.format(c="")}</table>',
            _all("solid", 0.45, None),
            None,
        ),
        (
            f"<table>{_TWO.format(c=' class="red"')}</table>",
            None,
            _all("dotted", 0.9, 0xFFFF0000),
        ),
        (
            f"<table>{_TWO.format(c=' class="under"')}</table>",
            None,
            {"bottom": ("solid", 0.45, 0xFF336699)},
        ),
        (
            f'<table border="1">{_TWO.format(c="")}</table>',
            _all("outset", 0.45, _GREY),
            _all("inset", 0.45, None),
        ),
        (
            f'<table border="3">{_TWO.format(c="")}</table>',
            _all("outset", 1.35, _GREY),
            _all("inset", 0.45, None),
        ),
        (f'<table border="0">{_TWO.format(c="")}</table>', None, None),
        (
            f'<table border="1">{_TWO.format(c=' class="red"')}</table>',
            _all("outset", 0.45, _GREY),
            _all("dotted", 0.9, 0xFFFF0000),
        ),
    ],
    ids=[
        "none",
        "css-table",
        "css-cell-dotted-red",
        "css-cell-bottom-only",
        "attr-1",
        "attr-3",
        "attr-0",
        "cell-css-beats-attr",
    ],
)
def test_a_table_and_its_cells_carry_their_borders(
    table_html, table_border, cell_border
):
    assert _bordered(table_html) == (table_border, cell_border)


@pytest.mark.unit
def test_without_a_stylizer_only_the_border_attribute_counts():
    assert _bordered(
        f'<table border="2" class="b">{_TWO.format(c="")}</table>', None
    ) == (
        _all("outset", 0.9, _GREY),
        _all("inset", 0.45, None),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, argb",
    [
        ("black", None),
        ("currentColor", None),
        ("red", 0xFFFF0000),
        ("Gray", 0xFF808080),
        ("grey", 0xFF808080),
        ("#f00", 0xFFFF0000),
        ("#336699", 0xFF336699),
        ("rgb(51, 102, 153)", 0xFF336699),
        ("#000000", None),
        ("darkslategray", None),
        (None, None),
    ],
)
def test_border_colour_as_argb(value, argb):
    assert _conv._border_colour(value) == argb


@pytest.mark.unit
@pytest.mark.parametrize(
    "side, expected",
    [
        (("hidden", 0.72, "black"), None),
        (("none", 0.72, "black"), None),
        (("solid", 0.0, "black"), None),
        (("solid", "thin", "black"), {"top": ("solid", 0.45, None)}),
        (("solid", "medium", "red"), {"top": ("solid", 1.35, 0xFFFF0000)}),
        (("solid", 0.72, "transparent"), None),
        (("solid", 0.72, "rgba(0, 0, 0, 0)"), None),
        (("solid", 0.72, "rgba(255, 0, 0, 0.0)"), None),
        (("solid", 0.72, "rgba(255, 0, 0, 1)"), {"top": ("solid", 0.45, 0xFFFF0000)}),
    ],
    ids=[
        "hidden",
        "none",
        "zero-width",
        "thin",
        "medium",
        "transparent",
        "rgba-zero",
        "rgba-zero-float",
        "rgba-opaque",
    ],
)
def test_css_border_reads_one_computed_side(side, expected):
    none = ("none", "medium", "currentColor")
    assert _conv._css_border({"border-sides": (side, none, none, none)}) == expected


# --- column widths (#264) ----------------------------------------------------
#
# A native table records its columns' percentage widths, as Kindle Previewer
# 4.0.1 writes them into $152: from <col> (style or width attribute, `span`
# repeating it), else from the cells (the last row that sets a column wins; a
# spanning cell sets none). Absolute widths are left out: Previewer turns them
# into percentages by laying the page out, which can't be reproduced.


def _width_resolver(elem):
    # Stands in for the resolver's declared `width` (Stylizer.get).
    m = re.search(r"width:\s*([\d.]+(?:%|px|em))", elem.get("style") or "")
    return {"width": m.group(1)} if m else {}


def _widths(table_html, resolver=_width_resolver):
    blocks = extract_blocks_from_html(
        _doc(table_html), style_resolver=resolver, native_tables=True
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    t = table["table"]
    flags = [[c.get("width_set", False) for c in r["cells"]] for r in t["rows"]]
    return t.get("column_widths"), flags


def _rows3(*attrs):
    return "".join(
        "<tr>" + "".join(f"<td{a}>x</td>" for a in row) + "</tr>" for row in attrs
    )


def _sw(value):
    return f' style="width:{value}"'


def _aw(value):
    return f' width="{value}"'


_PLAIN3 = ("", "", "")
_COLS = (
    f"<table><colgroup><col{_sw('20%')}/><col{_sw('30%')}/><col{_sw('50%')}/>"
    "</colgroup>"
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "table_html, expected",
    [
        (f"<table>{_rows3(_PLAIN3)}</table>", (None, [[False] * 3])),
        (
            f"{_COLS}{_rows3(_PLAIN3)}</table>",
            (("col", [20.0, 30.0, 50.0]), [[False] * 3]),
        ),
        (
            f"<table><col{_aw('20%')}/><col/><col{_aw('50%')}/>{_rows3(_PLAIN3)}</table>",
            (("col", [20.0, None, 50.0]), [[False] * 3]),
        ),
        (
            f'<table><col span="2"{_sw("25%")}/><col{_sw("50%")}/>{_rows3(_PLAIN3)}</table>',
            (("col", [25.0, 25.0, 50.0]), [[False] * 3]),
        ),
        (
            f"<table>{_rows3(('', _sw('40%'), ''))}</table>",
            (("cell", [None, 40.0, None]), [[False, True, False]]),
        ),
        (
            f"<table>{_rows3((_aw('25%'), _aw('25%'), _aw('50%')))}</table>",
            (("cell", [25.0, 25.0, 50.0]), [[True, True, True]]),
        ),
        (
            f"<table>{_rows3((_sw('30%'), '', ''), (_sw('60%'), '', ''))}</table>",
            (
                ("cell", [60.0, None, None]),
                [[True, False, False], [True, False, False]],
            ),
        ),
        (
            f"<table>{_rows3((_sw('100px'), _sw('5em'), _aw('80')))}</table>",
            (None, [[False] * 3]),
        ),
        (
            f'<table><tr><td colspan="2"{_sw("60%")}>x</td><td>y</td></tr>'
            f"{_rows3(_PLAIN3)}</table>",
            (None, [[True, False], [False] * 3]),
        ),
        (
            f"<table><tr><td{_aw('20%')}{_sw('120px')}>x</td><td>y</td><td>z</td></tr></table>",
            (None, [[False] * 3]),
        ),
        (
            '<table><tr><td rowspan="2">a</td><td>b</td><td>c</td></tr>'
            f"<tr><td{_aw('40%')}>x</td><td{_aw('60%')}>y</td></tr></table>",
            (("cell", [None, 40.0, 60.0]), [[False] * 3, [True, True]]),
        ),
    ],
    ids=[
        "none",
        "col-style",
        "col-attr-partial",
        "col-span",
        "cell-second-column",
        "cell-attr-all",
        "last-row-wins",
        "absolute-units-ignored",
        "spanning-cell-sets-none",
        "css-absolute-beats-attr-percent",
        "rowspan-shifts-columns",
    ],
)
def test_a_table_records_its_column_widths(table_html, expected):
    assert _widths(table_html) == expected


@pytest.mark.unit
def test_without_a_stylizer_width_attributes_still_count():
    html = f"<table><col{_aw('20%')}/><col{_aw('80%')}/>{_rows3(('', ''))}</table>"
    assert _widths(html, None)[0] == ("col", [20.0, 80.0])


@pytest.mark.unit
@pytest.mark.parametrize(
    "attrs, expected",
    [
        ("", None),
        (_sw("100%"), 100.0),
        (_aw("100%"), 100.0),
        (_sw("80%"), 80.0),
        (_sw("300px"), None),
    ],
    ids=["none", "css-100", "attr-100", "css-80", "absolute"],
)
def test_a_table_records_its_percentage_width(attrs, expected):
    # Kindle Previewer 4.0.1 writes a percentage table width as min-width
    # $63 (and width $56 at 100%); without it the Kindle sizes the table to
    # its content and narrow columns' widths don't show (#264).
    blocks = extract_blocks_from_html(
        _doc(f"<table{attrs}>{_rows3(_PLAIN3)}</table>"),
        style_resolver=_width_resolver,
        native_tables=True,
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    assert table["table"].get("width") == expected


# --- cell padding and row borders (#264) -------------------------------------
#
# A cell's declared padding (calibre's UA default is 1px, which kfxgen already
# writes) is recorded in ems per side, as Kindle Previewer 4.0.1 converts it
# (1px = 0.45pt, 12pt = 1em), or ("%", n) for a percentage. Under
# border-collapse a row's and a row group's CSS borders are recorded too;
# Previewer writes them on the $279 / row group. Separated, it writes none.


def _pad_resolver(elem):
    out = {}
    tag = _conv._local_tag(elem.tag)
    m = re.search(r"padding:\s*([^;]+)", elem.get("style") or "")
    if tag in ("td", "th"):
        vals = m.group(1).split() if m else ["1px"]
        vals = (vals * 4)[:4] if len(vals) == 1 else (vals + vals)[:4]
        out["padding-sides"] = tuple(vals)
    classes = (elem.get("class") or "").split()
    if tag == "table":
        out["border-collapse"] = "collapse" if "coll" in classes else "separate"
    if "rb" in classes:
        none = ("none", "medium", "currentColor")
        out["border-sides"] = (none, none, ("solid", 0.72, "black"), none)
    return out


def _cell_padding(td_attrs, resolver=_pad_resolver):
    blocks = extract_blocks_from_html(
        _doc(f"<table><tr><td{td_attrs}>a</td><td>b</td></tr></table>"),
        style_resolver=resolver,
        native_tables=True,
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    return table["table"]["rows"][0]["cells"][0].get("padding")


@pytest.mark.unit
@pytest.mark.parametrize(
    "td_attrs, expected",
    [
        ("", None),
        (
            ' style="padding: 1em"',
            dict.fromkeys(("top", "right", "bottom", "left"), 1.0),
        ),
        (
            ' style="padding: 0.5em 10px"',
            {"top": 0.5, "right": 0.375, "bottom": 0.5, "left": 0.375},
        ),
        (
            ' style="padding: 6pt"',
            dict.fromkeys(("top", "right", "bottom", "left"), 0.5),
        ),
        (' style="padding: 0"', dict.fromkeys(("top", "right", "bottom", "left"), 0.0)),
        (
            ' style="padding: 5%"',
            dict.fromkeys(("top", "right", "bottom", "left"), ("%", 5.0)),
        ),
        (
            ' style="padding: 2ex 1em"',
            {"top": 0.0375, "right": 1.0, "bottom": 0.0375, "left": 1.0},
        ),
    ],
    ids=[
        "default-1px",
        "em",
        "em-and-px",
        "pt",
        "zero",
        "percent",
        "unreadable-keeps-default",
    ],
)
def test_a_cell_records_its_padding_in_ems(td_attrs, expected):
    got = _cell_padding(td_attrs)
    if isinstance(expected, dict):
        assert got.keys() == expected.keys()
        for side, want in expected.items():
            if isinstance(want, tuple):
                assert got[side] == want
            else:
                assert got[side] == pytest.approx(want)
    else:
        assert got == expected


@pytest.mark.unit
def test_without_a_stylizer_a_cell_has_no_padding():
    assert _cell_padding(' style="padding: 1em"', None) is None


def _row_borders(table_attrs, row_attrs, group_attrs=""):
    blocks = extract_blocks_from_html(
        _doc(
            f"<table{table_attrs}><tbody{group_attrs}><tr{row_attrs}><td>a</td><td>b</td></tr>"
            "<tr><td>c</td><td>d</td></tr></tbody></table>"
        ),
        style_resolver=_pad_resolver,
        native_tables=True,
    )
    (table,) = [b for b in blocks if b.get("type") == "table"]
    first = table["table"]["rows"][0]
    return first.get("border"), first.get("group_border")


_RULE = {"bottom": ("solid", 0.45, None)}


@pytest.mark.unit
@pytest.mark.parametrize(
    "table_attrs, row_attrs, group_attrs, expected",
    [
        (' class="coll"', ' class="rb"', "", (_RULE, None)),
        ("", ' class="rb"', "", (None, None)),
        (' class="coll"', "", ' class="rb"', (None, _RULE)),
        (' class="coll"', "", "", (None, None)),
    ],
    ids=["collapse-row", "separate-row", "collapse-group", "collapse-none"],
)
def test_rows_and_groups_carry_their_borders_only_under_collapse(
    table_attrs, row_attrs, group_attrs, expected
):
    assert _row_borders(table_attrs, row_attrs, group_attrs) == expected


@pytest.mark.unit
def test_table_width_is_the_widest_row_not_the_last():
    # #297 review: `width = end` instead of `max(width, end)` was not caught.
    # The native check can't see it (any row past the limit returns at once),
    # so the function's own value is pinned: the widest row wins.
    doc = _doc(
        "<table><tr>" + "<td>x</td>" * 5 + "</tr><tr><td>a</td><td>b</td></tr></table>"
    )
    (table,) = list(doc.iter("{http://www.w3.org/1999/xhtml}table"))
    assert _conv._table_width(table) == 5


# --- percent-encoded link targets (#278) -------------------------------------
#
# A link's file name and fragment are percent-encoded in the markup, while an
# id is written as-is and calibre keeps some file names encoded and others not.
# Link keys and anchor keys are now both built from the decoded file name, and
# a link's fragment is decoded, so the two meet.


@pytest.mark.unit
@pytest.mark.parametrize(
    "href, base, expected",
    [
        ("c2.xhtml#fn%3A1", "c1.xhtml", "c2.xhtml#fn:1"),
        ("c2.xhtml#%C3%A9", "c1.xhtml", "c2.xhtml#é"),
        ("my%20notes.xhtml#t1", "c1.xhtml", "my notes.xhtml#t1"),
        ("not%C3%A9s.xhtml#t1", "c1.xhtml", "notés.xhtml#t1"),
        ("my notes.xhtml#t1", "c1.xhtml", "my notes.xhtml#t1"),
        ("#fn%3A1", "my%20notes.xhtml", "my notes.xhtml#fn:1"),
        ("c2.xhtml#plain", "c1.xhtml", "c2.xhtml#plain"),
    ],
    ids=[
        "frag-colon",
        "frag-utf8",
        "file-space",
        "file-utf8",
        "file-literal-space",
        "same-file",
        "plain",
    ],
)
def test_a_link_key_is_decoded(href, base, expected):
    assert _conv._resolve_link_target(href, base) == expected


@pytest.mark.unit
@pytest.mark.parametrize("base", ["my%20notes.xhtml", "my notes.xhtml"])
def test_an_anchor_key_uses_the_decoded_file_name(base):
    blocks = [{"text": "t", "anchor_ids": ["t1"], "anchor_offsets": {}}]
    _conv._attach_anchor_keys(blocks, base)
    assert "my notes.xhtml#t1" in blocks[0]["anchor_keys"]


def _encoded_links_book(directory):
    from tests.fixtures.epub_builder import EpubBuilder
    from tests.fixtures.golden.inputs import _xhtml_page
    from tests.fixtures.oeb_shim import EpubAsOeb

    links = (
        '<p>See <a href="chapter_2.xhtml#fn%3A1">one</a>, '
        '<a href="chapter_2.xhtml#%C3%A9">two</a>, '
        '<a href="my%20notes.xhtml#t1">three</a>, '
        '<a href="not%C3%A9s.xhtml#t1">four</a> and '
        '<a href="chapter_2.xhtml#plain">five</a>.</p>'
    )
    targets = (
        '<p id="fn:1">Note one.</p><p id="é">Note two.</p><p id="plain">Note five.</p>'
    )
    b = EpubBuilder().set_metadata(title="Links", author="A")
    b.add_chapter("One", _xhtml_page("One", links).encode("utf-8"))
    b.add_chapter("Two", _xhtml_page("Two", targets).encode("utf-8"))
    for i, (href, text) in enumerate(
        (("my%20notes.xhtml", "Note three."), ("notés.xhtml", "Note four."))
    ):
        b.add_manifest_item(
            item_id=f"x{i}",
            href=href,
            media_type="application/xhtml+xml",
            data=_xhtml_page(f"X{i}", f'<p id="t1">{text}</p>').encode("utf-8"),
            in_spine=True,
        )
    return EpubAsOeb(str(b.build(directory, "links")))


@pytest.mark.unit
def test_percent_encoded_links_land(tmp_path):
    from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

    out = tmp_path / "links.kfx"
    _conv.convert_oeb_to_kfx(
        _encoded_links_book(tmp_path), str(out), opts=MagicMock(), log=_silent_log()
    )
    frags = load_fragments(out)
    linked = [
        sp
        for story in by_type(frags, "$259")
        for e in iter_entries(val(story)["$146"])
        for sp in e.get("$142") or []
        if "$179" in sp
    ]
    assert len(linked) == 5


# --- links into a file with no text (#278) -----------------------------------
#
# A spine file that shows nothing (only `<div id="e"></div>`) produced no
# block, so its own key and its ids went nowhere and links to it were plain
# text. They now go on the next shown block, or the last one when nothing
# follows.


def _empty_file_book(directory, empty_last=False, next_html="<p>Next shown block.</p>"):
    from tests.fixtures.epub_builder import EpubBuilder
    from tests.fixtures.golden.inputs import _xhtml_page
    from tests.fixtures.oeb_shim import EpubAsOeb

    links = '<p>To <a href="chapter_2.xhtml">file</a> and <a href="chapter_2.xhtml#e">frag</a>.</p>'
    b = EpubBuilder().set_metadata(title="E", author="A")
    b.add_chapter("One", _xhtml_page("One", links).encode())
    if empty_last:
        b.add_chapter("Two", _xhtml_page("Two", '<div id="e"></div>').encode())
    else:
        b.add_chapter("Two", _xhtml_page("Two", '<div id="e"></div>').encode())
        b.add_chapter("Three", _xhtml_page("Three", next_html).encode())
        # A later chapter, so the next shown block is not also the last one.
        b.add_chapter("Four", _xhtml_page("Four", "<p>Later block.</p>").encode())
    return EpubAsOeb(str(b.build(directory, "e")))


def _block_keys(oeb):
    chapters = _conv.extract_chapters_from_oeb(
        oeb, MagicMock(), {"title": "T", "author": "A"}, native_tables=True
    )
    return {
        blk["text"]: set(blk.get("anchor_keys") or ())
        for ch in chapters
        for blk in ch.get("blocks") or []
    }


@pytest.mark.unit
def test_an_empty_files_keys_go_on_the_next_shown_block(tmp_path):
    keys = _block_keys(_empty_file_book(tmp_path))
    assert {"chapter_2.xhtml", "chapter_2.xhtml#e"} <= keys["Next shown block."]
    assert not {"chapter_2.xhtml", "chapter_2.xhtml#e"} & keys["Later block."]


@pytest.mark.unit
def test_an_empty_last_files_keys_go_on_the_last_block(tmp_path):
    keys = _block_keys(_empty_file_book(tmp_path, empty_last=True))
    assert {"chapter_2.xhtml", "chapter_2.xhtml#e"} <= keys["To file and frag."]


@pytest.mark.unit
def test_links_into_an_empty_file_land(tmp_path):
    from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

    out = tmp_path / "e.kfx"
    _conv.convert_oeb_to_kfx(
        _empty_file_book(tmp_path), str(out), opts=MagicMock(), log=_silent_log()
    )
    linked = [
        sp
        for story in by_type(load_fragments(out), "$259")
        for e in iter_entries(val(story)["$146"])
        for sp in e.get("$142") or []
        if "$179" in sp
    ]
    assert len(linked) == 2


@pytest.mark.unit
def test_links_into_an_empty_file_before_a_table_land_on_its_first_row(tmp_path):
    from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

    table = (
        "<table><tr><td>a1</td><td>b1</td></tr><tr><td>a2</td><td>b2</td></tr></table>"
    )
    out = tmp_path / "e.kfx"
    _conv.convert_oeb_to_kfx(
        _empty_file_book(tmp_path, next_html=table),
        str(out),
        opts=MagicMock(kfxgen_disable_native_tables=False),
        log=_silent_log(),
    )
    frags = load_fragments(out)
    entries = [
        e for st in by_type(frags, "$259") for e in iter_entries(val(st)["$146"])
    ]
    rows = [e["$155"] for e in entries if str(e.get("$159")) == "$279"]
    targets = {str(f.fid): val(f)["$183"]["$155"] for f in by_type(frags, "$266")}
    linked = [
        str(sp["$179"]) for e in entries for sp in e.get("$142") or [] if "$179" in sp
    ]
    assert len(linked) == 2
    assert {targets[n] for n in linked} == {rows[0]}


# ── #280: text written straight into <body> ──────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "body, texts",
    [
        ("<p>c</p>tail", ["c", "tail"]),
        ("<p>c</p><br/>tail", ["c", "tail"]),
        ("lead<p>c</p>", ["lead", "c"]),
        ("<p>a</p>mid<p>b</p>", ["a", "mid", "b"]),
        ("<table><tr><td>c</td></tr></table>tail", ["c", "tail"]),
        ("<ul><li>c</li></ul>tail", ["• c", "tail"]),
        (
            "<p>First.</p>loose tail text <b>bold</b> more loose<p>See.</p>",
            ["First.", "loose tail text bold more loose", "See."],
        ),
    ],
)
def test_loose_body_text_is_kept(body, texts):
    assert _texts(body) == texts


@pytest.mark.unit
def test_loose_body_text_keeps_its_runs():
    """As inside a <div>: one paragraph, its bold run and its link kept."""
    blocks = extract_blocks_from_html(
        _doc('<p>a</p>see <b>this</b> and <a href="c2.xhtml#n">that</a><p>b</p>'),
        base_href="c1.xhtml",
    )
    from kfxgen.inline_style import FLAG_BOLD, make_link_flag

    assert blocks[1]["text"] == "see this and that"
    assert blocks[1]["spans"] == [
        (4, 4, frozenset({FLAG_BOLD})),
        (13, 4, frozenset({make_link_flag("c2.xhtml#n")})),
    ]


@pytest.mark.unit
def test_a_body_without_loose_text_keeps_one_paragraph_per_child():
    """Notes written as inline elements straight in <body>, one after the
    other, stay one paragraph each; walking such a body like a <div> ran a
    library book's 149 notes into one paragraph."""
    assert _texts("<span>1. One.</span>\n<span>2. Two.</span>") == [
        "1. One.",
        "2. Two.",
    ]


@pytest.mark.unit
def test_a_nav_listing_in_a_body_with_loose_text_is_still_discarded():
    for listing in (
        '<ol><li><a href="c1.xhtml">Chapter I</a></li></ol>',
        # Links alone hold no block, so only the listing test sends it on.
        '<a href="c1.xhtml">Chapter I</a> <a href="c2.xhtml">Chapter II</a>',
    ):
        body = f'lead<nav epub:type="toc">{listing}</nav><p>Real prose.</p>'
        assert _texts(body) == ["lead", "Real prose."]


@pytest.mark.unit
def test_an_aside_in_a_body_with_loose_text_keeps_its_paragraphs():
    assert _texts("lead<aside><p>a</p><p>b</p></aside>") == ["lead", "a", "b"]


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["center", "address", "header", "dl"])
def test_a_browser_block_next_to_loose_body_text_stays_its_own_paragraph(tag):
    assert _texts(f"loose<{tag}>C</{tag}>tail") == ["loose", "C", "tail"]


@pytest.mark.unit
def test_a_display_block_span_next_to_loose_body_text_stays_its_own_paragraph():
    blocks = extract_blocks_from_html(
        _doc("loose<span>C</span>tail"),
        style_resolver=lambda e: (
            {"display": "block"} if e.tag.endswith("span") else None
        ),
    )
    assert [b["text"] for b in blocks] == ["loose", "C", "tail"]


# ── #231: words an SVG draws as <text> ───────────────────────────────────────

_SVG_NS = (
    'xmlns:svg="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
)


def _svg_page(inner):
    return (
        f'<svg:svg {_SVG_NS} viewBox="0 0 10 10">'
        '<svg:image xlink:href="p.jpg" width="10" height="10"/>'
        f"{inner}</svg:svg>"
    )


@pytest.mark.unit
def test_svg_text_follows_its_picture():
    blocks = extract_blocks_from_html(
        _doc(_svg_page('<svg:text x="1" y="5">Once upon</svg:text>'))
    )
    assert [b["text"] for b in blocks] == [
        _conv._make_img_token("p.jpg", "", _conv.SVG_IMAGE_SIZE),
        "Once upon",
    ]


@pytest.mark.unit
def test_svg_text_in_a_div_follows_its_picture_in_the_same_block():
    """A <div> holding only the <svg> is one block: picture, then words."""
    (block,) = extract_blocks_from_html(
        _doc(f"<div>{_svg_page('<svg:text>Once upon</svg:text>')}</div>")
    )
    token = _conv._make_img_token("p.jpg", "", _conv.SVG_IMAGE_SIZE)
    assert block["text"] == f"{token} Once upon"


@pytest.mark.unit
@pytest.mark.parametrize(
    "inner, texts",
    [
        # A tspan placed on a line of its own is a word break.
        (
            '<svg:text><svg:tspan x="1" y="2">Once</svg:tspan>'
            '<svg:tspan x="1" y="4">upon</svg:tspan></svg:text>',
            ["Once upon"],
        ),
        # One without a position continues the line: a run, not a new word.
        ("<svg:text>Won<svg:tspan>der</svg:tspan>ful</svg:text>", ["Wonderful"]),
        # Each <text> is its own paragraph, in document order.
        (
            "<svg:text>One.</svg:text><svg:g><svg:text>Two.</svg:text></svg:g>",
            ["One.", "Two."],
        ),
        # Text in parts SVG does not paint, or that are not shown, is not read.
        (
            "<svg:defs><svg:text>hidden</svg:text></svg:defs>"
            "<svg:title>a title</svg:title><svg:style>.a{}</svg:style>"
            "<svg:text>  shown  </svg:text><svg:text> </svg:text>",
            ["shown"],
        ),
    ],
)
def test_svg_text_paragraphs(inner, texts):
    blocks = extract_blocks_from_html(_doc(_svg_page(inner)))
    assert [b["text"] for b in blocks[1:]] == texts


@pytest.mark.unit
def test_an_svg_documents_text_follows_its_picture():
    """A spine document that is itself an SVG (#165)."""
    doc = etree.fromstring(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
            '<image xlink:href="p.jpg" width="10" height="10"/>'
            '<text x="1" y="5">Once upon</text></svg>'
        ).encode()
    )
    texts = [b["text"] for b in extract_blocks_from_html(doc)]
    assert texts[1:] == ["Once upon"]


@pytest.mark.unit
def test_svg_text_in_a_paragraph_stays_in_it():
    body = (
        f'<p {_SVG_NS}>Before <svg:svg><svg:image xlink:href="p.jpg"/>'
        "<svg:text>Once upon</svg:text></svg:svg> after.</p>"
    )
    text = _texts(body)[0]
    assert text.endswith("Once upon after.")


@pytest.mark.unit
def test_svg_text_is_reported_once_per_book(tmp_path):
    from tests.fixtures.epub_builder import EpubBuilder
    from tests.fixtures.golden.inputs import _xhtml_page
    from tests.fixtures.oeb_shim import EpubAsOeb

    page = _svg_page("<svg:text>Once upon</svg:text>")
    b = EpubBuilder().set_metadata(title="S", author="A")
    b.add_chapter("One", _xhtml_page("One", page).encode())
    b.add_chapter("Two", _xhtml_page("Two", page).encode())
    b.add_chapter("Three", _xhtml_page("Three", "<p>No pictures.</p>").encode())
    log = MagicMock()
    _conv.extract_chapters_from_oeb(
        EpubAsOeb(str(b.build(tmp_path, "s"))), log, {"title": "T", "author": "A"}
    )
    said = [c.args[0] for c in log.warn.call_args_list if "SVG" in c.args[0]]
    assert said == [
        "  Words drawn in SVG pictures in 2 files are written after the picture, "
        "not over it (#231)"
    ]
