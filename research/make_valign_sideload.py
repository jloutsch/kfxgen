#!/usr/bin/env python3
"""Build a sideload A/B pair for a table cell's vertical alignment (#269).

Each chapter is one table whose rows pair a short label with a cell that wraps
onto several lines, so the label's vertical position shows. This builds one
source book and converts it twice through the real `ebook-convert`, each
plugin in its own isolated calibre config under the output directory:

    "Valign New"   this checkout's plugin
    "Valign Old"   an earlier plugin zip given with --old-plugin (for example
                   the v5.8.12 release zip), where every cell is centred

The titles differ so the two files get different ASINs and neither replaces
the other on the device. All text in the book is invented. Five chapters:

    1  top, from the stylesheet (`td.top`)              New $633 $58
    2  top, from `valign="top"` on each row             New $633 $58
    3  bottom, from the stylesheet on the row group     New $633 $60
    4  nothing set (control)                            $633 $320 on both
    5  baseline                                         New: no $633, as
                                                        Kindle Previewer writes it

    .venv/bin/python research/make_valign_sideload.py \\
        --old-plugin kfxgen-plugin-5.8.12.zip [out_dir]

Output (default `test_books/valign/`) is gitignored. Do not commit it.

On each device, both books:
  1. Ch 1 and 2: on New, each label is level with the first line of the cell
     beside it. On Old it is level with the middle of that cell.
  2. Ch 3: on New, each label is level with the last line of the cell beside
     it. On Old, the middle.
  3. Ch 4: the label is level with the middle of the cell on both books.
  4. Ch 5: note where the label sits on New (top, middle or bottom). Previewer
     writes no alignment for baseline, so this records the device's default.
  5. The TOC button is present and each of the 5 entries opens its chapter.
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

CSS = """
td.top { vertical-align: top; }
tbody.bottom { vertical-align: bottom; }
td.baseline { vertical-align: baseline; }
"""
_LONG = (
    "The keeper's log for that season records the reading twice, once at "
    "dawn and once at dusk, and the two rarely agree; the surveyor took the "
    "lower of the two, noting that the dusk reading was made by lamplight."
)
_LABELS = ["North wall", "Lamp house", "Beacon", "Slipway"]


def _table(td="", tr="", tbody=""):
    rows = "".join(
        f"<tr{tr}><td{td}>{label}</td><td>{_LONG}</td></tr>" for label in _LABELS
    )
    return f"<table><tbody{tbody}>{rows}</tbody></table>"


CHAPTERS = [
    ("1. Top, from the stylesheet", _table(td=' class="top"')),
    ("2. Top, from valign", _table(tr=' valign="top"')),
    ("3. Bottom, from the row group", _table(tbody=' class="bottom"')),
    ("4. Nothing set", _table()),
    ("5. Baseline", _table(td=' class="baseline"')),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="valign.css"/></head>\n'
        f"<body>\n<h1>{title}</h1>\n<p>Survey readings.</p>\n{body}\n"
        "<p>End of the readings.</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Valign", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="valign.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "valign-source")


def label_alignments(kfx):
    """Each storyline's label cells as a count of their $633 values, so a
    missing stylesheet or a dropped attribute shows before anything is copied
    to a device."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        seen = collections.Counter()
        for e in iter_entries(val(f)["$146"]):
            ref = e.get("$145")
            if ref is None:
                continue
            text = str(content[str(ref["name"])][int(ref["$403"])])
            if text in _LABELS:
                v = styles[str(e["$157"])].get("$633")
                seen[str(v) if v is not None else "none"] += 1
        if seen:
            out.append(dict(seen))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/valign"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "valign-new.kfx"
    convert_with_calibre(env, source, new, "Valign New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: label $633 per chapter {label_alignments(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "valign-old.kfx"
        convert_with_calibre(old_env, source, old, "Valign Old", native=True)
        print(f"  {old.name}: label $633 per chapter {label_alignments(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
