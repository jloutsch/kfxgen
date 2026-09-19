"""
Tier-1 unit tests for _safe_write_bytes — output-path symlink + traversal
defense (#45).

Threat: Calibre passes the user-chosen output path directly to the plugin.
A symlink at that path would silently redirect a write through to the
target (e.g. /etc/passwd if a user got tricked into setting it). Path with
'..' segments could escape the chosen library directory.
"""

import errno
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.native_generator import _safe_write_bytes


@pytest.mark.tier1
@pytest.mark.unit
class TestSafeWriteHappyPath:
    def test_creates_new_file(self, tmp_path):
        target = tmp_path / "book.kfx"
        _safe_write_bytes(str(target), b"KFX-data")
        assert target.read_bytes() == b"KFX-data"

    def test_overwrites_existing_regular_file(self, tmp_path):
        target = tmp_path / "book.kfx"
        target.write_bytes(b"old")
        _safe_write_bytes(str(target), b"new")
        assert target.read_bytes() == b"new"

    def test_owner_can_read_and_write_what_was_written(self, tmp_path):
        """The part of `0o644` that means anything on every platform.

        Kept separate from the exact mode below so Windows has an assertion
        here rather than a skip. `os.chmod` there toggles the read-only
        attribute and nothing else, so the file comes back `0o666` — nothing
        is wrong with the write, and a bare skip would leave that platform
        with nothing checking this at all (#173).
        """
        target = tmp_path / "book.kfx"
        _safe_write_bytes(str(target), b"x")
        assert os.access(target, os.R_OK | os.W_OK)

    @pytest.mark.skipif(
        os.name == "nt",
        reason=(
            "Windows has no POSIX permission bits and no umask: os.chmod "
            "toggles the read-only attribute, so st_mode comes back 0o100666 "
            "and the 0o644 the production code requests is not expressible. "
            "The owner-access assertion above covers that platform (#173)."
        ),
    )
    def test_mode_is_0644(self, tmp_path):
        target = tmp_path / "book.kfx"
        _safe_write_bytes(str(target), b"x")
        # Mask out any umask effects we don't care about (sticky/setuid).
        assert (target.stat().st_mode & 0o777) == (0o644 & ~_current_umask())

    def test_no_tmp_file_left_behind(self, tmp_path):
        target = tmp_path / "book.kfx"
        _safe_write_bytes(str(target), b"x")
        assert not (tmp_path / "book.kfx.tmp").exists()


@pytest.mark.tier1
@pytest.mark.unit
class TestSafeWriteTraversalRejection:
    @pytest.mark.parametrize(
        "path",
        [
            "../escape.kfx",
            "/tmp/../etc/passwd",
            "books/../../../etc/passwd",
            "OEBPS/../escape.kfx",
        ],
    )
    def test_traversal_segment_rejected(self, path):
        with pytest.raises(ValueError, match=r"\.\."):
            _safe_write_bytes(path, b"x")


@pytest.mark.tier1
@pytest.mark.unit
class TestSafeWriteDriveRelativeRejection:
    """Windows drive-relative '..' patterns bypass the segment-split check
    because there is no separator before the '..'. Match them by regex
    before normalization so 'C:..\\foo' fails closed even on POSIX hosts
    where normpath leaves the prefix alone."""

    @pytest.mark.parametrize(
        "path",
        [
            "C:..\\foo",
            "C:..",
            "c:..\\foo",  # lowercase drive letter
            "Z:..\\..\\evil",
            "C:.\\..\\foo",  # mixed: normpath collapses to '..'
        ],
    )
    def test_drive_relative_traversal_rejected(self, path):
        with pytest.raises(ValueError, match=r"\.\."):
            _safe_write_bytes(path, b"x")

    @pytest.mark.parametrize(
        "path",
        [
            "..\\foo",
            "\\..\\foo",
        ],
    )
    def test_classic_traversal_still_rejected(self, path):
        with pytest.raises(ValueError, match=r"\.\."):
            _safe_write_bytes(path, b"x")


@pytest.mark.tier1
@pytest.mark.unit
class TestSafeWriteSymlinkRejection:
    def test_refuses_symlink_at_destination(self, tmp_path):
        # Bait: pretend an attacker pre-created a symlink at the output path
        # pointing to a file we must not overwrite.
        sensitive = tmp_path / "sensitive.txt"
        sensitive.write_bytes(b"DO NOT OVERWRITE")
        symlink = tmp_path / "book.kfx"
        symlink.symlink_to(sensitive)

        with pytest.raises(OSError) as excinfo:
            _safe_write_bytes(str(symlink), b"attacker-payload")
        assert excinfo.value.errno == errno.ELOOP

        # Critical assertion: symlink target was NOT touched.
        assert sensitive.read_bytes() == b"DO NOT OVERWRITE"
        # And the symlink itself still points where it did.
        assert symlink.is_symlink()
        # `realpath` on both sides rather than the raw strings: Windows returns
        # the extended-length `\\?\C:\...` form from `readlink`, the same path
        # spelled differently. Comparing spellings failed there while every
        # assertion above — refusal, target untouched, still a symlink — passed
        # (#173).
        assert os.path.realpath(os.readlink(str(symlink))) == os.path.realpath(
            str(sensitive)
        )

    def test_the_target_comparison_survives_two_spellings_of_one_path(self, tmp_path):
        """`realpath` on both sides is what makes the assertion above portable.

        Windows' `readlink` returns the extended-length form, so comparing it
        to the path the test wrote failed there while the defense worked
        perfectly (#173). That is not really a Windows quirk: a path has more
        than one spelling wherever a directory symlink is involved, and macOS
        ships `/tmp` -> `/private/tmp` doing exactly this. Built here rather
        than relying on that, so it holds anywhere symlinks do.
        """
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        alias_dir = tmp_path / "alias"
        alias_dir.symlink_to(real_dir, target_is_directory=True)

        sensitive = real_dir / "sensitive.txt"
        sensitive.write_bytes(b"DO NOT OVERWRITE")
        link = tmp_path / "book.kfx"
        link.symlink_to(alias_dir / "sensitive.txt")

        with pytest.raises(OSError):
            _safe_write_bytes(str(link), b"attacker-payload")
        assert sensitive.read_bytes() == b"DO NOT OVERWRITE"

        via_alias = os.readlink(str(link))
        assert via_alias != str(sensitive), (
            "this test is pointless unless the two spellings really differ"
        )
        assert os.path.realpath(via_alias) == os.path.realpath(str(sensitive))

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW unavailable on Windows; tmp-slot symlink defense degrades there",
    )
    def test_refuses_symlink_at_tmp_slot(self, tmp_path):
        # If an attacker pre-creates path + ".tmp" as a symlink, O_NOFOLLOW
        # on the os.open should fail before any data is written.
        sensitive = tmp_path / "sensitive.txt"
        sensitive.write_bytes(b"DO NOT OVERWRITE")
        target = tmp_path / "book.kfx"
        tmp_link = tmp_path / "book.kfx.tmp"
        tmp_link.symlink_to(sensitive)

        with pytest.raises(OSError) as excinfo:
            _safe_write_bytes(str(target), b"attacker-payload")
        # macOS returns ELOOP, Linux returns ELOOP or EMLINK depending on
        # kernel version.
        assert excinfo.value.errno in (errno.ELOOP, errno.EMLINK)

        assert sensitive.read_bytes() == b"DO NOT OVERWRITE"
        assert not target.exists()


@pytest.mark.tier1
@pytest.mark.unit
class TestSafeWriteEdgeCases:
    """Pin behaviour at the edges that PR #64's review flagged: missing
    parent dir and a pre-existing regular file at the tmp slot. Neither
    is a vulnerability, but the test pins what `os.open` does today so
    a future change to the open-flags doesn't silently regress."""

    def test_missing_parent_directory_raises_filenotfound(self, tmp_path):
        # Parent directory must already exist; _safe_write_bytes does not
        # mkdir -p its own parents. os.open returns FileNotFoundError.
        target = tmp_path / "nonexistent_dir" / "book.kfx"
        with pytest.raises(FileNotFoundError):
            _safe_write_bytes(str(target), b"x")
        # Nothing was created.
        assert not target.exists()
        assert not (tmp_path / "nonexistent_dir").exists()

    def test_tmp_slot_existing_regular_file_overwritten(self, tmp_path):
        # If the tmp slot is a regular file (e.g. left over from a prior
        # crashed run), O_TRUNC on the os.open overwrites it. This is
        # current behaviour; pinning it so a future change to the flags
        # doesn't silently regress to surprising-fail-or-append.
        target = tmp_path / "book.kfx"
        tmp_slot = tmp_path / "book.kfx.tmp"
        tmp_slot.write_bytes(b"stale")

        _safe_write_bytes(str(target), b"fresh")

        assert target.read_bytes() == b"fresh"
        # tmp slot is renamed to target by os.replace, not left behind.
        assert not tmp_slot.exists()


@pytest.mark.tier1
@pytest.mark.unit
class TestSafeWriteCleanup:
    def test_dest_unchanged_when_write_fails(self, tmp_path, monkeypatch):
        # Simulate a write failure mid-way; assert dest is untouched and
        # tmp slot is cleaned up.
        target = tmp_path / "book.kfx"
        target.write_bytes(b"existing")

        original_fdopen = os.fdopen

        def boom(fd, mode):
            handle = original_fdopen(fd, mode)

            class _ExplodingWrapper:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    handle.close()
                    return False

                def write(self_inner, _data):
                    raise IOError("disk full")

            return _ExplodingWrapper()

        monkeypatch.setattr(os, "fdopen", boom)
        with pytest.raises(IOError, match="disk full"):
            _safe_write_bytes(str(target), b"new-data")

        # Original content preserved, tmp slot cleaned up.
        assert target.read_bytes() == b"existing"
        assert not (tmp_path / "book.kfx.tmp").exists()


@pytest.mark.tier1
@pytest.mark.unit
class TestSafeWriteAcceptsPathLike:
    """#102: callers may pass a pathlib.Path, not just str. The safety
    checks ran on str(path) but tmp/islink/replace used the raw object,
    so `path + ".tmp"` raised TypeError on a Path. Normalize via
    os.fspath and keep the traversal defenses intact for Path inputs."""

    def test_accepts_pathlib_path(self, tmp_path):
        target = tmp_path / "book.kfx"
        _safe_write_bytes(target, b"KFX-data")
        assert target.read_bytes() == b"KFX-data"
        assert not (tmp_path / "book.kfx.tmp").exists()

    def test_pathlib_path_traversal_still_rejected(self, tmp_path):
        # The '..' defense must still fire when the input is a Path,
        # not only when it is a str (fspath normalization must not
        # bypass the layered checks).
        evil = tmp_path / ".." / "escape.kfx"
        with pytest.raises(ValueError, match=r"\.\."):
            _safe_write_bytes(evil, b"attacker-payload")
        assert not (tmp_path.parent / "escape.kfx").exists()


def _current_umask():
    """Return current umask without permanently changing it."""
    mask = os.umask(0)
    os.umask(mask)
    return mask
