#!/usr/bin/env python3
"""Build a sideload A/B pair for cell padding and row borders (#264).

Native cells were all written with a 1px padding, and rows and row groups
drew no border. They now carry the book's padding, and under
border-collapse its row and group borders, as Kindle Previewer 4.0.1 writes
them. This builds one source book and converts it twice through the real
`ebook-convert`, each plugin in its own isolated calibre config under the
output directory:

    "Padding New"   this checkout's plugin
    "Padding Old"   an earlier plugin zip given with --old-plugin (for example
                    a build of main before this change): 1px padding, no
                    row lines

The titles differ so the two files get different ASINs and neither replaces
the other on the device. All text in the book is invented. Six chapters, one
table each, every cell bordered so its padding shows as space inside the
lines:

    1  padding: 1em             a clear gap between each number and its lines
    2  padding: 0               numbers touching their lines
    3  padding: 0.5em 2em       more space at the sides than above and below
    4  rows ruled (collapsed)   a line under every row, none round the cells
    5  header ruled (collapsed) one thick line under the header row only
    6  rows ruled (separated)   no lines (a browser draws none either)

    .venv/bin/python research/make_padding_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/padding/`) is gitignored. Do not commit it.

On each device, "Padding New" (Old shows 1px padding everywhere and no row
or header lines):
  1. Ch 1-3: the spacing inside the cell lines as listed above.
  2. Ch 4: a line under each row; ch 5: one thick line under the header.
  3. Ch 6: no lines.
  4. No text clipped or covered; the zoom button opens each table; the TOC
     opens all 6 chapters.
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
table.lined td { border: 1px solid black; }
table.p1 td { padding: 1em; }
table.p0 td { padding: 0; }
table.pmix td { padding: 0.5em 2em; }
table.coll { border-collapse: collapse; }
tr.rule { border-bottom: 1px solid black; }
table.head thead { border-bottom: 3px solid black; }
"""


def _rows(n=3, tr=""):
    return "".join(
        f"<tr{tr}>"
        + "".join(f"<td>{10 + 17 * r + 7 * c}</td>" for c in range(3))
        + "</tr>"
        for r in range(n)
    )


CHAPTERS = [
    ("1. Padding 1em", f'<table class="lined p1">{_rows()}</table>'),
    ("2. Padding 0", f'<table class="lined p0">{_rows()}</table>'),
    ("3. Padding 0.5em 2em", f'<table class="lined pmix">{_rows()}</table>'),
    (
        "4. Rows ruled, collapsed",
        f'<table class="coll">{_rows(tr=" class='rule'")}</table>',
    ),
    (
        "5. Header ruled, collapsed",
        '<table class="coll head"><thead><tr><th>Station</th><th>Depth</th>'
        f"<th>Keeper</th></tr></thead><tbody>{_rows()}</tbody></table>",
    ),
    ("6. Rows ruled, separated", f"<table>{_rows(tr=" class='rule'")}</table>"),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="padding.css"/></head>\n'
        f"<body>\n<h1>{title}</h1>\n<p>Survey readings.</p>\n{body}\n"
        "<p>End of the readings.</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Padding", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="padding.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "padding-source")


def summary(kfx):
    """Each table: its first cell's padding keys and how many rows or groups
    carry a style, so a dropped stylesheet shows before a device copy."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        for e in iter_entries(val(f)["$146"]):
            if str(e.get("$159")) != "$278":
                continue
            parts = [g for g in e["$146"]] + [r for g in e["$146"] for r in g["$146"]]
            styled = sum(1 for x in parts if "$157" in x)
            cell = e["$146"][0]["$146"][0]["$146"][0]
            pad = styles[str(cell["$157"])].get("$53")
            out.append(
                {
                    "left padding": str(pad["$307"]) if pad else "none",
                    "styled rows/groups": styled,
                }
            )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/padding"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "padding-new.kfx"
    convert_with_calibre(env, source, new, "Padding New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: {summary(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "padding-old.kfx"
        convert_with_calibre(old_env, source, old, "Padding Old", native=True)
        print(f"  {old.name}: {summary(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
