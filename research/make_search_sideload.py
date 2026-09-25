#!/usr/bin/env python3
"""Build the sideload set that tests on-device content search (#151).

#151 records that search inside kfxgen output returns nothing on device, and
that our `$550` position map carries no `$143` character offsets where Amazon's
carries them on 99% of entries. It also records why that is a lead and not a
diagnosis: Calibre KFX Output has the same all-zero shape, so the comparison
does not yet separate "kfxgen is broken" from "no non-Amazon KFX searches".

Nothing on the desk can settle that. This builds a controlled pair — one source
text, two output formats — so the device answers it directly:

    AZW3 finds the terms, KFX does not  -> the format or our KFX is at fault
    neither finds them                  -> sideloaded search is broken generally,
                                           and the symptom was never ours
    both find them                      -> #151's premise needs re-examining

Search terms are invented words planted at known positions, so a result is a
count and a location rather than an impression. Searching a real word proves
much less: you cannot tell a missing hit from a hit you did not scroll to.

    python research/make_search_sideload.py [out_dir]

Output is gitignored. Do not commit it.

Result (2026-09-07, Kindle Voyage, firmware 5.13.6): **both books returned
every term**, so the all-zero `$143` offsets do not gate search, and #151's
theory was wrong. The original symptom came from the reporting device's
indexing backlog. A Kindle indexes sideloaded books in the background and
returns no hits for any term until a book is indexed. Before trusting a
negative search result on any device, check its "not indexed" count on the
device itself; connecting over USB pauses indexing.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT))

from kfxgen import converter as conv  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

CHAPTERS = 40
PARAS_PER_CHAPTER = 6

#: term -> (chapter numbers it appears in). Invented words, so a hit count is
#: exact and unambiguous: none of these can occur in the filler prose.
#: `PLENTIFOX` is deliberately multi-hit — a reader that finds one occurrence
#: and stops is a different failure from one that finds nothing.
SENTINELS = {
    "ZARFBLINK": [4],
    "QUOXHAVEN": [20],
    "MIRTHGLADE": [36],
    "PLENTIFOX": [2, 11, 21, 30, 39],
}

#: Ordinary word guaranteed present throughout. If even this returns nothing,
#: search is not running at all and the sentinels tell you nothing extra.
CONTROL_WORD = "harbour"

_FILLER = (
    "The keeper walked the harbour wall at dusk and counted the lamps that "
    "still burned. Rope creaked against the bollards. Somewhere beyond the "
    "breakwater a bell answered the swell, slow and unhurried, marking a "
    "channel no one had used since the spring. He wrote the number in his "
    "book, closed it, and went back along the stones to the office where the "
    "kettle had already boiled twice and been forgotten."
)


class _Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


def _chapter_text(n):
    paras = []
    for p in range(PARAS_PER_CHAPTER):
        para = f"Chapter {n}, paragraph {p + 1}. {_FILLER}"
        for term, chapters in SENTINELS.items():
            if n in chapters and p == 2:
                # Named exactly once per placement. An earlier draft
                # repeated the term in a second sentence, which doubled every
                # count and would have had the tester record 2 where the
                # checklist said 1 — a false negative dressed as a result.
                para += (
                    f" This paragraph is the only place in chapter {n} that "
                    f"carries the marker {term} for search testing."
                )
        paras.append(para)
    return "\n\n".join(paras)


def build_source(out_dir):
    builder = EpubBuilder().set_metadata(title="Search Test A kfxgen", author="kfxgen")
    for n in range(1, CHAPTERS + 1):
        builder.add_chapter(f"Chapter {n}", _chapter_text(n))
    return builder.build(out_dir, "search-source")


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "test_books/search")
    out_dir.mkdir(parents=True, exist_ok=True)

    source = build_source(out_dir)

    # kfxgen build, through the entry point __init__.py uses.
    kfx = out_dir / "search-a-kfxgen.kfx"
    conv.convert_oeb_to_kfx(EpubAsOeb(str(source)), str(kfx), opts=None, log=_Log())

    # AZW3 baseline. Distinct title so it cannot share an ASIN with the KFX
    # build and overwrite it on the home screen.
    azw3 = out_dir / "search-b-baseline.azw3"
    proc = subprocess.run(
        [
            "ebook-convert",
            str(source),
            str(azw3),
            "--title",
            "Search Test B AZW3 baseline",
            "--authors",
            "kfxgen",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print("ebook-convert failed:\n" + proc.stdout[-2000:] + proc.stderr[-2000:])
        return 1

    words = CHAPTERS * PARAS_PER_CHAPTER * len(_FILLER.split())
    print(f"\nwrote to {out_dir}\n")
    for path in (source, kfx, azw3):
        print(f"  {path.name:<28} {path.stat().st_size / 1e6:>6.2f} MB")
    print(f"\n{CHAPTERS} chapters, roughly {words:,} words.\n")

    print("search terms — each invented, so hit counts are exact:")
    for term, chapters in SENTINELS.items():
        where = ", ".join(f"ch {c}" for c in chapters)
        print(f"  {term:<12} expect {len(chapters)} hit(s)  ({where})")
    print(f"  {CONTROL_WORD:<12} expect many hits (ordinary word, every chapter)")

    print(
        "\n"
        "on each device, for each of the two books\n"
        "-----------------------------------------\n"
        "  1. Open the book once and leave it a few minutes, so anything that\n"
        "     indexes on first open has run.\n"
        "  2. Search each term above. Record the hit count, not just yes/no.\n"
        "  3. Tap a result. Does it land on the paragraph that names the term?\n"
        "  4. Search the control word. If that returns nothing either, search\n"
        "     is not running at all and the sentinel results mean nothing.\n"
        "\n"
        "third and fourth data points, no build needed\n"
        "---------------------------------------------\n"
        "  - A Calibre KFX Output file (one already exists locally). It has the\n"
        "    same all-zero $143 shape we do, so if it searches, #151's leading\n"
        "    theory is wrong.\n"
        "  - Any purchased book already on the device. Amazon KFX is the\n"
        "    positive control: if search fails there too, the device is the\n"
        "    variable, not the file.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
