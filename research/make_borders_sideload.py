#!/usr/bin/env python3
"""Build a sideload A/B pair for table and cell borders (#264).

Native tables were drawn with no borders. They now carry the book's borders
as Kindle Previewer 4.0.1 writes them. This builds one source book and
converts it twice through the real `ebook-convert`, each plugin in its own
isolated calibre config under the output directory:

    "Borders New"   this checkout's plugin
    "Borders Old"   an earlier plugin zip given with --old-plugin (for
                    example a build of main before #264): no borders

The titles differ so the two files get different ASINs and neither replaces
the other on the device. All text in the book is invented. Six chapters, one
table each:

    1  border="1": a grey frame round the table, a thin rule round each cell
    2  CSS 1px solid black on table and cells, collapsed: a single-line grid
    3  CSS border-bottom on the header cells only: one rule under the header
    4  CSS 2px dotted red round each cell
    5  border="3": a thick grey frame round the table
    6  a one-column box with a 1px solid border (no zoom button)

    .venv/bin/python research/make_borders_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/borders/`) is gitignored. Do not commit it.

On each device, "Borders New" (Old shows the same tables with no lines):
  1. Each chapter's lines as listed above, around the right cells, none
     missing and none extra.
  2. The text inside each cell is not covered or clipped by a line.
  3. Ch 1 to 5: the zoom button opens the table, with the same lines.
  4. Ch 6: no zoom button; the box's four sides drawn.
  5. The TOC button is present and each of the 6 entries opens its chapter.
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

CSS = """
table.grid, table.grid td, table.grid th { border: 1px solid black; }
table.grid { border-collapse: collapse; }
table.rule th { border-bottom: 1px solid black; }
table.dot td { border: 2px dotted red; }
table.box { border: 1px solid black; }
"""


def _table(attrs, head=False):
    first = (
        "<tr><th>Station</th><th>Reading</th><th>Keeper</th></tr>"
        if head
        else "<tr><td>Station</td><td>Reading</td><td>Keeper</td></tr>"
    )
    rows = "".join(
        f"<tr><td>North {n}</td><td>{n * 17} fathoms</td><td>Keeper {n}</td></tr>"
        for n in (1, 2, 3)
    )
    return f"<table{attrs}>{first}{rows}</table>"


CHAPTERS = [
    ("1. The border attribute", _table(' border="1"')),
    ("2. A single-line grid", _table(' class="grid"', head=True)),
    ("3. A rule under the header", _table(' class="rule"', head=True)),
    ("4. Dotted red cells", _table(' class="dot"')),
    ("5. A thick frame", _table(' border="3"')),
    (
        "6. A bordered box",
        '<table class="box"><tr><td><p>The lamp was lit at the harbour mouth,</p>'
        "<p>and the keeper climbed the stair.</p></td></tr></table>",
    ),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="borders.css"/></head>\n'
        f"<body>\n<h1>{title}</h1>\n<p>Survey readings.</p>\n{body}\n"
        "<p>End of the readings.</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Borders", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="borders.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "borders-source")


def bordered_per_chapter(kfx):
    """Each storyline's bordered tables and cells, so a dropped stylesheet
    shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    keys = {f"${n}" for n in range(84, 98)} | {"$83"}
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        tables = cells = 0
        for e in iter_entries(val(f)["$146"]):
            st = styles.get(str(e.get("$157")), {})
            drawn = {str(k) for k in st} & (keys - {"$83"})
            if drawn and str(e.get("$159")) == "$278":
                tables += 1
            elif drawn:
                cells += 1
        out.append({"tables": tables, "cells": cells})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/borders"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "borders-new.kfx"
    convert_with_calibre(env, source, new, "Borders New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: bordered per chapter {bordered_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "borders-old.kfx"
        convert_with_calibre(old_env, source, old, "Borders Old", native=True)
        print(f"  {old.name}: bordered per chapter {bordered_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
