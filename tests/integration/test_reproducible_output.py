"""Converting the same book twice must produce the same bytes (#96).

The defect this pins is per-process: CPython randomizes string hashing, so a
set iterated in one interpreter can yield a different order in the next. A
same-process double conversion cannot see it — these tests run the conversion
in subprocesses with different PYTHONHASHSEED values.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _jpeg(width: int, height: int) -> bytes:
    """A JPEG carrying a SOF0 with the given dimensions, which is all the size
    classifier reads.

    `MINIMAL_JPEG` has no SOF at all and so classifies as `inline`; pairing it
    with this one puts two size classes in one book, which is what makes the
    allocation order observable. Padded past 100 bytes with a comment segment
    because `converter.py` drops anything smaller as not-really-an-image.
    """
    pad = b"kfxgen reproducibility fixture padding. " * 4
    comment = b"\xff\xfe" + bytes([(len(pad) + 2) >> 8, (len(pad) + 2) & 0xFF]) + pad
    return (
        b"\xff\xd8"
        + comment
        + b"\xff\xc0\x00\x11\x08"
        + bytes([height >> 8, height & 0xFF, width >> 8, width & 0xFF])
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
        + b"\xff\xd9"
    )


_SCRIPT = textwrap.dedent(
    """
    import sys, hashlib
    from pathlib import Path
    root = Path(sys.argv[1])
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "plugin"))
    from kfxgen import converter
    from tests._helpers import MINIMAL_JPEG, NullLog
    from tests.fixtures.epub_builder import EpubBuilder
    from tests.fixtures.oeb_shim import EpubAsOeb
    from tests.integration.test_reproducible_output import _jpeg

    out = Path(sys.argv[2])
    body = (
        '<p>Opening paragraph.</p>'
        '<p><img src="page.jpg" alt="a page-sized image"/></p>'
        '<p>Middle paragraph.</p>'
        '<p><img src="tiny.jpg" alt="an unsized image"/></p>'
        '<p>Closing paragraph.</p>'
    )
    page = (
        '<?xml version="1.0" encoding="utf-8"?>\\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Images</title></head><body>' + body + '</body></html>'
    )
    epub = (
        EpubBuilder()
        .set_metadata(title="Repro", author="Repro Author")
        .add_chapter("Images", page.encode())
        .add_chapter("Plain", "<p>Second chapter body.</p>".encode())
        .add_manifest_item(
            item_id="page", href="page.jpg", media_type="image/jpeg",
            data=_jpeg(700, 700),
        )
        .add_manifest_item(
            item_id="tiny", href="tiny.jpg", media_type="image/jpeg",
            data=MINIMAL_JPEG,
        )
        .build(out, "repro")
    )
    kfx = out / "repro.kfx"
    converter.convert_oeb_to_kfx(EpubAsOeb(epub), str(kfx), opts=None, log=NullLog())
    print(hashlib.sha256(kfx.read_bytes()).hexdigest())
    """
)


def _convert_with_seed(seed: str, work: Path) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    out = work / f"seed{seed}"
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(_ROOT), str(out)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ROOT),
    )
    assert proc.returncode == 0, f"conversion failed:\n{proc.stderr[-2000:]}"
    return proc.stdout.strip().splitlines()[-1]


@pytest.mark.integration
def test_same_book_converts_to_identical_bytes_across_processes(tmp_path):
    """A book with more than one image size class must still be reproducible.

    The image `$157` styles were allocated by iterating a set of size-class
    names, so their order — and every local symbol id numbered after them —
    varied per interpreter. (#96)
    """
    digests = {seed: _convert_with_seed(seed, tmp_path) for seed in ("0", "1", "2")}
    assert len(set(digests.values())) == 1, (
        f"Conversion is not reproducible across processes: {digests}"
    )
