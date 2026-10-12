#!/usr/bin/env python3
"""Build a sideload A/B pair for paragraphs over 2,000 characters (#226).

pg22210 (In the Forbidden Land, public domain) converted twice through the
real `ebook-convert`, each plugin in its own isolated calibre config under
the output directory:

    "Forbidden Land Long New"   this checkout's plugin
    "Forbidden Land Long Old"   an earlier plugin zip given with --old-plugin
                                (for example a build of main), which cuts a
                                paragraph every 2,000 characters

The titles differ so the files get different ASINs and neither replaces the
other on the device.

    .venv/bin/python research/make_long_paragraph_sideload.py \\
        --old-plugin main.zip [out_dir]

Output (default `test_books/long_paragraphs/`) is gitignored. Do not commit
it.

On each device, "Forbidden Land Long New" first, then "Forbidden Land Long
Old" for comparison:
  1. Search for "all my provisions, some clot" (the court testimony, about
     81%). New: "some clothes, and all my shoes" reads on in one paragraph.
     Old: "some clot", then a new paragraph starting "hes, and".
  2. Search for "Spaniards or Southern Italian" (CHAPTER V, about 13%) and
     "I was assured, are not satisf" (FUNERAL RITES, about 24%). New: one
     paragraph through "Italians." and "satisfied". Old: a paragraph break
     inside the word.
  3. The longest paragraph, 23,250 characters: the testimony under "IN THE
     COURT OF J. LARKIN..." in the TOC (about 81%), from "Having made up
     my mind to travel in Turkistan and Tibet" to "...who at first slept
     round me." Page forward through it to its end and back to its start.
     Every page is full, nothing repeats or goes missing.
  4. At each of the three places above, note the progress % and the
     location in New and in Old. They should match.
  5. Go to a location inside the longest paragraph (Menu, Go To,
     Location). New lands on the page holding it, as Old does.
  6. Search for "all set off at a hard gallop", from the middle of the
     longest paragraph. New finds it.
  7. On a page inside a long paragraph, tap the TOC button: the TOC opens.
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
    CALIBRE_CUSTOMIZE,
    EBOOK_CONVERT,
    calibre_version,
    convert_with_calibre,
    install_plugin,
)

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402

_BOOK = ROOT / "research/gutenberg-top-90/pg22210.epub"


def facts(kfx):
    """Long entries, locations and where the longest paragraph starts, so a
    wrong build shows before anything is copied to a device."""
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    texts = [
        str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if "$145" in e
    ]
    locations = [
        int(loc["$143"])
        for f in by_type(frags, "$550")
        for group in val(f)
        for loc in group["$182"]
    ]
    longest = max(texts, key=len)
    return {
        "entries over 2,000 characters": sum(len(t) > 2000 for t in texts),
        "longest entry": len(longest),
        "longest starts": longest[:60],
        "locations": len(locations),
        "locations inside an entry": sum(1 for off in locations if off),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "out_dir", nargs="?", default=str(ROOT / "test_books/long_paragraphs")
    )
    ap.add_argument("--old-plugin", type=Path, help="plugin zip for the Old file")
    args = ap.parse_args()
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        sys.exit("calibre not found (ebook-convert / calibre-customize)")
    if not _BOOK.exists():
        sys.exit(f"{_BOOK} not found")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")
    envs = [("new", "New", install_plugin(out))]
    if args.old_plugin:
        envs.append(("old", "Old", install_zip(out, args.old_plugin)))
    print(f"built with: {calibre_version(envs[0][2])}, isolated configs")
    for tag, label, env in envs:
        kfx = out / f"pg22210-long-{tag}.kfx"
        convert_with_calibre(
            env, _BOOK, kfx, f"Forbidden Land Long {label}", native=True
        )
        print(f"  {kfx.name}: {facts(kfx)}")
    print(__doc__[__doc__.index("On each device") :])
    return 0


if __name__ == "__main__":
    sys.exit(main())
