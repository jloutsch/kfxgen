#!/usr/bin/env python3
"""Report what the converter does to a book's chapters — without naming the book.

`describe_epub.py` reports the shape of a *file*. This reports the shape of the
*conversion*: how many chapters come out, which of them carry pictures, and
what changes when the metadata-driven front-matter rewrites run.

That difference is the one most image-loss reports have turned on. A book
converts, the text is all there, and some pictures are not — and the question
is always which chapter dropped them and which code path it took. Answering it
has meant writing the same throwaway comparison by hand each time (#168, #178,
#182), so here it is once.

The comparison is the useful part. Chapters are extracted twice from the same
book — once with the book's metadata and once without — and only the
front-matter rewrites differ between the runs:

  * a title page's body becomes title + author
  * a half-title's becomes the title
  * a recognised contents listing is rebuilt from the chapter titles

Anything that loses an image between the two runs lost it to one of those.

Like `describe_epub.py`, this prints nothing that identifies the book. Chapter
labels are shown only when they are *structural* — Cover, Title Page,
Contents and the rest — and any other label is reported as `<chapter>`,
because a chapter title is as much the publisher's text as the prose is. The
output is safe to paste into a public thread as it stands.

Usage:
    python3 research/describe_chapters.py BOOK.epub
    python3 research/describe_chapters.py --json BOOK.epub

Needs the repository on the path (it imports the plugin), but not Calibre:
front-matter rewriting is pure Python. Image *conversion* does need Calibre,
so a GIF page will read as unresolved here and resolve in a real conversion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "plugin"))

from kfxgen import converter  # noqa: E402
from kfxgen._img_tokens import IMG_TOKEN_RE  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

#: Labels that are structural rather than authored, and so safe to print. Taken
#: from the converter's own sets so this cannot drift from what it acts on.
STRUCTURAL = (
    set(converter.TITLE_PAGE_TITLES)
    | set(converter.HALF_TITLE_TITLES)
    | set(converter._CONTENTS_TITLES)
    | set(converter.SMALL_TEXT_CHAPTERS)
    | {"cover", "cover page", "front cover", "frontispiece", "dedication"}
)


class _Quiet:
    def info(self, *a):
        pass

    warn = warning = error = debug = info


def _label(title: str) -> str:
    """A structural label, or a placeholder for anything the publisher wrote."""
    norm = converter._normalize_title(title or "")
    if not norm:
        return "<untitled>"
    return norm if norm in STRUCTURAL else "<chapter>"


def _row(index: int, ch: dict) -> dict:
    return {
        "index": index,
        "label": _label(ch.get("title", "")),
        "image_tokens": len(IMG_TOKEN_RE.findall(ch.get("text") or "")),
        "preserved_images": len(ch.get("preserved_images") or []),
        "text_chars": len(IMG_TOKEN_RE.sub("", ch.get("text") or "").strip()),
        "flags": sorted(
            k
            for k in ("_omit_from_toc", "_omit_title_heading", "toc_links", "font_size")
            if ch.get(k)
        ),
    }


def describe(path: Path) -> dict:
    oeb = EpubAsOeb(str(path))
    metadata = converter.extract_metadata(oeb, _Quiet())
    with_md = converter.extract_chapters_from_oeb(
        EpubAsOeb(str(path)), _Quiet(), metadata=metadata
    )
    without_md = converter.extract_chapters_from_oeb(
        EpubAsOeb(str(path)), _Quiet(), metadata=None
    )

    rows = [_row(i, c) for i, c in enumerate(with_md)]
    base = [_row(i, c) for i, c in enumerate(without_md)]

    # Pair by index. The two runs differ only in the front-matter rewrites, so
    # the chapter list is the same length; if it ever is not, say so rather
    # than silently comparing the wrong pages to each other.
    aligned = len(rows) == len(base)
    lost = []
    if aligned:
        for a, b in zip(rows, base):
            delta = (a["image_tokens"] + a["preserved_images"]) - b["image_tokens"]
            if delta < 0:
                lost.append(
                    {"index": a["index"], "label": a["label"], "images_lost": -delta}
                )

    return {
        "chapters_with_metadata": len(with_md),
        "chapters_without_metadata": len(without_md),
        "aligned": aligned,
        "image_tokens_with_metadata": sum(r["image_tokens"] for r in rows),
        "preserved_images_total": sum(r["preserved_images"] for r in rows),
        "image_tokens_without_metadata": sum(r["image_tokens"] for r in base),
        "chapters_losing_images": lost,
        "chapters": rows,
    }


def render(d: dict) -> str:
    out = ["chapter diagnostics", "===================", ""]
    for key, label in (
        ("chapters_with_metadata", "chapters (with metadata)"),
        ("chapters_without_metadata", "chapters (without metadata)"),
        ("image_tokens_with_metadata", "image tokens, with metadata"),
        ("preserved_images_total", "  ...plus preserved_images"),
        ("image_tokens_without_metadata", "image tokens, without metadata"),
    ):
        out.append(f"  {label:34} {d[key]}")
    if not d["aligned"]:
        out.append(
            "  NOTE: the two runs produced different chapter counts, so the "
            "per-chapter comparison below is not paired."
        )
    out.append("")
    out.append(
        f"  {'idx':>4}  {'label':<18} {'imgs':>5} {'kept':>5} {'chars':>7}  flags"
    )
    for r in d["chapters"]:
        out.append(
            f"  {r['index']:>4}  {r['label']:<18} {r['image_tokens']:>5} "
            f"{r['preserved_images']:>5} {r['text_chars']:>7}  {','.join(r['flags'])}"
        )
    if d["chapters_losing_images"]:
        out.append("")
        out.append("  chapters that lose images to the metadata rewrites:")
        for r in d["chapters_losing_images"]:
            out.append(f"    idx {r['index']} ({r['label']}): {r['images_lost']}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        print(__doc__.strip().split("Usage:")[1].strip(), file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 1
    d = describe(path)
    print(json.dumps(d, indent=2) if "--json" in argv else render(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
