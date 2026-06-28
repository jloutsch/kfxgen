#!/usr/bin/env python3
"""Identify embedded fonts (and other resources) inside a single KFX file.

Usage:
    python3 tools/fontscan.py <file.kfx>

Phase 0 helper for embedding fonts in kfxgen output (see issues #15, #16).
KFX stores every binary resource as a $164 (metadata) + $417 (raw bytes)
pair, linked by a location name. This tool walks those pairs, classifies
each by the magic bytes at the start of its $417 BLOB, and reports:

  * which resources are fonts (TTF / OTF / TrueType / WOFF / WOFF2),
  * the $161 format symbol each font $164 uses (the number #15 needs),
  * any $417 with font magic that has no $164 partner (orphans),
  * an inventory of $157 style property symbols, so the font-family
    property can be spotted by eye.

Detection is by magic bytes, so it works regardless of how a given
producer (jhowell's KFX Output, Kindle Previewer, KDP) labels resources.
"""

import json
import os
import sys

_THIS = os.path.abspath(os.path.dirname(__file__))
_PLUGIN = os.path.normpath(os.path.join(_THIS, "..", "plugin"))
sys.path.insert(0, _PLUGIN)
sys.path.insert(0, _THIS)

# Reuse the loader + helpers from kfxanalyze.py rather than duplicating them.
from kfxanalyze import by_type, get_val, load, plain  # noqa: E402

# First-bytes signatures. Fonts use the sfnt/web-font wrappers; images are
# listed so we can label non-font resources instead of calling them unknown.
_FONT_SIGS = {
    b"\x00\x01\x00\x00": "ttf",  # TrueType (sfnt 1.0)
    b"OTTO": "otf",  # OpenType with CFF outlines
    b"true": "truetype",  # older Mac TrueType
    b"typ1": "truetype",  # PostScript-in-sfnt
    b"ttcf": "ttc",  # TrueType Collection
    b"wOFF": "woff",  # WOFF
    b"wOF2": "woff2",  # WOFF2
}


def font_kind(data):
    """Return a font subtype string if `data` starts with a font signature."""
    if len(data) < 4:
        return None
    return _FONT_SIGS.get(bytes(data[:4]))


def image_kind(data):
    """Return 'jpeg'/'png' if `data` looks like a supported image, else None."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"\x89PNG":
        return "png"
    return None


def blob_bytes(frag):
    """Extract raw bytes from a $417 fragment value (IonBLOB or bytes-like)."""
    v = frag.value
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    inner = getattr(v, "value", None)
    if isinstance(inner, (bytes, bytearray)):
        return bytes(inner)
    return b""


def scan(frags):
    """Pair $164 metadata with $417 bytes and classify each resource."""
    bt = by_type(frags)

    # location name (str) -> raw bytes, keyed by the $417 fragment's fid.
    blobs = {str(f.fid): blob_bytes(f) for f in bt.get("$417", [])}

    fonts, images, other = [], [], []
    referenced_locs = set()

    for f in bt.get("$164", []):
        v = plain(get_val(f), mx=4)
        loc = v.get("$165", "?") if isinstance(v, dict) else "?"
        fmt = v.get("$161", "?") if isinstance(v, dict) else "?"
        mime = v.get("$162", "") if isinstance(v, dict) else ""
        referenced_locs.add(str(loc))
        data = blobs.get(str(loc), b"")
        rec = {
            "fid": str(f.fid),
            "loc": str(loc),
            "fmt": str(fmt),
            "mime": str(mime),
            "size": len(data),
        }
        fk = font_kind(data)
        if fk:
            rec["kind"] = fk
            fonts.append(rec)
        elif image_kind(data):
            rec["kind"] = image_kind(data)
            images.append(rec)
        else:
            rec["kind"] = "unknown" if data else "missing-blob"
            other.append(rec)

    # $417 BLOBs with font magic that no $164 referenced.
    orphans = []
    for loc, data in blobs.items():
        if loc not in referenced_locs:
            fk = font_kind(data)
            if fk:
                orphans.append({"loc": loc, "kind": fk, "size": len(data)})

    return fonts, images, other, orphans, bt


def style_property_inventory(bt):
    """Count $157 style property symbols with a sample value for each.

    The font-family property is unknown to kfxgen today; listing every
    distinct $157 key lets the investigator spot the unfamiliar one in a
    font-embedded reference file.
    """
    keys = {}
    samples = {}
    for f in bt.get("$157", []):
        v = plain(get_val(f), mx=3)
        if not isinstance(v, dict):
            continue
        for k, val in v.items():
            keys[k] = keys.get(k, 0) + 1
            if k not in samples:
                samples[k] = val
    return keys, samples


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = argv[1]
    frags = load(path)
    fonts, images, other, orphans, bt = scan(frags)

    print("=" * 70)
    print(f"fontscan: {path}")
    print(
        f"  {len(bt.get('$164', []))} resource manifests ($164), "
        f"{len(bt.get('$417', []))} raw blobs ($417)"
    )
    print("=" * 70)

    print(f"\nFONTS ({len(fonts)}):")
    for r in fonts:
        print(
            f"  fid={r['fid']}  loc={r['loc']}  $161={r['fmt']}  "
            f"kind={r['kind']}  size={r['size']:,}"
        )

    if orphans:
        print(f"\nORPHAN font blobs ($417 with no $164) ({len(orphans)}):")
        for r in orphans:
            print(f"  loc={r['loc']}  kind={r['kind']}  size={r['size']:,}")

    print(
        f"\nIMAGES ({len(images)}): " + ", ".join(sorted({r["kind"] for r in images}))
        if images
        else "\nIMAGES (0):"
    )
    print(f"OTHER/UNCLASSIFIED ({len(other)}):")
    for r in other[:20]:
        print(
            f"  fid={r['fid']}  $161={r['fmt']}  kind={r['kind']}  "
            f"mime={r['mime']}  size={r['size']:,}"
        )
    if len(other) > 20:
        print(f"  ... and {len(other) - 20} more")

    # The headline Phase 0 datum: the format symbol(s) fonts use under $161.
    font_fmts = sorted({r["fmt"] for r in fonts})
    print("\n" + "-" * 70)
    if fonts:
        print(f"Distinct $161 format symbol(s) among fonts: {font_fmts}")
        print("  ^ this is the font format symbol kfxgen must emit (issue #15).")
    else:
        print("No embedded fonts found — this KFX does not carry fonts.")
        print("  (Confirm the source EPUB actually ships font files; see #16.)")
    print("-" * 70)

    # Style property inventory to help locate the font-family symbol.
    keys, samples = style_property_inventory(bt)
    print(f"\n$157 style property inventory ({len(keys)} distinct keys):")
    for k in sorted(keys, key=lambda x: -keys[x]):
        sample = json.dumps(samples[k], default=str)[:80]
        print(f"  {k}: seen {keys[k]}x   sample={sample}")
    print(
        "\nProperties kfxgen already emits: $13 weight, $16 size, $23 "
        "decoration, $34 align, $36 indent, $42 line-height, $46/$47/$48 "
        "box. An unfamiliar key referencing a font is the font-family lead."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
