"""
Tier-3 golden-file corpus diff (#48).

For each fixture in `tests.fixtures.golden.inputs.GOLDEN_INPUTS`:

1. Build a fresh KFX through the full production pipeline
   (`converter.convert_oeb_to_kfx`).
2. Diff that fresh build against the committed golden under
   `tests/fixtures/golden/expected/<name>.kfx`.

Two diff layers:

- **Structural diff (default, `tier3`)** — load both via `load_fragments`,
  compare fragment-type multiset and per-fragment-type top-level key
  set. Tolerant of benign byte-level reorderings; fails on shape
  regressions like a missing fragment type or a renamed key.
- **Byte-identical diff (`tier3_strict`)** — SHA-256 the file.
  Excluded from `pytest.ini`'s `addopts`, so a plain `pytest` skips it;
  CI runs it as its own step and it gates every PR. Verifies that the
  generator is bit-stable across runs of the same input. Possible
  because Container ID and ASIN are derived from book content rather
  than randomly (`native_generator.py:1964`), and because #96 removed
  the last source of run-to-run drift.

A third class of test, `test_fixture_exercises_target_shape`, asserts
that each fixture still emits the structural element it was designed
to lock (e.g. body_images must have `$259` entries with `$175` image
refs). This guards against fixture rot — if the input stops triggering
the regression class it claims to cover, the fixture loses its value
and the test should fail loudly rather than passing as a same-shape
golden.

Updating goldens after an intentional generator change:
    python -m tests.fixtures.golden.regenerate
    pytest -m tier3
    git add tests/fixtures/golden/expected/
See CONTRIBUTING.md for the full procedure.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen import converter  # noqa: E402
from kfxgen.kfxlib_minimal.ion import IS  # noqa: E402

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.fixtures.golden.inputs import GOLDEN_INPUTS  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

EXPECTED_DIR = Path(__file__).parent.parent / "fixtures" / "golden" / "expected"


from tests._helpers import NullLog as _NullLog  # noqa: E402


def _build_fresh(name: str, builder, work_dir: Path) -> bytes:
    """Run the same pipeline regenerate.py uses. Kept in lockstep with
    `tests/fixtures/golden/regenerate.py::build_kfx` — divergence here
    means the test no longer reproduces the regenerate path."""
    out_dir = work_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    epub_path = builder(out_dir)
    oeb = EpubAsOeb(epub_path)
    kfx_path = out_dir / f"{name}.kfx"
    converter.convert_oeb_to_kfx(oeb, str(kfx_path), opts=None, log=_NullLog())
    return kfx_path.read_bytes()


def _structural_fingerprint(kfx_bytes: bytes) -> tuple[Counter, dict[str, frozenset]]:
    """Reduce a KFX file to (fragment-type counts, per-type top-level key sets).

    The returned tuple is value-comparable so two fingerprints can be
    diffed with `==`. Failure messages walk the structures to point at
    the specific fragment-type or key that diverged.

    Sensitivity caveat: the key set for each fragment type is the
    *union* over all instances of that type. So a regression that adds
    a spurious key to even one fragment is caught (the union grows),
    but a regression that *removes* a key from a single instance of a
    multi-instance type is NOT caught as long as at least one other
    instance still carries that key. The per-fixture shape assertions
    below compensate for the cases that matter (image resource refs,
    cover fragments, section count); for new fixture classes that need
    per-instance key strictness, add a targeted shape assertion.
    """
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
        f.write(kfx_bytes)
    try:
        frags = load_fragments(path)
    finally:
        os.unlink(path)

    type_counts: Counter = Counter(str(f.ftype) for f in frags)

    keys_by_type: dict[str, set[str]] = {}
    for f in frags:
        ftype = str(f.ftype)
        v = val(f)
        if hasattr(v, "keys"):
            for k in v.keys():
                keys_by_type.setdefault(ftype, set()).add(str(k))
    keys_frozen = {t: frozenset(ks) for t, ks in keys_by_type.items()}
    return type_counts, keys_frozen


def _format_structural_diff(
    name: str,
    fresh: tuple[Counter, dict[str, frozenset]],
    golden: tuple[Counter, dict[str, frozenset]],
) -> str:
    """Render a per-fragment-type breakdown of where two fingerprints
    differ. Returned string is the assertion message."""
    fresh_counts, fresh_keys = fresh
    golden_counts, golden_keys = golden
    lines = [f"Structural mismatch on golden {name!r}:"]

    all_types = sorted(set(fresh_counts) | set(golden_counts))
    for t in all_types:
        fc = fresh_counts.get(t, 0)
        gc = golden_counts.get(t, 0)
        if fc != gc:
            lines.append(f"  count[{t}]: fresh={fc} golden={gc}")
        fk = fresh_keys.get(t, frozenset())
        gk = golden_keys.get(t, frozenset())
        added = fk - gk
        removed = gk - fk
        if added:
            lines.append(f"  keys[{t}] added: {sorted(added)}")
        if removed:
            lines.append(f"  keys[{t}] removed: {sorted(removed)}")
    lines.append(
        "To accept these changes intentionally: run "
        "`python -m tests.fixtures.golden.regenerate` and commit the updated "
        "expected/ files. See CONTRIBUTING.md."
    )
    return "\n".join(lines)


@pytest.mark.tier3
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_golden_structural_diff(name, builder, tmp_path):
    """A fresh build must match the committed golden at the structural level
    (fragment-type counts + per-type key sets). Tolerates byte-level
    reorderings; catches shape regressions."""
    fresh_bytes = _build_fresh(name, builder, tmp_path)
    golden_path = EXPECTED_DIR / f"{name}.kfx"
    assert golden_path.exists(), (
        f"Golden file missing: {golden_path}. Run "
        f"`python -m tests.fixtures.golden.regenerate` to seed it."
    )
    golden_bytes = golden_path.read_bytes()

    fresh_fp = _structural_fingerprint(fresh_bytes)
    golden_fp = _structural_fingerprint(golden_bytes)

    if fresh_fp != golden_fp:
        pytest.fail(_format_structural_diff(name, fresh_fp, golden_fp))


@pytest.mark.tier3_strict
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_golden_byte_identical(name, builder, tmp_path):
    """A fresh build must be byte-for-byte identical to the committed
    golden. Run with `pytest -m tier3_strict`. Asserts the generator
    is bit-stable across runs — the strongest "this PR didn't drift
    output" signal available below device verification.

    Rests on Container ID and ASIN being content-derived rather than
    random (`native_generator.py:1964`). If you bump one of those
    derivations, expect this test to fail until you regenerate the
    goldens via `python -m tests.fixtures.golden.regenerate`."""
    import hashlib

    fresh_bytes = _build_fresh(name, builder, tmp_path)
    golden_path = EXPECTED_DIR / f"{name}.kfx"
    assert golden_path.exists(), f"Golden file missing: {golden_path}"

    fresh_sha = hashlib.sha256(fresh_bytes).hexdigest()
    golden_sha = hashlib.sha256(golden_path.read_bytes()).hexdigest()
    assert fresh_sha == golden_sha, (
        f"Byte-identical mismatch on golden {name!r}:\n"
        f"  fresh  sha256: {fresh_sha}  ({len(fresh_bytes)} bytes)\n"
        f"  golden sha256: {golden_sha}  ({golden_path.stat().st_size} bytes)\n"
        f"Run `pytest -m tier3` to see what changed at the structural level. "
        f"To accept intentional changes: regenerate goldens."
    )


# ---------------------------------------------------------------------------
# Per-fixture shape assertions: guard against fixture rot.
#
# Each entry pins a property the fixture was designed to exercise. If the
# fixture stops emitting that shape, the structural diff alone wouldn't
# catch it (both fresh and golden would lose the shape together), so we
# encode the shape independently here.
# ---------------------------------------------------------------------------


def _count_image_resource_entries(frags) -> int:
    """Number of $259 leaf entries that carry a $175 resource ref."""
    n = 0
    for f in by_type(frags, "$259"):
        v = val(f)
        outers = v.get(IS("$146")) or v.get(IS("$181")) or []
        for outer in outers:
            if not hasattr(outer, "get"):
                continue
            nested = outer.get(IS("$146")) or [outer]
            for e in nested:
                if hasattr(e, "get") and e.get(IS("$175")) is not None:
                    n += 1
    return n


@pytest.mark.tier3
@pytest.mark.integration
def test_fixture_minimal_shape():
    """minimal: 3 sections, no images."""
    frags = load_fragments(EXPECTED_DIR / "minimal.kfx")
    assert len(by_type(frags, "$260")) == 3, "minimal must have 3 $260 sections"
    assert _count_image_resource_entries(frags) == 0


@pytest.mark.tier3
@pytest.mark.integration
def test_fixture_body_images_shape():
    """body_images: at least 2 $259 image entries (with $175 resource ref)."""
    frags = load_fragments(EXPECTED_DIR / "body_images.kfx")
    assert _count_image_resource_entries(frags) >= 2, (
        "body_images must emit at least two $259 image entries — fixture rotted"
    )


@pytest.mark.tier3
@pytest.mark.integration
def test_fixture_captioned_images_shape():
    """captioned_images: both images survive sharing a container with a block
    sibling (#113).

    Asserts resources *and* refs. Resources alone would not catch a regression:
    an image whose ref is lost becomes an orphan, and the #102 drop rule then
    deletes the resource too, so the count would fall silently rather than
    leaving evidence behind.
    """
    frags = load_fragments(EXPECTED_DIR / "captioned_images.kfx")
    assert len(by_type(frags, "$164")) == 2, (
        "captioned_images must emit exactly two $164 resources — an image "
        "sharing a container with a caption div was dropped (#113)"
    )
    assert _count_image_resource_entries(frags) == 2, (
        "captioned_images must emit two $259 entries carrying $175 — the "
        "images are in the container but nothing draws them"
    )


@pytest.mark.tier3
@pytest.mark.integration
def test_fixture_with_cover_shape():
    """with_cover: at least one $164 cover-image fragment + one $417 payload."""
    frags = load_fragments(EXPECTED_DIR / "with_cover.kfx")
    assert len(by_type(frags, "$164")) >= 1, "with_cover must emit a $164 fragment"
    assert len(by_type(frags, "$417")) >= 1, "with_cover must emit a $417 payload"


@pytest.mark.tier3
@pytest.mark.integration
def test_fixture_multi_chapter_shape():
    """multi_chapter: 8 sections (exercises larger-corpus path)."""
    frags = load_fragments(EXPECTED_DIR / "multi_chapter.kfx")
    assert len(by_type(frags, "$260")) == 8, (
        "multi_chapter must have 8 $260 sections — fixture rotted"
    )


#: Link spans publisher_structure must emit. An exact number so a silently
#: dropped link fails; update deliberately when the fixture gains a link.
#:
#: Five from the navigation document (both part1 ids, the note, the whole-file
#: afterword href, and the endnote appendix) plus two from part1's body (the
#: <sup> marker and the cross-folder prose link, which share a target and so
#: resolve to one anchor between them).
#:
#: Deleting the anchor-carry in `native_generator` drops this to 5, not 6: the
#: appendix link dies (its target id is on the elided heading — the #62 case),
#: and so does the whole-file `back/afterword.xhtml` link, because that
#: chapter's `<h1>Afterword</h1>` is elided too and takes the bare-filename
#: key with it. The carry protects whole-file targets as well as fragments.
EXPECTED_PUBLISHER_LINKS = 7

#: Distinct `$266` anchors those links resolve to — one per unique target.
#: Asserted alongside the link count because a link and its anchor are emitted
#: from different code paths; a count that moves without the other moving is a
#: resolution bug, not a fixture edit.
EXPECTED_PUBLISHER_ANCHORS = 5


@pytest.mark.integration
def test_fixture_publisher_structure_shape(tmp_path):
    """Content assertions for the publisher_structure fixture.

    The structural fingerprint above compares fragment-type counts and key
    sets, and deliberately tolerates text changes — which is exactly why the
    golden corpus passed straight through a regression that discarded 44% of a
    book's body text (#58). Text-level damage needs asserting directly.
    """
    from kfxgen.kfxlib_minimal.ion import IS
    from tests._kfx_introspect import by_type, load_fragments, val
    from tests.fixtures.golden.inputs import make_publisher_structure

    # Build from the CURRENT code, not the committed golden — otherwise a
    # regression in the converter would leave the golden untouched and this
    # test would pass while the product was broken.
    fresh = _build_fresh("publisher_structure", make_publisher_structure, tmp_path)
    written = tmp_path / "fresh_ps.kfx"
    written.write_bytes(fresh)
    frags = load_fragments(written)
    texts = [
        x
        for f in by_type(frags, "$145")
        for x in (val(f).get(IS("$146")) or [])
        if isinstance(x, str)
    ]
    # #58: each TOC entry is its own block. Flattening merges a Part heading
    # and every child entry into one run-on paragraph.
    assert not any("PART I" in t and "First Chapter" in t for t in texts), (
        f"nested list flattened into one block: {texts}"
    )
    # #58 (second half): a container's own inline text must survive alongside
    # its block children.
    assert any("Lead-in text" in t for t in texts), (
        "inline text of a container with block children was dropped"
    )
    # #64: the chapter whose nav title is "3. Split Opener" prints the numeral
    # and title as separate blocks; the synthesized heading must replace them,
    # not sit on top of them.
    assert "Split Opener" not in [t.strip() for t in texts], (
        f"split chapter opener duplicated below the heading: {texts}"
    )
    assert "3. Split Opener" in [t.strip() for t in texts], "heading missing"
    # #60: hidden landmarks/page-list navs are markup, not reading content.
    # Live only because the nav document is not titled "Contents" — under that
    # title its blocks are discarded wholesale and this passes vacuously (#76).
    assert not any(t.strip() in ("Page List", "Begin Reading") for t in texts), (
        f"hidden nav leaked into the body: {texts}"
    )
    # Every link must resolve, and the anchor for the elided appendix heading
    # must be among them — reverting the anchor-carry drops it (#62, #76).
    anchors = {
        str(val(x)[IS("$180")]) for x in by_type(frags, "$266") if IS("$180") in val(x)
    }
    targets = [
        str(sp[IS("$179")])
        for x in by_type(frags, "$259")
        for e in (val(x).get(IS("$146")) or [])
        for sp in (e.get(IS("$142")) or [])
        if IS("$179") in sp
    ]
    # An exact count, not just "none dangling". kfxgen DROPS a link whose
    # target will not resolve rather than emitting a dangling $179, so
    # `set(targets) - anchors` stays empty even if every link vanishes — the
    # absence check alone is satisfied by emitting nothing at all (#79).
    assert len(targets) == EXPECTED_PUBLISHER_LINKS, (
        f"expected {EXPECTED_PUBLISHER_LINKS} link spans, got {len(targets)}. "
        "A drop shows up here and nowhere else."
    )
    assert not (set(targets) - anchors), (
        f"links resolve to nothing: {sorted(set(targets) - anchors)}"
    )
    assert len(anchors) == EXPECTED_PUBLISHER_ANCHORS, (
        f"expected {EXPECTED_PUBLISHER_ANCHORS} anchors, got {len(anchors)}. "
        "An anchor lost without a link lost means a target stopped resolving."
    )
    # It legitimately appears twice — once as a navigation entry, once as the
    # chapter heading. Duplication looks like two *adjacent* copies.
    assert not any(
        a.strip() == b.strip() == "Endnote Appendix" for a, b in zip(texts, texts[1:])
    ), f"back-matter heading duplicated below its own title: {texts}"


#: The format's per-`$145` ceiling, and the metric upstream `kfxlib` applies
#: to it (`yj_structure.py`): the LAST string of a fragment is excluded from
#: the total, and the comparison is `>=` — a fragment measuring exactly 8192
#: is already an error, not the last passing value. Asserting against a naive
#: sum of every string would be both wrong and stricter than the format.
MAX_CONTENT_FRAGMENT_SIZE = 8192


def _upstream_content_bytes(strings) -> int:
    return sum(len(s.encode("utf-8")) for s in strings[:-1])


def _content_fragment_strings(frags):
    """Every `$145` fragment's string list, in file order."""
    from kfxgen.kfxlib_minimal.ion import IS

    return [
        [s for s in (val(f).get(IS("$146")) or []) if isinstance(s, str)]
        for f in by_type(frags, "$145")
    ]


@pytest.mark.tier3
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_no_content_fragment_exceeds_the_format_maximum(name, builder, tmp_path):
    """No fixture may emit an oversized `$145` (#37).

    Built from current code rather than the committed golden: a regression in
    the generator leaves the golden untouched, so checking the golden would
    pass while the product was broken.
    """
    fresh = _build_fresh(name, builder, tmp_path)
    written = tmp_path / f"fresh_{name}.kfx"
    written.write_bytes(fresh)

    oversized = [
        (i, _upstream_content_bytes(strings), len(strings))
        for i, strings in enumerate(_content_fragment_strings(load_fragments(written)))
        if _upstream_content_bytes(strings) >= MAX_CONTENT_FRAGMENT_SIZE
    ]
    assert not oversized, (
        f"{name}: {len(oversized)} $145 fragment(s) at or over the "
        f"{MAX_CONTENT_FRAGMENT_SIZE}-byte maximum: {oversized}"
    )


@pytest.mark.tier3
@pytest.mark.integration
def test_fixture_long_chapter_shape(tmp_path):
    """long_chapter must actually exceed one fragment's worth of text.

    Without this the fixture could be trimmed to a short book, keep passing
    the cap assertion above, and stop covering #37 entirely — the same
    fixture-rot failure that let #76 sit behind three vacuous assertions.
    """
    from tests.fixtures.golden.inputs import make_long_chapter

    fresh = _build_fresh("long_chapter", make_long_chapter, tmp_path)
    written = tmp_path / "fresh_long.kfx"
    written.write_bytes(fresh)
    frags = load_fragments(written)

    groups = _content_fragment_strings(frags)
    assert len(groups) > 2, (
        f"long_chapter emitted {len(groups)} $145 fragments — its chapters no "
        "longer overflow the cap, so the split path is untested"
    )
    total = sum(len(s.encode("utf-8")) for g in groups for s in g)
    assert total > 3 * MAX_CONTENT_FRAGMENT_SIZE, (
        f"long_chapter holds only {total} bytes of text; it must stay several "
        "fragments' worth so an off-by-one in the budget still shows up"
    )

    # Every text entry must address a real string. A splitter that mis-maps an
    # index silently serves the wrong paragraph — no decoder flags that, and
    # the byte-size assertion above would still pass.
    from kfxgen.kfxlib_minimal.ion import IS

    # The name lives on the fragment id; the decoder does not keep a $4 copy
    # in the value.
    by_name = {
        str(f.fid): [s for s in (val(f).get(IS("$146")) or []) if isinstance(s, str)]
        for f in by_type(frags, "$145")
    }
    refs = 0
    for storyline in by_type(frags, "$259"):
        for entry in val(storyline).get(IS("$146")) or []:
            ref = entry.get(IS("$145"))
            if not ref:
                continue
            # $4 is a standard symbol, so the decoder resolves it to its text
            # ("name"); an unresolved container still yields the raw form.
            key = IS("$4") if IS("$4") in ref else IS("name")
            fragment_name = str(ref[key])
            index = ref[IS("$403")]
            assert fragment_name in by_name, f"entry points at unknown {fragment_name}"
            assert 0 <= index < len(by_name[fragment_name]), (
                f"$403 index {index} out of range for {fragment_name} "
                f"({len(by_name[fragment_name])} strings)"
            )
            refs += 1
    assert refs > 0, "no text entries carried a $145 reference"
