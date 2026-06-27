"""
Property-based fuzz for the Ion binary *deserializer* (#123 / G1, #126 / G4).

`test_ion_roundtrip.py` (#51) fuzzes the *serializer* with valid values. It
does not feed attacker-controlled bytes to the *decoder*. The only decoder
hardening before this module was the single hand-written length-bound case in
`test_deserializer_bound.py` (#47, the 2 GB `extract` attack).

This module closes that gap. It asserts the decoder is robust against
arbitrary / mutated bytes: every input either decodes or raises a *controlled*
exception. The two failures it hunts are uncontrolled crash classes:

  - `RecursionError`  — crafted deep nesting recursing past the interpreter
                        limit (the bug G1 found; see TestDeepNestingRejected).
  - `MemoryError`     — a lying length field allocating unbounded memory
                        (guarded by `MAX_DECODE_SIZE` from #47).

Hangs are caught by Hypothesis' per-example `deadline`; inputs are size-bounded
so a legitimate decode never approaches it.

Scope note: kfxgen is an *output* plugin and does not deserialize untrusted KFX
in normal operation. This is defense-in-depth on the same vendored decoder #47
hardened — the test suite and `_kfx_introspect` both decode generated KFX, and
the decoder is the one raw-byte attack surface in the codebase.

Tier-1 + unit (per #42 oracle hierarchy): runs entirely in-process, gates
every PR via the default CI selection.
"""

from __future__ import annotations

import decimal
import os
import sys

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.kfxlib_minimal import ion_binary  # noqa: E402
from kfxgen.kfxlib_minimal.ion import (  # noqa: E402
    IS,
    IonAnnotation,
    IonBLOB,
    IonCLOB,
    IonDecimal,
    IonList,
    IonStruct,
)
from kfxgen.kfxlib_minimal.ion_binary import IonBinary, serialize_vluint  # noqa: E402

from tests._helpers import (  # noqa: E402
    ION_STRUCT_KEY_POOL as _STRUCT_KEYS,
    make_ion_symtab as _make_symtab,
)

# Symbols generated values may reference (pre-registered so encode can resolve
# them and the decoder reads them back through the same table) and the symtab
# factory both live in tests/_helpers.py, shared with test_ion_roundtrip (#128).


def _decode(data: bytes):
    """Decode raw bytes via a fresh IonBinary. Returns on success; controlled
    failures raise (and are swallowed by the callers). RecursionError and
    MemoryError deliberately propagate — those are the crash classes under test."""
    return IonBinary(_make_symtab()).deserialize_multiple_values(data)


# The two crash classes that must never escape the decoder. Everything else
# (generic Exception, struct.error, UnicodeDecodeError, ValueError, ...) is an
# acceptable controlled rejection of malformed input.
_UNCONTROLLED = (RecursionError, MemoryError)


# 200 examples × 3 byte-fuzz properties. Inputs are ≤2 KB so decode is fast;
# the 2 s deadline catches a genuine hang without flaking on a slow example.
_FUZZ = settings(
    max_examples=200,
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ---------------------------------------------------------------------------
# G1 — arbitrary / mutated bytes must fail controlled, never crash-class.
# ---------------------------------------------------------------------------


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerByteFuzz:
    @_FUZZ
    @given(data=st.binary(min_size=0, max_size=2048))
    def test_arbitrary_bytes(self, data):
        """Pure random bytes: almost always fail the signature check, but must
        do so without a RecursionError / MemoryError / hang."""
        try:
            _decode(data)
        except _UNCONTROLLED:
            raise
        except Exception:
            pass  # controlled rejection — acceptable

    @_FUZZ
    @given(body=st.binary(min_size=0, max_size=2048))
    def test_signature_prefixed_bytes(self, body):
        """Prefix the valid Ion signature so the bytes get past the signature
        gate and exercise the value-decode paths with hostile content."""
        try:
            _decode(IonBinary.SIGNATURE + body)
        except _UNCONTROLLED:
            raise
        except Exception:
            pass


def _valid_corpus() -> list[bytes]:
    """A handful of valid serialized values to mutate. Covers the variable-
    width container/scalar types the decoder branches on."""
    binary = IonBinary(_make_symtab())
    samples = [
        "hi",
        42,
        IonList([1, 2, IonList([3, "x"])]),
        IonStruct(IS("k0"), "v", IS("k1"), 7),
        IonBLOB(b"\x00\x01\x02payload\xff"),
        IonDecimal(decimal.Decimal("1.5")),
        IonAnnotation([IS("k2")], "annotated"),
    ]
    return [binary.serialize_single_value(v) for v in samples]


_CORPUS = _valid_corpus()


@pytest.mark.tier1
@pytest.mark.unit
class TestDeserializerMutationFuzz:
    """Bit-flips and truncations of valid blobs probe the deep decode paths a
    random byte string rarely reaches (it usually dies at the signature)."""

    @_FUZZ
    @given(idx=st.integers(min_value=0, max_value=len(_CORPUS) - 1), data=st.data())
    def test_mutated_valid_blob(self, idx, data):
        blob = bytearray(_CORPUS[idx])
        # Flip up to 8 random bytes.
        for _ in range(data.draw(st.integers(min_value=0, max_value=8))):
            if not blob:
                break
            pos = data.draw(st.integers(min_value=0, max_value=len(blob) - 1))
            blob[pos] = data.draw(st.integers(min_value=0, max_value=255))
        # Optionally truncate.
        if blob and data.draw(st.booleans()):
            blob = blob[: data.draw(st.integers(min_value=0, max_value=len(blob)))]
        try:
            _decode(IonBinary.SIGNATURE + bytes(blob))
        except _UNCONTROLLED:
            raise
        except Exception:
            pass


# ---------------------------------------------------------------------------
# G1 — deep-nesting regression. This is the bug the fuzz found.
# ---------------------------------------------------------------------------


def _craft_nested_lists(depth: int) -> bytes:
    """Build a byte stream of `depth` singly-nested list descriptors directly.

    The serializer can't produce this (it hits RecursionError around depth 800
    on its own recursion), but a hand-crafted stream is ~3 bytes per level —
    tiny and arbitrarily deep. Innermost is an empty list (0xB0); each wrapping
    layer is a one-element list whose length prefix points at the layer below.
    """
    data = b"\xb0"
    for _ in range(depth):
        n = len(data)
        if n < ion_binary.IonBinary.VARIABLE_LEN_FLAG:
            data = bytes([0xB0 | n]) + data
        else:
            data = bytes([0xBE]) + serialize_vluint(n) + data
    return IonBinary.SIGNATURE + data


@pytest.mark.tier1
@pytest.mark.unit
class TestDeepNestingRejected:
    """Regression for #123. Before the `MAX_ION_NESTING_DEPTH` guard, a crafted
    ~1.4 KB stream of 500 nested list descriptors recursed one Python frame per
    level and raised an uncontrolled `RecursionError`. The guard converts that
    into a controlled rejection before the interpreter limit is reached."""

    @pytest.mark.parametrize("depth", [200, 500, 1500, 5000])
    def test_deep_nesting_controlled_rejection(self, depth):
        # Must raise the bounded security error — NOT RecursionError. (If the
        # guard regressed, pytest.raises would still match Exception, so assert
        # the specific message to prove it's the guard, not an incidental fail.)
        with pytest.raises(Exception, match="nesting depth exceeds security limit"):
            _decode(_craft_nested_lists(depth))

    def test_deep_nesting_is_not_recursion_error(self):
        """Explicit: the failure class is controlled, not RecursionError."""
        try:
            _decode(_craft_nested_lists(2000))
        except RecursionError:
            pytest.fail("deep nesting raised uncontrolled RecursionError (#123)")
        except Exception as exc:
            assert "security limit" in str(exc)

    def test_shallow_nesting_still_decodes(self):
        """Nesting under the bound must still decode — the guard must not break
        legitimate (shallow) KFX structures."""
        _decode(_craft_nested_lists(50))  # no exception

    def test_bound_is_configurable(self, monkeypatch):
        """`MAX_ION_NESTING_DEPTH` is a module attribute so ops/tests can tune
        it. Verify the patched value takes effect at decode time."""
        monkeypatch.setattr(ion_binary, "MAX_ION_NESTING_DEPTH", 10)
        with pytest.raises(Exception, match="security limit"):
            _decode(_craft_nested_lists(20))
        # 5 levels is under the patched bound — still fine.
        _decode(_craft_nested_lists(5))


# ---------------------------------------------------------------------------
# G4 — round-trip the type families #51 explicitly deferred.
# ---------------------------------------------------------------------------


def _roundtrip(value, symtab=None):
    binary = IonBinary(symtab or _make_symtab())
    return binary.deserialize_single_value(binary.serialize_single_value(value))


@pytest.mark.tier1
@pytest.mark.unit
class TestDeferredTypeRoundTrip:
    """#51 deferred timestamps, Decimal, Symbol, CLOB, and Annotation from the
    round-trip fuzz. G4 (#126) closes that gap. Decimal matters because $157
    style magnitudes serialize as IonDecimal (see test_kfx_invariants
    ::TestStyleMagnitudeIsDecimal) — a real, exercised code path.

    Timestamps are covered on the *decode* side by the byte/mutation fuzz above
    (random descriptors hit the timestamp signature); a valid-value encode
    round-trip needs the precision-format plumbing and is left to a follow-up."""

    @_FUZZ
    @given(
        d=st.decimals(
            allow_nan=False,
            allow_infinity=False,
            min_value=decimal.Decimal("-1e20"),
            max_value=decimal.Decimal("1e20"),
        ).filter(lambda x: len(x.as_tuple().digits) <= 25)
    )
    def test_decimal_roundtrip(self, d):
        # Bounded to ≤25 significant digits: the decimal *decoder* rebuilds the
        # value under Python's default 28-digit context, so decimals beyond that
        # round on decode. 25 leaves margin and dwarfs any real KFX decimal
        # ($157 style magnitudes are 1-2 significant digits). The >28-digit
        # behaviour is pinned in test_decimal_high_precision_is_context_rounded.
        value = IonDecimal(d)
        assert _roundtrip(value) == value

    @pytest.mark.parametrize(
        "raw",
        ["0", "1.5", "-12345.6789", "0.000001", "1e10", "-1E-10", "3.14159265358979"],
    )
    def test_decimal_boundary_values(self, raw):
        value = IonDecimal(decimal.Decimal(raw))
        assert _roundtrip(value) == value

    def test_decimal_high_precision_is_context_rounded(self):
        """Documents a known codec fidelity limit (cousin of the -0.0 note in
        test_ion_roundtrip): a decimal with more significant digits than the
        active decimal context (default 28) is rounded to the context on decode.
        Real KFX decimals never approach this, so it is documented, not fixed —
        fixing would mean changing the vendored decimal-decode context with
        broader blast radius. If a future need for full-precision decimals
        appears, that is the follow-up trigger."""
        high = decimal.Decimal("3943821202101044318.6043079628")  # 29 sig digits
        assert len(high.as_tuple().digits) > 28
        out = _roundtrip(IonDecimal(high))
        # Round-trips to the context-rounded form, not the original.
        assert out == +high  # unary plus applies the active context rounding
        assert out != high

    @_FUZZ
    @given(b=st.binary(min_size=0, max_size=2000))
    def test_clob_roundtrip(self, b):
        value = IonCLOB(b)
        assert _roundtrip(value) == value

    @given(name=st.sampled_from(_STRUCT_KEYS))
    def test_symbol_roundtrip(self, name):
        value = IS(name)
        assert _roundtrip(value) == value

    @_FUZZ
    @given(name=st.sampled_from(_STRUCT_KEYS), text=st.text(min_size=0, max_size=200))
    def test_annotation_roundtrip(self, name, text):
        # IonAnnotation defines no __eq__, so compare its fields explicitly.
        value = IonAnnotation([IS(name)], text)
        out = _roundtrip(value)
        assert isinstance(out, IonAnnotation)
        assert list(out.annotations) == list(value.annotations)
        assert out.value == value.value
