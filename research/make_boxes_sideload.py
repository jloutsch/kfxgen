#!/usr/bin/env python3
"""Build a sideload A/B pair for boxed sidebars and callouts (#238, stage 2).

On `main`, a bordered or filled container comes out as plain paragraphs
(with its margins, since stage 1). With stage 2 it is a box, as Kindle
Previewer writes it: a `$270` around the paragraphs with the border, the
fill, padding and side margins.

    "Boxes New"   this checkout's plugin
    "Boxes Old"   an earlier plugin zip given with --old-plugin (main)

    .venv/bin/python research/make_boxes_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/boxes/`) is gitignored. Do not commit it.

On each device, both books. Open each entry from the TOC button; each
chapter has a "Before" and an "After" paragraph outside the box:
  1. "1. A sidebar": New: "Tip" and two lines inside a box with a thin
     border and a light grey fill, indented from both sides. Old: the same
     lines indented, no border or fill.
  2. "2. A bordered note": New: a thin border round two lines. Old: none.
  3. "3. A shaded box": New: a grey fill behind two lines, no border.
     Old: no fill.
  4. "4. A ruled quotation": New: a vertical line at the left of the two
     lines. Old: no line.
  5. "5. A long box": 30 numbered paragraphs under a line drawn across the
     top. New: the line above paragraph 1; the paragraphs run over several
     pages normally, every page turn works, and no text is cut off or
     overlaps at a page break. Old: no line.
  6. Spacing, on New, in "2. A bordered note" (paragraphs with space
     between them) and in "8. An indented book" (paragraphs indented, no
     space between them):
       a. the gap between "Before" and the box's top border, against the
          gap between its bottom border and "After": about the same?
       b. the space between the top border and the first line inside,
          against the space between the last line and the bottom border:
          about the same?
       c. the text inside the box: the same size as the text outside?
  7. "6. A box opens the chapter": New: the heading "6. A box opens the
     chapter" once, then a bordered box starting with "The keeper's log".
     The title is not repeated inside the box.
  8. "7. A link into a box": tap "the second line of the bordered note".
     New: it opens chapter 2 at the box. Old: at the same line, no box.
  9. "8. An indented book": New: the bordered note between two indented
     paragraphs, its own paragraphs indented too. Old: no border.
  10. The TOC button works on every page.
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

CSS = """
.sidebar { border: 1px solid black; background-color: #eeeeee; padding: 1em; margin: 1em 2em; }
.note { border: 1px solid black; padding: 0.5em; }
.shade { background-color: #dddddd; }
.rule { border-left: 3px solid black; padding-left: 1em; }
.topline { border-top: 1px solid black; }
.ind p { text-indent: 1.5em; }
"""
_LINE = "The keeper climbed the stair at dusk and wiped the salt from the glass. "

CHAPTERS = [
    (
        "1. A sidebar",
        f'<aside class="sidebar"><h4>Tip</h4><p>Sidebar one. {_LINE}</p><p>Sidebar two.</p></aside>',
    ),
    (
        "2. A bordered note",
        f'<div class="note"><p>Note one. {_LINE}</p><p id="second">The second line of the note.</p></div>',
    ),
    (
        "3. A shaded box",
        f'<div class="shade"><p>Shade one. {_LINE}</p><p>Shade two.</p></div>',
    ),
    (
        "4. A ruled quotation",
        f'<blockquote class="rule"><p>Quoted one. {_LINE}</p><p>Quoted two.</p></blockquote>',
    ),
    (
        "5. A long box",
        '<div class="topline">'
        + "".join(f"<p>{n}. {_LINE * 3}</p>" for n in range(1, 31))
        + "</div>",
    ),
]


def _page(title, body, framed=True):
    """A chapter page; `framed` puts a heading and a Before and an After
    paragraph round `body`."""
    if framed:
        body = (
            f"<h1>{title}</h1>\n<p>Before. {_LINE}</p>\n{body}\n<p>After. {_LINE}</p>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="boxes.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


_OPENS = "6. A box opens the chapter"
_LAST = [
    # The page opens with the box, its first line the chapter title.
    (
        _OPENS,
        _page(
            _OPENS,
            f'<div class="note"><p>{_OPENS}</p><p>The keeper\'s log. {_LINE}</p></div>'
            f"<p>After. {_LINE}</p>",
            framed=False,
        ),
    ),
    (
        "7. A link into a box",
        _page(
            "7. A link into a box",
            '<p>Tap <a href="chapter_2.xhtml#second">the second line of the bordered '
            "note</a>.</p>",
        ),
    ),
    # Paragraphs indented, as many books set them: no space between them.
    (
        "8. An indented book",
        _page(
            "8. An indented book",
            f'<h1>8. An indented book</h1><div class="ind"><p>Before. {_LINE}</p>'
            f'<div class="note"><p>Boxed one. {_LINE}</p><p>Boxed two. {_LINE}</p></div>'
            f"<p>After. {_LINE}</p></div>",
            framed=False,
        ),
    ),
]


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Boxes", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    for title, page in _LAST:
        builder.add_chapter(title, page.encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="boxes.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "boxes-source")


def boxes_per_chapter(kfx):
    """`$270` boxes in each storyline, in reading order."""
    frags = load_fragments(kfx)
    return [
        sum(1 for e in iter_entries(val(f)["$146"]) if str(e.get("$159")) == "$270")
        for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0))
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/boxes"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)
    env = install_plugin(out)
    new = out / "boxes-new.kfx"
    convert_with_calibre(env, source, new, "Boxes New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: boxes per chapter {boxes_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "boxes-old.kfx"
        convert_with_calibre(old_env, source, old, "Boxes Old", native=True)
        print(f"  {old.name}: boxes per chapter {boxes_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
