"""
End-to-end integration test for the #49 EPUB fixture corpus.

For each fixture in CORPUS:
  1. Build the .epub via the corpus builder function.
  2. Wrap in EpubAsOeb and feed through converter.extract_metadata
     and converter.extract_chapters_from_oeb.
  3. Run NativeKFXGenerator.generate_full_book to produce a .kfx.
  4. Assert the fixture's Outcome contract (succeeds | raises).
  5. On success: assert KFX output non-empty, #43 invariants hold,
     kfxlib_minimal can round-trip-parse the output.

Tier-1 (in-process invariants) + tier-2 (kfxlib round-trip).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen import converter  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402

from tests._kfx_introspect import (  # noqa: E402
    by_type,
    load_fragments,
    val,
    walk_for_key,
)
from tests.fixtures.epub_corpus import CORPUS  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402


from tests._helpers import NullLog as _NullLog  # noqa: E402


# TODO(PR2): extract to tests/_kfx_invariants_helpers.py — re-implements
# logic from tests/unit/test_kfx_invariants.py and tests/unit/test_position_map.py.
# When extracted, both unit and integration paths should call the shared helper.
def _assert_invariants_43(kfx_path: Path, frags) -> None:
    """Subset of #43 invariants applied to integration output:
    EID uniqueness, position envelope <= 16000, $265 sentinel.

    See tests/unit/test_kfx_invariants.py for the full #43 invariant
    suite (reading-order chain, $164/$417 divergence, $157.$307 type,
    etc.). Integration coverage runs only this subset to keep
    per-fixture runtime fast; unit tests cover the rest against
    fresh fixtures.
    """
    # EID uniqueness across $259/$260/$389
    seen: dict = {}
    for f in frags:
        if str(f.ftype) not in ("$259", "$260", "$389"):
            continue
        for pos in walk_for_key(val(f), "$155"):
            try:
                key = int(pos)
            except (TypeError, ValueError):
                continue
            location = (str(f.fid), str(f.ftype))
            assert key not in seen or seen[key] == location, (
                f"Duplicate $155 EID at {key}: {seen[key]} vs {location}"
            )
            seen[key] = location

    # Position envelope <= 16000
    pos_types = ("$259", "$260", "$265", "$264", "$389", "$550")
    max_pos = 0
    where = None
    for f in frags:
        if str(f.ftype) not in pos_types:
            continue
        for k in ("$155", "$185"):
            for p in walk_for_key(val(f), k):
                try:
                    n = int(p)
                except (TypeError, ValueError):
                    continue
                if n > max_pos:
                    max_pos = n
                    where = (str(f.fid), str(f.ftype), k)
    assert max_pos <= 16000, f"Position id {max_pos} exceeds envelope at {where}"

    # $265 sentinel position 0 at the end
    map_265 = by_type(frags, "$265")
    assert map_265, "Missing $265 fragment"
    from kfxgen.kfxlib_minimal.ion import IS  # noqa: E402

    v = val(map_265[0])
    entries = v if isinstance(v, list) else (v.get(IS("$181")) or [])
    assert entries, "$265 has no entries"
    last = entries[-1]
    sentinel = last.get(IS("$185")) if hasattr(last, "get") else None
    assert int(sentinel) == 0, f"$265 sentinel is {sentinel}, expected 0"

    # $264 and $550 content positions must be a subset of $265 content
    # positions. Mirrors tests/unit/test_position_map.py::
    # TestImagePositionsConsistent::test_264_and_550_positions_consistent_with_265.
    # MEMORY.md / v5.3.5: "positions in $264/$550 absent from $265 break the
    # progress walk."
    pos_in_265 = set()
    for e in entries:
        if hasattr(e, "get"):
            p = e.get(IS("$185"))
            if p is not None and int(p) != 0:
                pos_in_265.add(int(p))

    section_base = NativeKFXGenerator.SECTION_POS_BASE

    for f264 in by_type(frags, "$264"):
        v264 = val(f264)
        for sec in v264 if isinstance(v264, list) else [v264]:
            pids = sec.get(IS("$181")) if hasattr(sec, "get") else []
            for pid in pids or []:
                try:
                    n = int(pid)
                except (TypeError, ValueError):
                    continue
                if n >= section_base:
                    continue  # section positions are fine in $264
                assert n in pos_in_265, (
                    f"$264 references content position {n} not in $265 — "
                    f"would break Kindle progress (#4)"
                )

    for f550 in by_type(frags, "$550"):
        v550 = val(f550)
        for item in v550 if isinstance(v550, list) else [v550]:
            if not hasattr(item, "get"):
                continue
            positions = item.get(IS("$182")) or []
            for p in positions:
                pos = p.get(IS("$155")) if hasattr(p, "get") else None
                if pos is None:
                    continue
                try:
                    n = int(pos)
                except (TypeError, ValueError):
                    continue
                if n >= section_base or n == 0:
                    continue
                assert n in pos_in_265, (
                    f"$550 references content position {n} not in $265 — "
                    f"would break Kindle progress (#4)"
                )


def _assert_kfxlib_round_trip(kfx_path: Path, frags) -> None:
    """Tier 2: kfxlib_minimal must parse the generated KFX without raising."""
    assert frags, f"{kfx_path}: kfxlib_minimal returned no fragments"


@pytest.mark.tier1
@pytest.mark.tier2
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", CORPUS)
def test_epub_corpus_fixture(name, builder, corpus_dir):
    out_dir = corpus_dir / name
    out_dir.mkdir(exist_ok=True)
    epub_path, outcome = builder(out_dir)
    oeb = EpubAsOeb(epub_path)
    kfx_path = out_dir / f"{name}.kfx"

    def _run():
        metadata = converter.extract_metadata(oeb, _NullLog())
        chapters = converter.extract_chapters_from_oeb(oeb, _NullLog())
        cover_data, _cover_href = converter.extract_cover_image(oeb, _NullLog())
        NativeKFXGenerator().generate_full_book(
            title=metadata.get("title", "Untitled"),
            author=metadata.get("author", "Unknown"),
            chapters=chapters,
            cover_image=cover_data,
            output_path=str(kfx_path),
        )

    if outcome.kind == "raises":
        with pytest.raises(outcome.exception_type, match=outcome.message_pattern or ""):
            _run()
        return

    _run()
    assert kfx_path.stat().st_size > 0, f"{name}: KFX output is empty"
    frags = load_fragments(kfx_path)
    _assert_kfxlib_round_trip(kfx_path, frags)
    _assert_invariants_43(kfx_path, frags)
    if outcome.additional_check is not None:
        outcome.additional_check(kfx_path)
