"""The bundled packages are found from the source tree and from the
plugin zip (#235).

In calibre the plugin runs from its zip, and ziamath reads its font from a
file, so `kfxgen/vendor/` is unpacked once into a folder of its own."""

import zipfile

import pytest

from kfxgen import _vendor


def _plugin_zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("kfxgen/__init__.py", "")
        for name, data in members.items():
            z.writestr(name, data)
    return path


@pytest.mark.unit
def test_the_source_tree_uses_its_vendor_folder():
    folder = _vendor.vendor_folder()
    assert folder is not None
    assert (folder / "ziamath" / "__init__.py").is_file()
    assert (folder / "ziamath" / "fonts" / "STIXTwoMath-Regular.ttf").is_file()


@pytest.mark.unit
def test_the_zip_is_unpacked_once(tmp_path):
    plugin = _plugin_zip(
        tmp_path / "kfxgen.zip",
        {
            "kfxgen/vendor/pkg/__init__.py": "X = 1\n",
            "kfxgen/vendor/pkg/f.bin": b"\0\1",
        },
    )
    target = tmp_path / "cache"
    folder = _vendor.unpack(plugin, "kfxgen", target)
    assert (folder / "pkg" / "__init__.py").read_text() == "X = 1\n"
    assert (folder / "pkg" / "f.bin").read_bytes() == b"\0\1"
    (folder / "pkg" / "__init__.py").write_text("X = 2\n")
    assert _vendor.unpack(plugin, "kfxgen", target) == folder
    assert (folder / "pkg" / "__init__.py").read_text() == "X = 2\n"


@pytest.mark.unit
def test_a_different_zip_unpacks_to_a_new_folder(tmp_path):
    target = tmp_path / "cache"
    one = _plugin_zip(tmp_path / "a.zip", {"kfxgen/vendor/pkg/__init__.py": "X = 1\n"})
    two = _plugin_zip(tmp_path / "b.zip", {"kfxgen/vendor/pkg/__init__.py": "X = 2\n"})
    first, second = (
        _vendor.unpack(one, "kfxgen", target),
        _vendor.unpack(two, "kfxgen", target),
    )
    assert first != second
    assert (second / "pkg" / "__init__.py").read_text() == "X = 2\n"


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    ["kfxgen/vendor/../evil.py", "kfxgen/vendor/pkg/../../evil.py", "/abs/evil.py"],
)
def test_a_member_outside_the_vendor_folder_is_not_written(tmp_path, name):
    plugin = _plugin_zip(
        tmp_path / "kfxgen.zip", {"kfxgen/vendor/pkg/__init__.py": "", name: "bad"}
    )
    folder = _vendor.unpack(plugin, "kfxgen", tmp_path / "cache")
    written = sorted(
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("evil.py")
    )
    assert written == []
    assert (folder / "pkg" / "__init__.py").is_file()


@pytest.mark.unit
def test_other_plugin_files_are_not_unpacked(tmp_path):
    plugin = _plugin_zip(
        tmp_path / "kfxgen.zip",
        {
            "kfxgen/vendor/pkg/__init__.py": "",
            "kfxgen/converter.py": "",
            "about.txt": "",
        },
    )
    folder = _vendor.unpack(plugin, "kfxgen", tmp_path / "cache")
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == [
        ".complete",
        "__init__.py",
    ]


@pytest.mark.unit
def test_a_zip_path_is_split_from_a_module_path(tmp_path):
    plugin = _plugin_zip(tmp_path / "kfxgen.zip", {})
    inside = plugin / "kfxgen"
    assert _vendor.split_zip(inside) == (plugin, "kfxgen")
    assert _vendor.split_zip(tmp_path) == (None, None)
