"""
KFX Converter - Extract content from OEB and generate KFX

Uses NativeKFXGenerator to produce KFX with working TOC navigation.
"""

import logging
import os
import re
from urllib.parse import unquote

from ._img_tokens import (
    IMG_TOKEN_DELIM as _IMG_TOKEN_DELIM,
    IMG_TOKEN_FIELD as _IMG_TOKEN_FIELD,
    IMG_TOKEN_RE as _IMG_TOKEN_RE,
    IMG_TOKEN_SPACE as _IMG_TOKEN_SPACE,
)
from .image_optimize import optimize_images
from .inline_style import FLAG_BOLD, FLAG_ITALIC, compute_block_style, normalize_runs
from .native_generator import NativeKFXGenerator

_ITALIC_TAGS = {"em", "i"}
_BOLD_TAGS = {"strong", "b"}

_security_log = logging.getLogger(__name__ + ".security")


def _build_style_resolver(oeb_book, item, log):
    """Return a callable elem->computed-CSS-dict using Calibre's Stylizer, or
    None when Calibre/Stylizer is unavailable or construction fails. Never
    raises — failure degrades to no per-element block styling."""
    try:
        from calibre.ebooks.oeb.stylizer import Stylizer  # noqa: PLC0415

        profile = getattr(getattr(oeb_book, "opts", None), "output_profile", None)
        stylizer = Stylizer(item.data, item.href, oeb_book, oeb_book.opts, profile)

        def resolve(elem):
            try:
                st = stylizer.style(elem)
                return {
                    "text-align": st.get("text-align"),
                    "text-indent": st.get("text-indent"),
                }
            except Exception:
                return None

        return resolve
    except Exception as e:
        log.warn(f"  Stylizer unavailable ({e}); skipping per-element CSS")
        return None


def _has_real_text(text):
    """True if `text` has any non-whitespace content once IMG tokens are removed.

    An image-only page (e.g. the EPUB's own cover.xhtml, which is just an
    <img>) reduces to empty here.
    """
    return bool(_IMG_TOKEN_RE.sub("", text or "").strip())


def _local_tag(tag):
    """Strip the lxml namespace prefix from a tag, returning the local name."""
    if not isinstance(tag, str):
        return None
    return tag.rsplit("}", 1)[-1]


def _make_img_token(href, alt):
    """Encode an <img> reference as a placeholder token string.

    Spaces in alt text are escaped to a control char so str.split() doesn't
    fragment the token during whitespace normalization.
    """
    escaped_alt = (
        (alt or "").replace(_IMG_TOKEN_SPACE, "").replace(" ", _IMG_TOKEN_SPACE)
    )
    return (
        f"{_IMG_TOKEN_DELIM}IMG{_IMG_TOKEN_FIELD}{href}"
        f"{_IMG_TOKEN_FIELD}{escaped_alt}{_IMG_TOKEN_DELIM}"
    )


def _walk_inline(elem, flags=frozenset()):
    """Yield (segment, flags) pairs for inline content, accumulating italic/
    bold from ancestor <em>/<i>/<strong>/<b>. <img> becomes an IMG token
    segment with empty flags so the generator still splits on it."""
    local = _local_tag(elem.tag)
    cur = set(flags)
    if local in _ITALIC_TAGS:
        cur.add(FLAG_ITALIC)
    if local in _BOLD_TAGS:
        cur.add(FLAG_BOLD)
    cur = frozenset(cur)
    parts = []
    if elem.text:
        parts.append((elem.text, cur))
    for child in elem:
        clocal = _local_tag(child.tag)
        if clocal == "img":
            href = child.get("src", "") or ""
            alt = child.get("alt", "") or ""
            parts.append((_make_img_token(href, alt), frozenset()))
        else:
            parts.extend(_walk_inline(child, cur))
        if child.tail:
            parts.append((child.tail, flags))
    return parts


def extract_blocks_from_html(element, style_resolver=None):
    """Like extract_text_from_html but returns structured blocks:
    [{"text": str, "spans": [(start, length, frozenset)], "block_style": dict|None}],
    preserving inline emphasis as spans and inline <img> as IMG tokens in `text`.
    When style_resolver is given, it is called per block element (elem -> css_dict|None)
    and the result is passed to compute_block_style to populate block_style."""
    body = element.find(".//{http://www.w3.org/1999/xhtml}body")
    if body is None:
        body = element.find(".//body")
    if body is None:
        body = element

    ns = "{http://www.w3.org/1999/xhtml}"
    block_tags = set()
    for tag in (
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "li",
        "section",
        "article",
        "figure",
    ):
        block_tags.add(tag)
        block_tags.add(ns + tag)

    blocks = []
    for elem in body.iter():
        if elem.tag in block_tags:
            if any(child.tag in block_tags for child in elem):
                continue
            text, spans = normalize_runs(_walk_inline(elem))
            if text:
                bstyle = None
                if style_resolver is not None:
                    css = style_resolver(elem)
                    if css is not None:
                        bstyle = compute_block_style(css)
                blocks.append({"text": text, "spans": spans, "block_style": bstyle})
            continue

        # Bare <img> directly under body (rare, but exists in some EPUBs)
        if _local_tag(elem.tag) == "img":
            parent = elem.getparent()
            if parent is not None and parent.tag in block_tags:
                continue  # already handled by the block walker above
            href = elem.get("src", "") or ""
            alt = elem.get("alt", "") or ""
            blocks.append(
                {"text": _make_img_token(href, alt), "spans": [], "block_style": None}
            )

    if blocks:
        return blocks

    # Fallback: no block elements — flat extraction, no spans (unchanged rule).
    text = body.xpath("string()")
    lines = [line.strip() for line in text.split("\n")]
    text = " ".join(line for line in lines if line)
    return [{"text": text, "spans": [], "block_style": None}] if text else []


def extract_text_from_html(element):
    """Plain-text extraction (IMG tokens preserved). Now derived from
    extract_blocks_from_html so the two never diverge."""
    return "\n\n".join(b["text"] for b in extract_blocks_from_html(element))


def extract_metadata(oeb_book, log):
    """
    Extract metadata from OEB book.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger

    Returns:
        dict: Metadata dictionary with title, author, language
    """
    metadata = {
        "title": "Untitled",
        "author": "Unknown",
        "language": "en",
        "publisher": "kfxgen",
        "issue_date": None,
    }

    try:
        if oeb_book.metadata.title:
            metadata["title"] = str(oeb_book.metadata.title[0])

        if oeb_book.metadata.creator:
            metadata["author"] = ", ".join(str(c) for c in oeb_book.metadata.creator)

        if oeb_book.metadata.language:
            lang = str(oeb_book.metadata.language[0])
            metadata["language"] = lang[:2].lower()

        if hasattr(oeb_book.metadata, "publisher") and oeb_book.metadata.publisher:
            metadata["publisher"] = str(oeb_book.metadata.publisher[0])

        if hasattr(oeb_book.metadata, "date") and oeb_book.metadata.date:
            raw_date = str(oeb_book.metadata.date[0])
            metadata["issue_date"] = raw_date[:10]  # YYYY-MM-DD from ISO format

    except Exception as e:
        log.error(f"Error extracting metadata: {e}")

    return metadata


_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


def _is_unsafe_href(href):
    """True if href looks like path traversal, an absolute path, a URL scheme,
    or a Windows drive letter. Percent-encoded forms (%2e%2e, %2f) are decoded
    iteratively until stable before checking, so multi-pass encodings like
    %252e%252e are caught at any depth. See SECURITY.md (#44, #60)."""
    if not href:
        return False
    # Iterate unquote until the string stabilises. The 16-iteration bound is
    # generous (no realistic href ever needs more than 2-3 layers) and is
    # only there to prevent pathological inputs from looping unboundedly.
    # The length-bound rejects malformed inputs that grow during decode —
    # legitimate percent-decoding monotonically shrinks.
    prev = href
    original_len = len(href)
    for _ in range(16):
        nxt = unquote(prev)
        if nxt == prev:
            break
        if len(nxt) > max(original_len, 1) * 4:
            # Decode shouldn't expand. Treat as malformed.
            return True
        prev = nxt
    decoded = prev
    candidates = {href, decoded}
    for candidate in candidates:
        if candidate.startswith(("/", "\\")):
            return True
        # Tightened drive-letter check: require an ASCII alpha first character.
        # The previous `candidate[1] == ':'` alone matched any 2-char prefix
        # ending in ':' (e.g. emoji + ':' or '0:'), which over-flagged.
        if _DRIVE_LETTER_RE.match(candidate):
            return True
        lowered = candidate.lower()
        if "://" in lowered or lowered.startswith(("javascript:", "data:", "file:")):
            return True
        parts = candidate.replace("\\", "/").split("/")
        if any(p == ".." for p in parts):
            return True
    return False


def _normalize_href(href):
    """Normalize an href by removing anchors and extracting filename.

    Returns "" for hrefs containing path traversal, absolute paths, URL schemes,
    or drive letters. The basename strip below already neutralises today's
    callers, but raw hrefs flow through chunk text in memory and may reach
    future code paths that resolve them — fail closed at the source. (#44)
    """
    if not href:
        return ""
    href = href.split("#")[0]
    if _is_unsafe_href(href):
        _security_log.warning("rejected unsafe href in _normalize_href: %r", href)
        return ""
    # Strip directory components. Split on both separators so a Windows-style
    # backslash can't survive into the returned "basename" and be re-read as a
    # path separator by a downstream resolver (#125 fuzz finding). Backslash
    # traversal (..\..) is already rejected by _is_unsafe_href above; this
    # neutralises the non-traversal residue (e.g. '....\' -> '').
    basename = re.split(r"[\\/]", href)[-1]
    # Re-check the extracted basename. Stripping directories can *surface* a
    # scheme/drive fragment that sat mid-path and so passed the full-href check
    # above (e.g. 'foo/javascript:alert(1)' -> 'javascript:alert(1)', or
    # '..../file:' -> 'file:'). The full-scheme-with-'//' forms are already
    # caught by the '://' check, but bare scheme prefixes are not. Fail closed
    # so the result is always a clean basename (#125 fuzz finding).
    if _is_unsafe_href(basename):
        _security_log.warning(
            "rejected unsafe basename in _normalize_href: %r (from %r)", basename, href
        )
        return ""
    return basename


def _find_manifest_item(oeb_book, href):
    """Return a manifest item whose href matches (exactly or by basename)."""
    if not hasattr(oeb_book, "manifest") or oeb_book.manifest is None:
        return None
    norm = _normalize_href(href)
    if not norm:
        return None

    hrefs = getattr(oeb_book.manifest, "hrefs", None)
    if hrefs:
        item = hrefs.get(href)
        if item is not None:
            return item
        for h, manifest_item in hrefs.items():
            if _normalize_href(h) == norm:
                return manifest_item

    try:
        for manifest_item in oeb_book.manifest:
            m_href = getattr(manifest_item, "href", "") or ""
            if _normalize_href(m_href) == norm:
                return manifest_item
    except TypeError:
        pass
    return None


def _extract_text_from_manifest_item(oeb_book, href, log):
    """Extract chapter text from a manifest item, used when the href isn't in the spine.

    Returns None when nothing usable is found — caller decides whether to drop
    the TOC entry or warn.
    """
    try:
        item = _find_manifest_item(oeb_book, href)
    except Exception as e:
        log.warn(f"  Manifest lookup failed for {_normalize_href(href)}: {e}")
        return None
    if item is None or not hasattr(item, "data") or item.data is None:
        return None
    media = (getattr(item, "media_type", "") or "").lower()
    if "html" not in media and "xml" not in media:
        # Empty/absent media_type or any non-XHTML type (image, font, ...) —
        # don't try to parse the data as XHTML.
        return None
    try:
        text = extract_text_from_html(item.data)
    except Exception as e:
        log.warn(f"  Manifest fallback failed for {_normalize_href(href)}: {e}")
        return None
    if text and text.strip():
        return text
    return None


def extract_chapters_from_oeb(oeb_book, log, metadata=None):
    """
    Extract structured chapters from OEB book by mapping TOC to spine items.

    Produces a list of {'title': str, 'text': str} dicts suitable for
    NativeKFXGenerator.generate_full_book(chapters=...).

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger
        metadata: Optional dict with 'title' and 'author' for title page replacement

    Returns:
        list: List of chapter dicts with 'title' and 'text' keys
    """
    # Build spine item map: normalized href -> text
    spine_map = {}
    spine_items_ordered = []

    log.info(f"Processing {len(oeb_book.spine)} spine items...")

    for i, item in enumerate(oeb_book.spine):
        # Per-item try/except (#73): a single bad item logs a warning and
        # the loop continues. The previous shape wrapped the whole loop in
        # a bare except Exception, which aborted iteration on the first
        # parse error and silently lost every subsequent item.
        #
        # Scope covers the `.data` access too: some OEB-shaped sources
        # (e.g. our test EpubAsOeb shim) parse XHTML eagerly on .data
        # access, so a malformed-XHTML spine item raises before we ever
        # call extract_text_from_html.
        try:
            if not hasattr(item, "data") or item.data is None:
                continue
            resolver = _build_style_resolver(oeb_book, item, log)
            blocks = extract_blocks_from_html(item.data, style_resolver=resolver)
            text = "\n\n".join(b["text"] for b in blocks)
        except Exception as e:
            href_for_log = getattr(item, "href", "") or "<unknown>"
            log.warn(f"  Spine item {i + 1} parse failed ({href_for_log}): {e}")
            continue

        if not text or len(text.strip()) == 0:
            continue

        # Get the href for this spine item
        href = getattr(item, "href", "") or ""
        norm_href = _normalize_href(href)

        spine_map[norm_href] = text
        spine_map[href] = text
        spine_items_ordered.append({"href": href, "text": text, "blocks": blocks})
        log.info(f"  Spine item {i + 1}: {len(text)} chars ({norm_href})")

    if not spine_items_ordered:
        # Raise instead of returning a "No content extracted." sentinel (#72).
        # The sentinel was non-empty so it bypassed generate_full_book's
        # documented "Raises ValueError if empty or None" validation,
        # producing silent success for inputs that had nothing convertible.
        # Calibre's plugin runtime surfaces ValueError as a conversion error.
        raise ValueError(
            "No spine items with extractable text — EPUB has no convertible content"
        )

    # Try to extract TOC with hrefs
    toc_entries = _extract_toc_with_hrefs(oeb_book, log)

    if toc_entries:
        # Each TOC entry owns all spine items from its href up to (but not
        # including) the next TOC-referenced spine item. This handles Calibre's
        # split-at-page-break output, where a chapter's body lives in sibling
        # `_split_001` / `_split_002` spine items that have no TOC entry.
        spine_order = [_normalize_href(s["href"]) for s in spine_items_ordered]

        toc_spine_indices = []
        for entry in toc_entries:
            norm = _normalize_href(entry["href"])
            try:
                toc_spine_indices.append(spine_order.index(norm))
            except ValueError:
                toc_spine_indices.append(None)

        chapters = []
        seen_starts = set()
        covered_spine_indices = set()
        # Titles from TOC entries that got dropped by spine-index dedup
        # (e.g. anchored siblings). Bound to orphan spine items below so
        # CIA-Factbook–style books surface as A/B/C/... instead of opaque
        # filename stems.
        dropped_toc_titles = []

        for i, entry in enumerate(toc_entries):
            start = toc_spine_indices[i]

            if start is None:
                # Manifest fallback (#6) is disabled in v5.3.2: it recovered
                # image-only chapters (Title Page, Maps) with near-empty
                # text bodies that contributed to a Kindle progress
                # display regression. Re-enabling needs a separate fix
                # for the body-image emission path. See #6 for status.
                log.warn(f"  TOC entry {entry['title']!r} dropped: not in spine")
                continue

            if start in seen_starts:
                log.warn(
                    f"  TOC entry {entry['title']!r} skipped: spine "
                    f"index {start} already claimed"
                )
                dropped_toc_titles.append(entry["title"])
                continue
            seen_starts.add(start)

            # Default: own only this spine item. Extend forward only when a
            # later TOC entry has a valid spine index further downstream,
            # meaning there are orphan spine items (e.g. Calibre split-at-
            # page-break siblings) between us and the next TOC anchor that
            # belong to this chapter.
            #
            # Issue #1's first fix (commit 4de5168) used wide absorption
            # (`end = len(spine_order)`) which fixed split-chapter content
            # but caused the *last* TOC entry to absorb unrelated back-matter
            # (e.g. next-reads.xhtml), shifting KFX nav positions. The
            # narrowed default below preserves the split-recovery behavior
            # only for chapters that genuinely have orphans between them
            # and the next TOC anchor.
            # Forward-extension picks up orphan spine items between this
            # TOC anchor and the next one (Calibre's --enable-heuristics
            # splits a chapter into chap.html + chap_split_001.html +
            # chap_split_002.html, and only chap.html is in the TOC).
            #
            # Stop the search at the first successor with a known spine
            # index. If that successor maps to the same spine index, the
            # source EPUB is using within-file #anchor navigation (e.g.
            # CIA Factbook: 27 TOC entries all pointing to spine[0]#A..#Z),
            # and we must NOT extend — extending would absorb every
            # subsequent content spine item until the next distinct TOC
            # anchor and either drown the KFX writer in a mega-chapter or
            # silently drop content downstream of the position-id envelope.
            end = start + 1
            for j in range(i + 1, len(toc_entries)):
                nxt = toc_spine_indices[j]
                # Skip None (TOC entry not in spine) and skip non-monotonic
                # entries (nxt < start, EPUBs whose TOC is out of spine order).
                # Stop on nxt == start (anchored sibling, don't extend) or
                # nxt > start (extend to absorb orphans, Calibre-split case).
                if nxt is None or nxt < start:
                    continue
                if nxt > start:
                    end = nxt
                break

            parts = [
                spine_items_ordered[k]["text"]
                for k in range(start, end)
                if spine_items_ordered[k]["text"]
            ]
            text = "\n\n".join(parts)
            if text.strip():
                chapter = {"title": entry["title"], "text": text}
                block_parts = []
                for k in range(start, end):
                    if spine_items_ordered[k]["text"]:
                        block_parts.extend(spine_items_ordered[k]["blocks"])
                if block_parts:
                    chapter["blocks"] = block_parts
                chapters.append(chapter)
                covered_spine_indices.update(range(start, end))

        # Recover spine items the TOC never references. This fires when
        # every TOC entry normalizes to the same spine item (anchored
        # within-file navigation, e.g. CIA World Factbook: 27 TOC entries
        # all #A,#B,#C inside spine[0]). Without this, spine items 1..N
        # are silently dropped — the book was losing ~98% of its text.
        # Orphans append in spine order with filename-stem fallback titles.
        if chapters:
            orphan_indices = [
                k
                for k in range(len(spine_items_ordered))
                if k not in covered_spine_indices
            ]
            if orphan_indices:
                log.info(
                    f"  Recovering {len(orphan_indices)} spine item(s) "
                    f"not referenced by TOC"
                )
                for k in orphan_indices:
                    item = spine_items_ordered[k]
                    # Skip image-only orphans (no real text once IMG tokens
                    # are stripped). The common case is the EPUB's own
                    # cover.xhtml: its <img> points at the cover, which is
                    # emitted separately (#32), so the token never resolves
                    # to a body resource. Recovering it appended a junk
                    # trailing chapter that rendered blank and produced zero
                    # content chunks — which crashed the generator with an
                    # IndexError on a trailing empty chapter. This mirrors
                    # the #6 policy above: image-only chapters are not
                    # recovered. (A resolvable inline image inside a chapter
                    # with prose still flows normally — only orphans with no
                    # prose at all are dropped here.)
                    if not _has_real_text(item["text"]):
                        log.info(
                            f"  Skipping image-only orphan "
                            f"{_normalize_href(item['href'])}"
                        )
                        continue
                    if dropped_toc_titles:
                        title = dropped_toc_titles.pop(0)
                    else:
                        norm = _normalize_href(item["href"])
                        stem = norm.rsplit(".", 1)[0] if "." in norm else norm
                        title = stem or f"Section {k + 1}"
                    chapter = {"title": title, "text": item["text"]}
                    if item.get("blocks"):
                        chapter["blocks"] = item["blocks"]
                    chapters.append(chapter)

        if chapters:
            log.info(f"Mapped {len(chapters)} TOC entries to spine content")
            _replace_title_page(chapters, metadata, log)
            return chapters
        else:
            log.info(
                "No TOC entries matched spine items, using spine items as chapters"
            )

    # Fallback: use each spine item as a chapter
    chapters = []
    for i, item in enumerate(spine_items_ordered):
        chapter = {"title": f"Section {i + 1}", "text": item["text"]}
        if item.get("blocks"):
            chapter["blocks"] = item["blocks"]
        chapters.append(chapter)

    log.info(f"Using {len(chapters)} spine items as chapters (no TOC mapping)")
    _replace_title_page(chapters, metadata, log)
    return chapters


SMALL_TEXT_CHAPTERS = {
    "copyright",
    "copyright page",
    "also by",
    "also by the author",
    "about the author",
    "about the authors",
    "dedication",
    "epigraph",
    "acknowledgments",
    "acknowledgements",
    "colophon",
    "credits",
}

SMALL_FONT_SIZE = 0.75

# TOC labels that denote the full title page (book title + author).
TITLE_PAGE_TITLES = frozenset({"title page", "title"})

# TOC labels that denote a half-title (a.k.a. bastard title) page. Print
# convention shows ONLY the book title — no author, no subtitle. The
# label itself ("Half Title Page") is structural navigation metadata and
# must never render as visible heading text; one observed book leaked the
# literal words "Half Title Page" onto the page because this set did not
# recognise the variant. Keep the spelling variants in sync with
# CONTENTS_SKIP_TITLES below. (#107)
HALF_TITLE_TITLES = frozenset(
    {
        "half title",
        "half-title",
        "half title page",
        "half-title page",
        "halftitle",
        "halftitle page",
        "bastard title",
    }
)


def _replace_title_page(chapters, metadata, log):
    """Replace title page, reformat copyright/contents, and set font sizes for front/back matter."""
    if not metadata:
        return
    title = metadata.get("title", "")
    author = metadata.get("author", "")
    if not title:
        return
    for ch in chapters:
        ch_title = ch["title"].lower().strip()
        if ch_title in TITLE_PAGE_TITLES:
            ch["text"] = f"{title}\n\nby\n\n{author}"
            ch.pop("blocks", None)
            # The replaced body already contains the book title — don't
            # also render the chapter's TOC name ("Title Page") as a
            # heading on top of it (#33).
            ch["_omit_title_heading"] = True
            log.info(f"  Replaced title page with: {title} by {author}")
        elif ch_title in HALF_TITLE_TITLES:
            # Half-title convention: book title only, no author. The TOC
            # label ("Half Title Page") is structural metadata, never
            # printed content — replace with the title and suppress the
            # label as a heading so it can't leak onto the page. (#107)
            ch["text"] = title
            ch.pop("blocks", None)
            ch["_omit_title_heading"] = True
            log.info(f"  Replaced half-title page with: {title}")
        elif ch_title in ("copyright", "copyright page"):
            ch["font_size"] = SMALL_FONT_SIZE
            log.info(f"  Copyright page (font_size={SMALL_FONT_SIZE})")
        elif ch_title in ("contents", "table of contents"):
            # Rebuild contents page from actual chapter titles
            _rebuild_contents_page(ch, chapters, log)
            ch.pop("blocks", None)
            ch["font_size"] = SMALL_FONT_SIZE

        if ch_title in SMALL_TEXT_CHAPTERS:
            ch["font_size"] = SMALL_FONT_SIZE


# Chapter titles to exclude from the generated contents listing.
# Built from the shared title/half-title sets so a new spelling variant
# only has to be added in one place. (#107)
CONTENTS_SKIP_TITLES = (
    TITLE_PAGE_TITLES
    | HALF_TITLE_TITLES
    | {
        "cover",
        "contents",
        "table of contents",
        "copyright",
        "copyright page",
    }
)


def _rebuild_contents_page(contents_ch, all_chapters, log):
    """Rebuild a Contents chapter with underlined, linked entries."""
    toc_links = []
    for i, ch in enumerate(all_chapters):
        ch_lower = ch["title"].lower().strip()
        if ch_lower in CONTENTS_SKIP_TITLES:
            continue
        toc_links.append({"text": ch["title"], "target_chapter_idx": i})

    # Build display text (header + entries)
    lines = ["Contents"]
    for link in toc_links:
        lines.append(link["text"])
    contents_ch["text"] = "\n\n".join(lines)
    contents_ch.pop("blocks", None)

    # Structured link data for the native generator
    contents_ch["toc_links"] = toc_links
    log.info(f"  Rebuilt contents page with {len(toc_links)} linked entries")


def _extract_toc_with_hrefs(oeb_book, log):
    """
    Extract TOC entries preserving href targets for chapter mapping.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger

    Returns:
        list: List of {'title': str, 'href': str, 'level': int} dicts
    """
    toc_entries = []

    try:
        if not hasattr(oeb_book, "toc") or not oeb_book.toc:
            log.info("No TOC found in source book")
            return toc_entries

        log.info("Extracting TOC with hrefs...")

        def process_toc_node(node, level=0):
            if hasattr(node, "title") and node.title:
                href = getattr(node, "href", "") or ""
                entry = {"title": str(node.title), "href": href, "level": level}
                toc_entries.append(entry)
                log.info(
                    f"  {'  ' * level}[{level}] {node.title} -> {_normalize_href(href)}"
                )

            try:
                for child in node:
                    process_toc_node(child, level + 1)
            except (TypeError, AttributeError):
                pass

        try:
            for node in oeb_book.toc:
                process_toc_node(node)
        except (TypeError, AttributeError):
            process_toc_node(oeb_book.toc)

        if toc_entries:
            log.info(f"Extracted {len(toc_entries)} TOC entries with hrefs")
        else:
            log.info("TOC found but no entries extracted")

    except Exception as e:
        log.error(f"Error extracting TOC: {e}")

    return toc_entries


def extract_images_from_oeb(oeb_book, log, exclude_hrefs=None):
    """
    Walk OEB manifest for image/* items and return their raw bytes.

    Used by Phase 4 to emit $164 + $417 resource pairs. The cover image is
    typically excluded (handled separately via extract_cover_image), so callers
    pass its href in `exclude_hrefs`.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger
        exclude_hrefs: Optional iterable of normalized hrefs to skip

    Returns:
        dict: { href: bytes } for every image manifest item that's not excluded
    """
    images = {}
    skipped_unsupported = 0
    excluded = {_normalize_href(h) for h in (exclude_hrefs or [])}
    try:
        for item in oeb_book.manifest:
            media = (getattr(item, "media_type", "") or "").lower()
            if "image" not in media:
                continue
            data = getattr(item, "data", None)
            if not isinstance(data, (bytes, bytearray)) or len(data) <= 100:
                continue
            href = getattr(item, "href", "") or ""
            if not href or _normalize_href(href) in excluded:
                continue
            # Pre-filter to formats the generator handles (JPEG, PNG). The
            # generator silently drops unrecognized magic bytes; doing the
            # check here lets us log the skip with the offending href.
            if data[:3] != b"\xff\xd8\xff" and data[:4] != b"\x89PNG":
                skipped_unsupported += 1
                log.warn(
                    f"  Skipping image {href!r}: unsupported format "
                    f"(magic bytes {bytes(data[:4]).hex()}); only JPEG and PNG "
                    f"are emitted as KFX resources"
                )
                continue
            images[href] = bytes(data)
    except Exception as e:
        log.warn(f"Error walking manifest for images: {e}")
    summary = f"  Extracted {len(images)} body image(s) from manifest"
    if skipped_unsupported:
        summary += f" ({skipped_unsupported} skipped — unsupported format)"
    log.info(summary)
    return images


def _get_cover_image_data(item, log):
    """Get binary cover-image data from a manifest item.

    Validates JPEG/PNG magic bytes before returning — Calibre identifies
    images by manifest media-type only, so we don't trust the label and
    sniff the actual bytes (#46). Mismatched or unrecognized formats are
    rejected here so garbage never reaches the binary serializer.

    Validation is magic-byte-only by design; structurally invalid JPEGs
    (correct header, corrupt body) flow through and are rejected by Kindle
    at render time.

    `log` is passed explicitly (rather than captured via closure) so this
    helper is independently testable and reusable across discovery paths.
    """
    if not item or not hasattr(item, "data"):
        return None
    data = item.data
    if not isinstance(data, bytes) or len(data) <= 100:
        return None
    if data[:3] != b"\xff\xd8\xff" and data[:4] != b"\x89PNG":
        href = getattr(item, "href", "") or "<unknown>"
        log.warn(
            f"  Skipping cover candidate {href!r}: unsupported format "
            f"(magic bytes {bytes(data[:4]).hex()}); only JPEG and PNG "
            f"are accepted as cover images"
        )
        return None
    return data


def extract_cover_image(oeb_book, log):
    """
    Extract cover image binary data from OEB book.

    Calibre's oeb_book.metadata.cover returns a manifest item ID (not an href).
    We need to find the manifest item by ID, then get its image data.

    Args:
        oeb_book: Calibre OEB book object
        log: Calibre logger

    Returns:
        tuple: (bytes, href) or (None, None). The href is needed by the body
        image pipeline to skip the cover (avoid double-emit as $164 cover_img +
        $164 img_N) regardless of which discovery method located it.
    """
    # Method 1: metadata.cover → manifest item ID → image data
    try:
        if oeb_book.metadata.cover:
            cover_id = str(oeb_book.metadata.cover[0])
            log.info(f"  metadata.cover ID: {cover_id}")

            for item in oeb_book.manifest:
                if getattr(item, "id", None) == cover_id:
                    media_type = getattr(item, "media_type", "") or ""
                    if "image" in media_type:
                        data = _get_cover_image_data(item, log)
                        if data:
                            href = getattr(item, "href", "") or ""
                            log.info(
                                f"  Cover image: {len(data):,} bytes from manifest ID '{cover_id}'"
                            )
                            return data, href

            # Also try as href (some books use href in metadata.cover)
            cover_item = oeb_book.manifest.hrefs.get(cover_id)
            if cover_item:
                data = _get_cover_image_data(cover_item, log)
                if data:
                    href = getattr(cover_item, "href", "") or cover_id
                    log.info(
                        f"  Cover image: {len(data):,} bytes from manifest href '{cover_id}'"
                    )
                    return data, href
    except Exception as e:
        log.warn(f"Could not extract cover from metadata: {e}")

    # Method 2: guide entries with type='cover'
    try:
        if hasattr(oeb_book, "guide") and oeb_book.guide:
            for ref in oeb_book.guide:
                ref_type = getattr(ref, "type", "") or ""
                if ref_type.lower() in ("cover", "other.ms-coverimage-standard"):
                    href = getattr(ref, "href", "")
                    if href:
                        item = oeb_book.manifest.hrefs.get(href)
                        data = _get_cover_image_data(item, log)
                        if data:
                            log.info(f"  Cover image: {len(data):,} bytes from guide")
                            return data, href
    except Exception as e:
        log.warn(f"Could not extract cover from guide: {e}")

    # Method 3: scan manifest for items with 'cover' in ID or href + image type
    try:
        for item in oeb_book.manifest:
            item_id = (getattr(item, "id", "") or "").lower()
            item_href = (getattr(item, "href", "") or "").lower()
            media_type = (getattr(item, "media_type", "") or "").lower()
            if "image" in media_type and ("cover" in item_id or "cover" in item_href):
                data = _get_cover_image_data(item, log)
                if data:
                    href = getattr(item, "href", "") or ""
                    log.info(
                        f"  Cover image: {len(data):,} bytes from manifest scan (id={item_id})"
                    )
                    return data, href
    except Exception as e:
        log.warn(f"Could not scan manifest for cover: {e}")

    log.info("  No cover image found")
    return None, None


def convert_oeb_to_kfx(oeb_book, output_path, opts, log):
    """
    Convert Calibre OEB book to KFX format using native generator.

    Args:
        oeb_book: Calibre OEB book object
        output_path: Path to write KFX file
        opts: Conversion options
        log: Calibre logger

    Returns:
        None (writes to output_path)
    """
    from . import __version__ as _kfxgen_version

    log.info("=" * 70)
    log.info(f"kfxgen v{_kfxgen_version} - Native KFX Generator")
    log.info("=" * 70)

    # Extract metadata
    log.info("Extracting metadata...")
    metadata = extract_metadata(oeb_book, log)
    log.info(f"  Title: {metadata['title']}")
    log.info(f"  Author: {metadata['author']}")
    log.info(f"  Language: {metadata['language']}")
    log.info(f"  Publisher: {metadata['publisher']}")
    if metadata["issue_date"]:
        log.info(f"  Date: {metadata['issue_date']}")

    # Extract cover image (and the href it was located at, so we can skip it
    # in body-image extraction regardless of which method found it).
    log.info("Extracting cover image...")
    cover_image, cover_href = extract_cover_image(oeb_book, log)

    # ISSUE-4 INVESTIGATION (image rendering): re-enable body image
    # emission with the new dedicated image style. Diagnostic build only
    # — do not merge until Kindle device-test confirms images render.
    log.info("Extracting body images... (#4 image-rendering investigation)")
    images = extract_images_from_oeb(
        oeb_book, log, exclude_hrefs=[cover_href] if cover_href else []
    )

    # Optimize over-size images unless the user opted to embed originals (#11).
    if getattr(opts, "kfxgen_embed_original_images", False):
        log.info("  Image optimization disabled (embed original images)")
    else:
        cover_image, images = optimize_images(cover_image, images, log)

    # Extract structured chapters
    log.info("Extracting chapters...")
    chapters = extract_chapters_from_oeb(oeb_book, log, metadata=metadata)
    total_chars = sum(len(ch["text"]) for ch in chapters)
    log.info(f"  Chapters: {len(chapters)}")
    log.info(f"  Total content: {total_chars:,} characters")

    # Generate KFX
    log.info("Generating KFX file...")
    gen = NativeKFXGenerator()
    gen.generate_full_book(
        title=metadata["title"],
        author=metadata["author"],
        chapters=chapters,
        output_path=output_path,
        cover_image=cover_image,
        images=images,  # ISSUE-4 INVESTIGATION (image rendering)
        language=metadata["language"],
        publisher=metadata["publisher"],
        issue_date=metadata.get("issue_date"),
    )

    if os.path.isfile(output_path):
        size = os.path.getsize(output_path)
        log.info("=" * 70)
        log.info(f"KFX generated: {output_path} ({size:,} bytes)")
        log.info("=" * 70)
    else:
        raise Exception("KFX generation failed - no output file created")
