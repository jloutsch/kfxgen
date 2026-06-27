#!/usr/bin/env python3
"""Glossary deep-dive: compare the glossary section structure between a
reference KFX and a kfxgen-generated KFX of the same EPUB.

Usage:
    python3 tools/glossary_compare.py <reference.kfx> <ours.kfx>

Originally written to nail the recipe Calibre's KFX Output uses for
glossary entries (a fantasy novel's in-book dictionary) so the
"definitions running together" rendering bug could be reproduced and
fixed (#3, Phase 3).

Generalizable to any glossary-bearing book by editing the
GLOSSARY_HEURISTIC list below — the default tokens are placeholders
keyed to the maintainer's local test book and should be replaced for
your own corpus.
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


def plain(o, depth=0, mx=30):
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


def get_val(f):
    v = f.value
    if hasattr(v, "value") and not isinstance(v, (bytes, bytearray)):
        v = v.value
    return v


def by_type(frags):
    out = {}
    for f in frags:
        out.setdefault(str(f.ftype), []).append(f)
    return out


# Replace these tokens with strings characteristic of your own
# glossary chapter. The script searches $145 fragments for any of
# these strings to locate the glossary content. The defaults below
# are keyed to the maintainer's local test book and will not match
# anything else.
GLOSSARY_HEURISTIC: list[str] = ["REPLACE_ME_GLOSSARY_TOKEN"]


def find_glossary_content_fids(frags, label):
    bt = by_type(frags)
    print(f"\n=== {label}: searching $145 fragments for glossary content ===")
    found = []
    for f in bt.get("$145", []):
        v = plain(get_val(f), mx=4)
        s = json.dumps(v, default=str)
        for w in GLOSSARY_HEURISTIC:
            if w in s:
                found.append((str(f.fid), w, len(s)))
                break
    print(f"  Matching $145 fragments: {len(found)}")
    for fid, word, size in found[:20]:
        print(f"    {fid}  matched={word!r}  total_size={size}")
    return [fid for fid, _, _ in found]


def show_259_for_content(frags, label, content_fids, max_entries=8):
    bt = by_type(frags)
    print(f"\n=== {label}: $259 entries pointing into glossary content ===")
    for f in bt.get("$259", []):
        s = json.dumps(plain(get_val(f), mx=8), default=str)
        if not any(cfid in s for cfid in content_fids):
            continue
        v = plain(get_val(f), mx=8)
        if isinstance(v, dict):
            entries = v.get("$146") or v.get("$181") or []
            print(f"\n  $259 fid={f.fid}  entries={len(entries)}")
            for i, e in enumerate(entries[:max_entries]):
                print(f"    [{i}] {json.dumps(e, default=str)[:300]}")
            if len(entries) > max_entries:
                print(f"    ... ({len(entries)} total)")


def section_summary(frags, label):
    bt = by_type(frags)
    print(f"\n=== {label}: $260 sections (summary) ===")
    for f in bt.get("$260", []):
        v = plain(get_val(f), mx=8)
        if isinstance(v, dict):
            name = v.get("$174") or v.get("name", "?")
            disp = v.get("$159", "?")
            pgbreak = v.get("$156", "-")
            keys = list(v.keys())
            print(
                f"  fid={f.fid}  name={name}  display={disp}  "
                f"page-break={pgbreak}  keys={keys}"
            )


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    ref_path, ours_path = argv[1], argv[2]

    print("\n" + "=" * 70)
    print("=== REFERENCE ===")
    print("=" * 70)
    ref = load(ref_path)
    ref_cids = find_glossary_content_fids(ref, "REF")
    if ref_cids:
        show_259_for_content(ref, "REF", ref_cids)

    print("\n" + "=" * 70)
    print("=== OURS ===")
    print("=" * 70)
    ours = load(ours_path)
    ours_cids = find_glossary_content_fids(ours, "OURS")
    if ours_cids:
        show_259_for_content(ours, "OURS", ours_cids)

    section_summary(ref, "REF")
    section_summary(ours, "OURS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
