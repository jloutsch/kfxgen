"""
Shared assets and helpers used across the test suite (#90).

Centralizes test fixtures that were previously duplicated in 4-5 files:

- `MINIMAL_JPEG`: a minimal-but-valid 1×1 JPEG that passes the converter's
  magic-byte sniff. Used wherever a test needs to exercise the
  cover/body-image pipeline without bundling a real photo.
- `NullLog`: a no-op logger that satisfies the converter's `log` parameter
  protocol (`info` / `warn` / `warning` / `error` / `debug`). Lets tests
  drive the conversion pipeline without polluting test output.

`tests/_kfx_introspect.py` covers KFX *fragment* helpers; this module
covers everything else worth deduplicating.
"""

from __future__ import annotations


# Minimal valid 1×1 JPEG accepted by the converter's magic-byte sniff
# (`\xff\xd8\xff` + JFIF APP0 + length>100). Hex-encoded inline so tests
# don't need to bundle binary fixtures for trivial image-pipeline cases.
MINIMAL_JPEG: bytes = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606"
    "07060805070707090908" + "0a" * 100 + "ffc0000b08000100010101" + "00" * 30 + "ffd9"
)


class NullLog:
    """No-op logger satisfying the kfxgen `log` parameter protocol.

    Implements every method the converter and generator might call on a
    log object so tests can drive the pipeline silently. If you need
    to capture output for diagnostics, instantiate `caplog` from pytest
    or a `unittest.mock.MagicMock()` instead — this is the silent
    default.
    """

    def info(self, *a, **kw):
        pass

    def warn(self, *a, **kw):
        pass

    def warning(self, *a, **kw):
        pass

    def error(self, *a, **kw):
        pass

    def debug(self, *a, **kw):
        pass


# Fixed pool of struct keys for Ion fuzz / round-trip tests. Pre-registering
# these in a symbol table lets generated structs use any of them as a key
# without the table growing mid-fuzz — Ion struct serialization requires
# `symtab.get_id(key)` to succeed. Shared by test_ion_roundtrip.py (#51) and
# test_deserializer_fuzz.py (#123) so the construction contract lives in one
# place (#128).
ION_STRUCT_KEY_POOL: list[str] = [f"k{i}" for i in range(20)]


def make_ion_symtab():
    """Fresh Ion ``LocalSymbolTable`` with ``ION_STRUCT_KEY_POOL`` registered.

    kfxgen is imported lazily so this module stays import-safe regardless of
    when it is first loaded; ``conftest.py`` puts ``plugin/`` on ``sys.path``
    before collection, so the import resolves by the time any test calls this.
    """
    from kfxgen.kfxlib_minimal.ion_symbol_table import (
        LocalSymbolTable,
        SymbolTableCatalog,
    )

    catalog = SymbolTableCatalog(add_global_shared_symbol_tables=True)
    symtab = LocalSymbolTable(catalog=catalog)
    for key in ION_STRUCT_KEY_POOL:
        symtab.create_local_symbol(key)
    return symtab
