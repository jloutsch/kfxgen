#!/usr/bin/env python3
"""Build the sideload pair that gates native tables on device (#219).

kfxgen writes simple tables as native KFX tables (`$278` table, `$151`/`$454`
row groups, `$279` rows, `$269` cells) by default. Through 5.8.8 every table
was flattened to one paragraph per row. Native tables are the first output
that depends on the reader's table layout, and nothing but a Kindle can say
whether it lays one out. Two decoders (ours, and KFX Input's) read the file
back correctly; that predicts the device and does not confirm it.

This builds a controlled pair: one source text, converted twice through the
real `ebook-convert`, with this checkout's plugin installed into an isolated
calibre config under the output directory (so calibre's Stylizer runs, as it
does for users, and the user's own calibre setup is not touched):

    "Table Gate Native"   default options, native tables on
    "Table Gate Rows"     `--kfxgen-disable-native-tables`, the 5.8.8 shape

If `ebook-convert` or `calibre-customize` is not found (on PATH, in
/Applications/calibre.app, or next to $KFXGEN_EBOOK_CONVERT), it exits
non-zero and writes nothing. The test shim (`EpubAsOeb`) has no Stylizer, so
a pair built through it carries no CSS-derived alignment or fonts and is not
fit for the device gate.

The titles differ so the two files get different ASINs and neither replaces the
other on the device. Twelve chapters, each a different table shape:

    1  plain 3x4 table of numbers; also holds the 10 note links, a link into
       a cell of chapter 6, and the links to chapters 7 and 8
    2  <thead>, colspan, rowspan
    3  60 rows (page turns forward and back through a long table)
    4  8 columns of 3-5 words (wide: does it fit, wrap, or pan?)
    5  notes table in calibre's MOBI-to-EPUB layout: each `<a id="nN">` sits
       AFTER its own `<tr>`; the 10 links come from chapter 1, and one more
       from a cell in chapter 10
    6  the TOC entry points at the first cell's id, and a body link elsewhere
       points at a cell in row 8. (A TOC entry past a table's start makes
       that table fall back to rows, so the entry names the start.)
    7  table-level link targets: `<a id="before">` just before a table with
       `id="tbl"`. Chapter 1 links to `#before` and to `#tbl`.
    8  a separate file whose first block is a table; chapter 1 links to the
       whole file. kfxgen writes the three table-level targets (7's two, 8's
       file) on the table's first row (`$279`), never on the `$278`; each
       should land at that row.
    9  embedded Charis SIL (regular, bold, italic, bold italic, from
       test_books/font-matching-test): a plain table, then a table whose cells
       are bold by CSS. Cells take their face from the matched font (#50).
   10  formatted runs in cells: a centred header cell holding an italic run,
       a bold run and an italic run in body cells, a superscript note link
       out of a cell to note 7 in chapter 5, and the word "marrowquill",
       which appears only in one cell (the search check).
   11  2,000 rows (the largest real native tables run to 1,943 rows): page
       turns, progress, and how long the chapter takes to open
   12  24 columns (the widest real one is 27). Since the gate, a table wider
       than the column limit keeps rows (8 after the gate, 10 since #254), so
       on the Native file this chapter matches the Rows file.

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
    Only the large or wide tables fail (chapter 4, 11 or 12: unreadable,
    cut off, very slow to open, or progress stuck), and the small ones pass
        -> fall back to rows by row or column count: set the limit from the
           largest table that passed (row count from chapters 3 and 11,
           column count from chapters 4 and 12), add it to
           `_table_is_native` with a test, and rebuild the pair. For a wide
           table, also file an issue for the table viewer (`$629`/`$630`,
           `yj_table_viewer`). Choose on #219.
    Cells in chapter 9 or 10 lose their face or formatting (a bold table in
    the regular face, a missing italic, bold or raised note number, the
    header cell not centred), or the note link in the chapter 10 cell does
    nothing
        -> a cell style or link bug, not a layout one: file it on #219 with
           the device and fix it before merge. The Rows file is the control
           for the face, the runs and the link, but not for the bold table:
           5.8.8 writes a row as one paragraph and drops a cell's own CSS
           weight, so that table is regular there (seen in this build's
           styles, not a device result).
    "marrowquill" is not found on the Native file but is on the Rows file
        -> first check the device has finished indexing (see the indexing
           queue); if it has, the search index skips nested entries: post on
           #219 before merge.
    Rows file differs from 5.8.8 behaviour
        -> the opt-out is not byte-faithful to 5.8.8. Investigate before
           anything else.

Per device, record for both files: TOC button present; each of the 12 TOC
entries opens at its chapter start (chapter 6 opens at the top of its table,
with the chapter title above it); tables show rows and columns
with the header row distinct and colspan/rowspan cells spanning; wide tables
readable (fits / wraps / pan-zoom: say which, for chapters 4 and 12); long
tables page through all rows both ways (60 in chapter 3; in chapter 11 page
through the first and last 50 rows and note how long the chapter takes to
open); progress rises and is never stuck at 0% or 100%; each of the 10 note
links opens the page holding its own note; the link into a cell lands on the
page with that row; the links to `#before`, `#tbl` and the whole file each land
on the page with the first row of the table they name; chapter 9's cells are
in Charis SIL, the second table bold; chapter 10's header cell is centred with
its italic run, the body runs are bold and italic, and the raised note 7 opens
note 7; a search for "marrowquill", after indexing, finds the chapter 10 cell.

    .venv/bin/python research/make_table_sideload.py [out_dir]

Output is gitignored (`*.kfx`, `*.epub`, `*.zip`, and the calibre config
directory). Do not commit it. All text in the book is invented.

Result (8db4e5d, run by the maintainer on 2026-10-01, PR #251):
    Paperwhite 5.19.2: every check passes, including chapter 12.
    Voyage 5.13.6: every check passes except chapter 12, where columns 2-24
        are drawn with almost no width.
    Oasis 5.18.2.1.1 (short check: TOC, links, chapter 2's spans, chapter 4):
        those pass; chapter 12 fails, with headers wrapping one character per
        line and later columns cut off at the page edge.
    Chapter 4 (8 columns) fits on all three; TOC and navigation pass on all
    three. Per the decision table, tables over 8 columns now keep rows
    (`_MAX_NATIVE_COLUMNS`); the table viewer is #254.

Width test for #254 (main 3feb5b4 with the limit lifted, 2026-10-01): tables
of 8-13 and 17 columns, plus pg24855 with its 9-17 column tables native. The
Voyage 5.13.6 displays the synthetic 13 and fails at 17, but pg24855's real
13-column table splits a header word ("L" / "oading.") and its 10-column
table reads correctly. The Oasis 5.18.2.1.1 shows the synthetic 13 and 17.
The limit is now 10.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402
from tests.fixtures.golden.inputs import _xhtml_page  # noqa: E402
from tests.integration.test_calibre_list_markers import (  # noqa: E402
    CALIBRE_CUSTOMIZE,
    EBOOK_CONVERT,
    _build_plugin,
)

AUTHOR = "kfxgen test"
PARAS_PER_CHAPTER = 12
NOTES = 10
LONG_TABLE_ROWS = 60
HUGE_TABLE_ROWS = 2000
VERY_WIDE_COLUMNS = 24

#: The cell the chapter 6 TOC entry points at, and the cell chapter 1 links to.
TOC_CELL_ID = "toc-cell"
LINK_CELL_ID = "link-cell"

#: Chapter 7's table-level link targets, and the file that opens with a table.
BEFORE_ID = "before"
TABLE_ID = "tbl"
TABLE_FIRST_FILE = "chapter_8.xhtml"

#: The note chapter 10's cell links to, and the word only that cell holds.
CELL_NOTE = 7
SEARCH_WORD = "marrowquill"

FONT_DIR = ROOT / "test_books" / "font-matching-test" / "OEBPS" / "fonts"
#: (file, font-weight, font-style) for each Charis SIL face.
FONTS = [
    ("CharisSILR.ttf", "normal", "normal"),
    ("CharisSILB.ttf", "bold", "normal"),
    ("CharisSILI.ttf", "normal", "italic"),
    ("CharisSILBI.ttf", "bold", "italic"),
]
CSS_HREF = "tables.css"

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
    "9. Embedded font tables",
    "10. Formatted cells",
    "11. Two thousand rows",
    "12. Twenty-four columns",
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


def _css():
    faces = "\n".join(
        f'@font-face {{ font-family: "Charis SIL"; font-weight: {weight}; '
        f"font-style: {style}; src: url(fonts/{name}); }}"
        for name, weight, style in FONTS
    )
    return (
        f"{faces}\n"
        'body.charis { font-family: "Charis SIL", serif; }\n'
        "table.bold td { font-weight: bold; }\n"
        "th.centre { text-align: center; }\n"
    )


def _styled_page(title, body_html, body_class=""):
    """`_xhtml_page` with the shared stylesheet linked."""
    cls = f' class="{body_class}"' if body_class else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        f'href="{CSS_HREF}"/></head>\n'
        f"<body{cls}>\n{body_html}\n</body>\n"
        "</html>\n"
    )


def _para(chapter, p):
    return f"<p>Chapter {chapter}, paragraph {p}. {_FILLER}</p>"


def _chapter(n, table_html, *, before="", after="", table_after=3, body_class=None):
    """Heading, filler paragraphs with `table_html` after paragraph
    `table_after`. `before` and `after` are extra markup around the table.
    `body_class`, when given, links the stylesheet and sets the body class."""
    parts = [f"<h1>{_TITLES[n - 1]}</h1>"]
    for p in range(1, PARAS_PER_CHAPTER + 1):
        parts.append(_para(n, p))
        if p == table_after:
            parts.extend([before, table_html, after])
    body = "\n".join(x for x in parts if x)
    if body_class is not None:
        return _styled_page(_TITLES[n - 1], body, body_class)
    return _xhtml_page(_TITLES[n - 1], body)


def _table(rows, head=None, attrs=""):
    out = [f"<table{attrs}>"]
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


def _long(n_rows=LONG_TABLE_ROWS):
    rows = [_tr([f"Row {r}", r * 7, r * 13 % 100]) for r in range(1, n_rows + 1)]
    return _table(rows, head=_tr(["Row", "Count", "Share"], tag="th"))


def _wide():
    rows = [_tr([f"{cell} {r}" for cell in _WIDE_CELLS]) for r in range(1, 9)]
    return _table(rows, head=_tr(_WIDE_CELLS, tag="th"))


def _very_wide():
    head = _tr([f"C{c}" for c in range(1, VERY_WIDE_COLUMNS + 1)], tag="th")
    rows = [
        _tr([f"{r}.{c}" for c in range(1, VERY_WIDE_COLUMNS + 1)]) for r in range(1, 7)
    ]
    return _table(rows, head=head)


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


def _font_tables():
    rows = [_tr([f"Lamp {r}", f"{r * 3} hours", "lit at dusk"]) for r in range(1, 5)]
    head = _tr(["Lamp", "Burned", "When"], tag="th")
    return "\n".join(
        [
            "<p>A plain table in Charis SIL:</p>",
            _table(rows, head=head),
            "<p>The same table with every body cell bold:</p>",
            _table(rows, head=head, attrs=' class="bold"'),
        ]
    )


def _formatted_cells():
    head = (
        "<tr><th>Item</th>"
        '<th class="centre">The <i>harbour</i> record</th>'
        "<th>Note</th></tr>"
    )
    rows = [
        "<tr><td><b>Lamp</b> oil</td><td>burned <i>slowly</i> all night</td>"
        f'<td>Checked<sup><a href="chapter_5.xhtml#n{CELL_NOTE}">{CELL_NOTE}</a>'
        "</sup></td></tr>",
        f"<tr><td>Ledger</td><td>the {SEARCH_WORD} entry</td><td>kept</td></tr>",
        "<tr><td>Rope</td><td><b>new</b> and <i>dry</i></td><td>stored</td></tr>",
    ]
    return _table(rows, head=head)


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
        _chapter(9, _font_tables(), table_after=2, body_class="charis"),
        _chapter(10, _formatted_cells(), table_after=2, body_class=""),
        _chapter(11, _long(HUGE_TABLE_ROWS), table_after=2),
        _chapter(12, _very_wide(), table_after=2),
    ]
    builder = _Builder().set_metadata(title=title, author=AUTHOR)
    for name, body in zip(_TITLES, bodies):
        builder.add_chapter(name, body.encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href=CSS_HREF, media_type="text/css", data=_css().encode()
    )
    for i, (name, _, _) in enumerate(FONTS):
        builder.add_manifest_item(
            item_id=f"font{i}",
            href=f"fonts/{name}",
            media_type="font/ttf",
            data=(FONT_DIR / name).read_bytes(),
        )
    slug = title.lower().replace(" ", "-")
    return builder.build(out_dir, f"{slug}-source")


def install_plugin(out_dir):
    """An isolated calibre config under `out_dir` with this checkout's plugin
    installed. Returns the environment to run `ebook-convert` in."""
    config = out_dir / "calibre-config"
    shutil.rmtree(config, ignore_errors=True)
    config.mkdir(parents=True)
    # The config holds calibre's own files (json, plugin copies); keep them
    # out of git whatever the repo's ignore rules say.
    (config / ".gitignore").write_text("*\n")
    env = dict(os.environ, CALIBRE_CONFIG_DIRECTORY=str(config))
    plugin = out_dir / "kfxgen-plugin.zip"
    _build_plugin(plugin)
    subprocess.run(
        [CALIBRE_CUSTOMIZE, "-a", str(plugin)], env=env, check=True, capture_output=True
    )
    return env


def convert_with_calibre(env, source, kfx, title, native):
    cmd = [
        EBOOK_CONVERT,
        str(source),
        str(kfx),
        "--title",
        title,
        "--authors",
        AUTHOR,
    ]
    if not native:
        cmd.append("--kfxgen-disable-native-tables")
    run = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if run.returncode != 0 or not kfx.exists():
        sys.exit(f"ebook-convert failed:\n{run.stdout[-3000:]}\n{run.stderr[-3000:]}")


def calibre_version(env):
    out = subprocess.run(
        [EBOOK_CONVERT, "--version"], env=env, capture_output=True, text=True
    ).stdout
    return out.splitlines()[0] if out else "unknown"


def facts(kfx):
    """(`$278` tables, `$279` rows, `$269` cells under a row, yj_table version,
    {entry kind: body links targeting it}, `$262` font faces)."""
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
    fonts = len(by_type(frags, "$262"))
    return tables, rows, cells, version, targets, fonts


def main():
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        print(
            "error: ebook-convert / calibre-customize not found (install calibre, "
            "or set KFXGEN_EBOOK_CONVERT to its ebook-convert). The gate pair is "
            "only built through the real calibre path; nothing was written.",
            file=sys.stderr,
        )
        return 1
    out_dir = Path(
        sys.argv[1] if len(sys.argv) > 1 else ROOT / "test_books/table-native"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    builds = [("Table Gate Native", True), ("Table Gate Rows", False)]
    env = install_plugin(out_dir)
    how = f"real ebook-convert ({calibre_version(env)}), isolated config"
    results = []
    for title, native in builds:
        source = build_source(out_dir, title)
        kfx = out_dir / (title.lower().replace(" ", "-") + ".kfx")
        kfx.unlink(missing_ok=True)
        convert_with_calibre(env, source, kfx, title, native)
        results.append((title, kfx, facts(kfx)))

    print(f"\nwrote to {out_dir}\nbuilt with: {how}\n")
    print(
        f"  {'title':<20} {'author':<12} {'$278':>5} {'$279':>6} {'$269':>6}  "
        "yj_table  $262  body links by target kind"
    )
    for title, kfx, (tables, rows, cells, version, targets, fonts) in results:
        print(
            f"  {title:<20} {AUTHOR:<12} {tables:>5} {rows:>6} {cells:>6}  "
            f"{version if version is not None else '-':<8}  {fonts:>4}  "
            + " ".join(f"{k}:{n}" for k, n in sorted(targets.items()))
        )
    for _, kfx, _ in results:
        print(f"  {kfx.name:<28} {kfx.stat().st_size / 1e3:>8.1f} kB")

    print(
        "\n"
        "on each device, for both books (see the docstring for the decision table)\n"
        "---------------------------------------------------------------------\n"
        "  1. Read the firmware off the device and write it down.\n"
        "  2. Open the TOC. Is the button present? Tap each of the 12 entries.\n"
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
        " 10. Ch 9: both tables are in Charis SIL (compare the 'g' and 'a' with\n"
        "     the Rows file); on the Native file every body cell of the second\n"
        "     table is bold. (On the Rows file it is regular: 5.8.8 drops a\n"
        "     cell's own CSS weight.)\n"
        " 11. Ch 10: the middle header cell is centred and 'harbour' in it is\n"
        "     italic; 'Lamp' and 'new' are bold, 'slowly' and 'dry' italic; the\n"
        f"     raised {CELL_NOTE} after 'Checked' is small and raised, and tapping\n"
        f"     it opens the page with note {CELL_NOTE} in ch 5.\n"
        " 12. Ch 11: time how long the TOC jump takes to open the chapter. Page\n"
        "     through the first 50 rows and back; jump to the end of the chapter\n"
        "     and page back 50 rows. Progress moves as you go.\n"
        " 13. Ch 12: on the Native file the 24-column table is now rows,\n"
        "     the same as the Rows file. Do all 24 values show?\n"
        f" 14. Once the book is indexed, search for '{SEARCH_WORD}'. It is only\n"
        "     in one ch 10 cell. Does the result open that page?\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
