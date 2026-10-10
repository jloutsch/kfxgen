#!/usr/bin/env python3
"""Build a sideload A/B pair for nested boxes and text-only boxes (#310).

Since #238 a bordered or filled container is a box (a `$270`). With #310 a
box also nests what it holds, as Kindle Previewer does: a table, a picture
or another box inside it. And text written straight in a bordered <div>
is a box too. On `main` those were written flat.

    "Nested Boxes New"   this checkout's plugin
    "Nested Boxes Old"   an earlier plugin zip given with --old-plugin (main)

    .venv/bin/python research/make_nested_boxes_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/nested-boxes/`) is gitignored. Do not commit it.

On each device, both books. Open each entry from the TOC button:
  1. "1. Text straight in a box": New: one line inside a thin border.
     Old: the line, no border.
  2. "2. A box with a picture": New: a border round a line, a striped
     picture and a second line. Old: no border.
  3. "3. A box with a table": New: a border round a line, a 3-column table
     (Day, Wind, Sea) and a closing line. Old: the table, no border.
  4. "4. A box in a box": New: a bordered box holding "Outer", then a grey
     shaded box holding "Inner", then "Outer again". Old: the grey box, but
     no border round the outside.
  5. "5. A deep box": New: three boxes inside each other (border, grey,
     border) round a small table. On this page, open the TOC button: it
     must open and list every chapter. Old: no boxes.
  6. "6. A link into a boxed table": tap "the reading for day 3". New: it
     opens chapter 3 at the row for day 3 inside the box.
  7. "7. A nested title": New: the heading "7. A nested title" once, then a
     bordered box starting "The keeper's log". The title is not repeated
     inside the box. Old: the same, without the box.
  8. The TOC button works on every page.
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
.box { border: 1px solid black; padding: 0.5em; }
.shade { background-color: #dddddd; padding: 0.5em; }
"""
_LINE = "The keeper climbed the stair at dusk and wiped the salt from the glass. "
_WIND = ["calm", "light", "fresh", "strong"]
_SEA = ["smooth", "slight", "moderate"]


def _readings(days):
    rows = "".join(
        f'<tr id="day{d}"><td>{d}</td><td>{_WIND[d % 4]}</td><td>{_SEA[d % 3]}</td></tr>'
        for d in range(1, days + 1)
    )
    return f"<table><tr><th>Day</th><th>Wind</th><th>Sea</th></tr>{rows}</table>"


CHAPTERS = [
    (
        "1. Text straight in a box",
        '<div class="box">Written straight in the box.</div>',
    ),
    (
        "2. A box with a picture",
        f'<div class="box"><p>Before the picture. {_LINE}</p>'
        '<p><img src="picture.png" alt=""/></p><p>After the picture.</p></div>',
    ),
    (
        "3. A box with a table",
        f'<div class="box"><p>The week\'s readings.</p>{_readings(5)}<p>End of the readings.</p></div>',
    ),
    (
        "4. A box in a box",
        f'<div class="box"><p>Outer. {_LINE}</p><div class="shade"><p>Inner. {_LINE}</p></div>'
        "<p>Outer again.</p></div>",
    ),
    (
        "5. A deep box",
        '<div class="box"><p>Level one.</p><div class="shade"><p>Level two.</p>'
        f'<div class="box"><p>Level three.</p>{_readings(2).replace("day", "deep")}</div></div></div>',
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
        'href="nested-boxes.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


_LAST = [
    (
        "6. A link into a boxed table",
        _page(
            "6. A link into a boxed table",
            '<p>Tap <a href="chapter_3.xhtml#day3">the reading for day 3</a>.</p>',
        ),
    ),
    # A box holding a box holding the title in two lines (#310).
    (
        "7. A nested title",
        _page(
            "7. A nested title",
            '<div class="box"><div class="shade"><p>7.</p><p>A nested title</p></div>'
            f"<p>The keeper's log. {_LINE}</p></div><p>After. {_LINE}</p>",
            framed=False,
        ),
    ),
]


def _png():
    """A striped picture: the generator skips images of 100 bytes or less."""
    import io

    from PIL import Image

    im = Image.new("RGB", (300, 160))
    for x in range(300):
        for y in range(160):
            im.putpixel((x, y), (x % 256, (y * 3) % 256, 90))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Nested Boxes", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    for title, page in _LAST:
        builder.add_chapter(title, page.encode("utf-8"))
    builder.add_manifest_item(
        item_id="css", href="nested-boxes.css", media_type="text/css", data=CSS.encode()
    )
    builder.add_manifest_item(
        item_id="picture", href="picture.png", media_type="image/png", data=_png()
    )
    return builder.build(out_dir, "nested-boxes-source")


def boxes_per_chapter(kfx):
    """`$270` boxes in each storyline, in reading order."""
    frags = load_fragments(kfx)
    return [
        sum(1 for e in iter_entries(val(f)["$146"]) if str(e.get("$159")) == "$270")
        for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0))
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/nested-boxes"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)
    env = install_plugin(out)
    new = out / "nested-boxes-new.kfx"
    convert_with_calibre(env, source, new, "Nested Boxes New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: boxes per chapter {boxes_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "nested-boxes-old.kfx"
        convert_with_calibre(old_env, source, old, "Nested Boxes Old", native=True)
        print(f"  {old.name}: boxes per chapter {boxes_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
