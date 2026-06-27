"""
EpubAsOeb — read-only duck-typed Calibre-OEB wrapper for converter.py.

converter.py reads (verified by Task 2 audit, grep run 2026-05-03):
    oeb_book.metadata.title[0]            (str())
    oeb_book.metadata.creator             (list, joined with ', ')
    oeb_book.metadata.language[0]         (str())
    oeb_book.metadata.date                (list, str()[:10] for ISO date)
    oeb_book.metadata.publisher[0]        (str(), hasattr-guarded)
    oeb_book.metadata.cover[0]            (str(), only used by extract_cover_image — PR2 territory)
    oeb_book.manifest                     (iterable, hasattr/None guarded)
    oeb_book.manifest.hrefs               (dict[str, item], getattr None-default)
    oeb_book.manifest.hrefs.get(href)     (lookup)
    oeb_book.spine                        (iterable + len)
    oeb_book.toc                          (hasattr-guarded — shim omits)
    oeb_book.guide                        (hasattr-guarded — shim omits)
    manifest_item.href                    (str)
    manifest_item.id                      (str)
    manifest_item.media_type              (str)
    manifest_item.data                    (parsed lxml.etree._Element for
                                           XHTML/XML media; raw bytes for
                                           anything else — matches Calibre
                                           OEB contract; lazy + cached)
    manifest_item.data_bytes              (raw bytes regardless of media —
                                           used by callers like cover-image
                                           extraction that need the unparsed
                                           payload)

Three deliberate pass-throughs (do NOT sanitize):
1. Invalid UTF-8 chapter bytes -> .data raises (lxml parse failure) on
   XHTML/XML items. This matches Calibre's OEB, which also fails to parse
   malformed XHTML at load time. Callers that explicitly want the raw
   bytes should use .data_bytes (which never parses).
2. Missing zip entry -> .data raises KeyError on access
3. Path-traversal hrefs -> verbatim in href and hrefs dict
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Iterator

from lxml import etree

_OPF_NS = "{http://www.idpf.org/2007/opf}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"


class _MetadataItem:
    """Wraps a string so str(item) returns the value, matching Calibre's
    `oeb_book.metadata.title[0]` access pattern."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"_MetadataItem({self._value!r})"


class _Metadata:
    def __init__(
        self,
        *,
        title: str,
        creator: str,
        language: str,
        date: str | None = None,
        publisher: str | None = None,
    ) -> None:
        self.title: list[_MetadataItem] = [_MetadataItem(title)] if title else []
        self.creator: list[_MetadataItem] = [_MetadataItem(creator)] if creator else []
        self.language: list[_MetadataItem] = (
            [_MetadataItem(language)] if language else []
        )
        self.date: list[_MetadataItem] = [_MetadataItem(date)] if date else []
        self.publisher: list[_MetadataItem] = (
            [_MetadataItem(publisher)] if publisher else []
        )


class _ManifestItem:
    def __init__(
        self,
        *,
        zf_path: Path,
        item_id: str,
        href: str,
        media_type: str,
        opf_dir: str,
    ) -> None:
        self._zf_path = zf_path
        self._opf_dir = opf_dir
        self.id: str = item_id
        self.href: str = href
        self.media_type: str = media_type
        # _cached_data may hold either an lxml Element (XHTML/XML media) or
        # raw bytes (everything else). _cached_bytes always holds the raw
        # zip payload regardless of media type.
        self._cached_data: etree._Element | bytes | None = None
        self._cached_bytes: bytes | None = None

    def _read_bytes(self) -> bytes:
        if self._cached_bytes is None:
            with zipfile.ZipFile(self._zf_path) as zf:
                arcname = f"{self._opf_dir}/{self.href}" if self._opf_dir else self.href
                self._cached_bytes = zf.read(arcname)
        return self._cached_bytes

    @property
    def data(self) -> etree._Element | bytes:
        """For XHTML/XML media: parsed lxml.etree._Element (matching Calibre
        OEB's `manifest_item.data` contract — converter.extract_text_from_html
        calls .find() on this). For other media (images, fonts, css): raw
        bytes. Lazy-cached. Parse failures propagate to the caller."""
        if self._cached_data is None:
            raw = self._read_bytes()
            mt = (self.media_type or "").lower()
            if "xhtml" in mt or "xml" in mt:
                self._cached_data = etree.fromstring(raw)
            else:
                self._cached_data = raw
        return self._cached_data

    @property
    def data_bytes(self) -> bytes:
        """Raw zip payload regardless of media type. For callers that need
        the unparsed bytes (e.g. cover-image fixtures in PR2)."""
        return self._read_bytes()


class _Manifest:
    def __init__(self, items: list[_ManifestItem]) -> None:
        self._items = items
        self.hrefs: dict[str, _ManifestItem] = {it.href: it for it in items}

    def __iter__(self) -> Iterator[_ManifestItem]:
        return iter(self._items)


class _Spine:
    def __init__(self, items: list[_ManifestItem]) -> None:
        self._items = items

    def __iter__(self) -> Iterator[_ManifestItem]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


class EpubAsOeb:
    def __init__(self, epub_path: Path) -> None:
        self._epub_path = Path(epub_path)
        self._metadata: _Metadata | None = None
        self._manifest: _Manifest | None = None
        self._spine: _Spine | None = None
        self._parsed = False

    def _ensure_parsed(self) -> None:
        if self._parsed:
            return
        with zipfile.ZipFile(self._epub_path) as zf:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find(
                f"{_CONTAINER_NS}rootfiles/{_CONTAINER_NS}rootfile"
            )
            if rootfile is None:
                raise ValueError(
                    f"{self._epub_path}: META-INF/container.xml missing rootfile"
                )
            opf_path = rootfile.attrib["full-path"]
            opf_dir = "/".join(opf_path.split("/")[:-1])
            opf = ET.fromstring(zf.read(opf_path))

        meta_el = opf.find(f"{_OPF_NS}metadata")
        title = self._dc_text(meta_el, "title")
        creator = self._dc_text(meta_el, "creator")
        language = self._dc_text(meta_el, "language")
        date = self._dc_text(meta_el, "date")
        publisher = self._dc_text(meta_el, "publisher")
        self._metadata = _Metadata(
            title=title,
            creator=creator,
            language=language,
            date=date,
            publisher=publisher,
        )

        items: dict[str, _ManifestItem] = {}
        for item_el in opf.findall(f"{_OPF_NS}manifest/{_OPF_NS}item"):
            item = _ManifestItem(
                zf_path=self._epub_path,
                item_id=item_el.attrib["id"],
                href=item_el.attrib["href"],
                media_type=item_el.attrib.get("media-type", ""),
                opf_dir=opf_dir,
            )
            items[item.id] = item

        self._manifest = _Manifest(list(items.values()))

        spine_items: list[_ManifestItem] = []
        for ref in opf.findall(f"{_OPF_NS}spine/{_OPF_NS}itemref"):
            idref = ref.attrib["idref"]
            if idref in items:
                spine_items.append(items[idref])
        self._spine = _Spine(spine_items)

        self._parsed = True

    @staticmethod
    def _dc_text(meta_el: ET.Element | None, tag: str) -> str:
        if meta_el is None:
            return ""
        el = meta_el.find(f"{_DC_NS}{tag}")
        if el is None or el.text is None:
            return ""
        return el.text

    @property
    def metadata(self) -> _Metadata:
        self._ensure_parsed()
        assert self._metadata is not None
        return self._metadata

    @property
    def manifest(self) -> _Manifest:
        self._ensure_parsed()
        assert self._manifest is not None
        return self._manifest

    @property
    def spine(self) -> _Spine:
        self._ensure_parsed()
        assert self._spine is not None
        return self._spine
