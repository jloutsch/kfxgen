"""
Shared helpers for walking and loading KFX fragment trees in tests.

Extracted from tests/unit/test_kfx_invariants.py and tests/unit/test_position_map.py
so unit and integration paths use identical predicates. (#49)

load_fragments is the round-trip parse helper, extracted from the
tests/conftest.py::load_kfx_fragments fixture and the previously-duplicated
tests/integration/test_epub_corpus.py::_load_fragments. (#82)
"""

from pathlib import Path

from kfxgen.kfxlib_minimal.ion import IS


def by_type(frags, ftype):
    """Filter fragments by string ftype (e.g. '$259')."""
    return [f for f in frags if str(f.ftype) == ftype]


def val(f):
    """Unwrap an Ion-annotated fragment value to its inner struct/list."""
    v = f.value
    if hasattr(v, "value"):
        v = v.value
    return v


def walk_for_key(node, key_name):
    """Yield every value associated with `key_name` anywhere in the nested
    Ion struct/list tree rooted at `node`. IonStruct is dict-like."""
    target = IS(key_name)
    if hasattr(node, "items"):
        try:
            for k, v in node.items():
                if k == target:
                    yield v
                yield from walk_for_key(v, key_name)
        except (AttributeError, TypeError):
            return
    elif isinstance(node, list):
        for item in node:
            yield from walk_for_key(item, key_name)


class _BytesDataSource:
    """Minimal DataFile-shaped wrapper accepted by KfxContainer."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def get_data(self) -> bytes:
        return self.data


def load_fragments(kfx_path):
    """Round-trip a generated .kfx through kfxlib_minimal.

    The real KfxContainer API is `KfxContainer(symtab, datafile=...)` where
    `datafile` is any object with a `.get_data()` method, followed by
    `.deserialize(ignore_drm=True)` and `.get_fragments()`. There is no
    classmethod constructor.
    """
    from kfxgen.kfxlib_minimal.ion_symbol_table import (
        LocalSymbolTable,
        SymbolTableCatalog,
    )
    from kfxgen.kfxlib_minimal.kfx_container import KfxContainer

    catalog = SymbolTableCatalog(add_global_shared_symbol_tables=True)
    symtab = LocalSymbolTable(catalog=catalog)
    with open(Path(kfx_path), "rb") as fh:
        data = fh.read()
    container = KfxContainer(symtab, datafile=_BytesDataSource(data))
    container.deserialize(ignore_drm=True)
    return list(container.get_fragments())
