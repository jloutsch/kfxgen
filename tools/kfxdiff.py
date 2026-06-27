#!/usr/bin/env python3
"""Dump a KFX file's fragment structure as a human-readable text report.

Usage:
    python3 tools/kfxdiff.py <input.kfx> [output.txt]

If output.txt is omitted, the dump is written to <input>.dump.txt.

The dump groups fragments by Ion type ($145, $259, $260, ...) and prints
the structure of each fragment under each type. Useful for diffing the
output of `kfxgen` against a gold-standard Calibre KFX Output for the
same source EPUB. See tools/kfxanalyze.py for a side-by-side comparison.
"""

import json
import os
import sys

_THIS = os.path.abspath(os.path.dirname(__file__))
_PLUGIN = os.path.normpath(os.path.join(_THIS, "..", "plugin"))
sys.path.insert(0, _PLUGIN)

from kfxgen.kfxlib_minimal.kfx_container import KfxContainer  # noqa: E402
from kfxgen.kfxlib_minimal.ion_symbol_table import (  # noqa: E402
    LocalSymbolTable,
    SymbolTableCatalog,
)


class _BytesDataSource:
    def __init__(self, data):
        self.data = data

    def get_data(self):
        return self.data


def load(filepath):
    catalog = SymbolTableCatalog(add_global_shared_symbol_tables=True)
    symtab = LocalSymbolTable(catalog=catalog)
    with open(filepath, "rb") as f:
        data = f.read()
    container = KfxContainer(symtab, datafile=_BytesDataSource(data))
    container.deserialize(ignore_drm=True)
    return container.get_fragments()


def to_plain(o, depth=0, max_depth=20):
    if depth > max_depth:
        return "<truncated>"
    if hasattr(o, "annotations") and hasattr(o, "value"):
        return to_plain(o.value, depth + 1, max_depth)
    if hasattr(o, "items"):
        return {str(k): to_plain(v, depth + 1, max_depth) for k, v in o.items()}
    if isinstance(o, list):
        return [to_plain(x, depth + 1, max_depth) for x in o]
    if isinstance(o, (bytes, bytearray)):
        return f"<bytes len={len(o)}>"
    if hasattr(o, "value") and not isinstance(o, str):
        return to_plain(o.value, depth + 1, max_depth)
    if isinstance(o, (int, float, bool, type(None))):
        return o
    return str(o)


def fragments_by_type(frags):
    by_type = {}
    for f in frags:
        by_type.setdefault(str(f.ftype), []).append(f)
    return by_type


def write_dump(frags, out_path):
    by_type = fragments_by_type(frags)
    with open(out_path, "w") as o:
        o.write(f"=== Total: {len(frags)} fragments ===\n\n")
        o.write("Fragment type counts:\n")
        for t, fs in sorted(by_type.items()):
            o.write(f"  {t}: {len(fs)}\n")
        for t in sorted(by_type.keys()):
            o.write(f"\n{'=' * 80}\n{t} ({len(by_type[t])} fragments)\n{'=' * 80}\n")
            for f in by_type[t]:
                o.write(f"\n--- fid={f.fid} ---\n")
                o.write(
                    json.dumps(to_plain(f.value, max_depth=15), indent=2, default=str)
                )
                o.write("\n")


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    inp = argv[1]
    if not os.path.isfile(inp):
        print(f"error: not a file: {inp}", file=sys.stderr)
        return 1
    out = argv[2] if len(argv) > 2 else inp + ".dump.txt"
    frags = load(inp)
    write_dump(frags, out)
    print(f"Wrote {len(frags)} fragments to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
