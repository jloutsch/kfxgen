#!/usr/bin/env python3
"""Build a sideload A/B pair for which tables get the zoom button (#272).

Every table of 2 or more columns keeps the table viewer (the zoom button); a
one-column table goes without it and reads as plain paragraphs. (Kindle
Previewer 4.0.1 also leaves it off plain 2-3 column tables, but on the Voyage
those then lose their columns, which chapters 2 and 3 showed with an earlier
build of #272.) This builds one source book and converts it twice through
the real `ebook-convert`, each plugin in its own isolated calibre config under
the output directory:

    "Zoom New"   this checkout's plugin
    "Zoom Old"   an earlier plugin zip given with --old-plugin (for example
                 a build of main before #272), where every table has the
                 zoom button

The titles differ so the two files get different ASINs and neither replaces
the other on the device. All text in the book is invented. Six chapters:

    1  a one-column poem box, long enough to cross a page, with a link     New: no button
    2  a plain two-column table, short label beside running text           New: button
    3  a plain three-column table with long words                          New: button
    4  a two-column table with a border                                    New: button
    5  a plain four-column table                                           New: button
    6  a two-column table with a header row                                New: button

    .venv/bin/python research/make_zoom_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/zoom/`) is gitignored. Do not commit it.

On each device, both books:
  1. Ch 1: New shows no zoom button; Old shows one at the bottom left.
  2. Ch 1 on New: the stanzas read as paragraphs, the poem pages onto the
     next page normally, and the link "the harbour survey" opens chapter 2.
  3. Ch 2 to 6: the zoom button on both books, the columns side by side,
     and tapping the button opens the table.
  4. The TOC button is present and each of the 6 entries opens its chapter.
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

CSS = "table.ruled td { border: 1px solid black; }\n"
_POEM = [
    "The lamp was lit at the harbour mouth,",
    "and the keeper climbed the stair,",
    "and counted the boats that came in from the south",
    "with the salt still in their hair.",
]
_TEXT = (
    "The settlement of the embankment was measured weekly at each of the "
    "marked stations, and the readings were entered in the record book."
)
_LONG_WORDS = ["Superintendence", "Transportation", "Reconstruction", "Administration"]


def _poem_box():
    stanzas = []
    for n in range(1, 13):
        lines = "<br/>".join(_POEM)
        stanzas.append(f"<p>{n}. {lines}</p>")
    stanzas.append('<p>See <a href="chapter_2.xhtml">the harbour survey</a>.</p>')
    return f"<table><tr><td>{''.join(stanzas)}</td></tr></table>"


def _rows(cells_per_row, n=4):
    return "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in cells_per_row(r)) + "</tr>"
        for r in range(1, n + 1)
    )


CHAPTERS = [
    ("1. A poem box", _poem_box()),
    (
        "2. A plain two-column table",
        f"<table>{_rows(lambda r: [f'Station {r}', _TEXT])}</table>",
    ),
    (
        "3. A plain three-column table",
        f"<table>{_rows(lambda r: [_LONG_WORDS[r - 1], _LONG_WORDS[-r], str(r * 17)])}</table>",
    ),
    (
        "4. A bordered table",
        f'<table class="ruled">{_rows(lambda r: [f"Station {r}", _TEXT])}</table>',
    ),
    (
        "5. A plain four-column table",
        f"<table>{_rows(lambda r: [f'S{r}', str(r * 3), str(r * 5), str(r * 7)])}</table>",
    ),
    (
        "6. A table with a header row",
        "<table><tr><th>Station</th><th>Reading</th></tr>"
        f"{_rows(lambda r: [f'Station {r}', _TEXT])}</table>",
    ),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="zoom.css"/></head>\n'
        f"<body>\n<h1>{title}</h1>\n<p>Survey readings.</p>\n{body}\n"
        "<p>End of the readings.</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Zoom", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="zoom.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "zoom-source")


def zoom_per_chapter(kfx):
    """Each storyline's tables as 'zoom' or 'plain', so a dropped stylesheet
    or a wrong rule shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        kinds = collections.Counter(
            "zoom" if "$629" in e else "plain"
            for e in iter_entries(val(f)["$146"])
            if str(e.get("$159")) == "$278"
        )
        if kinds:
            out.append(dict(kinds))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/zoom"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "zoom-new.kfx"
    convert_with_calibre(env, source, new, "Zoom New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: tables per chapter {zoom_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "zoom-old.kfx"
        convert_with_calibre(old_env, source, old, "Zoom Old", native=True)
        print(f"  {old.name}: tables per chapter {zoom_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
