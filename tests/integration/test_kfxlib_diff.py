"""
Tier-2 differential decode against Calibre's upstream `kfxlib` (#50).

The synthetic golden corpus (#48) is decoded by two parsers:
  1. Our vendored subset `kfxgen.kfxlib_minimal`.
  2. The full upstream `kfxlib` extracted from Calibre's `KFX Input.zip`
     plugin (vendored in `tests/fixtures/vendor/kfx_input_plugin.zip`).

The two parsers share their pre-fork ancestry (both descend from
jhowell's kfxlib) but have evolved separately — divergence between
them surfaces generation bugs that one decoder happens to accept and
the other rejects. This is the council brief's "independent provenance"
oracle, complementing tier-3 (golden bytes) and tier-1 (in-process
invariants).

What this test deliberately tolerates:
    Upstream `kfxlib` emits warnings against the shipping generator's
    output that don't break Kindle on real devices —
    `position_id content extra at idx=N`, `location_map failed to
    locate eid 10000+`, `Feature/content mismatch: reflow-section-size`,
    etc. These predate this PR and represent parser-disagreement on
    format choices the device verifies as correct. Failing on them
    would block every CI run from day one with no actionable signal.
    A separate follow-up may tighten the warning-count budget once
    the legitimate device-verified noise floor is characterized.

What this test asserts:
    - Upstream `kfxlib.YJ_Book.decode_book()` does not raise.
    - The fragment-type set from upstream is a *subset* of the set
      from `kfxlib_minimal`. The reverse direction is intentionally
      not asserted: upstream's `decode_book()` runs a semantic pass
      that drops unreferenced fragments (e.g. `content_1`/`s0_h`
      after cover-chapter insertion), and those legitimately appear
      only in our raw decode. The catch-target is "upstream sees a
      type minimal misses" — that means our decoder has a gap.
    - Critical fragment types (`$490`, `$259`, `$260`, `$265`) are
      present in upstream's output. Catches "fragment got dropped
      silently" regressions.

Refresh procedure: see CONTRIBUTING.md → The upstream kfxlib copy.
"""

from __future__ import annotations

import os
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen import converter  # noqa: E402

from tests._kfx_introspect import load_fragments  # noqa: E402
from tests.fixtures.golden.inputs import GOLDEN_INPUTS  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

VENDOR_ZIP = (
    Path(__file__).parent.parent / "fixtures" / "vendor" / "kfx_input_plugin.zip"
)


from tests._helpers import NullLog as _NullLog  # noqa: E402


@pytest.fixture(scope="session")
def upstream_kfxlib(tmp_path_factory):
    """Extract the vendored Calibre KFX Input plugin once per session and
    expose its `kfxlib` module. Skips the test cleanly if the vendored
    zip is missing — happens in shallow clones or after a manual repo
    surgery; CI on a fresh checkout always has it."""
    if not VENDOR_ZIP.exists():
        pytest.skip(
            f"Upstream kfxlib zip not found at {VENDOR_ZIP}; "
            f"see CONTRIBUTING.md → The upstream kfxlib copy for refresh procedure."
        )

    extract_dir = tmp_path_factory.mktemp("kfxlib_upstream")
    with zipfile.ZipFile(VENDOR_ZIP) as zf:
        zf.extractall(extract_dir)

    # `kfxlib` lives at <extract>/kfxlib/, with bundled deps (pypdf,
    # BeautifulSoup, etc.) under kfxlib/calibre-plugin-modules/. Both
    # paths must be on sys.path for the imports to resolve.
    kfxlib_root = str(extract_dir)
    plugin_modules = str(extract_dir / "kfxlib" / "calibre-plugin-modules")
    sys.path.insert(0, kfxlib_root)
    sys.path.insert(0, plugin_modules)

    try:
        import kfxlib.yj_book as yj_book

        yield yj_book.YJ_Book
    finally:
        # Tidy sys.path so the upstream kfxlib doesn't leak into other
        # tests in the same session.
        for path in (kfxlib_root, plugin_modules):
            if path in sys.path:
                sys.path.remove(path)
        # Drop any cached `kfxlib*` modules so re-runs in a long-lived
        # interpreter (rare for pytest, but safe) get a fresh import.
        for mod_name in [m for m in sys.modules if m.startswith("kfxlib")]:
            del sys.modules[mod_name]


@pytest.fixture(scope="session")
def built_kfx(tmp_path_factory):
    """Build each golden-corpus KFX once per session; cache by fixture name.

    Three test functions parametrize over `GOLDEN_INPUTS`, so without
    caching each (name, builder) pair would run the full
    `convert_oeb_to_kfx` pipeline three times. This fixture lazily
    builds on first request and returns a closure callers invoke as
    `kfx_path = built_kfx(name, builder)`.
    """
    work_dir = tmp_path_factory.mktemp("kfxlib_diff_kfxs")
    cache: dict[str, Path] = {}

    def _build(name: str, builder) -> Path:
        if name not in cache:
            out_dir = work_dir / name
            out_dir.mkdir(parents=True, exist_ok=True)
            epub_path = builder(out_dir)
            oeb = EpubAsOeb(epub_path)
            kfx_path = out_dir / f"{name}.kfx"
            converter.convert_oeb_to_kfx(oeb, str(kfx_path), opts=None, log=_NullLog())
            cache[name] = kfx_path
        return cache[name]

    return _build


@pytest.fixture(scope="session")
def decoded_upstream(upstream_kfxlib, built_kfx):
    """Decode each golden-corpus KFX through upstream kfxlib once per
    session and cache the decoded `YJ_Book`. Same caching rationale as
    `built_kfx`: three parametrized tests would otherwise call
    `decode_book()` three times per fixture for output that doesn't
    change between test functions."""
    cache: dict[str, object] = {}

    def _decode(name: str, builder):
        if name not in cache:
            kfx_path = built_kfx(name, builder)
            book = upstream_kfxlib(str(kfx_path))
            book.decode_book()
            cache[name] = book
        return cache[name]

    return _decode


def _minimal_fragment_type_counts(kfx_path: Path) -> Counter:
    """Decode via our vendored `kfxlib_minimal` and return a Counter of
    fragment types — the diff target for upstream's decoded fragments."""
    return Counter(str(f.ftype) for f in load_fragments(kfx_path))


@pytest.mark.tier2
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_upstream_kfxlib_decodes_without_raising(name, builder, decoded_upstream):
    """Upstream kfxlib must decode every golden-corpus input without
    raising. Warnings/errors logged by kfxlib are tolerated (see module
    docstring); only an outright exception fails the test."""
    book = decoded_upstream(name, builder)  # raises on hard parse failure
    assert book.fragments, f"upstream kfxlib decoded {name} but found 0 fragments"


@pytest.mark.tier2
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_minimal_decoder_is_superset_of_upstream(
    name, builder, decoded_upstream, built_kfx
):
    """Every fragment-type that upstream kfxlib finds must also be
    present in our `kfxlib_minimal` decode of the same file. The
    reverse is NOT required: upstream's `decode_book()` runs a
    semantic pass that drops fragments it considers unreferenced
    (e.g. an unused `content_1`/`s0_h` pair after cover-chapter
    insertion). Those legitimately appear only in the minimal
    decoder's raw output.

    The directional invariant — `upstream ⊆ minimal` at the type
    level — catches the regression that matters: a generator change
    that emits something upstream accepts but our minimal decoder
    silently drops. If that ever happens, the minimal decoder has
    a gap and the rest of our tier-1 invariants would trust an
    incomplete view of the output."""
    kfx_path = built_kfx(name, builder)
    book = decoded_upstream(name, builder)
    upstream_counts = Counter(str(f.ftype) for f in book.fragments)
    minimal_counts = _minimal_fragment_type_counts(kfx_path)

    upstream_only_types = set(upstream_counts) - set(minimal_counts)
    assert not upstream_only_types, (
        f"Upstream kfxlib found fragment types that kfxlib_minimal did not "
        f"for {name!r}: {sorted(upstream_only_types)}. The minimal decoder "
        f"has a gap relative to upstream."
    )


@pytest.mark.tier2
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_critical_fragments_present(name, builder, decoded_upstream):
    """Fragment types the production runtime depends on must all be
    present in upstream-kfxlib's decoded output. Catches "$490 got
    dropped silently" / "$259 reading-order chain went missing"
    regressions that might pass our own decoder."""
    book = decoded_upstream(name, builder)

    # `$490` (book metadata), `$164` (resources — only when book has
    # cover/images), `$259`/`$260` (reading order), `$265` (position map).
    # `$164` is conditional: minimal/multi_chapter fixtures don't ship
    # a cover, so the resource fragment is optional for those.
    types_seen = {str(f.ftype) for f in book.fragments}
    required = {"$490", "$259", "$260", "$265"}
    missing = required - types_seen
    assert not missing, (
        f"Upstream kfxlib decoded {name} but the output is missing "
        f"required fragment types: {sorted(missing)}"
    )


@pytest.mark.tier2
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_upstream_reports_no_oversized_content_fragments(
    name, builder, upstream_kfxlib, built_kfx
):
    """Upstream kfxlib must log no "exceeds maximum" error for any fixture (#37).

    This is the authoritative oracle for the content-size cap. The tier-3
    assertion re-implements upstream's metric — the last string of a fragment
    excluded, `>=` rather than `>` — and a re-implementation can drift from
    what upstream actually enforces. Here upstream applies its own rule to our
    bytes, so the check cannot be wrong about the rule.

    Deliberately narrower than the module's blanket warning tolerance: this
    asserts on one specific error string rather than a total count, so the
    device-verified warning noise described above still passes through.
    """
    from kfxlib.message_logging import set_logger

    messages: list[str] = []
    set_logger(_MessageCollector(messages))
    try:
        book = upstream_kfxlib(str(built_kfx(name, builder)))
        book.decode_book()
    finally:
        set_logger(None)

    oversized = [m for m in messages if "exceeds maximum" in m]
    assert not oversized, (
        f"{name}: upstream kfxlib rejects {len(oversized)} content "
        f"fragment(s) as oversized: {oversized[:5]}"
    )


class _MessageCollector:
    """Minimal stand-in for the logger upstream kfxlib writes through.

    kfxlib routes every level through `message_logging.set_logger`, so any
    attribute access must return a callable that records.
    """

    def __init__(self, sink):
        self.sink = sink

    def _record(self, msg, *args, **kwargs):
        self.sink.append(str(msg) % args if args else str(msg))

    def __getattr__(self, _name):
        return self._record


@pytest.mark.tier2
@pytest.mark.integration
@pytest.mark.parametrize("name,builder", GOLDEN_INPUTS)
def test_upstream_reports_no_unreferenced_fragments(
    name, builder, upstream_kfxlib, built_kfx
):
    """Every fragment kfxgen emits must be reachable (#102).

    Upstream grades unreferenced fragments ERROR, and Amazon-produced files
    carry none. They are dead weight in the container, and — more usefully —
    a signal that something allocates a fragment it never fills.

    Asserted through upstream's own rule rather than a local reimplementation:
    a first attempt at computing "unreferenced" here reported zero on a file
    upstream flagged four times, because $270's entity map and $419's index
    enumerate every entity by design and make everything look referenced.
    """
    from kfxlib.message_logging import set_logger

    messages: list[str] = []
    set_logger(_MessageCollector(messages))
    try:
        book = upstream_kfxlib(str(built_kfx(name, builder)))
        book.decode_book()
    finally:
        set_logger(None)

    unreferenced = [m for m in messages if "Unreferenced fragments" in m]
    assert not unreferenced, f"{name}: {unreferenced[0]}"
