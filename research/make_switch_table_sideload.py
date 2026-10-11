#!/usr/bin/env python3
"""Build a sideload book for an <epub:switch> in a table cell (#315).

Since #234 kfxgen draws only a switch's <epub:default>. A table whose cell
held a switch was still written one paragraph per row: the checks that
choose a native table counted the dropped MathML case. With #315 they count
only what is drawn, so these tables are native.

    "Switch Table New"   this checkout's plugin

    .venv/bin/python research/make_switch_table_sideload.py [out_dir]

Output (default `test_books/switch_table/`) is gitignored. Do not commit it.

The script prints, for each `$278` table in the KFX, its cells' text. On
`main` it prints none: all three tables were written as rows.

On each device, open each entry from the TOC button. Each table has ruled
cells; a table written as rows would show one line of plain text per row.
  1. "S1. An equation in a cell": a ruled table of two rows and two
     columns. The first cell reads "Area A = pi r2 of a circle"; no
     "MATHML" anywhere.
  2. "S2. A two-line fallback": a ruled table; the first cell shows
     "FallbackOne" and "FallbackTwo" on separate lines, with no "MATHML".
  3. "S3. Switch with no fallback": a ruled table; the first cell reads
     "Before after", nothing between.
  4. The TOC button works on every page.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

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

_LINE = "The keeper climbed the stair at dusk and wiped the salt from the glass. "
_CASE = (
    '<epub:case required-namespace="http://www.w3.org/1998/Math/MathML">'
    '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>MATHML</mi></math>'
    "</epub:case>"
)


def _table(first_cell):
    return (
        '<table border="1">'
        f"<tr><td>{first_cell}</td><td>Second column</td></tr>"
        "<tr><td>Row two</td><td>Last cell</td></tr></table>"
    )


CHAPTERS = [
    (
        "S1. An equation in a cell",
        _table(
            f"Area <epub:switch>{_CASE}"
            "<epub:default>A = pi r2</epub:default></epub:switch> of a circle"
        ),
    ),
    (
        "S2. A two-line fallback",
        _table(
            f"<epub:switch>{_CASE}<epub:default><p>FallbackOne</p>"
            "<p>FallbackTwo</p></epub:default></epub:switch>"
        ),
    ),
    (
        "S3. Switch with no fallback",
        _table(f"Before <epub:switch>{_CASE}</epub:switch>after"),
    ),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n<h1>{title}</h1>\n<p>Before. {_LINE}</p>\n{body}\n"
        f"<p>After. {_LINE}</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Switch Table", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    return builder.build(out_dir, "switch-table-source")


def tables(kfx):
    """Each `$278` table's cell texts, in reading order."""
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if str(e.get("$159")) != "$278":
                continue
            out.append(
                [
                    str(content[str(x["$145"]["name"])][int(x["$145"]["$403"])])
                    for x in iter_entries(e["$146"])
                    if "$145" in x
                ]
            )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/switch_table"))
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)
    env = install_plugin(out)
    new = out / "switch-table-new.kfx"
    convert_with_calibre(env, source, new, "Switch Table New", native=True)
    print(f"built with: {calibre_version(env)}, isolated config")
    for cells in tables(new):
        print(f"  table: {cells}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
