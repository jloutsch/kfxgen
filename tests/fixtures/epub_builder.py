"""
EpubBuilder — programmatic EPUB construction for the #49 integration
fixture corpus. Produces a valid OCF (zip) container with metadata,
manifest, spine, and one XHTML chapter file per add_chapter call.

PR1 surface (this file):
    set_metadata(*, title, author, language="en") -> Self
    add_chapter(title, body: str | bytes) -> Self
    build(out_dir, name) -> Path

PR2 will extend with set_cover, add_manifest_item, add_raw_zip_write.
"""

from __future__ import annotations

import html
import zipfile
from pathlib import Path

_OCF_MIMETYPE = b"application/epub+zip"

_CONTAINER_XML = b"""\
<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_XHTML_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{title}</title></head>
<body>
<h1>{heading}</h1>
{body}
</body>
</html>
"""


class EpubBuilder:
    def __init__(self) -> None:
        self._title: str = "Test Book"
        self._author: str = "Test Author"
        self._language: str = "en"
        # Each chapter: (title, body) where body is str (gets escaped+wrapped)
        # or bytes (written verbatim — used for non_utf8 fixture).
        self._chapters: list[tuple[str, str | bytes]] = []
        self._cover: tuple[bytes, str, str, bool] | None = None
        # (image_bytes, media_type, href, declare_only)
        self._extra_manifest_items: list[tuple[str, str, str, bytes | None, bool]] = []
        # Each: (item_id, href, media_type, data, in_spine)

    def set_metadata(
        self, *, title: str, author: str, language: str = "en"
    ) -> "EpubBuilder":
        self._title = title
        self._author = author
        self._language = language
        return self

    def add_chapter(self, title: str, body: "str | bytes") -> "EpubBuilder":
        self._chapters.append((title, body))
        return self

    def set_cover(
        self,
        image_bytes: bytes,
        *,
        media_type: str = "image/jpeg",
        href: str = "cover.jpg",
        declare_only: bool = False,
    ) -> "EpubBuilder":
        """Records a cover image. declare_only=True emits the OPF manifest
        entry + <meta name="cover"> linkage but skips the zip write.
        Used by missing_cover.epub fixture."""
        self._cover = (image_bytes, media_type, href, declare_only)
        return self

    def add_manifest_item(
        self,
        *,
        item_id: str,
        href: str,
        media_type: str,
        data: bytes | None = None,
        in_spine: bool = False,
    ) -> "EpubBuilder":
        """Lower-level escape hatch for fixtures that need raw control of
        the manifest entry shape.

        data=None declares in OPF but skips the zip write — same semantics
        as set_cover(declare_only=True).

        in_spine=True appends a spine <itemref idref="..."/> for this
        item. Required for path_traversal_href and duplicate_basename
        fixtures: the runner only exercises _normalize_href and
        _find_manifest_item for items reached via the spine. Default False
        so cover-image-style declarations don't accidentally show up in
        the reading order."""
        self._extra_manifest_items.append((item_id, href, media_type, data, in_spine))
        return self

    def build(self, out_dir: Path, name: str) -> Path:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{name}.epub"

        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 1. mimetype FIRST, STORED (uncompressed). OCF requirement.
            zi = zipfile.ZipInfo("mimetype")
            zi.compress_type = zipfile.ZIP_STORED
            zf.writestr(zi, _OCF_MIMETYPE)

            # 2. container.xml
            zf.writestr("META-INF/container.xml", _CONTAINER_XML)

            # 3. content.opf
            zf.writestr("OEBPS/content.opf", self._render_opf().encode("utf-8"))

            # 4. chapter files
            for idx, (chap_title, body) in enumerate(self._chapters, start=1):
                arcname = f"OEBPS/chapter_{idx}.xhtml"
                if isinstance(body, bytes):
                    zf.writestr(arcname, body)
                else:
                    rendered = _XHTML_TEMPLATE.format(
                        title=html.escape(chap_title),
                        heading=html.escape(chap_title),
                        body="<p>"
                        + html.escape(body).replace("\n\n", "</p><p>")
                        + "</p>",
                    )
                    zf.writestr(arcname, rendered.encode("utf-8"))

            # Cover bytes (PR2 / #49). declare_only fixtures skip this zip write
            # but still get the OPF manifest entry + <meta> linkage from _render_opf.
            if self._cover is not None:
                image_bytes, _media_type, href, declare_only = self._cover
                if not declare_only:
                    zf.writestr(f"OEBPS/{href}", image_bytes)

            # Extra manifest items (PR2 / #49). data=None fixtures skip this zip
            # write but still get the OPF manifest entry from _render_opf.
            for (
                _item_id,
                href,
                _media_type,
                data,
                _in_spine,
            ) in self._extra_manifest_items:
                if data is not None:
                    # Write to OPF-relative path. zipfile normalizes some shapes
                    # (..) — that's acceptable since the fixture exists to
                    # exercise downstream rejection, not to produce a "valid" zip.
                    zf.writestr(f"OEBPS/{href}", data)

        return path

    def _render_opf(self) -> str:
        manifest_items: list[str] = []
        spine_items: list[str] = []
        if self._cover is not None:
            image_bytes, media_type, href, _declare_only = self._cover
            manifest_items.append(
                f'    <item id="cover-image" href="{html.escape(href)}" '
                f'media-type="{html.escape(media_type)}"/>'
            )
        for idx, (chap_title, _body) in enumerate(self._chapters, start=1):
            item_id = f"chapter{idx}"
            href = f"chapter_{idx}.xhtml"
            manifest_items.append(
                f'    <item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'    <itemref idref="{item_id}"/>')

        for (
            item_id,
            href,
            media_type,
            _data,
            in_spine,
        ) in self._extra_manifest_items:
            manifest_items.append(
                f'    <item id="{html.escape(item_id)}" '
                f'href="{html.escape(href)}" '
                f'media-type="{html.escape(media_type)}"/>'
            )
            if in_spine:
                spine_items.append(f'    <itemref idref="{html.escape(item_id)}"/>')

        manifest_block = "\n".join(manifest_items)
        spine_block = "\n".join(spine_items)

        cover_meta = ""
        if self._cover is not None:
            cover_meta = '\n    <meta name="cover" content="cover-image"/>'

        return f"""<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">test-{html.escape(self._title)}</dc:identifier>
    <dc:title>{html.escape(self._title)}</dc:title>
    <dc:creator>{html.escape(self._author)}</dc:creator>
    <dc:language>{html.escape(self._language)}</dc:language>{cover_meta}
  </metadata>
  <manifest>
{manifest_block}
  </manifest>
  <spine>
{spine_block}
  </spine>
</package>
"""
