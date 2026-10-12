#!/usr/bin/env python3
"""Build a sideload A/B pair for a chapter that opens with a picture and
then its title (#346).

One invented source book, converted twice through the real `ebook-convert`,
each plugin in its own isolated calibre config under the output directory:

    "Picture Title New"   this checkout's plugin
    "Picture Title Old"   an earlier plugin zip given with --old-plugin (for
                          example a build of main), which prints the title
                          above the picture and again under it

The titles differ so the files get different ASINs and neither replaces the
other on the device.

    .venv/bin/python research/make_picture_title_sideload.py \\
        --old-plugin main.zip [out_dir]

Output (default `test_books/picture_title/`) is gitignored. Do not commit it.

On each device, "Picture Title New" first, then "Picture Title Old" for
comparison. Open each entry from the TOC:
  1. "The Harbour": New: the picture (blue and orange bands) at the top of
     the page, then the heading "The Harbour" once, then the text. Old:
     "The Harbour" above the picture and again under it.
  2. "The Hill": the same, with the picture and heading inside a grey box.
     New: one "The Hill", inside the box under the picture. Old: one above
     the box and one inside it.
  3. "The Lighthouse": two pictures, then the heading once (New). Old: the
     heading above the pictures and again under them.
  4. "The Ferry": New and Old the same: "The Ferry" in bold above the
     picture, and the sentence "The Ferry left the quay at noon..." under
     it. The heading must not be missing.
  5. "The Map": New and Old the same: the picture only, with no heading.
  6. "The Quay": New and Old the same: an ordinary chapter, heading then
     text.
On every chapter, tap the TOC button: the TOC opens and lists all six.
"""

import argparse
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

_TEXT = (
    "The tide went out over the flats and the gulls came down to the mud, "
    "where the boats lay over on their sides until the water came back."
)
_PIC = '<p style="text-align: center"><img src="{}" alt=""/></p>'
_BOX = '<div style="background-color: #ddd; padding: 1em">{}</div>'


def _png(width, height, top, bottom):
    """A two-band picture, large enough that kfxgen keeps it."""
    rows = b"".join(
        b"\x00" + bytes(top if y < height // 2 else bottom) * width
        for y in range(height)
    )

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


PICTURES = {
    "harbour.png": _png(300, 200, (40, 90, 160), (230, 140, 40)),
    "hill.png": _png(300, 200, (60, 140, 70), (200, 200, 120)),
    "lamp.png": _png(300, 200, (200, 40, 40), (250, 250, 250)),
}


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def _text(n=3):
    return "".join(f"<p>{_TEXT}</p>" for _ in range(n))


PAGES = [
    ("The Harbour", _PIC.format("harbour.png") + "<h1>The Harbour</h1>" + _text()),
    (
        "The Hill",
        _BOX.format(_PIC.format("hill.png") + "<h1>The Hill</h1>" + _text(1))
        + _text(2),
    ),
    (
        "The Lighthouse",
        _PIC.format("lamp.png")
        + _PIC.format("harbour.png")
        + "<h1>The Lighthouse</h1>"
        + _text(),
    ),
    (
        "The Ferry",
        _PIC.format("hill.png")
        + "<p>The Ferry left the quay at noon, and nobody on the harbour wall "
        "looked up to watch it go.</p>" + _text(2),
    ),
    ("The Map", _PIC.format("lamp.png")),
    ("The Quay", "<h1>The Quay</h1>" + _text()),
]


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Picture Title", author=AUTHOR)
    for title, body in PAGES:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    for name, data in PICTURES.items():
        builder.add_manifest_item(
            item_id=name.split(".")[0], href=name, media_type="image/png", data=data
        )
    return builder.build(out_dir, "picture-title-source")


def facts(kfx):
    """Each chapter's opening, text and pictures in reading order, so a
    wrong build shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    out = []
    for st in by_type(frags, "$259"):
        shown = []
        for e in iter_entries(val(st)["$146"]):
            if "$145" in e:
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                shown.append(text if len(text) < 30 else text[:27] + "...")
            elif str(e.get("$159")) == "$271":
                shown.append("[picture]")
        out.append(" | ".join(shown[:4]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "out_dir", nargs="?", default=str(ROOT / "test_books/picture_title")
    )
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)
    envs = [("new", "New", install_plugin(out))]
    if args.old_plugin:
        envs.append(("old", "Old", install_zip(out, args.old_plugin)))
    print(f"built with: {calibre_version(envs[0][2])}, isolated configs")
    for tag, label, env in envs:
        kfx = out / f"picture-title-{tag}.kfx"
        convert_with_calibre(env, source, kfx, f"Picture Title {label}", native=True)
        print(f"  {kfx.name}:")
        for line in facts(kfx):
            print(f"    {line}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
