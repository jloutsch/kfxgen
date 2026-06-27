#!/usr/bin/env python3
"""Side-by-side structural comparison of two KFX files.

Usage:
    python3 tools/kfxanalyze.py <reference.kfx> <ours.kfx>

Compares fragment-type histograms, $164/$417 resource lists, $157 style
shapes, and $145 content fragment counts. Useful for tracking the diff
between kfxgen output and a Calibre KFX Output gold-standard build of
the same source EPUB.
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


class _BDS:
    def __init__(self, d):
        self.data = d

    def get_data(self):
        return self.data


def load(p):
    cat = SymbolTableCatalog(add_global_shared_symbol_tables=True)
    st = LocalSymbolTable(catalog=cat)
    with open(p, "rb") as f:
        d = f.read()
    c = KfxContainer(st, datafile=_BDS(d))
    c.deserialize(ignore_drm=True)
    return c.get_fragments()


def plain(o, depth=0, mx=25):
    if depth > mx:
        return "<...>"
    if hasattr(o, "annotations") and hasattr(o, "value"):
        return plain(o.value, depth + 1, mx)
    if hasattr(o, "items"):
        return {str(k): plain(v, depth + 1, mx) for k, v in o.items()}
    if isinstance(o, list):
        return [plain(x, depth + 1, mx) for x in o]
    if isinstance(o, (bytes, bytearray)):
        return f"<bytes:{len(o)}>"
    if hasattr(o, "value") and not isinstance(o, str):
        return plain(o.value, depth + 1, mx)
    if isinstance(o, (int, float, bool)) or o is None:
        return o
    return str(o)


def by_type(frags):
    out = {}
    for f in frags:
        out.setdefault(str(f.ftype), []).append(f)
    return out


def get_val(f):
    v = f.value
    if hasattr(v, "value") and not isinstance(v, (bytes, bytearray)):
        v = v.value
    return v


def fragment_histogram(frags, label_prefix):
    bt = by_type(frags)
    print(f"\n[{label_prefix}] Fragment-type histogram ({len(frags)} total):")
    for t in sorted(bt.keys()):
        print(f"  {t}: {len(bt[t])}")


def list_resources(frags, label_prefix):
    bt = by_type(frags)
    print(f"\n[{label_prefix}] $164 resource manifests ({len(bt.get('$164', []))}):")
    for f in bt.get("$164", []):
        v = plain(get_val(f), mx=4)
        loc = v.get("$165", "?") if isinstance(v, dict) else "?"
        fmt = v.get("$161", "?") if isinstance(v, dict) else "?"
        mime = v.get("$162", "") if isinstance(v, dict) else ""
        print(f"  fid={f.fid}  loc={loc}  fmt={fmt}  mime={mime}")


def style_summary(frags, label_prefix):
    bt = by_type(frags)
    styles = bt.get("$157", [])
    print(f"\n[{label_prefix}] $157 styles ({len(styles)}):")
    shape_counts = {}
    sample_by_shape = {}
    for f in styles:
        v = plain(get_val(f), mx=3)
        if isinstance(v, dict):
            keys = tuple(sorted(v.keys()))
            shape_counts[keys] = shape_counts.get(keys, 0) + 1
            if keys not in sample_by_shape:
                sample_by_shape[keys] = (str(f.fid), v)
    for keys, cnt in sorted(shape_counts.items(), key=lambda x: -x[1])[:10]:
        sample_fid, sample_v = sample_by_shape[keys]
        print(f"  {cnt}x  keys={keys}  sample fid={sample_fid}")
        print(f"        sample={json.dumps(sample_v, default=str)[:200]}")


def first_chunk_content(frags, label_prefix, n=3):
    bt = by_type(frags)
    print(f"\n[{label_prefix}] $145 content fragments ({len(bt.get('$145', []))}):")
    for f in bt.get("$145", [])[:n]:
        v = plain(get_val(f), mx=8)
        s = json.dumps(v, default=str)
        print(f"  fid={f.fid}  size={len(s)}  preview={s[:300]}")


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    ref_path, ours_path = argv[1], argv[2]
    ref = load(ref_path)
    ours = load(ours_path)

    print("=" * 70)
    print(f"Reference: {ref_path}")
    print(f"Ours:      {ours_path}")
    print("=" * 70)

    fragment_histogram(ref, "REF")
    fragment_histogram(ours, "OURS")

    list_resources(ref, "REF")
    list_resources(ours, "OURS")

    style_summary(ref, "REF")
    style_summary(ours, "OURS")

    first_chunk_content(ref, "REF")
    first_chunk_content(ours, "OURS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
