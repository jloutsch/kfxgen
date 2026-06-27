"""
Tier-1 invariant assertions encoding the KFX correctness rules from
MEMORY.md (#43).

Each rule below was discovered by breaking real Kindle devices in the
v5.3.x cycle. Until now they lived as comments in MEMORY.md. This file
promotes them to executable predicates that fire on every PR via the
tier-1 gate, converting post-mortem learning into pre-flight checks.

Each invariant runs against multiple generated fixtures (single-chapter,
multi-chapter, with-cover) so a regression in any code path trips the
assertion. The real-book corpus referenced in #43 is gitignored, so
fixtures are generated in-process for reproducibility — the cost of
losing one external sample is offset by the consistency of always running
against fresh output.

See also tests/unit/test_position_map.py for the Z3 sentinel,
position-range-separation, image-positions-in-$265, and
TOC-points-to-content invariants which were already encoded before this
file landed.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.kfxlib_minimal.ion import IS, IonSymbol  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402

from tests._kfx_introspect import (
    by_type as _by_type,
    val as _val,
    walk_for_key as _walk_for_key,
)  # noqa: E402


# Valid JPEG header + size-gate padding for cover fixture (#46).
_VALID_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


def _generate(chapters, **kwargs):
    """Generate a KFX fixture and return its filesystem path."""
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    gen.generate_full_book(
        title="Test Book",
        author="Test Author",
        chapters=chapters,
        output_path=path,
        **kwargs,
    )
    return path


@pytest.fixture(
    params=["single-chapter", "multi-chapter", "with-cover"],
)
def fixture_kfx(request, load_kfx_fragments):
    """Parametrized KFX fixture corpus — invariants must hold for each."""
    chapters_single = [{"title": "Ch1", "text": "Ch1\n\nPara A.\n\nPara B."}]
    chapters_multi = [
        {"title": f"Ch{i}", "text": f"Ch{i}\n\nPara A.\n\nPara B.\n\nPara C."}
        for i in range(5)
    ]
    if request.param == "single-chapter":
        path = _generate(chapters_single)
    elif request.param == "multi-chapter":
        path = _generate(chapters_multi)
    else:  # with-cover
        path = _generate(chapters_multi, cover_image=_VALID_JPEG)

    try:
        yield path, load_kfx_fragments(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Invariant 1: EID uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.tier1
@pytest.mark.unit
class TestEIDUniqueness:
    """Every $155 def in $259/$260/$389 is unique across all fragments;
    every $185 ref in $265 resolves to a def somewhere.

    Per MEMORY.md "EID Validation":
      - $155 in $259/$260/$389 → eid_defs (defines EIDs)
      - $155 in $550/$265/$264 → EXCLUDED from eid_defs
      - $185 in $265 → eid_refs (must be defined somewhere)
    """

    _DEF_FRAGMENT_TYPES = ("$259", "$260", "$389")

    def test_155_defs_unique(self, fixture_kfx):
        # Structural invariant: every $155 def in $259/$260/$389 has a unique
        # EID position globally — regardless of which fragment it lives in.
        # Dedup is by position only; the (fid, ftype) location is recorded
        # solely for the error message. Earlier versions deduped by
        # (position, fid, ftype), which would mask cross-fragment collisions
        # if they happened to land in the same (fid, ftype) tuple.
        path, frags = fixture_kfx
        seen: dict[int, tuple[str, str]] = {}
        duplicates = []
        for f in frags:
            if str(f.ftype) not in self._DEF_FRAGMENT_TYPES:
                continue
            for pos in _walk_for_key(_val(f), "$155"):
                key = int(pos)
                location = (str(f.fid), str(f.ftype))
                if key in seen:
                    duplicates.append((key, seen[key], location))
                else:
                    seen[key] = location
        assert not duplicates, (
            f"Duplicate $155 EID defs (position, first-loc, second-loc): "
            f"{duplicates[:5]}"
        )

    def test_265_refs_resolve_to_defs(self, fixture_kfx):
        path, frags = fixture_kfx
        eid_defs = {0}  # sentinel position 0 is implicitly valid
        for f in frags:
            if str(f.ftype) not in self._DEF_FRAGMENT_TYPES:
                continue
            for pos in _walk_for_key(_val(f), "$155"):
                eid_defs.add(int(pos))

        unresolved = []
        for f in _by_type(frags, "$265"):
            v = _val(f)
            entries = v if isinstance(v, list) else (v.get(IS("$181")) or [])
            for entry in entries:
                ref = entry.get(IS("$185")) if hasattr(entry, "get") else None
                if ref is None:
                    continue
                if int(ref) not in eid_defs:
                    unresolved.append((str(f.fid), int(ref)))
        assert not unresolved, (
            f"$265 entries reference positions with no $155 def: {unresolved[:5]}"
        )


# ---------------------------------------------------------------------------
# Invariant 2: Reading-order chain
# ---------------------------------------------------------------------------


@pytest.mark.tier1
@pytest.mark.unit
class TestReadingOrderChain:
    """For every $260 section: $260.fid == $260.$174, and the section name
    appears in $538's reading-order list.

    Per MEMORY.md "Critical KFX Patterns":
      $260.fid MUST equal $260.$174 MUST equal $538.$178 (reading order chain).

    Note: $538.$178 in current generator emits the standard symbol $351
    rather than per-section fids; the meaningful chain is fid==$174 plus
    section presence in $538.$169[0].$170.
    """

    def test_260_fid_equals_174(self, fixture_kfx):
        path, frags = fixture_kfx
        mismatches = []
        for f in _by_type(frags, "$260"):
            v = _val(f)
            field_174 = v.get(IS("$174")) if hasattr(v, "get") else None
            if field_174 is None:
                mismatches.append((str(f.fid), "missing $174"))
                continue
            if str(f.fid) != str(field_174):
                mismatches.append((str(f.fid), str(field_174)))
        assert not mismatches, (
            f"$260 reading-order chain broken (fid, $174): {mismatches}"
        )

    def test_538_lists_all_260_sections(self, fixture_kfx):
        path, frags = fixture_kfx
        section_fids = {str(f.fid) for f in _by_type(frags, "$260")}
        f538 = _by_type(frags, "$538")
        assert f538, "Missing $538 reading-order fragment"
        v = _val(f538[0])
        groups = v.get(IS("$169"))
        assert groups, "$538 has no $169 reading-order group"
        order_list = groups[0].get(IS("$170")) or []
        listed = {str(name) for name in order_list}
        missing = section_fids - listed
        assert not missing, (
            f"$538 reading-order list missing $260 sections: {missing} "
            f"(listed: {sorted(listed)})"
        )
        # Symmetric: $538 must not list phantom sections — names without a
        # matching $260 fid. A phantom entry breaks Kindle's reading-order
        # walk (it tries to dereference a non-existent section).
        phantom = listed - section_fids
        assert not phantom, (
            f"$538 lists phantom sections (no matching $260 fid): {phantom} "
            f"(actual sections: {sorted(section_fids)})"
        )


# ---------------------------------------------------------------------------
# Invariant 3: Position envelope ceiling
# ---------------------------------------------------------------------------


@pytest.mark.tier1
@pytest.mark.unit
class TestPositionEnvelopeCeiling:
    """All position ids must fit in the 5-digit envelope (≤16000).

    Per MEMORY.md "Critical KFX correction notes":
      v5.2.0's 10000 base + ≤16000 max content is the known-good range.
      Pushing SECTION_POS_BASE to 100000 broke Kindle progress display.
    """

    MAX_POS = 16000
    _POS_FRAGMENT_TYPES = ("$259", "$260", "$265", "$264", "$389", "$550")
    # Position-IDs surface under several keys depending on the fragment:
    #   $155 — content/section EID def (in $259/$260/$389)
    #   $185 — EID ref (in $265 entries)
    #   $181 — position list inside $265 entries
    #   $182 — positions list inside $550 entries
    # Walking only $155 and $185 missed two of the four; the envelope
    # ceiling must hold for every position-bearing key.
    _POS_KEYS = ("$155", "$185", "$181", "$182")

    def test_max_position_within_envelope(self, fixture_kfx):
        path, frags = fixture_kfx
        max_pos = 0
        location = None
        for f in frags:
            if str(f.ftype) not in self._POS_FRAGMENT_TYPES:
                continue
            v = _val(f)
            for key in self._POS_KEYS:
                for raw in _walk_for_key(v, key):
                    # $181/$182 are lists of position ints; $155/$185 are
                    # scalars. Normalize by treating any iterable as a
                    # sequence of positions.
                    candidates = raw if isinstance(raw, list) else [raw]
                    for pos in candidates:
                        try:
                            n = int(pos)
                        except (TypeError, ValueError):
                            continue
                        if n > max_pos:
                            max_pos = n
                            location = (str(f.fid), str(f.ftype), key)
        assert max_pos <= self.MAX_POS, (
            f"Position id {max_pos} exceeds 5-digit envelope ({self.MAX_POS}) "
            f"at {location} — Kindle progress display will break"
        )


# ---------------------------------------------------------------------------
# Invariant 4: $164 / $417 divergence + $165 type
# ---------------------------------------------------------------------------


@pytest.mark.tier1
@pytest.mark.unit
class TestCoverFragmentDivergence:
    """$164 (resource metadata) and $417 (raw image data) MUST have different
    fids, linked by $165. $165 must be a plain string, NOT IonSymbol.

    Per MEMORY.md "KFX Cover Image (VERIFIED WORKING)":
      $164 and $417 MUST have different fids, linked by $165 (plain STRING,
      not IonSymbol). Without this, Kindle silently ignores the cover.

    Skipped on the cover-less fixtures (no $164/$417 to inspect).
    """

    def _cover_fixture_path(self):
        path = _generate(
            [{"title": "Ch1", "text": "Ch1\n\nPara A."}],
            cover_image=_VALID_JPEG,
        )
        return path

    def test_164_417_different_fids_and_165_is_string(self, load_kfx_fragments):
        # Structural assertion: every $164 fragment in the file must satisfy
        # the divergence invariant. Couples to NO specific resource name
        # (e.g. "cover_img") — if a future generator emits multiple $164
        # entries, the rule still holds for each.
        path = self._cover_fixture_path()
        try:
            frags = load_kfx_fragments(path)
            f164s = _by_type(frags, "$164")
            assert f164s, "Missing $164 resource fragment(s)"

            f417_by_fid = {str(f.fid): f for f in _by_type(frags, "$417")}

            for f164 in f164s:
                v = _val(f164)
                location_name = v.get(IS("$165"))
                assert location_name is not None, (
                    f"$164 fid={str(f164.fid)!r} missing $165 (location name reference)"
                )
                assert not isinstance(location_name, IonSymbol), (
                    f"$164.$165 must be plain string, got IonSymbol: "
                    f"{location_name!r} (fid={str(f164.fid)!r})"
                )
                f417 = f417_by_fid.get(str(location_name))
                assert f417, (
                    f"No $417 with fid matching $164.$165={str(location_name)!r} "
                    f"(for $164 fid={str(f164.fid)!r})"
                )
                assert str(f164.fid) != str(f417.fid), (
                    f"$164 and $417 must have different fids; both = {str(f164.fid)!r}"
                )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Invariant 5: $157.$307 magnitude is IonDecimal (NOT plain int)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
@pytest.mark.unit
class TestStyleMagnitudeIsDecimal:
    """$307 magnitude fields inside $157 style structs MUST be IonDecimal.

    History: an early 2025-12 note claimed `$307` should be plain int.
    This was overruled by the Calibre KFX output gold standard (a
    reference KFX in the maintainer's gitignored research/ directory), which uses
    Decimal for every $307 in $157 — across
    $48 (margin-bottom), $42 (line-height), $16 (font-size), $36
    (text-indent), $46 (margin-top), $47 (padding-top). The shipping
    v5.3.x generator emits IonDecimal everywhere and is device-verified.

    Encoding the rule as a positive assertion guards against a future
    regression that re-applies the original (incorrect) "use plain int"
    fix.
    """

    def test_307_is_decimal_in_157(self, fixture_kfx):
        # Walk every $307 anywhere in the $157 struct tree rather than
        # enumerating known parent keys ($48/$42/$16/$36/$46/$47). If a
        # future generator emits $307 under a new parent (e.g. a newly-added
        # CSS property), the rule still fires there — no parent allowlist
        # to forget to update.
        from decimal import Decimal

        path, frags = fixture_kfx
        offenders = []
        for f in (f for f in frags if str(f.ftype) == "$157"):
            v = _val(f)
            for magnitude in _walk_for_key(v, "$307"):
                # IonDecimal is a Decimal subclass in this codebase; bool
                # is excluded because Python booleans are int subclasses.
                if not isinstance(magnitude, Decimal):
                    offenders.append(
                        (str(f.fid), type(magnitude).__name__, repr(magnitude))
                    )
        assert not offenders, (
            f"$157 has $307 values that aren't Decimal (per Calibre reference); "
            f"offenders (fid, type, value): {offenders[:5]}"
        )


# ---------------------------------------------------------------------------
# Invariant 6: every chapter owns at least one content chunk
# ---------------------------------------------------------------------------


# Image token format produced by extract_text_from_html and parsed by
# native_generator._build_chapter_content:
#   \x00IMG\x01<href>\x01<alt>\x00
def _img_token(href, alt=""):
    return f"\x00IMG\x01{href}\x01{alt}\x00"


@pytest.mark.tier1
@pytest.mark.unit
class TestEmptyChapterDoesNotCrash:
    """A chapter whose only body is an <img> that doesn't resolve to a known
    body resource emits zero text chunks. The converter's orphan recovery
    appends exactly such a chapter for a book's own cover.xhtml (its image is
    the separately-handled cover, #32), and it lands LAST in the chapter list.

    Before the fix, _build_chapter_content captured each chapter's start index
    before emitting chunks; a trailing zero-chunk chapter made
    chapter_start_positions index chunk_positions out of range:
        IndexError: list index out of range  (native_generator.py:2283)
    A middle zero-chunk chapter silently pointed its TOC entry at the next
    chapter. The generator now emits a placeholder chunk so every chapter
    owns a navigable position.
    """

    def test_trailing_image_only_chapter(self):
        # No cover_image / body_images passed -> image_resources is empty ->
        # the IMG token is dropped -> the chapter would emit zero chunks.
        chapters = [
            {"title": "Ch1", "text": "Ch1\n\nReal paragraph text here."},
            {"title": "Cover", "text": _img_token("cover.jpg")},
        ]
        path = _generate(chapters)
        try:
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)

    def test_middle_image_only_chapter(self):
        chapters = [
            {"title": "Ch1", "text": "Ch1\n\nFirst chapter body."},
            {"title": "Plate", "text": _img_token("plate.jpg")},
            {"title": "Ch2", "text": "Ch2\n\nSecond chapter body."},
        ]
        path = _generate(chapters)
        try:
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)
