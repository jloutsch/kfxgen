#!/usr/bin/env python3
"""Build a sideload A/B pair for the fixes after 5.9.0: #278, #277, #280, #231.

One source book, converted twice through the real `ebook-convert`, each
plugin in its own isolated calibre config under the output directory:

    "Fixes New"   this checkout's plugin
    "Fixes Old"   an earlier plugin zip given with --old-plugin (for example
                  the v5.9.0 release), before all four fixes

The titles differ so the two files get different ASINs and neither replaces
the other on the device. All text in the book is invented.

    .venv/bin/python research/make_fixes_sideload.py \\
        --old-plugin kfxgen-plugin-5.9.0.zip [out_dir]

Output (default `test_books/fixes/`) is gitignored. Do not commit it.

On each device, both books. Open each entry from the TOC:
  1. "1. Encoded links" (#278): New: "note one" and "the summer note" are
     links; each opens its note in "Notes". Old: both are plain text.
  2. "2. A link to an empty page" (#278): New: "the empty page" is a link
     and opens "3. After the empty page". Old: plain text.
  3. "4. The Keeper's Log" (#277): the chapter is one long paragraph. Go to
     its end and tap the small "1" after "until morning". In "Notes", tap
     "1." at the start of the keeper's note. New: you land back on the page
     with "until morning". Old: you land on the paragraph's first page,
     pages too early.
  4. "5. Loose text" (#280): New: three lines, the middle one "This
     sentence sits outside any paragraph." Old: that middle line is missing.
  5. "6. Picture words" (#231): New: the chapter heading, the picture, then
     "Once upon a time" and "there was a fox." under it. Old: the picture
     only, with no words and no heading.
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

_SVG_NS = (
    'xmlns:svg="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
)
_LOG = (
    "The wind came round to the north before dark, and the keeper went up "
    "to trim the lamp and wipe the salt from the glass, as he did every "
    "evening, and wrote down the hour and the state of the sea. "
)
_KEEPER = "4. The Keeper's Log"


def _page(title, body, head=True):
    h1 = f"<h1>{title}</h1>\n" if head else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" {_SVG_NS}>\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n{h1}{body}\n</body>\n</html>\n"
    )


def _png():
    """A patterned picture: the generator skips images of 100 bytes or less."""
    import io

    from PIL import Image

    im = Image.new("RGB", (300, 400))
    for x in range(300):
        for y in range(400):
            im.putpixel((x, y), (x % 256, y % 256, 90))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# (TOC label or None for an unlisted page, page markup). Files are
# chapter_1.xhtml ... chapter_8.xhtml in this order.
PAGES = [
    (
        "1. Encoded links",
        _page(
            "1. Encoded links",
            '<p>Tap <a href="chapter_8.xhtml#fn%3A1">note one</a> or '
            '<a href="chapter_8.xhtml#%C3%A9t%C3%A9">the summer note</a>. '
            "Both open a note in the Notes chapter.</p>",
        ),
    ),
    (
        "2. A link to an empty page",
        _page(
            "2. A link to an empty page",
            '<p>Tap <a href="chapter_3.xhtml">the empty page</a>. The page it '
            "names holds nothing to show, so the link opens the chapter after "
            "it.</p>",
        ),
    ),
    (None, _page("Map", '<div id="map"></div>', head=False)),
    (
        "3. After the empty page",
        _page(
            "3. After the empty page", "<p>The link in chapter 2 lands here.</p>"
        ),
    ),
    (
        _KEEPER,
        _page(
            _KEEPER,
            f"<p>{_KEEPER}. "
            + _LOG * 40
            + 'He slept a little and kept the light until morning.<a id="ref1" '
            'href="chapter_8.xhtml#n1"><sup>1</sup></a></p>\n'
            "<p>The log ends here.</p>",
            head=False,
        ),
    ),
    (
        "5. Loose text",
        _page(
            "5. Loose text",
            "<p>The first line is a paragraph.</p>"
            "This sentence sits outside any paragraph."
            "<p>The last line is a paragraph.</p>",
        ),
    ),
    (
        "6. Picture words",
        _page(
            "6. Picture words",
            '<svg:svg viewBox="0 0 300 400">'
            '<svg:image xlink:href="picture.png" width="300" height="400"/>'
            '<svg:text x="10" y="50">Once upon a time</svg:text>'
            '<svg:text x="10" y="90">there was a fox.</svg:text></svg:svg>',
            head=False,
        ),
    ),
    (
        "Notes",
        _page(
            "Notes",
            '<p id="fn:1">Note one. You came here from chapter 1.</p>'
            + "<p>&#160;</p>" * 3
            + '<p id="été">The summer note. You came here from chapter 1.</p>'
            + "<p>&#160;</p>" * 3
            + '<p id="n1"><a href="chapter_5.xhtml#ref1">1.</a> The keeper\'s '
            'note. Tap "1." to go back to the end of the long paragraph.</p>',
        ),
    ),
]


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Fixes", author=AUTHOR)
    for title, page in PAGES:
        builder.add_chapter(title or "Map", page.encode("utf-8"), listed=bool(title))
    builder.add_manifest_item(
        item_id="picture", href="picture.png", media_type="image/png", data=_png()
    )
    return builder.build(out_dir, "fixes-source")


def facts(kfx):
    """Links whose anchor exists, anchors, pictures, and text entries, so a
    wrong build shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    anchors = {str(f.fid) for f in by_type(frags, "$266")}
    entries = [
        e for st in by_type(frags, "$259") for e in iter_entries(val(st)["$146"])
    ]
    links = [
        str(sp["$179"]) for e in entries for sp in e.get("$142") or [] if "$179" in sp
    ]
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    text = " ".join(
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for e in entries
        if "$145" in e
    )
    return {
        "links": f"{sum(n in anchors for n in links)} of {len(links)} land",
        "pictures": sum(1 for e in entries if str(e.get("$159")) == "$271"),
        "loose line": "outside any paragraph" in text,
        "picture words": "Once upon a time" in text,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/fixes"))
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    source = build_source(out)

    env = install_plugin(out)
    new = out / "fixes-new.kfx"
    convert_with_calibre(env, source, new, "Fixes New", native=True)
    print(f"built with: {calibre_version(env)}, isolated configs")
    print(f"  {new.name}: {facts(new)}")
    if args.old_plugin:
        old_env = install_zip(out, args.old_plugin)
        old = out / "fixes-old.kfx"
        convert_with_calibre(old_env, source, old, "Fixes Old", native=True)
        print(f"  {old.name}: {facts(old)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
