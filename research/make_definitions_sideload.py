#!/usr/bin/env python3
"""Build a sideload book for definition lists (#229).

On `main`, a <dl> came out as one paragraph, its terms and definitions run
together ("TermOneDefOne text.TermTwo"). With #229 each term and each
definition is its own paragraph, and a definition is indented: calibre's
default sheet gives `dd` 40px, written as 40px (`$319`) on a definition
holding text, and as 4.6875 % on paragraphs inside a definition (#308's
carried rule). Previewer's rate makes the two the same width; check 2 is
whether a Kindle agrees.

    "Definitions New"   this checkout's plugin

    .venv/bin/python research/make_definitions_sideload.py [out_dir]

Output (default `test_books/definitions/`) is gitignored. Do not commit it.

On each device, open each entry from the TOC button. Each chapter has a
"Before" and an "After" paragraph at the normal margin:
  1. "D1. A glossary": four lines. "TermOne" and "TermTwo" flush left with
     Before/After; "DefOne text." and "DefTwo text." indented below them.
  2. "D1" against "D2. A definition of paragraphs": "DefOne text." (D1)
     and "DefP first." (D2) start at the same indent. This is the px
     margin; if D1's is wider, narrower or missing, note which.
  3. "D3. A nested list": "Inner" at the same indent as "OuterDef";
     "InnerDef" indented one step further.
  4. "D4. Lists in table cells": in both tables, the first cell shows
     "CellTerm" and "CellDef" on separate lines.
  5. "D5. Notes as a list": tap "1" and then "2" in the first paragraph.
     Each opens its own note: the "1" or "2" line, with its note below it.
     In the notes, tap "1" and "2": each returns to the first paragraph.
  6. "D6. A styled list": "StyledTerm" indented; "StyledDef" indented
     further than "StyledTerm".
  7. The TOC button works on every page.
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

CSS = """
dl.styled { margin-left: 3em; }
dl.styled dd { margin-left: 2em; }
"""
_LINE = "The keeper climbed the stair at dusk and wiped the salt from the glass. "
_FILLER = "".join(f"<p>Filler {n}. {_LINE * 3}</p>" for n in range(1, 13))

CHAPTERS = [
    (
        "D1. A glossary",
        "<dl><dt>TermOne</dt><dd>DefOne text.</dd>"
        "<dt>TermTwo</dt><dd>DefTwo text.</dd></dl>",
    ),
    (
        "D2. A definition of paragraphs",
        "<dl><dt>TermP</dt><dd><p>DefP first.</p><p>DefP second.</p></dd></dl>",
    ),
    (
        "D3. A nested list",
        "<dl><dt>Outer</dt><dd><p>OuterDef</p>"
        "<dl><dt>Inner</dt><dd>InnerDef</dd></dl></dd></dl>",
    ),
    (
        "D4. Lists in table cells",
        "<table><tr><td><dl><dt>CellTerm</dt><dd>CellDef</dd></dl></td>"
        "<td>Second column</td></tr></table>"
        "<p>One column:</p>"
        "<table><tr><td><dl><dt>CellTerm</dt><dd>CellDef</dd></dl></td></tr></table>",
    ),
    (
        "D5. Notes as a list",
        '<p id="r1">The keeper wrote it down<a href="#n1">1</a> and checked it '
        'twice<a id="r2" href="#n2">2</a>.</p>'
        f"{_FILLER}"
        '<dl><dt id="n1"><a href="#r1">1</a></dt><dd>Note one. The log was kept '
        "in pencil.</dd>"
        '<dt id="n2"><a href="#r2">2</a></dt><dd>Note two. The second check '
        "was at dawn.</dd></dl>",
    ),
    (
        "D6. A styled list",
        '<dl class="styled"><dt>StyledTerm</dt><dd>StyledDef</dd></dl>',
    ),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="definitions.css"/></head>\n'
        f"<body>\n<h1>{title}</h1>\n<p>Before. {_LINE}</p>\n{body}\n"
        f"<p>After. {_LINE}</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Definitions", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="definitions.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "definitions-source")


def left_margins(kfx):
    """(text start, left margin) for each list paragraph, cells included."""
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if "$145" not in e:
                continue
            t = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            if not t.startswith(
                ("Term", "Def", "Outer", "Inner", "Cell", "Styled", "Note")
            ):
                continue
            m = styles.get(str(e.get("$157")), {}).get("$48")
            out.append((t[:12], f"{m['$307']}{m['$306']}" if m else "-"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/definitions"))
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)
    env = install_plugin(out)
    new = out / "definitions-new.kfx"
    convert_with_calibre(env, source, new, "Definitions New", native=True)
    print(f"built with: {calibre_version(env)}, isolated config")
    print(f"  {new.name}: {left_margins(new)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
