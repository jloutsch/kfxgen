#!/usr/bin/env python3
"""Build a sideload A/B pair for notes laid out as a table (#268).

A notes section laid out as a two-column table (marker | note) is written as
one paragraph per note when the book links into it, and as a Kindle table
otherwise. This builds one source book and converts it twice through the real
`ebook-convert`, each plugin in its own isolated calibre config under the
output directory:

    "Notes Table New"   this checkout's plugin
    "Notes Table Old"   an earlier plugin zip given with --old-plugin (for
                        example the v5.8.11 release zip), where every such
                        table is a Kindle table

The titles differ so the two files get different ASINs and neither replaces
the other on the device. All text in the book is invented. Six chapters:

    1  text with 11 note references: 8 to chapter 2, 3 to chapter 3
    2  "Notes": 8 notes in calibre's MOBI-to-EPUB layout, a numbered marker
       linking back to the text, the note's anchor after its row, each note
       3-4 lines long. New: paragraphs. Old: a table, the number beside the
       middle of a long note
    3  "Notes without back-links": 3 notes marked *, dagger, double dagger,
       no link in the marker, anchors before rows. New: paragraphs
    4  a contents table: roman numerals linking out to chapters 1-3, nothing
       linking in (pg6133's shape). Both: a table
    5  a numbered data table, nothing linking in. Both: a table
    6  a numbered list set as a table, prose in the second column, nothing
       linking in. Both: a table

    .venv/bin/python research/make_notes_table_sideload.py \\
        --old-plugin kfxgen-plugin-5.8.11.zip [out_dir]

Output (default `test_books/notes-table/`) is gitignored. Do not commit it.

On each device, both books:
  1. Ch 2: each note starts on its own line with its number at the start of
     its first line (New). On Old the number sits beside the middle of the
     note.
  2. Ch 1: tap superscripts 1, 5 and 8: each opens the page with its own note
     in ch 2. Tap the *, the dagger and the double dagger: each opens its own
     note in ch 3.
  3. Ch 2: tap the "4." in front of note 4: it returns to the reference in
     ch 1.
  4. Ch 4, 5, 6 are tables on both books, with columns.
  5. The TOC button is present and each of the 6 entries opens its chapter.
"""

import argparse
import os
import shutil
import subprocess
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
from tests.fixtures.golden.inputs import _xhtml_page  # noqa: E402

NOTES = 8
SYMBOLS = ["*", "†", "‡"]
_TITLES = [
    "1. The Harbour Survey",
    "2. Notes",
    "3. Notes without back-links",
    "4. A contents table",
    "5. A numbered data table",
    "6. A numbered list set as a table",
]
_NOTE_TEXT = (
    "The keeper's log for that season records the reading twice, once at "
    "dawn and once at dusk, and the two rarely agree; the surveyor took the "
    "lower of the two, noting that the dusk reading was made by lamplight "
    "and that the gauge had been reset in the spring after the storm."
)


def _chapter_one():
    paras = []
    for n in range(1, NOTES + 1):
        paras.append(
            f"<p>Finding {n} of the survey concerns the north wall."
            f'<a id="r{n}" href="chapter_2.xhtml#n{n}"><sup>{n}</sup></a> '
            "The wall was measured at low tide from the lamp house steps.</p>"
        )
    for k, sym in enumerate(SYMBOLS, 1):
        paras.append(
            f"<p>A further remark on the channel markers."
            f'<a href="chapter_3.xhtml#s{k}"><sup>{sym}</sup></a></p>'
        )
    return "<h1>1. The Harbour Survey</h1>" + "".join(paras)


def _notes():
    """calibre's MOBI-to-EPUB notes layout: a numbered back-link, the note,
    and the note's anchor after its own row (#223)."""
    rows = "".join(
        f'<tr><td><a href="chapter_1.xhtml#r{n}">{n}.</a></td>'
        f"<td>Note {n}. {_NOTE_TEXT}</td></tr>"
        f'<a id="n{n}"></a>'
        for n in range(1, NOTES + 1)
    )
    return f"<h1>2. Notes</h1><table>{rows}</table>"


def _notes_without_backlinks():
    rows = "".join(
        f'<a id="s{k}"></a><tr><td>{sym}</td>'
        f"<td>Remark {k}. The channel markers were repainted that autumn and "
        "the survey copied their new colours into the margin.</td></tr>"
        for k, sym in enumerate(SYMBOLS, 1)
    )
    return f"<h1>3. Notes without back-links</h1><table>{rows}</table>"


def _contents_table():
    rows = "".join(
        f'<tr><td><a href="chapter_{n}.xhtml">{r}.</a></td><td>{t[3:]}</td></tr>'
        for n, (r, t) in enumerate(zip(["I", "II", "III"], _TITLES[:3]), 1)
    )
    return f"<h1>4. A contents table</h1><table>{rows}</table>"


def _data_table():
    rows = "".join(
        f"<tr><td>{n}</td><td>{n * 17} fathoms</td></tr>" for n in range(1, 6)
    )
    return f"<h1>5. A numbered data table</h1><table>{rows}</table>"


def _numbered_list_table():
    rows = "".join(
        f"<tr><td>{n}.</td><td>Walk the wall from the lamp house to the north "
        f"beacon and record step {n} of the inspection in the log.</td></tr>"
        for n in range(1, 5)
    )
    return f"<h1>6. A numbered list set as a table</h1><table>{rows}</table>"


def build_source(out_dir):
    bodies = [
        _chapter_one(),
        _notes(),
        _notes_without_backlinks(),
        _contents_table(),
        _data_table(),
        _numbered_list_table(),
    ]
    builder = EpubBuilder().set_metadata(title="Notes Table", author=AUTHOR)
    for title, body in zip(_TITLES, bodies):
        builder.add_chapter(title, _xhtml_page(title, body).encode("utf-8"))
    return builder.build(out_dir, "notes-table-source")


def install_zip(out_dir, plugin_zip):
    """An isolated calibre config with `plugin_zip` installed."""
    config = out_dir / "calibre-config-old"
    shutil.rmtree(config, ignore_errors=True)
    config.mkdir(parents=True)
    (config / ".gitignore").write_text("*\n")
    env = dict(os.environ, CALIBRE_CONFIG_DIRECTORY=str(config))
    subprocess.run(
        [CALIBRE_CUSTOMIZE, "-a", str(plugin_zip)],
        env=env,
        check=True,
        capture_output=True,
    )
    return env


def tables_per_chapter(kfx):
    """`$278` tables in each storyline, in reading order."""
    frags = load_fragments(kfx)
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        out.append(
            sum(1 for e in iter_entries(val(f)["$146"]) if str(e.get("$159")) == "$278")
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/notes-table"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "notes-table-new.kfx"
    convert_with_calibre(env, source, new, "Notes Table New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: tables per storyline {tables_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "notes-table-old.kfx"
        convert_with_calibre(old_env, source, old, "Notes Table Old", native=True)
        print(f"  {old.name}: tables per storyline {tables_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
