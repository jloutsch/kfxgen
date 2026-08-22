"""
Shared assets and helpers used across the test suite (#90).

Centralizes test fixtures that were previously duplicated in 4-5 files:

- `MINIMAL_JPEG`: a minimal-but-valid 1×1 JPEG that passes the converter's
  magic-byte sniff. Used wherever a test needs to exercise the
  cover/body-image pipeline without bundling a real photo.
- `MINIMAL_PNG`: the same idea in the other format, for fixtures that need
  two images the pipeline cannot confuse for one another.
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


#: Valid 16x16 RGBA PNG, 819 bytes. Exists so a fixture can hold two images
#: whose bytes *and* format differ: with both images identical, a count
#: assertion silently stops measuring "did both images survive" and starts
#: measuring "did anything dedupe them by content".
#:
#: Two constraints shape it, and a 1x1 PNG fails both. `extract_images_from_oeb`
#: skips any manifest image of 100 bytes or fewer (`converter.py:1171`), so the
#: payload has to clear that floor — and a solid-colour image compresses to well
#: under it however large the dimensions, which is why the pixels are a
#: deliberately incompressible gradient. Generated with zlib; all three chunk
#: CRCs verified.
MINIMAL_PNG: bytes = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000100000001008060000001ff3ff"
    "61000002fa4944415478da0dcc3168d5781cc0f187106e52ec201435cb5fe841"
    "2725e29574f82fa174a85c871732181c3a040b0e454243040be2f21f8a108460"
    "443887373c32e490a02039e586824382c3c3c125e8662c126e502852f8de6ff8"
    "ac9fc96432614928e1084ff822128930a210a56844277a310ac464624b60ff86"
    "b2cfe1d817f0eccbf8f615227b95c4be86b1d7286c4d696fd0d837e9ec29bd7d"
    "8bd1de017b57025702f71ccabd8ce3aee2b96bf8ee06913b25717730ee1e857b"
    "40e91ed2b8cfe8dc39bdfb9ad13d027721412041700115ace2041a2f98e207bb"
    "44c10149f00413cc2982b794c18226f84a179cd2074b8cc10a04eb12c412c497"
    "51f11a4e3cc58bf7f0e343a2784e121f61e22f14f12965bc4c135fa78bb7e9e3"
    "bb8cb18178264126417605956de064bb78d9217ef63751b620c97e62b2658a6c"
    "9d32bb4d933da4cb66f4d97bc6ec18b2b312541254aba86a8a531de05573fc6a"
    "41549d92542b986a9ba2ba4f59cd68aa0f74d5097da518ab2da8f625682568af"
    "a1da1d9cf6095e7b84dffe246a5748da00d31a8af60d657b4cd35ea26bb7e8db"
    "078c6d05ed670906098635d4b08733ccf1862ff8c332d1b04d3218ccf02fc570"
    "42395ca519eed00d2fe8874f8cc379183625b024b034ca3ac0b1dee259a7f8d6"
    "3a91759fc47a83b14e28ac3f28ad7d1aeb159df583de7218ad7b60bd944049a0"
    "3650ea10472df0d432beba4da46624ea18a3ae52a87d4af50f8d3a43a736e9d5"
    "6346f511d44509b404fa264a3fc3d15ff1f4757cfd90487f20d19730fa0e857e"
    "45a9cfd0e83fe9f4737afd8d51df00fd48825082708a0ae738e1295eb88d1fce"
    "88c21392700b13bea0087f50869b34e173baf03ffad0630c9f42f85d825482f4"
    "162a7d8d932ee1a577f1d3f744a922491f60d24f14a943993ea649bfd1a51e7d"
    "fa1763fa0b525f825c827c07951fe1e42b78b9c1cf8f89f22d92bcc2e4e729f2"
    "7b94f9479afc065dfe943effc5988790bf93a096a0de45d50b9c7a1daf9ee1d7"
    "6789ea7d92fa33a6dea4a85f52d61769ea4774f577fada67acdf41fd3bff0345"
    "b254df943c21df0000000049454e44ae426082"
)
