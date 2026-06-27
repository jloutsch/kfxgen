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
- **Byte-identical diff (opt-in, `tier3_strict`)** — SHA-256 the file.
  Run with `pytest -m tier3_strict`. Verifies that the generator is
  bit-stable across runs of the same input. Enabled by #89, which
  replaced the random Container ID + ASIN with content-derived
  deterministic IDs.

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

    Enabled by #89 (deterministic Container ID + ASIN). If you bump
    one of those derivations, expect this test to fail until you
    regenerate the goldens via `python -m tests.fixtures.golden.regenerate`."""
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
