#!/usr/bin/env python3
"""Build a sideload A/B pair for container margins (#238, stage 1).

On `main`, a paragraph inside an indented <div>, <blockquote> or sidebar
comes out at the default left margin: the container's margins are lost.
With #238 stage 1 they reach each paragraph, as Kindle Previewer writes
them. Borders and backgrounds are stage 2, not this pair.

    "Margins New"   this checkout's plugin
    "Margins Old"   an earlier plugin zip given with --old-plugin (main)

    .venv/bin/python research/make_margins_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/margins/`) is gitignored. Do not commit it.

On each device, both books. Open each entry from the TOC button; each
chapter has a "Before" and an "After" paragraph at the normal margin:
  1. "1. An inset": New: the two middle paragraphs are indented about the
     width of two letters "M" on both sides. Old: level with Before/After.
  2. "2. A sidebar": New: "Tip" and its two lines indented on both sides.
     Old: level with Before/After.
  3. "3. Nested indents": New: "Outer" indented, "Inner" indented further,
     then "Outer again" back at the first indent. Old: all level.
  4. "4. A quotation": New: the quotation indented on both sides, a little
     more than the inset. Old: level with Before/After.
  5. "5. A pixel inset": New: indented on both sides, a little less than
     the inset. Old: level with Before/After.
  6. Make the font larger: New's indents keep their width, since they are
     a share of the page, as Kindle Previewer writes them.
  7. The TOC button works on every page.
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
.inset { margin-left: 2em; margin-right: 2em; }
.sidebar { border: 1px solid black; padding: 1em; margin: 1em 2em; }
.outer { margin-left: 2em; }
.inner { margin-left: 2em; }
blockquote.q { margin: 1em 3em; }
.px { margin-left: 40px; margin-right: 40px; }
"""
_LINE = "The keeper climbed the stair at dusk and wiped the salt from the glass. "

CHAPTERS = [
    (
        "1. An inset",
        f'<div class="inset"><p>Inset one. {_LINE}</p><p>Inset two. {_LINE}</p></div>',
    ),
    (
        "2. A sidebar",
        f'<aside class="sidebar"><h4>Tip</h4><p>Sidebar one. {_LINE}</p><p>Sidebar two.</p></aside>',
    ),
    (
        "3. Nested indents",
        f'<div class="outer"><p>Outer. {_LINE}</p><div class="inner"><p>Inner. {_LINE}</p></div>'
        f"<p>Outer again. {_LINE}</p></div>",
    ),
    (
        "4. A quotation",
        f'<blockquote class="q"><p>Quoted. {_LINE * 2}</p></blockquote>',
    ),
    ("5. A pixel inset", f'<div class="px"><p>Pixels. {_LINE}</p></div>'),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f'<head><title>{title}</title><link rel="stylesheet" type="text/css" '
        'href="margins.css"/></head>\n'
        f"<body>\n<h1>{title}</h1>\n<p>Before. {_LINE}</p>\n{body}\n<p>After. {_LINE}</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Margins", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="margins.css", media_type="text/css", data=CSS.encode()
    )
    return builder.build(out_dir, "margins-source")


def left_margins(kfx):
    """(text start, left margin) for each paragraph that isn't Before/After."""
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
            if t.startswith(("Before", "After")) or "." not in t:
                continue
            m = styles.get(str(e.get("$157")), {}).get("$48")
            out.append((t[:12], f"{m['$307']}{m['$306']}" if m else "-"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/margins"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)
    env = install_plugin(out)
    new = out / "margins-new.kfx"
    convert_with_calibre(env, source, new, "Margins New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: {left_margins(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "margins-old.kfx"
        convert_with_calibre(old_env, source, old, "Margins Old", native=True)
        print(f"  {old.name}: {left_margins(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
