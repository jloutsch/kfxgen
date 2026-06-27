"""
Performance benchmark: ensure native KFX generation stays fast.

The whole point of kfxgen vs jhowell's `KFX Output` (which shells out to
Kindle Previewer 3 for ~130s on a 73-chapter novel) is the speedup. These
tests are guard rails against accidental regressions in the native path.

Marked `@pytest.mark.slow` so they're opt-in; default `pytest tests/unit`
doesn't run them. Invoke with:
    pytest tests/benchmarks -v
"""

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.native_generator import NativeKFXGenerator


@pytest.mark.slow
def test_minimal_prose_under_2s():
    """A 5-chapter prose book generates in well under 2s on any modern hardware.

    Catches algorithmic regressions early — anything slower than this on a
    trivial fixture indicates an O(n^2) loop snuck in somewhere.
    """
    chapters = [
        {
            "title": f"Ch{i}",
            "text": f"Ch{i}\n\n" + "\n\n".join(f"Paragraph {j}." for j in range(20)),
        }
        for i in range(5)
    ]
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    try:
        start = time.perf_counter()
        gen.generate_full_book(
            title="Bench Book",
            author="Test",
            chapters=chapters,
            output_path=path,
        )
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, (
            f"Native generation of 5-chapter book took {elapsed:.2f}s; "
            f"expected under 2s. Investigate whether an O(n^2) loop or "
            f"redundant fragment serialization was introduced."
        )
    finally:
        os.unlink(path)


@pytest.mark.slow
def test_long_book_under_15s():
    """A 75-chapter book (real-novel size) generates in under 15s.

    jhowell's KFX Output takes ~130s on the same input via Kindle Previewer 3.
    The native path's value proposition is preserving an order-of-magnitude
    speedup. 15s is a generous ceiling — typical M-series Mac runs ~5s.
    """
    chapters = [
        {
            "title": f"Chapter {i}",
            "text": (
                f"Chapter {i}\n\n"
                + "\n\n".join(f"Paragraph {j} of chapter {i}." for j in range(50))
            ),
        }
        for i in range(75)
    ]
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    try:
        start = time.perf_counter()
        gen.generate_full_book(
            title="Long Book Benchmark",
            author="Test",
            chapters=chapters,
            output_path=path,
        )
        elapsed = time.perf_counter() - start
        assert elapsed < 15.0, (
            f"Native generation of 75-chapter book took {elapsed:.2f}s; "
            f"expected under 15s. The whole product value over jhowell's "
            f"KFX Output (~130s via KP3) is the speedup — guard it."
        )
    finally:
        os.unlink(path)
