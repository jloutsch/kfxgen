#!/usr/bin/env python3
"""Build a sideload A/B pair for scene breaks (#230).

One invented source book, converted twice through the real `ebook-convert`,
each plugin in its own isolated calibre config under the output directory:

    "Breaks New"   this checkout's plugin
    "Breaks Old"   an earlier plugin zip given with --old-plugin (for example
                   a build of main), which drops <hr> and spacer paragraphs

pg345 (Dracula, public domain) is converted the same way as "Dracula
Breaks New" and "Dracula Breaks Old", for the breaks between its diary
entries. The titles differ so the files get different ASINs and none
replaces another on the device.

    .venv/bin/python research/make_breaks_sideload.py \\
        --old-plugin main.zip [out_dir]

Output (default `test_books/breaks/`) is gitignored. Do not commit it.

On each device, "Breaks New" first, then "Breaks Old" for comparison. Open
each entry from the TOC:
  1. "1. A rule": New: a thin horizontal line between "Above the rule."
     and "Below the rule.", with a little space above and below it. Old: no
     line; the two paragraphs follow each other.
  2. "2. Rules at the edges": New: a line under the chapter heading, as a
     browser draws it, and none after "The last paragraph." at the end of
     the chapter. Old: no line anywhere.
  3. "3. Blank lines": New: about one empty line above "After one blank
     line.", about two above "After two blank lines.". Old: no gap.
  4. "4. Space with no line": New: a gap of about two lines, and no line,
     above "After the space.". Old: no gap.
  5. "5. Many rules": a long chapter with a line after every sixth
     paragraph. Tap the TOC button on a page with a line: the TOC opens.
     Page through: the progress % and "Go to" location rise normally.
  6. "6. A link to a rule": tap "the third rule". New and Old: you land on
     "You landed after the third rule." in chapter 5.
In "Dracula Breaks New": in Mina's and Dr. Seward's diaries (from chapter
VI on), about one empty line between dated entries ("1 August.", "18
June."). Old: none.
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

_P = '<p style="text-indent: 1.5em; margin: 0">{}</p>'
_SPACER = "<p>&#160;</p>"
_TEXT = (
    "The tide went out over the flats and the gulls came down to the mud, "
    "where the boats lay over on their sides until the water came back. "
)
_DRACULA = ROOT / "research/gutenberg-top-90/pg345.epub"


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n<h1>{title}</h1>\n{body}\n</body>\n</html>\n"
    )


def _paras(*texts):
    return "".join(_P.format(t) for t in texts)


def _ordinary(n):
    return _paras(*(f"Paragraph {i}. {_TEXT}" for i in range(1, n + 1)))


def _many_rules():
    out = []
    for i in range(1, 37):
        text = f"Paragraph {i}. {_TEXT * 3}"
        if i == 19:
            text = "You landed after the third rule. " + text
        out.append(_P.format(text))
        if i % 6 == 0 and i < 36:
            out.append('<hr id="third"/>' if i == 18 else "<hr/>")
    return "".join(out)


PAGES = [
    (
        "1. A rule",
        _ordinary(3)
        + _paras("Above the rule.")
        + "<hr/>"
        + _paras("Below the rule.")
        + _ordinary(3),
    ),
    (
        "2. Rules at the edges",
        "<hr/>" + _ordinary(4) + _paras("The last paragraph.") + "<hr/>",
    ),
    (
        "3. Blank lines",
        _ordinary(3)
        + _SPACER
        + _paras("After one blank line.")
        + _ordinary(3)
        + _SPACER * 2
        + _paras("After two blank lines.")
        + _ordinary(2),
    ),
    (
        "4. Space with no line",
        _ordinary(3)
        + '<hr style="border: none; margin: 2.4em 0"/>'
        + _paras("After the space.")
        + _ordinary(2),
    ),
    ("5. Many rules", _many_rules()),
    (
        "6. A link to a rule",
        _paras('Tap <a href="chapter_5.xhtml#third">the third rule</a>.')
        + _ordinary(2),
    ),
]


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Breaks", author=AUTHOR)
    for title, body in PAGES:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    return builder.build(out_dir, "breaks-source")


def facts(kfx):
    """Rules, entries with written space above, and links that land, so a
    wrong build shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    anchors = {str(f.fid) for f in by_type(frags, "$266")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    entries = [
        e for st in by_type(frags, "$259") for e in iter_entries(val(st)["$146"])
    ]
    links = [
        str(sp["$179"]) for e in entries for sp in e.get("$142") or [] if "$179" in sp
    ]
    spaced = 0
    for e in entries:
        if "$145" not in e:
            continue
        above = {str(k): v for k, v in styles[str(e["$157"])].items()}.get("$47")
        if above is not None and float(str(above["$307"])) > 1:
            spaced += 1
    return {
        "rules": sum(1 for e in entries if str(e.get("$159")) == "$596"),
        "paragraphs with more than a line above": spaced,
        "links": f"{sum(n in anchors for n in links)} of {len(links)} land",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/breaks"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old files")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)
    books = [(source, "breaks", "Breaks")]
    if _DRACULA.exists():
        books.append((_DRACULA, "dracula-breaks", "Dracula Breaks"))

    envs = [("new", "New", install_plugin(out))]
    if args.old_plugin:
        envs.append(("old", "Old", install_zip(out, args.old_plugin)))
    print(f"built with: {calibre_version(envs[0][2])}, isolated configs")
    for src, stem, title in books:
        for tag, label, env in envs:
            kfx = out / f"{stem}-{tag}.kfx"
            convert_with_calibre(env, src, kfx, f"{title} {label}", native=True)
            print(f"  {kfx.name}: {facts(kfx)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
