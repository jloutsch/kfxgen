#!/usr/bin/env python3
"""Build the sideload pair that gates native tables on device (#219).

kfxgen writes simple tables as native KFX tables (`$278` table, `$151`/`$454`
row groups, `$279` rows, `$269` cells) by default. Through 5.8.8 every table
was flattened to one paragraph per row. Native tables are the first output
that depends on the reader's table layout, and nothing but a Kindle can say
whether it lays one out. Two decoders (ours, and KFX Input's) read the file
back correctly; that predicts the device and does not confirm it.

This builds a controlled pair: one source text, converted twice.

    "Table Gate Native"   default options, native tables on
    "Table Gate Rows"     `kfxgen_disable_native_tables = True`, the 5.8.8 shape

The titles differ so the two files get different ASINs and neither replaces the
other on the device. Six chapters, each a different table shape:

    1  plain 3x4 table of numbers; also holds the 10 note links and a link
       into a cell of chapter 6
    2  <thead>, colspan, rowspan
    3  60 rows (page turns forward and back through a long table)
    4  8 columns of 3-5 words (wide: does it fit, wrap, or pan?)
    5  notes table in calibre's MOBI-to-EPUB layout: each `<a id="nN">` sits
       AFTER its own `<tr>`; the 10 links come from chapter 1
    6  the TOC entry points at a cell id, and a body link elsewhere points at
       another cell

Decision table, per device (Voyage 5.13.6, Oasis 5.18.2, Paperwhite 5.19.2;
read the firmware off each device):

    TOC button missing, or TOC jumps break, on the Native file
        -> stop. Do not merge. Post on #219 with device and firmware. Next
           step is a narrower spike: one table, no row groups.
    Native tables render on every device, all checks pass
        -> record the result below, add `native_tables` to
           tests/device/checklist.py, continue.
    Only the wide table (chapter 4) fails
        -> file an issue for the table viewer (`$629`/`$630`,
           `yj_table_viewer`), then choose on #219: keep native tables with
           wide tables falling back to rows by column count, or ship as is.
    Rows file differs from 5.8.8 behaviour
        -> the opt-out is not byte-faithful to 5.8.8. Investigate before
           anything else.

Per device, record for both files: TOC button present; each of the 6 TOC
entries opens at its chapter start (chapter 6 opens at the top of its table,
because the converter starts the chapter at the table holding the targeted
cell, with the chapter title repeated above it); tables show rows and columns
with the header row distinct and colspan/rowspan cells spanning; wide table
readable (fits / wraps / pan-zoom: say which); long table pages through all 60
rows both ways; progress rises and is never stuck at 0% or 100%; each of the
10 note links opens the page holding its own note; the link into a cell lands
on the page with that row.

    .venv/bin/python research/make_table_sideload.py [out_dir]

Output is gitignored (`*.kfx`, `*.epub`). Do not commit it.

Result: not yet run on a device.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT))

from kfxgen import converter as conv  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402
from tests.fixtures.golden.inputs import _xhtml_page  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

AUTHOR = "kfxgen test"
PARAS_PER_CHAPTER = 12
NOTES = 10
LONG_TABLE_ROWS = 60

#: The cell the chapter 6 TOC entry points at, and the cell chapter 1 links to.
TOC_CELL_ID = "toc-cell"
LINK_CELL_ID = "link-cell"

_FILLER = (
    "The keeper walked the harbour wall at dusk and counted the lamps that "
    "still burned. Rope creaked against the bollards. Somewhere beyond the "
    "breakwater a bell answered the swell, slow and unhurried, marking a "
    "channel no one had used since the spring."
)

_TITLES = [
    "1. Plain table",
    "2. Header, colspan, rowspan",
    "3. Long table",
    "4. Wide table",
    "5. Notes table",
    "6. Table with cell targets",
]

_WIDE_CELLS = [
    "grey harbour wall",
    "slow tide table",
    "north channel marker",
    "old iron bell",
    "lamp oil supply",
    "keeper's written log",
    "spring swell report",
    "rope and bollard check",
]


class _Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


def _para(chapter, p):
    return f"<p>Chapter {chapter}, paragraph {p}. {_FILLER}</p>"


def _chapter(n, table_html, *, before="", after="", table_after=3):
    """Heading, filler paragraphs with `table_html` after paragraph
    `table_after`. `before` and `after` are extra markup around the table."""
    parts = [f"<h1>{_TITLES[n - 1]}</h1>"]
    for p in range(1, PARAS_PER_CHAPTER + 1):
        parts.append(_para(n, p))
        if p == table_after:
            parts.extend([before, table_html, after])
    return _xhtml_page(_TITLES[n - 1], "\n".join(x for x in parts if x))


def _table(rows, head=None):
    out = ["<table>"]
    if head:
        out += ["<thead>", head, "</thead>", "<tbody>"]
    out += rows
    if head:
        out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out)


def _tr(cells, tag="td"):
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def _plain():
    rows = [_tr([r * 4 + c + 1 for c in range(4)]) for r in range(3)]
    return _table(rows)


def _spans():
    head = (
        "<tr><th rowspan='2'>Year</th><th colspan='3'>Harbour traffic</th></tr>"
        "<tr><th>Boats</th><th>Barges</th><th>Ferries</th></tr>"
    )
    rows = [
        _tr([1901, 120, 14, 3]),
        "<tr><td rowspan='2'>1902</td><td>131</td><td>16</td><td>3</td></tr>",
        _tr([140, 18, 4]),
        _tr([1903, 152, 21, 5]),
    ]
    return _table(rows, head=head)


def _long():
    rows = [
        _tr([f"Row {r}", r * 7, r * 13 % 100]) for r in range(1, LONG_TABLE_ROWS + 1)
    ]
    return _table(rows, head=_tr(["Row", "Count", "Share"], tag="th"))


def _wide():
    rows = [_tr([f"{cell} {r}" for cell in _WIDE_CELLS]) for r in range(1, 9)]
    return _table(rows, head=_tr(_WIDE_CELLS, tag="th"))


def _notes():
    # calibre's MOBI-to-EPUB layout: the anchor follows its own row, nothing
    # precedes the first row, one follows the last.
    rows = []
    for n in range(1, NOTES + 1):
        rows.append(_tr([f"{n}.", f"Note {n} text, invented for the gate test."]))
        rows.append(f'<a id="n{n}"></a>')
    return _table(rows)


def _cells_table():
    rows = []
    for r in range(1, 11):
        a = f' id="{TOC_CELL_ID}"' if r == 6 else ""
        b = f' id="{LINK_CELL_ID}"' if r == 8 else ""
        rows.append(f"<tr><td{a}>Row {r} left</td><td{b}>Row {r} right</td></tr>")
    return _table(rows)


def _chapter_one():
    links = "\n".join(
        f"<p>Claim {n} in the harbour survey."
        f'<a href="chapter_5.xhtml#n{n}"><sup>{n}</sup></a></p>'
        for n in range(1, NOTES + 1)
    )
    cell_link = (
        f'<p>See <a href="chapter_6.xhtml#{LINK_CELL_ID}">row 8 of the last '
        "table</a>.</p>"
    )
    return _chapter(1, _plain(), after=links + "\n" + cell_link)


class _Builder(EpubBuilder):
    def _ncx(self):
        # Point chapter 6's navigation entry at a cell, not at the file.
        return (
            super()
            ._ncx()
            .replace('src="chapter_6.xhtml"', f'src="chapter_6.xhtml#{TOC_CELL_ID}"')
        )


def build_source(out_dir, title):
    """The source EPUB. It has an NCX with one entry per chapter: without one,
    calibre and the shim treat the note links as TOC entries and split the note
    pages into chapters."""
    bodies = [
        _chapter_one(),
        _chapter(2, _spans()),
        _chapter(3, _long(), table_after=2),
        _chapter(4, _wide()),
        _chapter(5, _notes(), table_after=2),
        _chapter(6, _cells_table(), table_after=2),
    ]
    builder = _Builder().set_metadata(title=title, author=AUTHOR)
    for name, body in zip(_TITLES, bodies):
        builder.add_chapter(name, body.encode("utf-8"))
    slug = title.lower().replace(" ", "-")
    return builder.build(out_dir, f"{slug}-source")


def facts(kfx):
    """(`$278` tables, `$279` rows, `$269` cells under a row, yj_table version)."""
    frags = load_fragments(kfx)
    tables = rows = cells = 0
    for story in by_type(frags, "$259"):
        for entry in iter_entries(val(story)["$146"]):
            kind = str(entry.get("$159"))
            if kind == "$278":
                tables += 1
            elif kind == "$279":
                rows += 1
                cells += sum(
                    1 for c in (entry.get("$146") or []) if str(c.get("$159")) == "$269"
                )
    version = None
    for f in by_type(frags, "$585"):
        for feat in val(f).get("$590") or []:
            if str(feat["$492"]) == "yj_table":
                version = int(feat["$589"]["version"]["$587"])
    return tables, rows, cells, version


def main():
    out_dir = Path(
        sys.argv[1] if len(sys.argv) > 1 else ROOT / "test_books/table-native"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    builds = [
        ("Table Gate Native", None),
        ("Table Gate Rows", SimpleNamespace(kfxgen_disable_native_tables=True)),
    ]
    results = []
    for title, opts in builds:
        source = build_source(out_dir, title)
        kfx = out_dir / (title.lower().replace(" ", "-") + ".kfx")
        conv.convert_oeb_to_kfx(EpubAsOeb(str(source)), str(kfx), opts=opts, log=_Log())
        results.append((title, kfx, facts(kfx)))

    print(f"\nwrote to {out_dir}\n")
    print(
        f"  {'title':<20} {'author':<12} {'$278':>5} {'$279':>6} {'$269':>6}  yj_table"
    )
    for title, kfx, (tables, rows, cells, version) in results:
        print(
            f"  {title:<20} {AUTHOR:<12} {tables:>5} {rows:>6} {cells:>6}  "
            f"{version if version is not None else '-'}"
        )
    for _, kfx, _ in results:
        print(f"  {kfx.name:<28} {kfx.stat().st_size / 1e3:>8.1f} kB")

    print(
        "\n"
        "on each device, for both books (see the docstring for the decision table)\n"
        "---------------------------------------------------------------------\n"
        "  1. Read the firmware off the device and write it down.\n"
        "  2. Open the TOC. Is the button present? Tap each of the 6 entries.\n"
        "  3. Ch 1 to 4: tables show rows and columns; ch 2 header row is\n"
        "     distinct and the Year cell spans two header rows, 'Harbour traffic'\n"
        "     spans 3 columns, 1902 spans two rows.\n"
        "  4. Ch 3: page forward and back through all 60 rows.\n"
        "  5. Ch 4: is the 8-column table readable? Fits, wraps, or pan/zoom?\n"
        "  6. Ch 1: tap each of the 10 superscript note links. Each should open\n"
        "     the page holding its own note in ch 5. Tap 'row 8' link: it should\n"
        "     land on the page with row 8 of ch 6's table.\n"
        "  7. Ch 6 TOC entry should open at the top of the table, under the\n     heading '6. Table with cell targets' (it is not expected at row 6).\n"
        "  8. Progress rises through the book, never stuck at 0% or 100%.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
