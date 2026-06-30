"""
Unit tests for converter.py — TOC and spine extraction.

Issue #6: TOC entries whose href isn't in the spine should fall back to
matching against the manifest, instead of being silently dropped.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen import converter as _conv
from kfxgen.converter import (
    CONTENTS_SKIP_TITLES,
    HALF_TITLE_TITLES,
    TITLE_PAGE_TITLES,
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
        f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body_inner}</body></html>'.encode()
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
    }
    assert blocks[1]["block_style"] == {
        "align": None,
        "indent": None,
        "margin_left": None,
        "margin_right": None,
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
