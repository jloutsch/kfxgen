"""
Tier-1 unit tests for Deserializer.extract length-field bound (#47).

Threat: a crafted Ion binary with length field = 0x7FFFFFFF would historically
let `serial.extract(length)` attempt to address ~2 GB of memory in Calibre's
plugin process. Python's slice-doesn't-pre-allocate behaviour saved us today,
but the defense should be explicit (catch BEFORE the slice, with a security-
relevant error message) so it survives future refactors.

The choke point is utilities.Deserializer.extract — every length-bounded
read in ion_binary.py funnels through it (lines 113, 127, 374, 415).
struct.unpack_from sites use fixed-format strings (no input-driven length);
deserialize_vluint reads byte-by-byte. Patching extract is sufficient.
"""

import logging
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.kfxlib_minimal import utilities
from kfxgen.kfxlib_minimal.utilities import Deserializer, MAX_DECODE_SIZE


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerHappyPath:
    """Sanity: legitimate uses are not regressed."""

    def test_extract_normal_size(self):
        ds = Deserializer(b"hello world")
        assert ds.extract(5) == b"hello"
        assert ds.extract(6) == b" world"

    def test_extract_default_size_consumes_remaining(self):
        ds = Deserializer(b"abc")
        assert ds.extract() == b"abc"

    def test_extract_zero_size(self):
        ds = Deserializer(b"abc")
        assert ds.extract(0) == b""
        # Offset unchanged.
        assert ds.extract() == b"abc"


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerOversizedRejected:
    """Crafted oversized length fields raise a bounded, security-tagged
    exception BEFORE any slice attempt — defends against the 2 GB attack
    described in #47."""

    # These tests couple to the exact error message strings raised by
    # Deserializer.extract. The kfxlib_minimal/ vendored module uses generic
    # Exception throughout; defining a DeserializerBoundError subclass would
    # expand the diff into vendored library code with merge friction against
    # future Calibre kfxlib syncs (see project_council_p0_security.md
    # "Vendored library modification" pattern). If error messages get
    # rewritten, update the match= patterns here in the same commit.
    def test_size_above_bound_rejected(self):
        ds = Deserializer(b"x" * 100)
        with pytest.raises(Exception, match="security limit"):
            ds.extract(MAX_DECODE_SIZE + 1)

    def test_2gb_attack_string_rejected(self):
        # Verbatim attack from #47: length field = 0x7FFFFFFF (~2 GB).
        ds = Deserializer(b"x" * 100)
        with pytest.raises(Exception, match="security limit"):
            ds.extract(0x7FFFFFFF)

    def test_offset_unchanged_on_rejection(self):
        ds = Deserializer(b"abcdef")
        ds.extract(2)  # consume 2 bytes
        assert ds.offset == 2
        with pytest.raises(Exception, match="security limit"):
            ds.extract(MAX_DECODE_SIZE + 1)
        # Rejection must not advance offset.
        assert ds.offset == 2


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerNegativeSize:
    """Negative length values get a distinct error path (was previously
    conflated with 'insufficient data' in a combined check)."""

    def test_negative_size_rejected(self):
        ds = Deserializer(b"abc")
        with pytest.raises(Exception, match="Negative size"):
            ds.extract(-1)

    def test_large_negative_size_rejected(self):
        ds = Deserializer(b"abc")
        with pytest.raises(Exception, match="Negative size"):
            ds.extract(-(2**31))


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerUptoBeforeOffset:
    """`upto` < current offset yields a negative computed size; the bound
    check must catch that path explicitly (review follow-up to #47)."""

    def test_upto_before_offset_rejected(self):
        ds = Deserializer(b"abcdef")
        ds.extract(4)  # offset = 4
        with pytest.raises(Exception, match="Negative size"):
            ds.extract(upto=2)

    def test_upto_equal_offset_returns_empty(self):
        # Boundary: upto == offset → size 0, valid no-op.
        ds = Deserializer(b"abcdef")
        ds.extract(3)
        assert ds.extract(upto=3) == b""
        assert ds.offset == 3


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerInsufficientData:
    """Pre-existing 'insufficient data' check still fires for legitimate
    truncation (bound check shouldn't swallow this case)."""

    def test_insufficient_data_still_raises(self):
        ds = Deserializer(b"abc")
        with pytest.raises(Exception, match="Insufficient data"):
            ds.extract(10)


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerBoundIsConfigurable:
    """MAX_DECODE_SIZE is a module attribute so tests (and ops, in a pinch)
    can adjust it without code changes. Verify the patched value takes
    effect at call time, not import time."""

    def test_monkeypatched_smaller_bound(self, monkeypatch):
        monkeypatch.setattr(utilities, "MAX_DECODE_SIZE", 16)
        ds = Deserializer(b"x" * 100)
        # 16 bytes is fine.
        assert len(ds.extract(16)) == 16
        # 17 bytes blocked.
        ds2 = Deserializer(b"x" * 100)
        with pytest.raises(Exception, match="security limit"):
            ds2.extract(17)


@pytest.mark.tier1
@pytest.mark.unit
class TestMaxDecodeSizeEnvOverride:
    """`KFXGEN_MAX_DECODE_SIZE` env var lets ops raise/lower the bound for
    legitimate-large-input cases (advanced/trusted-input only — see
    SECURITY.md). Verified via the helper so we don't have to reload the
    module to exercise import-time parsing."""

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("KFXGEN_MAX_DECODE_SIZE", raising=False)
        assert utilities._read_max_decode_size_override() == 64 * 1024 * 1024

    def test_smaller_bound_respected(self, monkeypatch):
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", "131072")
        assert utilities._read_max_decode_size_override() == 131072

    def test_larger_bound_respected(self, monkeypatch):
        # Trusted-input case: 256 MB.
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", "268435456")
        assert utilities._read_max_decode_size_override() == 268435456

    def test_invalid_string_falls_back(self, monkeypatch):
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", "invalid")
        assert utilities._read_max_decode_size_override() == 64 * 1024 * 1024

    def test_zero_falls_back(self, monkeypatch):
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", "0")
        assert utilities._read_max_decode_size_override() == 64 * 1024 * 1024

    def test_negative_falls_back(self, monkeypatch):
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", "-1")
        assert utilities._read_max_decode_size_override() == 64 * 1024 * 1024


@pytest.mark.tier1
@pytest.mark.unit
class TestMaxDecodeSizeHardCeiling:
    """Hard ceiling on `KFXGEN_MAX_DECODE_SIZE` override. Documentation
    alone is not a guardrail — a misconfigured
    `KFXGEN_MAX_DECODE_SIZE=2147483647` would silently re-enable the
    original #47 attack. Clamp at 1 GB so the attack surface stays
    bounded regardless of operator config."""

    HARD_CEILING = 1024 * 1024 * 1024  # 1 GB; mirrors module constant.

    def test_value_above_ceiling_clamps_with_warning(self, monkeypatch, caplog):
        # 2 GB — well above ceiling. Mirrors the original #47 attack
        # value if it were configured as a "trusted" override.
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", str(2 * 1024 * 1024 * 1024))
        with caplog.at_level(logging.WARNING, logger=utilities._security_log.name):
            result = utilities._read_max_decode_size_override()
        assert result == self.HARD_CEILING
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ceiling" in r.getMessage().lower() for r in warning_records), (
            f"expected ceiling warning, got: {[r.getMessage() for r in warning_records]}"
        )

    def test_value_at_ceiling_accepted(self, monkeypatch):
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", str(self.HARD_CEILING))
        assert utilities._read_max_decode_size_override() == self.HARD_CEILING

    def test_value_just_under_ceiling_accepted(self, monkeypatch):
        monkeypatch.setenv("KFXGEN_MAX_DECODE_SIZE", str(self.HARD_CEILING - 1))
        assert utilities._read_max_decode_size_override() == self.HARD_CEILING - 1


@pytest.mark.tier1
@pytest.mark.unit
def test_module_level_constant_reflects_env_var():
    """Verify `utilities.MAX_DECODE_SIZE` (the module-level constant
    `Deserializer.extract` actually reads) reflects the env override on
    a fresh import. The 6 helper-function tests above could all pass
    against a regression that hard-codes `MAX_DECODE_SIZE = 64*1024*1024`
    after the helper call — only a fresh-import check catches that.

    Subprocess so the import happens with `KFXGEN_MAX_DECODE_SIZE`
    already in env; pytest's import caching would otherwise mask the
    regression.
    """
    project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    code = textwrap.dedent("""
        import os
        import sys

        # Env MUST be set before utilities is imported — that's the whole
        # point of this test.
        os.environ["KFXGEN_MAX_DECODE_SIZE"] = "131072"

        sys.path.insert(0, "plugin")
        from kfxgen.kfxlib_minimal import utilities

        assert utilities.MAX_DECODE_SIZE == 131072, (
            "expected 131072, got %d" % utilities.MAX_DECODE_SIZE
        )
        print("OK")
    """)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout, f"unexpected stdout: {result.stdout!r}"
