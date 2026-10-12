#!/usr/bin/env python3
"""Build a sideload A/B pair for table cells over 2,000 characters (#348).

One invented source book, and pg12082 (Color Images from Mars Rovers, public
domain), each converted twice through the real `ebook-convert`, each plugin
in its own isolated calibre config under the output directory:

    "Long Cells New"   this checkout's plugin: the tables are native tables
    "Long Cells Old"   an earlier plugin zip given with --old-plugin (for
                       example a build of main): a cell over 2,000
                       characters keeps its table out of the native format
    "Mars Cells New" / "Mars Cells Old"   pg12082 the same way

The titles differ so the files get different ASINs and none replaces another
on the device.

    .venv/bin/python research/make_long_cell_sideload.py \\
        --old-plugin main.zip [out_dir]

Output (default `test_books/long_cells/`) is gitignored. Do not commit it.

Every long cell is made of numbered sentences, "Sentence 1." to "Sentence
100.", so a missing or cut-off part shows as a gap in the numbers.

On each device (the Voyage first, then the Paperwhite), "Long Cells New"
first, then "Long Cells Old". Open each entry from the TOC:
  1. "1. One column": a one-column table whose single cell runs over
     several pages. Page forward: Sentence 1 to Sentence 100 with no
     number missing or repeated, and nothing cut off at the bottom of a
     page.
  2. Page backward through the same cell, from Sentence 100 to Sentence 1.
  3. "2. Two columns" and "3. Three columns": the row with the long cell.
     Does it continue onto the next page (every sentence number shows), or
     is it cut off at the bottom of the screen? Note which.
  4. In each table, tap and hold, then open the table viewer: it scrolls
     through the whole long cell, Sentence 1 to 100.
  5. "4. A link into a cell": tap "Sentence 60". You land on the page
     showing Sentence 60 of the one-column cell.
In "Mars Cells New", open "Mars in Color, from “Spirit”" from the TOC and
search for "rock abrasion tool and science team members": the cell, about
2,070 characters, starts "NASA's Mars Exploration Rover Spirit took this
panoramic camera image" and ends "...science team members. 3/23/2004". It
shows inside the table and reads whole, and the table viewer opens on it.
"Old" lays the same table out as paragraphs, one per row.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from make_notes_table_sideload import install_zip  # noqa: E402
from make_table_sideload import (  # noqa: E402
    AUTHOR,
    CALIBRE_CUSTOMIZE,
    EBOOK_CONVERT,
    calibre_version,
    convert_with_calibre,
    install_plugin,
)

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402

_MARS = ROOT / "research/gutenberg-top-90/pg12082.epub"


def _long_cell(anchor_at=None):
    """100 numbered sentences, about 6,300 characters."""
    parts = []
    for i in range(1, 101):
        sentence = (
            f"Sentence {i}. The tide went out over the flats and the gulls came down. "
        )
        if i == anchor_at:
            sentence = f'<span id="s{i}">{sentence}</span>'
        parts.append(sentence)
    return "".join(parts)


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n<h1>{title}</h1>\n{body}\n</body>\n</html>\n"
    )


PAGES = [
    (
        "1. One column",
        "<p>Above the table.</p>"
        f"<table><tr><td>{_long_cell(anchor_at=60)}</td></tr></table>"
        "<p>Below the table.</p>",
    ),
    (
        "2. Two columns",
        "<table><tr><th>Place</th><th>Account</th></tr>"
        f"<tr><td>Harbour</td><td>{_long_cell()}</td></tr>"
        "<tr><td>Hill</td><td>A short one.</td></tr></table>"
        "<p>Below the table.</p>",
    ),
    (
        "3. Three columns",
        "<table><tr><th>A</th><th>Account</th><th>C</th></tr>"
        f"<tr><td>Left</td><td>{_long_cell()}</td><td>Right</td></tr>"
        "</table><p>Below the table.</p>",
    ),
    (
        "4. A link into a cell",
        '<p>Tap <a href="chapter_1.xhtml#s60">Sentence 60</a> to land inside '
        "the one-column cell.</p>",
    ),
]


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Long Cells", author=AUTHOR)
    for title, body in PAGES:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    return builder.build(out_dir, "long-cells-source")


def facts(kfx):
    """Native tables, the longest text entry inside one, and how many links
    land, so a wrong build shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    anchors = {str(f.fid) for f in by_type(frags, "$266")}

    def text(e):
        r = e["$145"]
        return str(content[str(r["name"])][int(r["$403"])])

    tables, longest, phrase = 0, 0, ""
    links = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            links += [str(s["$179"]) for s in e.get("$142") or [] if "$179" in s]
            if str(e.get("$159")) != "$278":
                continue
            tables += 1
            for c in iter_entries(e.get("$146") or []):
                if "$145" in c and len(text(c)) > longest:
                    longest, phrase = len(text(c)), text(c)[:50]
    return {
        "native tables": tables,
        "longest text in a table": longest,
        "it starts": phrase,
        "links": f"{sum(n in anchors for n in links)} of {len(links)} land",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/long_cells"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old files")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    books = [(build_source(out), "long-cells", "Long Cells")]
    if _MARS.exists():
        books.append((_MARS, "mars-cells", "Mars Cells"))
    envs = [("new", "New", install_plugin(out))]
    if args.old_plugin:
        envs.append(("old", "Old", install_zip(out, args.old_plugin)))
    print(f"built with: {calibre_version(envs[0][2])}, isolated configs")
    for src, stem, title in books:
        for tag, label, env in envs:
            kfx = out / f"{stem}-{tag}.kfx"
            convert_with_calibre(env, src, kfx, f"{title} {label}", native=True)
            print(f"  {kfx.name}: {facts(kfx)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
