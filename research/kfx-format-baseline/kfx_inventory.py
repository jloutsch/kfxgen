#!/usr/bin/env python3
"""KFX/KPF format-drift inventory tool (#18, item C).

Decodes an Amazon-produced KPF/KFX with jhowell's full ``kfxlib`` and emits a
stable inventory of the format surface Amazon actually used: fragment types +
the set of ``$``-symbols referenced. Diffing two inventories across Kindle
Previewer / kfxlib versions surfaces format drift (new symbols, new fragment
types) that the vendored ``kfxlib_minimal`` fork may need to catch up to.

Requires the full ``kfxlib`` from the installed *KFX Input* plugin, so run it
under Calibre's Python:

  # produce a fresh Amazon reference from a fixed source:
  "/Applications/Kindle Previewer 3.app/Contents/MacOS/Kindle Previewer 3" \
      gatsby.epub -convert -output out/

  # extract its inventory:
  calibre-debug kfx_inventory.py -- \
      "~/Library/Preferences/calibre/plugins/KFX Input.zip" \
      out/KPF/gatsby.kpf --previewer 3.98.0 > baseline-YYYYMMDD.json

  # on a later Previewer/kfxlib version, diff against the committed baseline:
  calibre-debug kfx_inventory.py -- <zip> new.kpf --diff baseline-YYYYMMDD.json

`--diff` exits non-zero and lists additions when new fragment types or symbols
appear — i.e. when the KFX format has drifted.
"""

import argparse
import json
import re
import sys
from collections import Counter

_SYM_RE = re.compile(r"\$(\d+)")


def _kfxlib_version(zip_path):
    import zipfile

    try:
        with zipfile.ZipFile(zip_path) as z:
            return z.read("kfxlib/version.py").decode("utf-8").split('"')[1]
    except Exception:
        return "unknown"


def build_inventory(zip_path, book_path, previewer=""):
    sys.path.insert(0, zip_path)
    from kfxlib import YJ_Book

    book = YJ_Book(book_path)
    book.decode_book()
    frags = list(book.fragments)

    ftypes = Counter(str(f.ftype) for f in frags)
    symbols = set()
    for f in frags:
        symbols.update(_SYM_RE.findall(str(f.ftype)))
        symbols.update(_SYM_RE.findall(repr(f.value)))
    sym_ints = sorted(int(s) for s in symbols)

    return {
        "source_epub": book_path.rsplit("/", 1)[-1],
        "previewer_version": previewer,
        "kfxlib_version": _kfxlib_version(zip_path),
        "fragment_total": len(frags),
        "fragment_types": dict(sorted(ftypes.items())),
        "symbol_count": len(sym_ints),
        "symbol_max": sym_ints[-1] if sym_ints else 0,
        "symbols": [f"${s}" for s in sym_ints],
    }


def diff(baseline, current):
    b_ft, c_ft = set(baseline["fragment_types"]), set(current["fragment_types"])
    b_sy, c_sy = set(baseline["symbols"]), set(current["symbols"])
    new_ft = sorted(c_ft - b_ft, key=lambda s: int(s[1:]))
    gone_ft = sorted(b_ft - c_ft, key=lambda s: int(s[1:]))
    new_sy = sorted(c_sy - b_sy, key=lambda s: int(s[1:]))
    print(
        f"baseline: previewer {baseline['previewer_version']} / "
        f"kfxlib {baseline['kfxlib_version']}"
    )
    print(
        f"current:  previewer {current['previewer_version']} / "
        f"kfxlib {current['kfxlib_version']}"
    )
    print(f"NEW fragment types: {new_ft or 'none'}")
    print(f"REMOVED fragment types: {gone_ft or 'none'}")
    print(f"NEW symbols: {new_sy or 'none'}")
    drifted = bool(new_ft or new_sy)
    print("DRIFT DETECTED" if drifted else "no drift")
    return drifted


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("zip_path", help="path to KFX Input.zip (full kfxlib)")
    p.add_argument("book_path", help="path to a .kpf/.kfx")
    p.add_argument("--previewer", default="", help="Kindle Previewer version")
    p.add_argument("--diff", metavar="baseline.json", help="diff vs a baseline")
    args = p.parse_args(argv)

    inv = build_inventory(args.zip_path, args.book_path, args.previewer)
    if args.diff:
        with open(args.diff) as f:
            baseline = json.load(f)
        return 1 if diff(baseline, inv) else 0
    print(json.dumps(inv, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # calibre-debug passes script args after "--"
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    sys.exit(main(argv))
