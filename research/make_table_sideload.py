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
other on the device. Eight chapters, each a different table shape:

    1  plain 3x4 table of numbers; also holds the 10 note links, a link into
       a cell of chapter 6, and the links to chapters 7 and 8
    2  <thead>, colspan, rowspan
    3  60 rows (page turns forward and back through a long table)
    4  8 columns of 3-5 words (wide: does it fit, wrap, or pan?)
    5  notes table in calibre's MOBI-to-EPUB layout: each `<a id="nN">` sits
       AFTER its own `<tr>`; the 10 links come from chapter 1
    6  the TOC entry points at the first cell's id, and a body link elsewhere
       points at a cell in row 8. (A TOC entry past a table's start makes
       that table fall back to rows, so the entry names the start.)
    7  table-level link targets: `<a id="before">` just before a table with
       `id="tbl"`. Chapter 1 links to `#before` and to `#tbl`.
    8  a separate file whose first block is a table; chapter 1 links to the
       whole file. kfxgen writes the three table-level targets (7's two, 8's
       file) on the table's first row (`$279`), never on the `$278`; each
       should land at that row.

Decision table, per device (Voyage 5.13.6, Oasis 5.18.2, Paperwhite 5.19.2;
read the firmware off each device):

    TOC button missing, TOC jumps break, or any body link (notes, cell,
    chapters 7 and 8) does nothing or lands on the wrong page, on the Native file
        -> stop. Leave the branch unmerged; 5.8.8's rows stay. Post on #219
           with device and firmware. Next step is a narrower spike: one
           table, no row groups.
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

Per device, record for both files: TOC button present; each of the 8 TOC
entries opens at its chapter start (chapter 6 opens at the top of its table,
with the chapter title above it); tables show rows and columns
with the header row distinct and colspan/rowspan cells spanning; wide table
readable (fits / wraps / pan-zoom: say which); long table pages through all 60
rows both ways; progress rises and is never stuck at 0% or 100%; each of the
10 note links opens the page holding its own note; the link into a cell lands
on the page with that row; the links to `#before`, `#tbl` and the whole file
each land on the page with the first row of the table they name.

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

#: Chapter 7's table-level link targets, and the file that opens with a table.
BEFORE_ID = "before"
TABLE_ID = "tbl"
TABLE_FIRST_FILE = "chapter_8.xhtml"

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
    "7. Table link targets",
    "8. File opening with a table",
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
        a = f' id="{TOC_CELL_ID}"' if r == 1 else ""
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
        f'<p>See <a href="chapter_6.xhtml#{LINK_CELL_ID}">row 8 of the '
        "chapter 6 table</a>.</p>"
    )
    table_links = (
        f'<p>Table targets: <a href="chapter_7.xhtml#{BEFORE_ID}">before the table</a>,'
        f' <a href="chapter_7.xhtml#{TABLE_ID}">the table</a>, and'
        f' <a href="{TABLE_FIRST_FILE}">the file that opens with a table</a>.</p>'
    )
    return _chapter(1, _plain(), after="\n".join([links, cell_link, table_links]))


def _target_table(label, table_id=""):
    rows = [
        _tr([f"{label} row {r} left", f"{label} row {r} right"]) for r in range(1, 9)
    ]
    open_tag = f'<table id="{table_id}">' if table_id else "<table>"
    return "\n".join([open_tag, *rows, "</table>"])


def _chapter_seven():
    return _chapter(
        7,
        _target_table("Chapter 7", TABLE_ID),
        before=f'<a id="{BEFORE_ID}"></a>',
        table_after=6,
    )


def _table_first_file():
    """Chapter 8: a spine file whose first block is a table, with no heading
    of its own; chapter 1 links to the whole file."""
    body = "\n".join([_target_table("File")] + [_para(8, p) for p in range(1, 4)])
    return _xhtml_page(_TITLES[7], body)


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
        _chapter_seven(),
        _table_first_file(),
    ]
    builder = _Builder().set_metadata(title=title, author=AUTHOR)
    for name, body in zip(_TITLES, bodies):
        builder.add_chapter(name, body.encode("utf-8"))
    slug = title.lower().replace(" ", "-")
    return builder.build(out_dir, f"{slug}-source")


def facts(kfx):
    """(`$278` tables, `$279` rows, `$269` cells under a row, yj_table version,
    {entry kind: body links targeting it})."""
    frags = load_fragments(kfx)
    tables = rows = cells = 0
    kind_of = {}
    for story in by_type(frags, "$259"):
        for entry in iter_entries(val(story)["$146"]):
            kind = str(entry.get("$159"))
            kind_of[entry["$155"]] = kind
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
    targets = {}
    for f in by_type(frags, "$266"):
        if str(val(f)["$180"]).startswith("body_anchor"):
            kind = kind_of[val(f)["$183"]["$155"]]
            targets[kind] = targets.get(kind, 0) + 1
    return tables, rows, cells, version, targets


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
        f"  {'title':<20} {'author':<12} {'$278':>5} {'$279':>6} {'$269':>6}  "
        "yj_table  body links by target kind"
    )
    for title, kfx, (tables, rows, cells, version, targets) in results:
        print(
            f"  {title:<20} {AUTHOR:<12} {tables:>5} {rows:>6} {cells:>6}  "
            f"{version if version is not None else '-':<8}  "
            + " ".join(f"{k}:{n}" for k, n in sorted(targets.items()))
        )
    for _, kfx, _ in results:
        print(f"  {kfx.name:<28} {kfx.stat().st_size / 1e3:>8.1f} kB")

    print(
        "\n"
        "on each device, for both books (see the docstring for the decision table)\n"
        "---------------------------------------------------------------------\n"
        "  1. Read the firmware off the device and write it down.\n"
        "  2. Open the TOC. Is the button present? Tap each of the 8 entries.\n"
        "  3. Ch 1 to 4: tables show rows and columns; ch 2 header row is\n"
        "     distinct and the Year cell spans two header rows, 'Harbour traffic'\n"
        "     spans 3 columns, 1902 spans two rows.\n"
        "  4. Ch 3: page forward and back through all 60 rows.\n"
        "  5. Ch 4: is the 8-column table readable? Fits, wraps, or pan/zoom?\n"
        "  6. Ch 1: tap each of the 10 superscript note links. Each should open\n"
        "     the page holding its own note in ch 5. Tap 'row 8' link: it should\n"
        "     land on the page with row 8 of ch 6's table.\n"
        "  7. Ch 6 TOC entry should open at the top of the table, under the\n"
        "     heading '6. Table with cell targets'.\n"
        "  8. Ch 1: tap 'before the table' and 'the table'. Each should land on\n"
        "     the page with 'Chapter 7 row 1' (the first row of ch 7's table).\n"
        "     Tap 'the file that opens with a table': it should land on the page\n"
        "     with 'File row 1', the top of ch 8. Any of the three doing\n"
        "     nothing is a navigation failure (see the decision table).\n"
        "  9. Progress rises through the book, never stuck at 0% or 100%.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
