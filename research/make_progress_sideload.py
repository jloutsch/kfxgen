#!/usr/bin/env python3
"""Build a sideload A/B pair for reading progress (#227).

One invented source book whose chapters differ in paragraph length, so that
the share of text before a chapter and the share of paragraphs before it are
far apart. Converted twice through the real `ebook-convert`, each plugin in
its own isolated calibre config under the output directory:

    "Progress New"   this checkout's plugin: a location every 110 positions
    "Progress Old"   an earlier plugin zip given with --old-plugin (for
                     example a build of main): one location per paragraph

The titles differ so the files get different ASINs and neither replaces the
other on the device.

    .venv/bin/python research/make_progress_sideload.py \\
        --old-plugin main.zip [out_dir]

Output (default `test_books/progress/`) is gitignored. Do not commit it.

The book: "1. Long paragraphs" (20 paragraphs of about 2,000 characters),
"2. Short lines" (1,000 lines of about 20 characters), "3. Long paragraphs
again" (20 more), "4. The end" (one line). By text, chapter 2 starts at
about 40% and chapter 3 at about 60%. By paragraph count, they start at
about 2% and 98%.

On each device (the Paperwhite first: its progress % counts locations),
"Progress New" first, then "Progress Old". Open each TOC entry and note the
% shown at the bottom of the first page:
  1. "2. Short lines": New about 40%, Old about 2%.
  2. "3. Long paragraphs again": New about 60%, Old about 98%.
  3. "4. The end": both about 100%.
  4. In "1. Long paragraphs", page forward a few pages: New's % rises
     steadily; Old's barely moves.
  5. Menu, Go To, Location: the highest location is about 900 in New and
     about 1,050 in Old. Go to a location in the middle of New's range: it
     lands near 50% of the book.
The facts printed below give each chapter's exact share for both builds.
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

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402

_SENTENCE = (
    "The tide went out over the flats and the gulls came down to the mud, "
    "where the boats lay over on their sides until the water came back. "
)


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n<h1>{title}</h1>\n{body}\n</body>\n</html>\n"
    )


def _long(n):
    return "".join(f"<p>Paragraph {i}. {_SENTENCE * 14}</p>" for i in range(1, n + 1))


def _short(n):
    return "".join(f"<p>Line {i}, a short one.</p>" for i in range(1, n + 1))


PAGES = [
    ("1. Long paragraphs", _long(20)),
    ("2. Short lines", _short(1000)),
    ("3. Long paragraphs again", _long(20)),
    ("4. The end", "<p>The last line of the book.</p>"),
]


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Progress", author=AUTHOR)
    for title, body in PAGES:
        builder.add_chapter(title, _page(title, body).encode("utf-8"))
    return builder.build(out_dir, "progress-source")


def facts(kfx):
    """For each chapter, the share of positions and of locations before it,
    so a wrong build shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    pm = sorted(
        (int(e["$184"]), int(e["$185"])) for f in by_type(frags, "$265") for e in val(f)
    )
    start = {}
    for pid, eid in pm:
        start.setdefault(eid, pid)
    total = pm[-1][0]
    sections = [int(val(f)["$141"][0]["$155"]) for f in by_type(frags, "$260")]
    # Where each location sits; main lists every section start first, so the
    # count is by position, not by place in the list.
    located = [
        start[int(loc["$155"])] + int(loc["$143"])
        for f in by_type(frags, "$550")
        for group in val(f)
        for loc in group["$182"]
    ]
    out = {"locations": len(located)}
    for (title, _), eid in zip(PAGES, sections):
        by_position = 100 * start[eid] / total
        by_location = 100 * sum(pid < start[eid] for pid in located) / len(located)
        out[title] = f"text {by_position:.0f}%, locations {by_location:.0f}%"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_books/progress"))
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
        kfx = out / f"progress-{tag}.kfx"
        convert_with_calibre(env, source, kfx, f"Progress {label}", native=True)
        print(f"  {kfx.name}: {facts(kfx)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
