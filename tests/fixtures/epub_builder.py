"""
EpubBuilder — programmatic EPUB construction for the #49 integration
fixture corpus. Produces a valid OCF (zip) container with metadata,
manifest, spine, and one document per add_chapter / add_image_page call.

Surface:
    set_metadata(*, title, author, language="en") -> Self
    set_fixed_layout(*, width=None, height=None) -> Self
    add_chapter(title, body: str | bytes, *, listed=True) -> Self
    add_image_page(title, *, image, href=None, wrapper="img",
                   attrs=None, style=None, alt="", listed=True) -> Self
    add_calibre_inline_toc(*, listed=False) -> Self
    set_cover(image_bytes, *, media_type, href, declare_only=False) -> Self
    add_manifest_item(*, item_id, href, media_type, data=None, in_spine=False) -> Self
    build(out_dir, name) -> Path

Most kfxgen defects are shape defects — whether a page survives depends on
how many spine documents the navigation lists, whether art is an `<img>` or
an `<svg>`, and whether the markup states a size. The books those shapes
occur in are usually ones nobody can attach to an issue, so the shapes are
built here instead:

    page-image book, art the nav never lists   add_image_page(..., listed=False)
    art sized by the markup                    add_image_page(..., attrs={"height": "98%"})
    art wrapped in <svg>                       add_image_page(..., wrapper="svg")
    a spine document that IS an <svg>          add_image_page(..., wrapper="svg-root")
    a page holding only the cover image        add_image_page(..., href=<cover href>)
    calibre's generated contents page          add_calibre_inline_toc()

`tests._helpers.jpeg_of(width, height)` supplies images whose declared
dimensions are real, which is what the image-sizing rules read.
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

#: A page whose whole content is one picture. No heading: a page-image book
#: has no text on the page, and a heading would make it text-bearing, which
#: is the distinction several of these fixtures exist to exercise.
_PAGE_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{title}</title></head>
<body>
<div class="page">{content}</div>
</body>
</html>
"""

#: A spine document that is itself an SVG — EPUB 3 fixed-layout producers
#: emit these, and they have no <body> at all.
_SVG_ROOT_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="100%" height="100%" viewBox="0 0 {width} {height}"
     preserveAspectRatio="xMidYMid meet">
  <image width="{width}" height="{height}" xlink:href="{href}"/>
</svg>
"""

#: calibre stamps the contents page its MOBI/AZW3 output generates with this
#: body id, and titles it "Table of Contents".
CALIBRE_INLINE_TOC_BODY_ID = "calibre_generated_inline_toc"

_CALIBRE_INLINE_TOC_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Table of Contents</title></head>
<body id="{body_id}" class="calibre">
<h2 class="calibre_toc_header">Table of Contents</h2>
<ul>
{entries}
</ul>
</body>
</html>
"""

#: Fallback image dimensions for an <svg> wrapper when the caller's image
#: carries none we can read. Only the markup shape depends on them.
_SVG_FALLBACK_SIZE = (600, 800)


def _read_image_size(data: bytes) -> tuple[int, int]:
    """(width, height) from PNG or JPEG bytes, else `_SVG_FALLBACK_SIZE`.

    Deliberately tiny and forgiving: it exists so an `<svg>` wrapper can
    carry a plausible viewBox, not to validate anything.
    """
    import struct

    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:2] == b"\xff\xd8":
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return int(w), int(h)
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
    return _SVG_FALLBACK_SIZE


class EpubBuilder:
    def __init__(self) -> None:
        self._title: str = "Test Book"
        self._author: str = "Test Author"
        self._language: str = "en"
        # One entry per spine document, in reading order. Each is a dict with
        # at least {"kind", "title", "listed"}; see add_chapter /
        # add_image_page / add_calibre_inline_toc for the rest.
        self._docs: list[dict] = []
        # href -> bytes, for images referenced by image pages.
        self._images: dict[str, bytes] = {}
        self._fixed_layout: tuple[int | None, int | None] | None = None
        self._cover: tuple[bytes, str, str, bool] | None = None
        # (image_bytes, media_type, href, declare_only)
        self._extra_manifest_items: list[tuple[str, str, str, bytes | None, bool]] = []
        # Each: (item_id, href, media_type, data, in_spine)

    # ── metadata ───────────────────────────────────────────────────────────

    def set_metadata(
        self, *, title: str, author: str, language: str = "en"
    ) -> "EpubBuilder":
        self._title = title
        self._author = author
        self._language = language
        return self

    def set_fixed_layout(
        self, *, width: int | None = None, height: int | None = None
    ) -> "EpubBuilder":
        """Declare the book pre-paginated.

        Metadata only — kfxgen reads none of it. It is here so a fixture can
        *say* it is fixed-layout, which is what distinguishes one from a
        reflowable book built out of the same page images.
        """
        self._fixed_layout = (width, height)
        return self

    # ── documents ──────────────────────────────────────────────────────────

    def add_chapter(
        self, title: str, body: "str | bytes", *, listed: bool = True
    ) -> "EpubBuilder":
        """A text chapter. `listed=False` keeps it in the spine and out of
        the navigation — the shape a chapter-level TOC gives every page
        between its entries."""
        self._docs.append(
            {"kind": "text", "title": title, "body": body, "listed": listed}
        )
        return self

    def add_image_page(
        self,
        title: str,
        *,
        image: bytes,
        href: str | None = None,
        wrapper: str = "img",
        attrs: dict[str, str] | None = None,
        style: str | None = None,
        alt: str = "",
        listed: bool = True,
    ) -> "EpubBuilder":
        """A spine document whose whole content is one picture.

        `wrapper` picks the markup: "img" a plain `<img>`, "svg" an `<svg>`
        wrapping an `<image>` (what calibre writes for a comic's title page,
        and what publishers use for covers and plates), "svg-root" a spine
        document that *is* an SVG and so has no `<body>`.

        `attrs` and `style` go on the `<img>` — `{"height": "98%"}` or
        `style="width:100%"` is how a page-image book says "fill the page".

        `href` names the image inside the container; passing the cover's href
        builds a page that shows only the cover, which is a page the reader
        should never be given twice.
        """
        if wrapper not in ("img", "svg", "svg-root"):
            raise ValueError(f"unknown wrapper: {wrapper!r}")
        href = href or f"image_{len(self._images) + 1}.jpg"
        self._images.setdefault(href, image)
        self._docs.append(
            {
                "kind": "image",
                "title": title,
                "listed": listed,
                "href": href,
                "wrapper": wrapper,
                "attrs": dict(attrs or {}),
                "style": style,
                "alt": alt,
            }
        )
        return self

    def add_calibre_inline_toc(self, *, listed: bool = False) -> "EpubBuilder":
        """calibre's generated contents page, as its MOBI/AZW3 output appends
        it: a list of links to every document, stamped with calibre's body id.

        Unlisted by default, which is where calibre puts it — in the spine and
        in the guide, not in the NCX.
        """
        self._docs.append(
            {"kind": "calibre_toc", "title": "Table of Contents", "listed": listed}
        )
        return self

    # ── resources ──────────────────────────────────────────────────────────

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

    # ── rendering ──────────────────────────────────────────────────────────

    def _doc_href(self, idx: int, doc: dict) -> str:
        """Filename for the document at 1-based position `idx`.

        Text chapters keep `chapter_N.xhtml` with N counted over *all*
        documents, so a book of chapters alone is named exactly as it was
        before image pages existed.
        """
        if doc["kind"] == "text":
            return f"chapter_{idx}.xhtml"
        if doc["kind"] == "calibre_toc":
            return f"inline_toc_{idx}.xhtml"
        return (
            f"page_{idx}.svg" if doc["wrapper"] == "svg-root" else f"page_{idx}.xhtml"
        )

    def _doc_id(self, idx: int, doc: dict) -> str:
        if doc["kind"] == "text":
            return f"chapter{idx}"
        if doc["kind"] == "calibre_toc":
            return f"inlinetoc{idx}"
        return f"page{idx}"

    def _doc_media_type(self, doc: dict) -> str:
        if doc["kind"] == "image" and doc["wrapper"] == "svg-root":
            return "image/svg+xml"
        return "application/xhtml+xml"

    def _render_doc(self, idx: int, doc: dict) -> bytes:
        if doc["kind"] == "text":
            body = doc["body"]
            if isinstance(body, bytes):
                return body
            return _XHTML_TEMPLATE.format(
                title=html.escape(doc["title"]),
                heading=html.escape(doc["title"]),
                body="<p>" + html.escape(body).replace("\n\n", "</p><p>") + "</p>",
            ).encode("utf-8")

        if doc["kind"] == "calibre_toc":
            entries = "\n".join(
                f'<li><a href="{self._doc_href(i, d)}">{html.escape(d["title"])}</a></li>'
                for i, d in enumerate(self._docs, start=1)
                if d["kind"] != "calibre_toc"
            )
            return _CALIBRE_INLINE_TOC_TEMPLATE.format(
                body_id=CALIBRE_INLINE_TOC_BODY_ID, entries=entries
            ).encode("utf-8")

        width, height = _read_image_size(self._images[doc["href"]])
        if doc["wrapper"] == "svg-root":
            return _SVG_ROOT_TEMPLATE.format(
                width=width, height=height, href=html.escape(doc["href"])
            ).encode("utf-8")

        if doc["wrapper"] == "svg":
            content = (
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="100%" height="100%" viewBox="0 0 {width} {height}">'
                f'<image width="{width}" height="{height}" '
                f'xlink:href="{html.escape(doc["href"])}"/></svg>'
            )
        else:
            attrs = "".join(
                f' {html.escape(k)}="{html.escape(v)}"'
                for k, v in sorted(doc["attrs"].items())
            )
            if doc["style"]:
                attrs += f' style="{html.escape(doc["style"])}"'
            content = (
                f'<img src="{html.escape(doc["href"])}" '
                f'alt="{html.escape(doc["alt"])}"{attrs}/>'
            )
        return _PAGE_TEMPLATE.format(
            title=html.escape(doc["title"]), content=content
        ).encode("utf-8")

    def _ncx(self) -> str:
        """An NCX naming each listed document.

        Without this every fixture's chapters were titled `Section 1`,
        `Section 2`, … because the converter had no table of contents to read.
        Two code paths key off a nav-derived chapter title — suppressing a
        duplicated opener (#64) and eliding a back-matter heading that equals
        its title (#62) — and neither could be exercised by a fixture. (#74)

        Documents added with `listed=False` are skipped, which is the point of
        that flag: a spine document the navigation never names.
        """
        points = []
        play_order = 0
        for idx, doc in enumerate(self._docs, start=1):
            if not doc["listed"]:
                continue
            play_order += 1
            points.append(
                f'    <navPoint id="np{idx}" playOrder="{play_order}">\n'
                f"      <navLabel><text>{html.escape(doc['title'])}</text></navLabel>\n"
                f'      <content src="{self._doc_href(idx, doc)}"/>\n'
                "    </navPoint>"
            )
        nav = "\n".join(points)
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            f'  <head><meta name="dtb:uid" content="test-{html.escape(self._title)}"/></head>\n'
            f"  <docTitle><text>{html.escape(self._title)}</text></docTitle>\n"
            f"  <navMap>\n{nav}\n  </navMap>\n"
            "</ncx>\n"
        )

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
            zf.writestr("OEBPS/toc.ncx", self._ncx().encode("utf-8"))

            # 4. spine documents
            for idx, doc in enumerate(self._docs, start=1):
                zf.writestr(
                    f"OEBPS/{self._doc_href(idx, doc)}", self._render_doc(idx, doc)
                )

            # 5. images referenced by image pages. An image page pointed at
            # the cover's href shares the cover's bytes rather than adding a
            # second copy.
            cover_href = self._cover[2] if self._cover is not None else None
            for href, data in self._images.items():
                if href != cover_href:
                    zf.writestr(f"OEBPS/{href}", data)

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
        cover_href = self._cover[2] if self._cover is not None else None
        for idx, doc in enumerate(self._docs, start=1):
            item_id = self._doc_id(idx, doc)
            manifest_items.append(
                f'    <item id="{item_id}" href="{self._doc_href(idx, doc)}" '
                f'media-type="{self._doc_media_type(doc)}"/>'
            )
            spine_items.append(f'    <itemref idref="{item_id}"/>')
        for n, href in enumerate(self._images, start=1):
            if href == cover_href:
                continue
            media = "image/png" if href.lower().endswith(".png") else "image/jpeg"
            manifest_items.append(
                f'    <item id="img{n}" href="{html.escape(href)}" '
                f'media-type="{media}"/>'
            )

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

        manifest_items.append(
            '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        )
        manifest_block = "\n".join(manifest_items)
        spine_block = "\n".join(spine_items)

        cover_meta = ""
        if self._cover is not None:
            cover_meta = '\n    <meta name="cover" content="cover-image"/>'
        if self._fixed_layout is not None:
            width, height = self._fixed_layout
            cover_meta += (
                '\n    <meta property="rendition:layout">pre-paginated</meta>'
                '\n    <meta name="fixed-layout" content="true"/>'
            )
            if width and height:
                cover_meta += f'\n    <meta name="original-resolution" content="{width}x{height}"/>'

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
  <spine toc="ncx">
{spine_block}
  </spine>
</package>
"""
