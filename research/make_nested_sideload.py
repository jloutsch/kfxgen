#!/usr/bin/env python3
"""Build a sideload A/B pair for tables inside tables (#263).

On `main`, a table holding a table is written as rows, with the inner
table's text run into its row. With #263, the row is split at the inner
table: the row's text before and after stays row paragraphs, and the inner
table is written as a native table of its own. Nothing is nested in the KFX.

One source book, converted twice through the real `ebook-convert`, each
plugin in its own isolated calibre config under the output directory:

    "Nested New"   this checkout's plugin
    "Nested Old"   an earlier plugin zip given with --old-plugin (main)

The titles differ so the two files get different ASINs. All text in the book
is invented. Its shapes follow the three library books that hold nested
tables (counts and shapes only).

    .venv/bin/python research/make_nested_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/nested/`) is gitignored. Do not commit it.

On each device, both books. Open each entry from the TOC button:
  1. "1. A box in a box": New: one bordered box around "The keeper's
     instructions" and its two lines. Old: the same words as plain lines.
  2. "2. A table in a cell": New: "Station" lines as paragraphs, then a
     3-column table (Day, Wind, Sea) with 11 rows and a zoom button; tapping
     zoom opens it. Old: one long line per row, no columns.
  3. "3. Text around a table": New: "Before the list.", then a 1-column
     list of 6 lighthouses, "Between the tables.", a 3-column table of
     readings with a zoom button, then "After the tables." Old: all of it run
     into one paragraph.
  4. "4. A link into a table": tap "the reading for day 7". New: lands on
     the row for day 7 in chapter 2's table. Old: lands on the long line
     holding it.
  5. The TOC button is present on every page and each entry opens its
     chapter.
"""

import argparse
import collections
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

CSS = "table.ruled, table.ruled td { border: 1px solid black; }\n"
_WIND = ["calm", "light", "fresh", "strong", "gale"]
_SEA = ["smooth", "slight", "moderate", "rough"]
_LIGHTS = [
    "North Head",
    "Gull Rock",
    "Long Point",
    "Black Ness",
    "Saltash",
    "Fair Isle",
]


def _readings(days, cols=3, link_day=None):
    head = "<tr><th>Day</th><th>Wind</th><th>Sea</th></tr>" if cols == 3 else ""
    rows = []
    for d in range(1, days + 1):
        rid = ' id="day7"' if d == link_day else ""
        rows.append(
            f"<tr{rid}><td>{d}</td><td>{_WIND[d % 5]}</td><td>{_SEA[d % 4]}</td></tr>"
        )
    return f"<table>{head}{''.join(rows)}</table>"


def _box_in_box():
    inner = (
        '<table class="ruled"><tr><td><p>The keeper\'s instructions</p>'
        "<p>Trim the wick at dusk.</p><p>Wipe the glass at dawn.</p></td></tr></table>"
    )
    return f'<table class="ruled"><tr><td>{inner}</td></tr></table>'


def _table_in_cell():
    rows = [
        f"<tr><td>Station {n}</td><td>Readings taken at the station.</td></tr>"
        for n in range(1, 7)
    ]
    rows.insert(
        3,
        "<tr><td>Log</td><td>" + _readings(11, link_day=7) + "</td></tr>",
    )
    return f"<table>{''.join(rows)}</table>"


def _text_around():
    lights = "".join(f"<tr><td>{n}</td></tr>" for n in _LIGHTS)
    cell = (
        "<p>Before the list.</p>"
        f"<table>{lights}</table>"
        "<p>Between the tables.</p>"
        f"{_readings(4)}"
        "<p>After the tables.</p>"
    )
    return f"<table><tr><td>{cell}</td></tr></table>"


CHAPTERS = [
    ("1. A box in a box", _box_in_box()),
    ("2. A table in a cell", _table_in_cell()),
    ("3. Text around a table", _text_around()),
    (
        "4. A link into a table",
        '<p>Tap <a href="chapter_2.xhtml#day7">the reading for day 7</a>.</p>',
    ),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="nested.css"/></head>\n'
        f"<body>\n<h1>{title}</h1>\n{body}\n<p>End of the chapter.</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Nested", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="nested.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "nested-source")


def tables_per_chapter(kfx):
    """Native tables in each storyline, as "zoom" or "plain"."""
    frags = load_fragments(kfx)
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        kinds = collections.Counter(
            "zoom" if "$629" in e else "plain"
            for e in iter_entries(val(f)["$146"])
            if str(e.get("$159")) == "$278"
        )
        out.append(dict(kinds))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/nested"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "nested-new.kfx"
    convert_with_calibre(env, source, new, "Nested New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: tables per chapter {tables_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "nested-old.kfx"
        convert_with_calibre(old_env, source, old, "Nested Old", native=True)
        print(f"  {old.name}: tables per chapter {tables_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
