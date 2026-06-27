#!/usr/bin/env python3
"""
Build Calibre Plugin ZIP

Creates kfxgen-plugin-X.Y.Z.zip from plugin/ directory for distribution.
"""

import zipfile
from pathlib import Path
import sys
import re


def get_plugin_version():
    """Extract version from the single source of truth.

    The version tuple lives in ``plugin/kfxgen/__init__.py``;
    ``plugin/__init__.py`` only imports it. Reading the wrapper and
    matching an unanchored ``version = (...)`` previously collided with
    ``minimum_calibre_version = (5, 0, 0)`` and mislabeled every build
    as 5.0.0 (#114). Anchor to start-of-line on the real source.
    """
    init_file = Path("plugin") / "kfxgen" / "__init__.py"
    if not init_file.exists():
        return None

    content = init_file.read_text()
    match = re.search(r"^version = \((\d+), (\d+), (\d+)\)", content, re.MULTILINE)
    if match:
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    return None


def build_plugin(clean=True):
    """Create plugin ZIP file"""
    plugin_dir = Path("plugin")
    output_dir = Path("dist")

    # Get version number
    version = get_plugin_version()
    if version:
        output_file = output_dir / f"kfxgen-plugin-{version}.zip"
    else:
        output_file = output_dir / "kfxgen-plugin.zip"
        print("Warning: Could not determine version, using generic filename")

    if not plugin_dir.exists():
        print(f"Error: {plugin_dir} not found")
        print("Run this script from the repository root")
        return False

    # Create dist directory
    output_dir.mkdir(exist_ok=True)

    # Remove old plugin ZIP if exists
    if output_file.exists() and clean:
        print(f"Removing old {output_file}")
        output_file.unlink()

    print(f"Building plugin: {output_file}")
    print(f"Source: {plugin_dir}")

    # Create ZIP
    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
        file_count = 0

        for file in plugin_dir.rglob("*"):
            if file.is_file():
                # Skip Python cache files
                if "__pycache__" in file.parts or file.suffix == ".pyc":
                    continue

                # Skip hidden files
                if file.name.startswith("."):
                    continue

                # Add to ZIP with relative path
                arcname = file.relative_to(plugin_dir)
                zf.write(file, arcname)
                print(f"  Added: {arcname}")
                file_count += 1

    print("\n✅ Plugin built successfully!")
    print(f"   Files: {file_count}")
    print(f"   Size: {output_file.stat().st_size / 1024:.1f} KB")
    print(f"   Output: {output_file}")
    print(f"\nTo install: calibre-customize -a {output_file}")

    return True


if __name__ == "__main__":
    success = build_plugin()
    sys.exit(0 if success else 1)
