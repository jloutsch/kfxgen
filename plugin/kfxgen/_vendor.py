"""Third-party packages bundled with the plugin, in `vendor/` (#235).

In the source tree `vendor/` is a folder and goes on `sys.path` as it is.
In calibre the plugin runs from its zip, and ziamath reads its font from a
file, so the zip's `kfxgen/vendor/` files are unpacked once into a folder
under calibre's configuration folder, named by a hash of those files, and
that folder goes on `sys.path`."""

import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_DONE = ".complete"


def split_zip(path):
    """(the zip file, the path inside it) for a path into a zip file, or
    (None, None) when no part of `path` is a zip file."""
    path = Path(path)
    for parent in [path, *path.parents]:
        if parent.is_file() and zipfile.is_zipfile(parent):
            return parent, path.relative_to(parent).as_posix()
    return None, None


def _members(z, prefix):
    """(member, its path under the vendor folder) for each file in
    `prefix`/vendor/, less any path that could leave the folder."""
    root = f"{prefix}/vendor/"
    for info in z.infolist():
        if info.is_dir() or not info.filename.startswith(root):
            continue
        parts = info.filename[len(root) :].split("/")
        if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
            continue
        yield info, parts


def unpack(zip_path, prefix, target):
    """The vendor folder of the plugin zip `zip_path`, unpacked under
    `target` once and reused after."""
    with zipfile.ZipFile(zip_path) as z:
        members = list(_members(z, prefix))
        key = hashlib.sha256()
        for info, parts in members:
            key.update(f"{'/'.join(parts)}\0{info.CRC}\0{info.file_size}\n".encode())
        target = Path(target)
        final = target / key.hexdigest()[:16]
        if (final / _DONE).is_file():
            return final
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".unpack-", dir=target))
        try:
            for info, parts in members:
                dest = work.joinpath(*parts)
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(
                    dest,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(fd, "wb") as out:
                    out.write(z.read(info))
            (work / _DONE).touch(mode=0o600)
            try:
                work.rename(final)
            except OSError:
                # Another conversion unpacked it first.
                if not (final / _DONE).is_file():
                    raise
        finally:
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)
    return final


def vendor_folder():
    """The folder holding the bundled packages, or None."""
    here = Path(__file__).resolve().parent
    folder = here / "vendor"
    if folder.is_dir():
        return folder
    zip_path, inside = split_zip(here)
    if zip_path is None:
        return None
    try:
        from calibre.constants import config_dir
    except ImportError:
        return None
    return unpack(zip_path, inside, Path(config_dir) / "plugins" / "kfxgen-vendor")


def on_path():
    """Put the bundled packages on `sys.path`; False when there are none."""
    folder = vendor_folder()
    if folder is None:
        return False
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    return True
