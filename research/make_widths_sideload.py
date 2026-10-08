#!/usr/bin/env python3
"""Build a sideload A/B pair for percentage column widths (#264).

Native tables were laid out with the device's own column widths. They now
carry the book's percentage widths as Kindle Previewer 4.0.1 writes them
($152 on the table). This builds one source book and converts it twice
through the real `ebook-convert`, each plugin in its own isolated calibre
config under the output directory:

    "Widths New 3"   this checkout's plugin
    "Widths Old 3"   an earlier plugin zip given with --old-plugin (for
                     example a build of main before this change): device
                     widths

The titles differ so the two files get different ASINs and neither replaces
the other on the device. ("2": a first version put four words in every cell,
and a column is never narrower than its longest word, so in portrait every
narrow column sat at that minimum and the widths seemed to be ignored.) Every
cell now holds a two-digit number. ("3": a table with no width of its own is
sized to its content on the Kindle, so with short cells its column widths
don't show; Kindle Previewer writes such a table the same way. Chapters 6 and
7 set `width: 100%` on the table, as books that want column widths to hold
do.) On Old the columns come out about equal. Seven chapters, one table each:

    1  <col> widths 20% / 30% / 50%
    2  cell widths 25% / 25% / 50%
    3  a narrow first column (10%) beside a wide one (90%)
    4  <col> widths 10% / 20% / 30% (adding up to 60%)
    5  <col> widths 20% / 80% with a border round every cell
    6  <col> widths 20% / 30% / 50% on a table set to width: 100%
    7  cell widths 25% / 25% / 50% on a table set to width: 100%

    .venv/bin/python research/make_widths_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/widths/`) is gitignored. Do not commit it.

On each device, in portrait, "Widths New 3" (Old 3 has columns of about
equal width). Chapters 1-5 may show equal columns in portrait, as Kindle
Previewer's own output would; chapters 6 and 7 are the check:
  1. Ch 1 and 2: the third column about as wide as the first two together.
  2. Ch 3: the first column narrow, its words still whole (not broken a
     letter per line); the second column takes the rest.
  3. Ch 4: the columns widen left to right in the proportions 1 : 2 : 3.
  4. Ch 5: the first column about a quarter as wide as the second, with
     the cell borders drawn.
  5. Ch 6 and 7: the table fills the page width; ch 6's columns are three
     different widths, the third widest; ch 7's first two are equal and the
     third about as wide as both together.
  6. The zoom button opens each table; the TOC opens all 7 chapters.
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


def _rows(n, cell_attrs=None):
    cell_attrs = cell_attrs or [""] * n
    return "".join(
        "<tr>"
        + "".join(f"<td{cell_attrs[c]}>{10 + 17 * r + 7 * c}</td>" for c in range(n))
        + "</tr>"
        for r in (1, 2, 3)
    )


def _cols(*pcts):
    return (
        "<colgroup>"
        + "".join(f'<col style="width:{p}%"/>' for p in pcts)
        + "</colgroup>"
    )


CHAPTERS = [
    ("1. Column widths 20, 30, 50", f"<table>{_cols(20, 30, 50)}{_rows(3)}</table>"),
    (
        "2. Cell widths 25, 25, 50",
        "<table>"
        + _rows(3, [' style="width:25%"', ' style="width:25%"', ' style="width:50%"'])
        + "</table>",
    ),
    ("3. A narrow first column", f"<table>{_cols(10, 90)}{_rows(2)}</table>"),
    ("4. Widths adding up to 60", f"<table>{_cols(10, 20, 30)}{_rows(3)}</table>"),
    ("5. Widths with borders", f'<table border="1">{_cols(20, 80)}{_rows(2)}</table>'),
    (
        "6. Column widths on a full-width table",
        f'<table style="width:100%">{_cols(20, 30, 50)}{_rows(3)}</table>',
    ),
    (
        "7. Cell widths on a full-width table",
        '<table style="width:100%">'
        + _rows(3, [' style="width:25%"', ' style="width:25%"', ' style="width:50%"'])
        + "</table>",
    ),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n<h1>{title}</h1>\n<p>Survey readings.</p>\n{body}\n"
        "<p>End of the readings.</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Widths", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    return builder.build(out_dir, "widths-source")


def widths_per_chapter(kfx):
    """Each storyline's tables' $152 widths, so a lost width shows before
    anything is copied to a device."""
    frags = load_fragments(kfx)
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        for e in iter_entries(val(f)["$146"]):
            if str(e.get("$159")) == "$278":
                out.append(
                    [
                        f"{float(x['$56']['$307']):g}%" if "$56" in x else "-"
                        for x in e.get("$152") or []
                    ]
                )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/widths"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "widths-new.kfx"
    convert_with_calibre(env, source, new, "Widths New 3", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: widths per table {widths_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "widths-old.kfx"
        convert_with_calibre(old_env, source, old, "Widths Old 3", native=True)
        print(f"  {old.name}: widths per table {widths_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
