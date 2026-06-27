"""
Property-based round-trip fuzz for the Ion binary serializer (#51).

Council finding (Munger + Sun Tzu): `plugin/kfxgen/kfxlib_minimal/ion_binary.py`
has zero adversarial test coverage for type-length encoding boundaries.
A regression in the length-prefix logic could silently corrupt large
blobs or long strings without tripping any existing test.

This module:

1. Generates random valid Ion values across the type families that
   `ion_binary` actually emits (`int`, `string`, `IonBLOB`, `IonList`,
   `IonStruct`, plus scalars: `bool`, `None`, `float`).
2. Runs each through three round-trips:
   - **Binary**: serialize via `IonBinary`, deserialize via `IonBinary`.
   - **Text**: serialize via `IonText`, deserialize via `IonText`.
   - **Differential**: both codecs deserialize back to equal values.
3. Asserts equality at every step.

Plus targeted parametrized cases for boundaries Hypothesis won't
reliably hit (2^31, 2^63, 1 MB blob, 1000-field struct, deeply
nested struct).

Tier-1 + unit (per #42 oracle hierarchy and #51 owner comment): runs
entirely in-process, gates every PR via the default CI selection.

Out of scope (file follow-ups if a coverage gap is found):
    timestamps, Decimal, Symbol values, CLOB, Annotation. The council
    brief was specifically about type-length encoding for the
    variable-width types (int, string, blob, list, struct).
"""

from __future__ import annotations

import math
import os
import sys

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.kfxlib_minimal.ion import (  # noqa: E402
    IS,
    IonBLOB,
    IonList,
    IonStruct,
)
from kfxgen.kfxlib_minimal.ion_binary import IonBinary  # noqa: E402
from kfxgen.kfxlib_minimal.ion_text import IonText  # noqa: E402

# Struct-key pool and symtab factory live in tests/_helpers.py, shared with
# test_deserializer_fuzz (#128). Pre-registering the pool lets generated structs
# use any key without the table growing mid-fuzz (struct serialization requires
# `symtab.get_id(key)` to succeed).
from tests._helpers import (  # noqa: E402
    ION_STRUCT_KEY_POOL as _STRUCT_KEY_POOL,
    make_ion_symtab as _make_symtab,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies for Ion values.
#
# Hypothesis' integers strategy hits 2^31 and 2^63 boundaries by default
# when the range covers them — capped at ±2^70 to bound the encoded
# bytes without losing the standard boundary cases.
# ---------------------------------------------------------------------------

# Arbitrary-precision ints (#93). The serializer now uses
# `int.to_bytes`/`int.from_bytes` and accepts values up to Python's
# arbitrary-precision limit. Bound the strategy at ±(2**256) to keep
# individual examples small enough that the test runs fast — there's
# no encoding correctness reason for the cap, just a per-example
# byte-budget one.
_int_strat = st.integers(min_value=-(2**256), max_value=2**256)
_str_strat = st.text(min_size=0, max_size=2000)
_blob_strat = st.binary(min_size=0, max_size=10000).map(IonBLOB)
_bool_strat = st.booleans()
_null_strat = st.just(None)
# Floats: drop NaN (NaN != NaN breaks equality assertions). Also drop -0.0
# specifically — the binary serializer emits the empty byte string for any
# `value == 0.0`, so -0.0 round-trips as +0.0 and the equality assertion
# silently passes despite the sign-bit loss. That's a known codec behaviour,
# not a #51 finding; document it rather than letting fuzz mask it.
_float_strat = st.floats(allow_nan=False, allow_infinity=False).filter(
    lambda x: x != 0.0 or math.copysign(1.0, x) > 0
)

_scalar_strat = st.one_of(
    _int_strat, _str_strat, _blob_strat, _bool_strat, _null_strat, _float_strat
)


def _ion_value_strategy():
    """Recursive Ion value strategy: scalars at the leaves, IonList /
    IonStruct as containers. `max_leaves=30` caps total node count to
    keep individual examples cheap and avoid Python recursion limits
    on pathological depths."""

    def _container(children):
        return st.one_of(
            st.lists(children, max_size=20).map(IonList),
            # Build structs from a fixed key pool and arbitrary values.
            # Hypothesis dictionaries() guarantees unique keys.
            st.dictionaries(
                keys=st.sampled_from(_STRUCT_KEY_POOL).map(IS),
                values=children,
                max_size=15,
            ).map(lambda d: IonStruct(*[x for kv in d.items() for x in kv])),
        )

    return st.recursive(_scalar_strat, _container, max_leaves=30)


# Hypothesis settings: 100 examples per property × 3 properties = 300
# fuzz cases plus ~40 explicit edge cases. Keeps tier-1 / pre-push
# runtime under ~6s on M-series. `deadline=None` disables the per-
# example timeout — a 1MB-blob example legitimately exceeds the
# default 200ms and we don't want spurious flakes.
_FUZZ_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@pytest.mark.tier1
@pytest.mark.unit
class TestIonRoundTrip:
    @_FUZZ_SETTINGS
    @given(value=_ion_value_strategy())
    def test_binary_roundtrip(self, value):
        """IonBinary encode → IonBinary decode must yield an equal value."""
        # Fresh symtab per-call: Hypothesis re-uses the test instance
        # across examples, so a stale symtab from a previous example
        # would accumulate locally-assigned ids and confuse the encoder.
        binary = IonBinary(_make_symtab())
        data = binary.serialize_single_value(value)
        # Decode with the SAME symtab — locally-assigned ids only mean
        # something within one (encode, decode) pairing.
        out = binary.deserialize_single_value(data)
        assert out == value, (
            f"Binary round-trip lost equality:\n  in : {value!r}\n  out: {out!r}"
        )

    @_FUZZ_SETTINGS
    @given(value=_ion_value_strategy())
    def test_text_roundtrip(self, value):
        """IonText encode → IonText decode must yield an equal value."""
        text = IonText(_make_symtab())
        data = text.serialize_single_value(value)
        out = text.deserialize_single_value(data)
        assert out == value, (
            f"Text round-trip lost equality:\n  in : {value!r}\n  out: {out!r}"
        )

    @_FUZZ_SETTINGS
    @given(value=_ion_value_strategy())
    def test_binary_text_agree(self, value):
        """The two codecs must agree on the value's identity. If they
        disagree, one of them is round-tripping incorrectly."""
        symtab = _make_symtab()
        binary = IonBinary(symtab)
        text = IonText(symtab)
        via_binary = binary.deserialize_single_value(
            binary.serialize_single_value(value)
        )
        via_text = text.deserialize_single_value(text.serialize_single_value(value))
        assert via_binary == via_text, (
            f"Binary and text codecs disagree on value:\n"
            f"  in        : {value!r}\n"
            f"  via binary: {via_binary!r}\n"
            f"  via text  : {via_text!r}"
        )


# ---------------------------------------------------------------------------
# Targeted edge cases — boundaries Hypothesis won't reliably hit on its own.
# ---------------------------------------------------------------------------


def _roundtrip_binary(value):
    binary = IonBinary(_make_symtab())
    return binary.deserialize_single_value(binary.serialize_single_value(value))


@pytest.mark.tier1
@pytest.mark.unit
class TestIonEdgeCases:
    """Explicit fixtures for length-prefix and recursion-depth boundaries.
    These complement the property-based tests above; they're cheap to run
    and document specific shapes the council brief flagged."""

    @pytest.mark.parametrize(
        "value",
        [
            0,
            1,
            -1,
            2**31 - 1,
            2**31,
            2**32,
            2**63 - 1,
            2**63,
            2**64 - 1,  # max representable in the legacy 8-byte path
            -(2**63),
            -(2**31),
            -(2**64 - 1),
        ],
        ids=lambda v: f"int_{v}",
    )
    def test_int_boundaries(self, value):
        """Verifies the powers-of-two and former 64-bit-ceiling boundaries
        round-trip. Hypothesis won't reliably hit these exact values;
        this nails them down. Arbitrary-precision values above the
        legacy ceiling are covered by `test_int_arbitrary_precision`."""
        assert _roundtrip_binary(value) == value

    @pytest.mark.parametrize(
        "value",
        [2**64, 2**100, 2**256, 2**512, -(2**64), -(2**100), -(2**256)],
        ids=lambda v: f"int_{v}",
    )
    def test_int_arbitrary_precision(self, value):
        """Pins arbitrary-precision int support (#93). The previous
        `struct.pack('>Q', ...)` path capped at 2**64 - 1 and hard-
        crashed on larger values; the rewrite uses `int.to_bytes` /
        `int.from_bytes` and round-trips any Python int."""
        assert _roundtrip_binary(value) == value

    @pytest.mark.parametrize(
        "size", [0, 1, 13, 14, 100, 10_000, 1_000_000], ids=lambda s: f"blob_{s}"
    )
    def test_blob_size_boundaries(self, size):
        """Length-prefix encoding switches between inline and varuint at 14 bytes
        (`VARIABLE_LEN_FLAG`); 1 MB exercises the multi-byte varuint path."""
        value = IonBLOB(bytes(range(256)) * (size // 256) + bytes(size % 256))
        assert _roundtrip_binary(value) == value

    @pytest.mark.parametrize(
        "size", [0, 1, 13, 14, 100, 10_000], ids=lambda s: f"str_{s}"
    )
    def test_string_length_boundaries(self, size):
        """Same length-prefix boundaries as blob, but routed through the
        UTF-8 string path."""
        value = "a" * size
        assert _roundtrip_binary(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "ASCII only",
            "café",  # Latin-1 multibyte
            "漢字テスト",  # CJK
            "🎉🚀✨",  # emoji (4-byte UTF-8)
            "mixed: ascii + café + 漢字 + 🎉",
        ],
        ids=lambda s: f"utf8_{len(s)}",
    )
    def test_utf8_multibyte_strings(self, value):
        assert _roundtrip_binary(value) == value

    def test_empty_struct(self):
        assert _roundtrip_binary(IonStruct()) == IonStruct()

    def test_empty_list(self):
        assert _roundtrip_binary(IonList()) == IonList()

    def test_struct_with_1000_fields(self):
        """Many-field struct exercises the symbol-table indirection path
        and the struct's own length-prefix at scale."""
        # Pre-register 1000 keys in the shared symtab.
        symtab = _make_symtab()
        for i in range(1000):
            symtab.create_local_symbol(f"field_{i}")
        binary = IonBinary(symtab)
        value = IonStruct(*[arg for i in range(1000) for arg in (IS(f"field_{i}"), i)])
        out = binary.deserialize_single_value(binary.serialize_single_value(value))
        assert out == value
        assert len(out) == 1000

    def test_deeply_nested_struct(self):
        """5-deep struct nesting — well below Python's recursion limit
        but enough to exercise the recursive serialize/deserialize path."""
        value = "leaf"
        for i in range(5):
            value = IonStruct(IS(f"k{i}"), value)
        assert _roundtrip_binary(value) == value

    def test_large_list_10k_elements(self):
        value = IonList([i for i in range(10_000)])
        assert _roundtrip_binary(value) == value

    def test_negative_zero_silently_normalizes_to_positive_zero(self):
        """Documents a known float-codec behaviour: `serialize_float_value`
        emits the empty byte string when `value == 0.0`, which collapses
        `-0.0` to `+0.0` on round-trip. The equality assertion `x == 0.0`
        is True for both, so the loss is silent. This is below the
        council's #51 brief (type-length encoding for variable-width
        types) but worth pinning so a future Ion fidelity audit doesn't
        rediscover it."""
        out = _roundtrip_binary(-0.0)
        assert out == 0.0  # equality holds despite sign-bit loss
        assert math.copysign(1.0, out) == 1.0, (
            "round-tripped -0.0 should reveal sign-bit loss"
        )
