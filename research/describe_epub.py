#!/usr/bin/env python3
"""Report the *shape* of an EPUB, so a book can be described without being shared.

Most kfxgen defects are shape defects. Whether a page survives depends on how many
spine documents the navigation lists, whether art is an `<img>` or an `<svg>`, and
whether the markup states a size — not on what the book says. Two books with the
same shape hit the same code path, so the shape is the reproducer.

That matters because the books these defects show up in are usually ones nobody can
attach to an issue. This prints the shape and nothing else:

  * no title, author, identifier, publisher or date
  * no filenames from inside the book, and not the book's own filename
  * no text content, ever — text is counted, never read out

so its output can be pasted into a public thread as-is.

Usage:
    python3 research/describe_epub.py BOOK.epub [MORE.epub ...]
    python3 research/describe_epub.py --json BOOK.epub

Standard library only. No install, no virtualenv — it runs anywhere python3 does.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

OPF_NS = "{http://www.idpf.org/2007/opf}"
NCX_NS = "{http://www.daisy.org/z3986/2005/ncx/}"
XHTML_NS = "{http://www.w3.org/1999/xhtml}"
SVG_NS = "{http://www.w3.org/2000/svg}"

#: Markup extensions we parse. Everything else is counted by extension only.
DOC_EXT = (".xhtml", ".html", ".htm", ".svg")

#: A CSS length or percentage in a style attribute, e.g. `width: 98%`.
_STYLE_SIZE_RE = re.compile(r"(?:^|;)\s*(width|height)\s*:\s*([^;]+)", re.I)


def _local(tag: str) -> str:
    """Tag name without its namespace."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _opf_path(z: zipfile.ZipFile) -> str | None:
    """Locate the OPF through container.xml, falling back to a scan."""
    try:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        for rf in root.iter():
            if _local(rf.tag) == "rootfile" and rf.get("full-path"):
                return rf.get("full-path")
    except Exception:
        pass
    for name in z.namelist():
        if name.endswith(".opf"):
            return name
    return None


def _parse(z: zipfile.ZipFile, name: str):
    """Parse a member leniently; malformed markup is common and not our concern."""
    try:
        return ET.fromstring(z.read(name))
    except Exception:
        return None


def _nav_targets(z: zipfile.ZipFile, names: list[str]) -> tuple[set[str], int]:
    """(spine hrefs the navigation points at, how many entries point at them).

    Both numbers, because they answer different questions and conflating them
    is misleading. Fragments are dropped from the hrefs, since a nav entry into
    the middle of a page still means that page is listed — which is what the
    spine walk cares about. But five entries pointing at five anchors in one
    document then collapse to one target, and reporting that as "nav entries"
    reads as a book with almost no table of contents when it has a full one.

    Gutenberg's illustrated books are exactly this shape, and the single number
    made a 5-entry TOC look like a 1-entry one.
    """
    targets: set[str] = set()
    entries = 0
    for name in names:
        low = name.lower()
        if low.endswith(".ncx"):
            root = _parse(z, name)
            if root is None:
                continue
            for el in root.iter(f"{NCX_NS}content"):
                if el.get("src"):
                    entries += 1
                    targets.add(el.get("src").split("#")[0].rsplit("/", 1)[-1])
        elif low.endswith((".xhtml", ".html")):
            root = _parse(z, name)
            if root is None:
                continue
            # An EPUB 3 nav document is an <xhtml:nav epub:type="toc">.
            for nav in root.iter():
                if _local(nav.tag) != "nav":
                    continue
                if not any(
                    k.endswith("type") and "toc" in (v or "")
                    for k, v in nav.attrib.items()
                ):
                    continue
                for a in nav.iter():
                    if _local(a.tag) == "a" and a.get("href"):
                        entries += 1
                        targets.add(a.get("href").split("#")[0].rsplit("/", 1)[-1])
    return targets, entries


def _describe_doc(root) -> dict:
    """Count the shapes inside one markup document."""
    out = {
        "svg_rooted": 1 if _local(root.tag) == "svg" else 0,
        "svg_nested": 0,
        "img": 0,
        "img_sized_attr": 0,
        "img_sized_style": 0,
        "svg_image": 0,
        "text_chars": 0,
    }
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "svg" and not out["svg_rooted"]:
            out["svg_nested"] = 1
        elif tag == "image":
            out["svg_image"] += 1
        elif tag == "img":
            out["img"] += 1
            if el.get("width") or el.get("height"):
                out["img_sized_attr"] += 1
            if _STYLE_SIZE_RE.search(el.get("style") or ""):
                out["img_sized_style"] += 1
        # Text is measured, never recorded.
        if tag not in ("script", "style"):
            out["text_chars"] += len((el.text or "").strip())
    return out


def describe(path: Path) -> dict:
    """The shape of one EPUB, with nothing identifying in it."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        opf_name = _opf_path(z)
        opf = _parse(z, opf_name) if opf_name else None
        base = opf_name.rsplit("/", 1)[0] + "/" if opf_name and "/" in opf_name else ""

        href_by_id, type_by_id, props_by_id = {}, {}, {}
        if opf is not None:
            for item in opf.iter(f"{OPF_NS}item"):
                iid = item.get("id")
                if not iid:
                    continue
                href_by_id[iid] = item.get("href", "")
                type_by_id[iid] = item.get("media-type", "")
                props_by_id[iid] = item.get("properties", "") or ""

        spine_ids = []
        if opf is not None:
            for ref in opf.iter(f"{OPF_NS}itemref"):
                if ref.get("idref"):
                    spine_ids.append(ref.get("idref"))

        fxl = False
        if opf is not None:
            for meta in opf.iter(f"{OPF_NS}meta"):
                if meta.get("property") == "rendition:layout":
                    fxl = fxl or (meta.text or "").strip() == "pre-paginated"
            fxl = fxl or any("rendition:layout" in p for p in props_by_id.values())

        nav, nav_entry_count = _nav_targets(z, names)

        totals = {
            "svg_rooted": 0,
            "svg_nested": 0,
            "img": 0,
            "img_sized_attr": 0,
            "img_sized_style": 0,
            "svg_image": 0,
        }
        media_counts: dict[str, int] = {}
        listed = unlisted = text_pages = image_only_pages = 0

        for iid in spine_ids:
            href = href_by_id.get(iid, "")
            mt = type_by_id.get(iid, "") or "(unstated)"
            media_counts[mt] = media_counts.get(mt, 0) + 1
            if href.rsplit("/", 1)[-1] in nav:
                listed += 1
            else:
                unlisted += 1
            member = base + href
            if member not in names:
                member = next((n for n in names if n.endswith(href)), None)
            if not member or not member.lower().endswith(DOC_EXT):
                continue
            root = _parse(z, member)
            if root is None:
                continue
            d = _describe_doc(root)
            for k in totals:
                totals[k] += d[k]
            if d["text_chars"] >= 200:
                text_pages += 1
            elif d["img"] or d["svg_image"]:
                image_only_pages += 1

        img_ext: dict[str, int] = {}
        for n in names:
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            if ext in ("jpg", "jpeg", "png", "gif", "svg", "webp"):
                img_ext[ext] = img_ext.get(ext, 0) + 1

        return {
            "spine_documents": len(spine_ids),
            "nav_entries": nav_entry_count,
            "nav_target_documents": len(nav),
            "spine_listed_in_nav": listed,
            "spine_not_listed_in_nav": unlisted,
            "fixed_layout": fxl,
            "spine_media_types": media_counts,
            "pages_mostly_text": text_pages,
            "pages_image_only": image_only_pages,
            "svg_rooted_documents": totals["svg_rooted"],
            "documents_with_nested_svg": totals["svg_nested"],
            "svg_image_elements": totals["svg_image"],
            "img_elements": totals["img"],
            "img_with_width_or_height_attr": totals["img_sized_attr"],
            "img_with_width_or_height_style": totals["img_sized_style"],
            "image_files_by_extension": img_ext,
        }


def render(shape: dict, index: int | None = None) -> str:
    label = "EPUB shape" if index is None else f"EPUB shape [{index}]"
    lines = [label, "=" * len(label)]
    order = [
        ("spine_documents", "spine documents"),
        ("nav_entries", "nav entries"),
        ("nav_target_documents", "...pointing at N documents"),
        ("spine_listed_in_nav", "spine docs listed in nav"),
        ("spine_not_listed_in_nav", "spine docs NOT listed in nav"),
        ("fixed_layout", "fixed layout (pre-paginated)"),
        ("pages_mostly_text", "pages that are mostly text"),
        ("pages_image_only", "pages that are image only"),
        ("svg_rooted_documents", "spine docs that ARE an <svg>"),
        ("documents_with_nested_svg", "pages with <svg> inside them"),
        ("svg_image_elements", "<image> elements inside <svg>"),
        ("img_elements", "<img> elements"),
        ("img_with_width_or_height_attr", "<img> with width/height attribute"),
        ("img_with_width_or_height_style", "<img> with width/height in style"),
    ]
    for key, label_text in order:
        lines.append(f"  {label_text:36} {shape[key]}")
    for key, label_text in (
        ("spine_media_types", "spine media types"),
        ("image_files_by_extension", "image files by extension"),
    ):
        pretty = ", ".join(f"{k} x{v}" for k, v in sorted(shape[key].items())) or "none"
        lines.append(f"  {label_text:36} {pretty}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    as_json = "--json" in argv[1:]
    if not args:
        print(__doc__.strip().split("Usage:")[1].strip(), file=sys.stderr)
        return 2

    shapes = []
    for path in args:
        p = Path(path)
        if not p.is_file():
            print(f"not a file: {path}", file=sys.stderr)
            return 1
        try:
            shapes.append(describe(p))
        except zipfile.BadZipFile:
            print(f"not an EPUB (not a zip): {path}", file=sys.stderr)
            return 1

    if as_json:
        print(json.dumps(shapes if len(shapes) > 1 else shapes[0], indent=2))
    else:
        for i, shape in enumerate(shapes, start=1):
            print(render(shape, i if len(shapes) > 1 else None))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
