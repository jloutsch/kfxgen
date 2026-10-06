#!/usr/bin/env python3
"""Build a sideload A/B pair for pictures in table cells (#262).

A table with a picture in a cell used to be written as one paragraph per row.
It is now a Kindle table whose cells hold the pictures, as Kindle Previewer
writes them: each picture an image entry with no style, shown at its own size
within its cell, and a caption a text entry beside it. This builds one source
book and converts it twice through the real `ebook-convert`, each plugin in
its own isolated calibre config under the output directory:

    "Picture Cells New"   this checkout's plugin
    "Picture Cells Old"   an earlier plugin zip given with --old-plugin (for
                          example a build of main before #262), where every
                          one of these tables is paragraphs, one per row

The titles differ so the two files get different ASINs and neither replaces
the other on the device. All text in the book is invented and the pictures
are generated patterns. Seven chapters, one table each:

    1  a 2x2 grid of photos (400x300) with a caption under each
    2  three photos (144x180) a row, each caption its own paragraph
    3  photos alone, two a row
    4  a small icon (24x24) beside text in each row
    5  a very wide picture (2000x500) beside text
    6  a one-column table holding one photo (no zoom button)
    7  a picture in the middle of a sentence in a cell

    .venv/bin/python research/make_picture_cells_sideload.py \\
        --old-plugin main-plugin.zip [out_dir]

Output (default `test_books/picture-cells/`) is gitignored. Do not commit it.

On each device, in portrait and in landscape, "Picture Cells New":
  1. Ch 1-5: each picture inside its own cell, side by side as in the
     source, none cut off or spilling into the next cell; captions under
     their pictures in ch 1 and 2.
  2. Ch 4: the icon stays small, about the height of a line of text.
  3. Ch 5: the wide picture fits its cell, scaled down, not cut off.
  4. Ch 6: no zoom button; the photo shows whole.
  5. Ch 7: the sentence and its picture all show, in order (the picture on
     its own line between the two halves of the sentence).
  6. Ch 1-5 and 7: the zoom button opens the table and the pictures show in
     the zoomed view.
  7. The TOC button is present and each of the 7 entries opens its chapter.
"Picture Cells Old" shows the same pictures and text as paragraphs, one per
row, for comparison.
"""

import argparse
import collections
import struct
import sys
import zlib
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


def _png(w, h, seed):
    """A patterned PNG; the pattern keeps it over the 100 bytes below which
    the generator skips an image as empty."""
    raw = b"".join(
        b"\x00"
        + bytes(
            ((x * (3 + seed) + y * (5 + seed)) // 8 * 37 + k * 80 + seed * 50) % 256
            for x in range(w)
            for k in range(3)
        )
        for y in range(h)
    )

    def chunk(t, d):
        return (
            struct.pack(">I", len(d))
            + t
            + d
            + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


IMAGES = {
    "photo1.png": _png(400, 300, 1),
    "photo2.png": _png(400, 300, 2),
    "photo3.png": _png(400, 300, 3),
    "photo4.png": _png(400, 300, 4),
    "port1.png": _png(144, 180, 5),
    "port2.png": _png(144, 180, 6),
    "port3.png": _png(144, 180, 7),
    "icon.png": _png(24, 24, 8),
    "wide.png": _png(2000, 500, 9),
}
_NAMES = ["Captain Smith", "The harbour", "The lamp house", "The north beacon"]
_TEXT = "The settlement of the embankment was measured weekly at each station."


def _img(src, alt):
    return f'<img src="{src}" alt="{alt}"/>'


CHAPTERS = [
    (
        "1. A picture grid with captions",
        "<table>"
        + "".join(
            "<tr>"
            + "".join(
                f"<td>{_img(f'photo{2 * r + c + 1}.png', _NAMES[2 * r + c])}<br/>"
                f"{_NAMES[2 * r + c]}</td>"
                for c in range(2)
            )
            + "</tr>"
            for r in range(2)
        )
        + "</table>",
    ),
    (
        "2. Three portraits a row",
        "<table><tr>"
        + "".join(
            f"<td><p>{_img(f'port{i}.png', f'Portrait {i}')}</p><p>Keeper {i}</p></td>"
            for i in (1, 2, 3)
        )
        + "</tr></table>",
    ),
    (
        "3. Pictures alone",
        "<table>"
        f"<tr><td>{_img('photo1.png', 'one')}</td><td>{_img('photo2.png', 'two')}</td></tr>"
        f"<tr><td>{_img('photo3.png', 'three')}</td><td>{_img('photo4.png', 'four')}</td></tr>"
        "</table>",
    ),
    (
        "4. An icon beside text",
        "<table>"
        + "".join(
            f"<tr><td>{_img('icon.png', 'mark')}</td><td>Note {n}. {_TEXT}</td></tr>"
            for n in (1, 2, 3)
        )
        + "</table>",
    ),
    (
        "5. A very wide picture",
        f"<table><tr><td>{_img('wide.png', 'wide')}</td><td>{_TEXT}</td></tr></table>",
    ),
    (
        "6. A one-column picture table",
        f"<table><tr><td>{_img('photo1.png', 'alone')}</td></tr></table>",
    ),
    (
        "7. A picture inside a sentence",
        f"<table><tr><td>Press {_img('icon.png', 'stop')} to stop the lamp.</td>"
        f"<td>{_TEXT}</td></tr></table>",
    ),
]


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n<h1>{title}</h1>\n<p>Survey pictures.</p>\n{body}\n"
        "<p>End of the pictures.</p>\n</body>\n</html>\n"
    )


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Picture Cells", author=AUTHOR)
    for title, body in CHAPTERS:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    for i, (name, data) in enumerate(IMAGES.items()):
        builder.add_manifest_item(
            item_id=f"img{i}", href=name, media_type="image/png", data=data
        )
    return builder.build(out_dir, "picture-cells-source")


def tables_per_chapter(kfx):
    """Each storyline's tables and the pictures in their cells, so a picture
    lost on the way shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    out = []
    for f in sorted(by_type(frags, "$259"), key=lambda f: int(str(f.fid)[1:] or 0)):
        entries = list(iter_entries(val(f)["$146"]))
        tables = [e for e in entries if str(e.get("$159")) == "$278"]
        pictures = sum(1 for e in entries if str(e.get("$159")) == "$271")
        if tables or pictures:
            out.append(dict(collections.Counter(tables=len(tables), pictures=pictures)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "out_dir", nargs="?", default=str(ROOT / "test_books/picture-cells")
    )
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "picture-cells-new.kfx"
    convert_with_calibre(env, source, new, "Picture Cells New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: per chapter {tables_per_chapter(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "picture-cells-old.kfx"
        convert_with_calibre(old_env, source, old, "Picture Cells Old", native=True)
        print(f"  {old.name}: per chapter {tables_per_chapter(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
