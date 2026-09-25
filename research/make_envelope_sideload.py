#!/usr/bin/env python3
"""Build the sideload pair/triple that tests the 16,000 position envelope.

`tests/unit/test_kfx_invariants.py::TestPositionEnvelopeCeiling` asserts that
no position id exceeds 16,000, citing "Kindle progress display will break".
That number is inherited, and every input the assertion has ever run against
is far too small to violate it — the largest fixture reaches 10,598. The
generator emits positions past 16,000 without complaint, so either the ceiling
is real and kfxgen silently ships broken large books, or the ceiling is about
`SECTION_POS_BASE` rather than content positions and large books are fine.

Only a device settles it. This builds three books that differ in exactly one
respect — how far their positions run — and prints what to look for.

    python research/make_envelope_sideload.py [out_dir]

Output is `.kfx`, which is gitignored. Do not commit these.

Result so far (2026-09-07, Kindle Oasis 10th generation, firmware 5.18.2):
**void, because the control failed.** At chapter 105, A (max position 12,998,
under the ceiling) reported 28% where 10.5% was expected, and B reported 26%
where 7.5% was expected. C locked the device up, but a 5,000-chapter book also
has a 5,000-entry contents list, so that may be the contents renderer rather
than positions. What the run did establish is that progress reporting is wrong
for kfxgen KFX in general, by roughly 3x, on a file with no envelope problem.
The envelope question cannot be answered until that is fixed. Re-run this
then.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT))

from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402
from tests._kfx_introspect import (  # noqa: E402
    by_type,
    load_fragments,
    val,
    walk_for_key,
)

#: (slug, title, chapter count). Counts calibrated against the chapter text
#: below, which costs 12 positions per chapter — a first pass sized against
#: single-paragraph chapters (8 per chapter) put the *control* over the
#: ceiling, which would have made the whole comparison meaningless. Verify the
#: printed table before sideloading; if the text changes, the counts must be
#: re-derived.
#:
#: Three points, not two, because two cannot tell "the ceiling is real" from
#: "the ceiling is lower than we think". A lands at 12,998 (under), B at
#: 17,798 (11% over), C at 60,998 (3.8x over). If only C misbehaves the real
#: limit sits between B and C; if none do, the ceiling is not about content
#: positions at all.
BOOKS = (
    ("a-control", "Envelope A control under 16k", 1000),
    ("b-just-over", "Envelope B just over 16k", 1400),
    ("c-far-over", "Envelope C far over 16k", 5000),
)

AUTHOR = "kfxgen position test"

#: Position-bearing keys and fragments, matching TestPositionEnvelopeCeiling.
_POS_KEYS = ("$155", "$185", "$181", "$182")
_POS_TYPES = ("$259", "$260", "$265", "$264", "$389", "$550")
MAX_POS = 16000


def max_position(path):
    best = 0

    def walk(node, depth=0):
        nonlocal best
        if depth > 40:
            return
        if hasattr(node, "items"):
            for key, sub in node.items():
                if str(key) in _POS_KEYS:
                    for cand in sub if isinstance(sub, list) else [sub]:
                        try:
                            best = max(best, int(cand))
                        except (TypeError, ValueError):
                            pass
                walk(sub, depth + 1)
        elif isinstance(node, list):
            for sub in node:
                walk(sub, depth + 1)
        elif hasattr(node, "value") and not isinstance(node, (str, bytes)):
            walk(node.value, depth + 1)

    for frag in load_fragments(path):
        if str(frag.ftype) in _POS_TYPES:
            walk(frag.value)
    return best


def chapters_for(count):
    """Self-describing chapters, so the device screen can be read as a verdict.

    Every chapter states its own number and the progress the reader *should*
    be showing there. Without that the tester has to hold the arithmetic in
    their head across 5,000 chapters, and "the percentage looked wrong" is not
    a result anyone can act on.
    """
    out = []
    for i in range(1, count + 1):
        pct = round(100.0 * i / count)
        marker = ""
        if i == 1 or i == count or i % max(1, count // 10) == 0:
            marker = "  *** CHECKPOINT ***"
        out.append(
            {
                "title": f"Chapter {i} of {count}",
                "text": (
                    f"Chapter {i} of {count}.{marker}\n\n"
                    f"Expected progress here: about {pct}%.\n\n"
                    f"Compare that against whatever the reader prints at the "
                    f"bottom of the screen. Record the number it shows, or "
                    f"record that it shows nothing at all."
                ),
            }
        )
    return out


def structural_report(path):
    """Everything the tier-1 invariants check, except the envelope itself.

    The point is to be able to say "the only thing unusual about this file is
    how far its positions run". A device that mishandles a structurally broken
    file tells us nothing about the envelope.
    """
    frags = load_fragments(path)
    from kfxgen.kfxlib_minimal.ion import IS

    defs, dupes = set(), 0
    for frag in frags:
        if str(frag.ftype) not in ("$259", "$260", "$389"):
            continue
        for raw in walk_for_key(val(frag), "$155"):
            pos = int(raw)
            if pos in defs:
                dupes += 1
            defs.add(pos)

    unresolved = 0
    for frag in by_type(frags, "$265"):
        value = val(frag)
        entries = value if isinstance(value, list) else (value.get(IS("$181")) or [])
        for entry in entries:
            ref = entry.get(IS("$185")) if hasattr(entry, "get") else None
            if ref is not None and int(ref) not in defs | {0}:
                unresolved += 1

    sections = {str(f.fid) for f in by_type(frags, "$260")}
    listed = set()
    f538 = by_type(frags, "$538")
    if f538:
        groups = val(f538[0]).get(IS("$169")) or []
        if groups:
            listed = {str(n) for n in (groups[0].get(IS("$170")) or [])}

    return {
        "fragments": len(frags),
        "sections": len(sections),
        "duplicate_eids": dupes,
        "unresolved_265_refs": unresolved,
        "sections_missing_from_538": len(sections - listed),
    }


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "test_books/envelope")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for slug, title, count in BOOKS:
        path = out_dir / f"envelope-{slug}.kfx"
        NativeKFXGenerator().generate_full_book(
            title, AUTHOR, chapters_for(count), output_path=str(path)
        )
        rows.append((title, count, path, max_position(path), structural_report(path)))

    print(f"\nwrote {len(rows)} file(s) to {out_dir}\n")
    print(f"{'title':<32} {'chapters':>8} {'max pos':>8} {'envelope':>9} {'MB':>5}")
    for title, count, path, mp, _ in rows:
        state = "OVER" if mp > MAX_POS else "under"
        print(
            f"{title:<32} {count:>8} {mp:>8} {state:>9} "
            f"{path.stat().st_size / 1e6:>5.1f}"
        )

    print(
        "\nstructural checks (all should read 0 — otherwise the file is the "
        "variable, not the envelope):"
    )
    for title, _count, _path, _mp, rep in rows:
        print(
            f"  {title:<32} fragments={rep['fragments']:<6} "
            f"sections={rep['sections']:<6} dup_eids={rep['duplicate_eids']} "
            f"unresolved_refs={rep['unresolved_265_refs']} "
            f"missing_from_538={rep['sections_missing_from_538']}"
        )
    print(
        "\n"
        "what to do on each device\n"
        "-------------------------\n"
        "Sideload all three. They carry distinct titles, so they will not\n"
        "overwrite one another on the home screen.\n"
        "\n"
        "For each book, in this order:\n"
        "  1. Open it. Does it open at all, and is the first page readable?\n"
        "  2. Read the bottom of the screen. Note what it shows — page,\n"
        "     location, percentage, or nothing.\n"
        "  3. Open the navigation pane and jump to a CHECKPOINT chapter near\n"
        "     the middle. Every chapter prints the progress it expects.\n"
        "     Compare that against what the reader shows.\n"
        "  4. Jump to the last chapter and compare again. This is where a\n"
        "     wrapped or truncated position shows up most clearly.\n"
        "  5. Page forward a few screens and confirm progress still moves\n"
        "     in the right direction.\n"
        "\n"
        "Record per device and per book: the firmware version, whether it\n"
        "opened, and the reported vs expected progress at the checkpoints.\n"
        "A is the control — if A misbehaves too, the problem is not the\n"
        "envelope and the whole comparison is void.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
