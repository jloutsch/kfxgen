"""
Unit tests for converter.py — TOC and spine extraction.

Issue #6: TOC entries whose href isn't in the spine should fall back to
matching against the manifest, instead of being silently dropped.
"""

import logging
import os
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

        chapters = extract_chapters_from_oeb(oeb, _silent_log())
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
        chapters = _assemble_chapters_by_coordinate(spine, toc, _silent_log())
        # Only chapter I; no image-only "Front Matter" leading chapter.
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
    assert [b["text"] for b in blocks] == ["Part", "Ch1", "Ch2"]


@pytest.mark.unit
def test_nested_ul_inside_li_is_not_flattened():
    blocks = _conv.extract_blocks_from_html(
        _doc("<ul><li>Top<ul><li>Sub</li></ul></li></ul>")
    )
    assert [b["text"] for b in blocks] == ["Top", "Sub"]


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
    # `</td></tr><tr><td>`. The separator closes the cell, so the row boundary
    # is covered by the same rule.
    blocks = _conv.extract_blocks_from_html(
        _doc(
            "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
        )
    )
    assert blocks[0]["text"] == "a b c d"


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
    assert blocks[0]["text"] == "A B c d"


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
