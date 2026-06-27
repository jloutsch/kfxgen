"""
Regenerate every golden KFX under `tests/fixtures/golden/expected/`.

Run after intentional generator changes that alter byte output:

    python -m tests.fixtures.golden.regenerate

The script:
  1. For each (name, builder) in GOLDEN_INPUTS:
     - Builds a fresh EPUB in a temp dir
     - Wraps it with EpubAsOeb, runs converter + NativeKFXGenerator
     - Writes the resulting KFX to expected/<name>.kfx
  2. Prints a summary diff (file size, sha256) so the operator can
     eyeball what changed before staging the new bytes.

Used by:
  - First-time fixture creation (commit the produced .kfx files)
  - Intentional-change updates (re-run, diff with `git diff --stat`,
    re-run `pytest -m tier3` to confirm structural diff is clean,
    commit input + golden together)
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

# Make project + plugin imports work when run as a script.
_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "plugin"))

from kfxgen import converter  # noqa: E402

from tests.fixtures.golden.inputs import GOLDEN_INPUTS  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

EXPECTED_DIR = Path(__file__).parent / "expected"


from tests._helpers import NullLog as _NullLog  # noqa: E402


def build_kfx(name: str, builder, work_dir: Path) -> bytes:
    """Run a single golden input through the production conversion pipeline
    (`converter.convert_oeb_to_kfx`) and return the resulting KFX bytes.

    Going through the full pipeline (not the leaner per-step path used in
    other tests) is deliberate: this matches what plugin users actually
    run, so a regression in any pipeline stage — metadata extraction,
    cover detection, body-image extraction, chapter splitting, or the
    generator itself — surfaces in a golden diff.
    """
    out_dir = work_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    epub_path = builder(out_dir)
    oeb = EpubAsOeb(epub_path)
    kfx_path = out_dir / f"{name}.kfx"
    converter.convert_oeb_to_kfx(oeb, str(kfx_path), opts=None, log=_NullLog())
    return kfx_path.read_bytes()


def main() -> int:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        for name, builder in GOLDEN_INPUTS:
            print(f"[regenerate] {name} ...", end=" ", flush=True)
            try:
                data = build_kfx(name, builder, work_dir)
            except Exception as e:  # surface failure but keep going
                print(f"FAIL: {e}")
                return 1
            target = EXPECTED_DIR / f"{name}.kfx"
            existed = target.exists()
            old_sha = (
                hashlib.sha256(target.read_bytes()).hexdigest() if existed else None
            )
            target.write_bytes(data)
            new_sha = hashlib.sha256(data).hexdigest()
            verb = (
                "updated"
                if existed and old_sha != new_sha
                else ("unchanged" if existed else "created")
            )
            print(
                f"{verb}  size={len(data):>7}  sha256={new_sha[:12]}"
                + (f"  (was {old_sha[:12]})" if existed and old_sha != new_sha else "")
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
