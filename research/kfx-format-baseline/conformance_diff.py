#!/usr/bin/env python3
"""Diff kfxgen's fragments against Amazon's for the same book (#112).

Every structural check kfxgen has compares kfxgen to kfxgen: tier-2 decodes our
own output with an independent parser, tier-3 diffs against our own goldens,
and the corpus sweep counts our own fragments. None of them can see a case
where kfxgen is internally consistent and simply says something differently
from the reference implementation.

This does that comparison. It is the by-hand procedure that found #120 (Amazon
marks images inline that kfxgen does not) and #123 (Amazon encodes raised text
with `$44`, kfxgen used `$31`), and it disproved #118 (Amazon does not emit
zoomable plugin resources either).

## The blocker in #112 was wrong

That issue said the comparison needs jhowell's KFX Output plugin, because a
Previewer KPF is the prepub form and differs structurally from a real KFX.
It does — but `kfxlib.YJ_Book.decode_book()` performs that fixup itself
(`kpf_book.py::fix_kpf_prepub_book`): it inlines `$608` into `$259`, drops
`$609`/`$610`/`$611`, and adds the `resource/` prefix. Verified on pg40739 —
after decode the KPF has `resource/`-prefixed `$417` fids and no `$608` or
`$610`, and its fragment-type set matches kfxgen's exactly.

So only Kindle Previewer and the already-vendored KFX Input zip are needed.

## Usage

    # convert once (slow), then diff as often as you like
    "/Applications/Kindle Previewer 3.app/Contents/MacOS/Kindle Previewer 3" \\
        book.epub -convert -output /tmp/prev     # the folder must exist first
    python research/kfx-format-baseline/conformance_diff.py \\
        book.epub /tmp/prev/KPF/book.kpf

## What it does and does not tell you

A difference here is not a defect. Both pipelines produce valid KFX and kfxgen
makes deliberate different choices for arbitrary EPUBs. What this answers is
narrower and more useful: *where kfxgen and Amazon make the same choice, do
they say it the same way?* Judgement about whether to converge stays with a
person — and where it changes rendering, with a device.
"""

from __future__ import annotations

import argparse
import collections
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VENDOR_ZIP = ROOT / "tests" / "fixtures" / "vendor" / "kfx_input_plugin.zip"


def _load_upstream_kfxlib(extract: Path):
    """Import the full upstream kfxlib out of the vendored plugin zip.

    Needed rather than `kfxlib_minimal` because only the full library carries
    `fix_kpf_prepub_book`, which is what makes a KPF comparable to a KFX.
    """
    if not VENDOR_ZIP.exists():
        raise SystemExit(
            f"Upstream kfxlib not found at {VENDOR_ZIP}.\n"
            "See CONTRIBUTING.md -> The upstream kfxlib copy."
        )
    import zipfile

    with zipfile.ZipFile(VENDOR_ZIP) as zf:
        zf.extractall(extract)
    sys.path.insert(0, str(extract))
    sys.path.insert(0, str(extract / "kfxlib" / "calibre-plugin-modules"))
    from kfxlib import YJ_Book  # noqa: PLC0415

    return YJ_Book


def _kfxgen_fragments(epub_path: Path, workdir: Path):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "plugin"))
    from kfxgen import converter as conv  # noqa: PLC0415

    from tests._helpers import NullLog  # noqa: PLC0415
    from tests._kfx_introspect import load_fragments  # noqa: PLC0415
    from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: PLC0415

    out = workdir / "kfxgen.kfx"
    conv.convert_oeb_to_kfx(
        EpubAsOeb(str(epub_path)), str(out), opts=None, log=NullLog()
    )
    return load_fragments(out)


def _counts(fragments) -> collections.Counter:
    return collections.Counter(str(f.ftype) for f in fragments)


def _nested_keys(fragments, ftype: str) -> set[str]:
    """Every key appearing at any depth inside fragments of one type.

    Top-level keys miss the interesting cases: `$601` and `$159` live inside
    `$259`'s nested entries, not on the fragment itself.

    `IonAnnotation` has to be unwrapped explicitly. It is a plain object with
    `.annotations` and `.value` — neither dict-like nor a list — so without the
    first branch the walk stops dead at every annotated node and everything
    beneath it vanishes from the comparison. That is not a corner case: the
    `$389` nav fragment wraps its entries in `$391`/`$393` annotations, so
    skipping them returns 2 of its 11 keys and reports the table of contents as
    matching while never having looked at entry type, label, or position ref.

    Duck-typed rather than an isinstance check because the two sides come from
    different classes — upstream `kfxlib.ion` for Amazon, `kfxlib_minimal.ion`
    for ours.
    """
    found: set[str] = set()

    def walk(node):
        if hasattr(node, "annotations") and hasattr(node, "value"):
            walk(node.value)
        elif hasattr(node, "keys"):
            for k in list(node.keys()):
                found.add(str(k))
                walk(node[k])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for f in fragments:
        if str(f.ftype) == ftype:
            walk(f.value)
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "epub", type=Path, help="source EPUB, converted by kfxgen in-process"
    )
    ap.add_argument("kpf", type=Path, help="Kindle Previewer KPF for the same EPUB")
    args = ap.parse_args(argv)

    for path in (args.epub, args.kpf):
        if not path.exists():
            raise SystemExit(f"not found: {path}")

    # Both the extracted plugin (~5 MB) and the generated KFX are scratch. The
    # intended workflow is to re-run this often, so they get cleaned up.
    with tempfile.TemporaryDirectory(prefix="conformance_diff_") as tmp:
        tmpdir = Path(tmp)
        YJ_Book = _load_upstream_kfxlib(tmpdir / "kfxlib")
        amazon = YJ_Book(str(args.kpf))
        amazon.decode_book()
        amazon_frags = list(amazon.fragments)
        ours_frags = _kfxgen_fragments(args.epub, tmpdir)

        a_counts = _counts(amazon_frags)
        o_counts = _counts(ours_frags)

        print(f"=== {args.epub.name} ===")
        print(f"{'fragment':<10} {'kfxgen':>8} {'amazon':>8}   note")
        for ftype in sorted(set(a_counts) | set(o_counts)):
            o, a = o_counts.get(ftype, 0), a_counts.get(ftype, 0)
            note = ""
            if o and not a:
                note = "kfxgen only"
            elif a and not o:
                note = "AMAZON ONLY — kfxgen never emits this"
            print(f"{ftype:<10} {o:>8} {a:>8}   {note}")

        print("\n=== property keys, by fragment type ===")
        print("(a key on one side only is a difference in what is being said)")
        for ftype in sorted(set(a_counts) & set(o_counts)):
            ours_keys = _nested_keys(ours_frags, ftype)
            amz_keys = _nested_keys(amazon_frags, ftype)
            only_amz = sorted(amz_keys - ours_keys)
            only_ours = sorted(ours_keys - amz_keys)
            if only_amz or only_ours:
                print(f"\n  {ftype}")
                if only_amz:
                    print(f"    amazon only : {only_amz}")
                if only_ours:
                    print(f"    kfxgen only : {only_ours}")

        print(
            "\nA difference is not a defect. Both pipelines produce valid KFX and "
            "kfxgen\nmakes deliberate different choices. Investigate what each key "
            "means before\nacting, and verify on a device before changing anything "
            "that renders."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
