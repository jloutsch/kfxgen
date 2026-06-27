#!/usr/bin/env python3
"""
EPUB to KFX Converter (Native Generator)

Converts EPUB files to KFX using the same chapter-based approach as the
Calibre plugin (converter.py), but parsing EPUBs directly without Calibre.

Features:
- Extracts structured chapters from EPUB (TOC → spine mapping)
- Preserves paragraph structure for per-paragraph KFX chunking
- Handles title page replacement, contents page rebuild, font sizes
- Extracts cover image
- Appends "-kfxgen" to the title
"""

import sys
import os
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

# Add plugin to path
sys.path.insert(0, os.path.abspath("plugin"))

from kfxgen.native_generator import NativeKFXGenerator


# ---------------------------------------------------------------------------
# HTML text extraction (preserves paragraph structure)
# ---------------------------------------------------------------------------

# Image placeholder tokens — must match converter.py exactly so the native
# generator's chunker decodes them into nested $259 image entries.
_IMG_TOKEN_DELIM = "\x00"
_IMG_TOKEN_FIELD = "\x01"
_IMG_TOKEN_SPACE = "\x02"


def _make_img_token(href, alt):
    escaped_alt = (
        (alt or "").replace(_IMG_TOKEN_SPACE, "").replace(" ", _IMG_TOKEN_SPACE)
    )
    return (
        f"{_IMG_TOKEN_DELIM}IMG{_IMG_TOKEN_FIELD}{href}"
        f"{_IMG_TOKEN_FIELD}{escaped_alt}{_IMG_TOKEN_DELIM}"
    )


class _ParagraphExtractor(HTMLParser):
    """Extract text preserving paragraph boundaries via block-level elements."""

    BLOCK_TAGS = frozenset([
        'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'li', 'section', 'article', 'tr', 'dt', 'dd',
    ])
    SKIP_TAGS = frozenset(['script', 'style', 'head'])

    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self._current = []
        self._skip_depth = 0
        self._in_body = False
        self._body_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'body':
            self._in_body = True
            self._body_depth = 1
            return
        if self._in_body:
            self._body_depth += 1
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == 'br':
            self._current.append('\n')
            return
        if tag == 'img':
            if self._skip_depth == 0 and self._in_body:
                attr_map = dict(attrs)
                src = attr_map.get('src') or ''
                alt = attr_map.get('alt') or ''
                if src:
                    self._current.append(_make_img_token(src, alt))
            return
        if tag in self.BLOCK_TAGS and self._current:
            text = ' '.join(''.join(self._current).split()).strip()
            if text:
                self.paragraphs.append(text)
            self._current = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'body':
            self._in_body = False
        if self._in_body:
            self._body_depth -= 1
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self.BLOCK_TAGS and self._current:
            text = ' '.join(''.join(self._current).split()).strip()
            if text:
                self.paragraphs.append(text)
            self._current = []

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        # Only collect text inside <body> (or if no body tag found, collect all)
        self._current.append(data)

    def get_text(self):
        # Flush remaining
        if self._current:
            text = ' '.join(''.join(self._current).split()).strip()
            if text:
                self.paragraphs.append(text)
            self._current = []
        return '\n\n'.join(self.paragraphs)


def extract_text_from_html(html_bytes):
    """Extract text from HTML bytes, preserving paragraph breaks as \\n\\n."""
    try:
        try:
            html_str = html_bytes.decode('utf-8')
        except UnicodeDecodeError:
            html_str = html_bytes.decode('latin-1')

        parser = _ParagraphExtractor()
        parser.feed(html_str)
        return parser.get_text()
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# EPUB parsing
# ---------------------------------------------------------------------------

def _find_opf(zf):
    """Find the OPF file path in an EPUB zip."""
    try:
        container = zf.read('META-INF/container.xml')
        root = ET.fromstring(container)
        ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        rf = root.find('.//c:rootfile', ns)
        if rf is not None:
            return rf.get('full-path')
    except Exception:
        pass
    for name in zf.namelist():
        if name.endswith('.opf'):
            return name
    return None


def _resolve_path(opf_dir, href):
    """Resolve an href relative to the OPF directory."""
    if opf_dir:
        full = os.path.join(opf_dir, href).replace('\\', '/')
    else:
        full = href
    parts = []
    for p in full.split('/'):
        if p == '..':
            if parts:
                parts.pop()
        elif p and p != '.':
            parts.append(p)
    return '/'.join(parts)


def _normalize_href(href):
    """Normalize href: strip anchor fragment and path prefix."""
    href = href.split('#')[0]
    if '/' in href:
        href = href.rsplit('/', 1)[-1]
    return href


def extract_epub_metadata(epub_path):
    """Extract title, author, language, publisher, date from EPUB."""
    meta = {
        'title': 'Untitled', 'author': 'Unknown', 'language': 'en',
        'publisher': 'kfxgen', 'issue_date': None,
    }
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            opf_path = _find_opf(zf)
            if not opf_path:
                return meta
            opf_root = ET.fromstring(zf.read(opf_path))
            ns = {'opf': 'http://www.idpf.org/2007/opf',
                  'dc': 'http://purl.org/dc/elements/1.1/'}

            el = opf_root.find('.//dc:title', ns)
            if el is not None and el.text:
                meta['title'] = el.text.strip()
            el = opf_root.find('.//dc:creator', ns)
            if el is not None and el.text:
                meta['author'] = el.text.strip()
            el = opf_root.find('.//dc:language', ns)
            if el is not None and el.text:
                meta['language'] = el.text.strip()[:2].lower()
            el = opf_root.find('.//dc:publisher', ns)
            if el is not None and el.text:
                meta['publisher'] = el.text.strip()
            el = opf_root.find('.//dc:date', ns)
            if el is not None and el.text:
                meta['issue_date'] = el.text.strip()[:10]
    except Exception as e:
        print(f"Warning: metadata extraction error: {e}")
    return meta


def extract_epub_spine(epub_path):
    """
    Extract per-spine-item text with paragraph preservation.

    Returns list of {'href': str, 'text': str} dicts.
    """
    items = []
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            opf_path = _find_opf(zf)
            if not opf_path:
                return items
            opf_dir = os.path.dirname(opf_path)
            opf_root = ET.fromstring(zf.read(opf_path))
            ns = {'opf': 'http://www.idpf.org/2007/opf'}

            # Build manifest: id -> (href, media_type)
            manifest = {}
            mel = opf_root.find('.//opf:manifest', ns)
            if mel is not None:
                for item in mel.findall('opf:item', ns):
                    iid = item.get('id')
                    href = item.get('href')
                    mt = item.get('media-type', '')
                    if iid and href:
                        manifest[iid] = (href, mt)

            # Get spine order
            spine_hrefs = []
            sel = opf_root.find('.//opf:spine', ns)
            if sel is not None:
                for iref in sel.findall('opf:itemref', ns):
                    idref = iref.get('idref')
                    if idref and idref in manifest:
                        href, mt = manifest[idref]
                        if 'html' in mt or 'xhtml' in mt:
                            spine_hrefs.append(href)

            if not spine_hrefs:
                spine_hrefs = [n for n in zf.namelist()
                               if n.endswith(('.html', '.xhtml', '.htm'))]

            for href in spine_hrefs:
                full_path = _resolve_path(opf_dir, href)
                try:
                    html_bytes = zf.read(full_path)
                except KeyError:
                    try:
                        html_bytes = zf.read(href)
                    except KeyError:
                        continue
                text = extract_text_from_html(html_bytes)
                if text and text.strip():
                    items.append({'href': href, 'text': text})

    except Exception as e:
        print(f"Warning: spine extraction error: {e}")
    return items


def extract_epub_toc(epub_path):
    """
    Extract TOC entries with hrefs from NCX (EPUB2) or nav (EPUB3).

    Returns list of {'title': str, 'href': str} dicts.
    """
    entries = []
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            # Prefer NCX (more reliable for EPUB2 books)
            ncx_name = None
            nav_name = None
            for name in zf.namelist():
                if name.endswith('.ncx'):
                    ncx_name = name
                if 'nav' in name.lower() and name.endswith(('.xhtml', '.html')):
                    nav_name = name

            if ncx_name:
                ncx_root = ET.fromstring(zf.read(ncx_name))
                ns = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
                for np in ncx_root.findall('.//ncx:navPoint', ns):
                    text_el = np.find('ncx:navLabel/ncx:text', ns)
                    content_el = np.find('ncx:content', ns)
                    if text_el is not None and text_el.text:
                        href = content_el.get('src', '') if content_el is not None else ''
                        entries.append({
                            'title': text_el.text.strip(),
                            'href': href,
                        })
            elif nav_name:
                nav_text = zf.read(nav_name).decode('utf-8', errors='ignore')
                matches = re.findall(
                    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>',
                    nav_text)
                for href, title in matches:
                    title = title.strip()
                    if title and len(title) > 1:
                        entries.append({'title': title, 'href': href})

    except Exception as e:
        print(f"Warning: TOC extraction error: {e}")
    return entries


def extract_cover_image(epub_path):
    """Extract cover image bytes from EPUB."""
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            opf_path = _find_opf(zf)
            if not opf_path:
                return None
            opf_dir = os.path.dirname(opf_path)
            opf_root = ET.fromstring(zf.read(opf_path))
            ns = {'opf': 'http://www.idpf.org/2007/opf'}

            # Build manifest
            manifest = {}
            mel = opf_root.find('.//opf:manifest', ns)
            if mel is not None:
                for item in mel.findall('opf:item', ns):
                    iid = item.get('id', '')
                    href = item.get('href', '')
                    mt = item.get('media-type', '')
                    manifest[iid] = (href, mt)

            # Method 1: <meta name="cover" content="item_id">
            for meta in opf_root.findall('.//opf:metadata/opf:meta', ns):
                if meta.get('name') == 'cover':
                    cover_id = meta.get('content', '')
                    if cover_id in manifest:
                        href, mt = manifest[cover_id]
                        if 'image' in mt:
                            full = _resolve_path(opf_dir, href)
                            data = zf.read(full)
                            if len(data) > 100:
                                print(f"  Cover image: {len(data):,} bytes (meta cover)")
                                return data

            # Method 2: manifest item with 'cover' in id + image type
            for iid, (href, mt) in manifest.items():
                if 'image' in mt and 'cover' in iid.lower():
                    full = _resolve_path(opf_dir, href)
                    try:
                        data = zf.read(full)
                        if len(data) > 100:
                            print(f"  Cover image: {len(data):,} bytes (manifest id={iid})")
                            return data
                    except KeyError:
                        pass

            # Method 3: manifest item with 'cover' in href + image type
            for iid, (href, mt) in manifest.items():
                if 'image' in mt and 'cover' in href.lower():
                    full = _resolve_path(opf_dir, href)
                    try:
                        data = zf.read(full)
                        if len(data) > 100:
                            print(f"  Cover image: {len(data):,} bytes (href={href})")
                            return data
                    except KeyError:
                        pass

    except Exception as e:
        print(f"Warning: cover extraction error: {e}")
    return None


def extract_epub_body_images(epub_path, exclude_data=None):
    """Walk EPUB manifest for image/* items; return {href: bytes}.

    Hrefs are kept as the manifest declares them — the native generator
    resolves matches by basename, so this lines up with the `src` attribute
    on `<img>` tags in chapter HTML. JPEG and PNG only; the generator
    silently drops anything else.

    `exclude_data` (bytes) lets the caller drop the cover image so it
    isn't emitted twice as a body resource.
    """
    images = {}
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            opf_path = _find_opf(zf)
            if not opf_path:
                return images
            opf_dir = os.path.dirname(opf_path)
            opf_root = ET.fromstring(zf.read(opf_path))
            ns = {'opf': 'http://www.idpf.org/2007/opf'}
            mel = opf_root.find('.//opf:manifest', ns)
            if mel is None:
                return images
            for item in mel.findall('opf:item', ns):
                href = item.get('href') or ''
                mt = (item.get('media-type') or '').lower()
                if 'image' not in mt or not href:
                    continue
                full_path = _resolve_path(opf_dir, href)
                try:
                    data = zf.read(full_path)
                except KeyError:
                    try:
                        data = zf.read(href)
                    except KeyError:
                        continue
                if len(data) <= 100:
                    continue
                if data[:3] != b'\xff\xd8\xff' and data[:4] != b'\x89PNG':
                    continue
                if exclude_data is not None and data == exclude_data:
                    continue
                images[href] = data
    except Exception as e:
        print(f"Warning: body image extraction error: {e}")
    return images


# ---------------------------------------------------------------------------
# Chapter building (mirrors converter.py logic)
# ---------------------------------------------------------------------------

SMALL_TEXT_CHAPTERS = {
    'copyright', 'copyright page', 'also by', 'also by the author',
    'about the author', 'about the authors', 'dedication', 'epigraph',
    'acknowledgments', 'acknowledgements', 'colophon', 'credits',
    'a note about the author',
}
SMALL_FONT_SIZE = 0.75

CONTENTS_SKIP_TITLES = {
    'title page', 'title', 'cover', 'contents', 'table of contents',
    'copyright', 'copyright page',
}


def build_chapters(spine_items, toc_entries, metadata):
    """
    Build chapter list. Spine order is authoritative for reading order; TOC entries
    provide display titles via normalized-href lookup. Every spine item becomes a
    chapter so content is never dropped when multiple TOC entries collapse to the
    same spine file via different #anchors.

    Returns list of chapter dicts suitable for NativeKFXGenerator.generate_full_book().
    """
    if not spine_items:
        return [{'title': 'Content', 'text': 'No content extracted.'}]

    # First TOC title per normalized href wins (subsequent anchored entries inside
    # the same spine file are ignored — they would need in-page anchor splitting
    # to surface, which this function does not do).
    title_by_href = {}
    for entry in toc_entries or []:
        norm = _normalize_href(entry['href'])
        title_by_href.setdefault(norm, entry['title'])

    chapters = []
    for i, item in enumerate(spine_items):
        norm = _normalize_href(item['href'])
        title = title_by_href.get(norm)
        if not title:
            stem = norm.rsplit('.', 1)[0] if '.' in norm else norm
            title = stem or f'Section {i + 1}'
        chapters.append({'title': title, 'text': item['text']})

    _replace_title_page(chapters, metadata)
    return chapters


def _replace_title_page(chapters, metadata):
    """Replace title page, rebuild contents, set font sizes."""
    if not metadata:
        return
    title = metadata.get('title', '')
    author = metadata.get('author', '')
    if not title:
        return

    for ch in chapters:
        ch_title = ch['title'].lower().strip()
        if ch_title in ('title page', 'title'):
            ch['text'] = f"{title}\n\nby\n\n{author}"
            print(f"  Replaced title page: {title} by {author}")
        elif ch_title in ('contents', 'table of contents'):
            _rebuild_contents_page(ch, chapters)
            ch['font_size'] = SMALL_FONT_SIZE
        if ch_title in SMALL_TEXT_CHAPTERS or ch_title in ('copyright', 'copyright page'):
            ch['font_size'] = SMALL_FONT_SIZE


def _rebuild_contents_page(contents_ch, all_chapters):
    """Rebuild contents with linked entries."""
    toc_links = []
    for i, ch in enumerate(all_chapters):
        if ch['title'].lower().strip() in CONTENTS_SKIP_TITLES:
            continue
        toc_links.append({'text': ch['title'], 'target_chapter_idx': i})

    lines = ["Contents"]
    for link in toc_links:
        lines.append(link['text'])
    contents_ch['text'] = '\n\n'.join(lines)
    contents_ch['toc_links'] = toc_links
    print(f"  Rebuilt contents page with {len(toc_links)} linked entries")


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def sanitize_filename(title):
    for ch in '<>:"/\\|?*':
        title = title.replace(ch, '_')
    title = title.strip(' .')
    return title[:100]


def convert_epub_to_kfx(epub_path, output_dir=None):
    """Convert EPUB to KFX using NativeKFXGenerator with chapter structure."""
    print("=" * 70)
    print("EPUB to KFX Converter (Native Generator)")
    print("=" * 70)

    epub_path = Path(epub_path)
    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB not found: {epub_path}")

    print(f"\nInput: {epub_path}")

    # 1. Metadata
    print("\nExtracting metadata...")
    metadata = extract_epub_metadata(epub_path)
    modified_title = f"{metadata['title']}-kfxgen"
    print(f"  Title: {metadata['title']} -> {modified_title}")
    print(f"  Author: {metadata['author']}")
    print(f"  Language: {metadata['language']}")

    # 2. Spine items (per-file text with paragraph preservation)
    print("\nExtracting spine items...")
    spine_items = extract_epub_spine(epub_path)
    print(f"  {len(spine_items)} spine items with text")
    for i, item in enumerate(spine_items[:3]):
        print(f"    {i+1}. {_normalize_href(item['href'])}: {len(item['text']):,} chars")
    if len(spine_items) > 3:
        print(f"    ... and {len(spine_items) - 3} more")

    # 3. TOC entries
    print("\nExtracting TOC...")
    toc_entries = extract_epub_toc(epub_path)
    print(f"  {len(toc_entries)} TOC entries")
    for entry in toc_entries[:5]:
        print(f"    - {entry['title']} -> {_normalize_href(entry['href'])}")
    if len(toc_entries) > 5:
        print(f"    ... and {len(toc_entries) - 5} more")

    # 4. Cover image
    print("\nExtracting cover image...")
    cover_image = extract_cover_image(epub_path)
    if not cover_image:
        print("  No cover image found")

    # 4b. Body images (excludes the cover by byte-match)
    print("\nExtracting body images...")
    body_images = extract_epub_body_images(epub_path, exclude_data=cover_image)
    print(f"  {len(body_images)} body image(s)")

    # 5. Build chapters
    print("\nBuilding chapters...")
    chapters = build_chapters(spine_items, toc_entries, metadata)
    total_chars = sum(len(ch['text']) for ch in chapters)
    print(f"  {len(chapters)} chapters, {total_chars:,} total characters")
    for i, ch in enumerate(chapters):
        fs_info = f" (font_size={ch['font_size']})" if 'font_size' in ch else ""
        links = f" [{len(ch['toc_links'])} links]" if 'toc_links' in ch else ""
        print(f"    {i+1}. {ch['title']}: {len(ch['text']):,} chars{fs_info}{links}")

    # 6. Generate KFX
    if output_dir:
        output_dir = Path(output_dir)
    else:
        output_dir = epub_path.parent
    output_path = output_dir / (sanitize_filename(modified_title) + ".kfx")

    print(f"\nGenerating KFX: {output_path}")
    gen = NativeKFXGenerator()
    data = gen.generate_full_book(
        title=modified_title,
        author=metadata['author'],
        chapters=chapters,
        output_path=str(output_path),
        cover_image=cover_image,
        language=metadata['language'],
        publisher=metadata.get('publisher', 'kfxgen'),
        issue_date=metadata.get('issue_date'),
        images=body_images or None,
    )

    print(f"\n  Generated {len(data):,} bytes -> {output_path}")
    print("=" * 70)
    return str(output_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Convert all EPUBs in research/
        epub_dir = Path("research")
        epubs = sorted(epub_dir.glob("*.epub"))
        if not epubs:
            print("Usage: python convert_epub_to_kfx.py <epub_file> [output_dir]")
            sys.exit(1)
        print(f"Found {len(epubs)} EPUB files in research/\n")
        for epub in epubs:
            try:
                convert_epub_to_kfx(epub, "research")
                print()
            except Exception as e:
                print(f"ERROR converting {epub.name}: {e}")
                import traceback
                traceback.print_exc()
                print()
    else:
        epub_path = sys.argv[1]
        output_dir = sys.argv[2] if len(sys.argv) > 2 else "research"
        try:
            output_path = convert_epub_to_kfx(epub_path, output_dir)
            print(f"\nSUCCESS: {output_path}")
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
