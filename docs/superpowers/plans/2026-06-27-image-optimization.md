# Image Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Downscale and recompress over-size images during KFX conversion so heavily illustrated books produce device-friendly files, on by default with an escape hatch.

**Architecture:** A new pure-ish module `plugin/kfxgen/image_optimize.py` exposes `optimize_image` (single image) and `optimize_images` (cover + body dict). Dimension is read with a dependency-free header parser; the actual resize/re-encode uses `calibre.utils.img`, lazily imported, with a no-op fallback when unavailable (CI / non-Calibre). `convert_oeb_to_kfx` calls `optimize_images` as a distinct stage unless the user set the "Embed original images" option.

**Tech Stack:** Python 3.13, pytest, `calibre.utils.img` (runtime only; mocked in tests).

## Global Constraints

- Python interpreter: Calibre's bundled Python; minimum Calibre 5.0.
- No new third-party dependencies (plugin path uses `calibre.utils.img`, already present).
- Optimization MUST never raise into the conversion — any failure falls back to original bytes.
- Default max long edge = 2048 px; default JPEG quality = 85.
- Only act on images whose longest edge exceeds max_dim; images at/under the limit are returned byte-identical (keeps golden fixtures unchanged).
- Env-var overrides: `KFXGEN_IMAGE_MAX_DIM`, `KFXGEN_IMAGE_QUALITY` (validated; fall back to default on invalid/out-of-range).
- Escape hatch: Calibre option `kfxgen_embed_original_images` (default False).
- Work happens in the public repo clone on a feature branch; land via PR.

---

### Task 1: Image size parser + env-var reader

**Files:**
- Create: `plugin/kfxgen/image_optimize.py`
- Test: `tests/unit/test_image_optimize.py`

**Interfaces:**
- Produces: `_read_image_size(data: bytes) -> tuple[int, int] | None`; `_read_env_int(name: str, default: int, lo: int, hi: int, log) -> int`; module constants `DEFAULT_MAX_DIM = 2048`, `DEFAULT_JPEG_QUALITY = 85`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_image_optimize.py
import struct
import pytest
from kfxgen import image_optimize as io


def _png(w, h):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00")


def _jpeg(w, h):
    # SOI, APP0 stub, SOF0 (len=17, precision=8, height, width), EOI
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00" + b"\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


class _Log:
    def __init__(self): self.warns = []
    def warn(self, m): self.warns.append(m)
    def info(self, m): pass
    def debug(self, m): pass


def test_read_size_png():
    assert io._read_image_size(_png(3000, 2000)) == (3000, 2000)


def test_read_size_jpeg():
    assert io._read_image_size(_jpeg(2500, 1800)) == (2500, 1800)


def test_read_size_unknown_returns_none():
    assert io._read_image_size(b"not an image") is None
    assert io._read_image_size(b"\xff\xd8short") is None


def test_env_int_default_when_unset(monkeypatch):
    monkeypatch.delenv("KFXGEN_IMAGE_MAX_DIM", raising=False)
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, _Log()) == 2048


def test_env_int_valid(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "1600")
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, _Log()) == 1600


def test_env_int_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "huge")
    log = _Log()
    assert io._read_env_int("KFXGEN_IMAGE_MAX_DIM", 2048, 16, 20000, log) == 2048
    assert log.warns


def test_env_int_out_of_range_falls_back(monkeypatch):
    monkeypatch.setenv("KFXGEN_IMAGE_QUALITY", "999")
    assert io._read_env_int("KFXGEN_IMAGE_QUALITY", 85, 1, 100, _Log()) == 85
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_image_optimize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kfxgen.image_optimize'` (or import error).

- [ ] **Step 3: Write minimal implementation**

```python
# plugin/kfxgen/image_optimize.py
"""Image optimization for KFX output (#11).

Downscales and recompresses over-size images so heavily illustrated books
produce device-friendly KFX. On by default in the conversion path. The actual
resize uses calibre.utils.img at runtime; outside Calibre (tests/CI) it is a
no-op so it never breaks anything.
"""

import os
import struct

DEFAULT_MAX_DIM = 2048
DEFAULT_JPEG_QUALITY = 85

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# JPEG Start-Of-Frame markers that carry image dimensions.
_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _read_image_size(data):
    """Return (width, height) for PNG or JPEG bytes, else None."""
    if len(data) >= 24 and data[:8] == _PNG_SIG:
        w, h = struct.unpack(">II", data[16:24])
        return (int(w), int(h))
    if data[:2] == b"\xff\xd8":  # JPEG SOI
        i, n = 2, len(data)
        while i + 1 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if i + 4 > n:
                break
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in _SOF_MARKERS:
                if i + 9 <= n:
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return (int(w), int(h))
                break
            i += 2 + seg_len
        return None
    return None


def _read_env_int(name, default, lo, hi, log):
    """Read an int env var, falling back to default on missing/invalid/range."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        log.warn(f"  ignoring invalid {name}={raw!r} (not an integer); using {default}")
        return default
    if n < lo or n > hi:
        log.warn(f"  ignoring out-of-range {name}={n} (allowed {lo}-{hi}); using {default}")
        return default
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_image_optimize.py -v`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/image_optimize.py tests/unit/test_image_optimize.py
git commit -m "feat(images): image size parser + env-var reader (#11)"
```

---

### Task 2: `optimize_image` single-image optimizer

**Files:**
- Modify: `plugin/kfxgen/image_optimize.py`
- Test: `tests/unit/test_image_optimize.py`

**Interfaces:**
- Consumes: `_read_image_size`, `DEFAULT_MAX_DIM`, `DEFAULT_JPEG_QUALITY` (Task 1).
- Produces: `optimize_image(data: bytes, *, max_dim=2048, jpeg_quality=85, log=None) -> bytes`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_image_optimize.py
import sys
import types


def test_optimize_image_small_is_identity():
    data = _jpeg(800, 600)
    assert io.optimize_image(data, max_dim=2048, log=_Log()) is data


def test_optimize_image_no_calibre_is_noop():
    # calibre.utils.img is absent in CI -> over-size image returns unchanged
    big = _jpeg(4000, 3000)
    assert io.optimize_image(big, max_dim=2048, log=_Log()) == big


def test_optimize_image_downscales_via_calibre(monkeypatch):
    calls = {}
    fake = types.ModuleType("calibre.utils.img")

    def scale_image(data, width, height, as_png=False, compression_quality=90):
        calls["args"] = (width, height, as_png, compression_quality)
        return ("JPEG", b"small-bytes")

    fake.scale_image = scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)

    big = _jpeg(4000, 3000)
    out = io.optimize_image(big, max_dim=2048, jpeg_quality=85, log=_Log())
    assert out == b"small-bytes"
    assert calls["args"] == (2048, 2048, False, 85)


def test_optimize_image_keeps_png_format(monkeypatch):
    seen = {}
    fake = types.ModuleType("calibre.utils.img")

    def scale_image(data, width, height, as_png=False, compression_quality=90):
        seen["as_png"] = as_png
        return ("PNG", b"x" * 10)

    fake.scale_image = scale_image
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)

    out = io.optimize_image(_png(4000, 3000), max_dim=2048, log=_Log())
    assert seen["as_png"] is True
    assert out == b"x" * 10


def test_optimize_image_keeps_original_if_result_larger(monkeypatch):
    fake = types.ModuleType("calibre.utils.img")
    fake.scale_image = lambda *a, **k: ("JPEG", b"Z" * 100000)
    monkeypatch.setitem(sys.modules, "calibre", types.ModuleType("calibre"))
    monkeypatch.setitem(sys.modules, "calibre.utils", types.ModuleType("calibre.utils"))
    monkeypatch.setitem(sys.modules, "calibre.utils.img", fake)

    big = _jpeg(4000, 3000)
    assert io.optimize_image(big, max_dim=2048, log=_Log()) == big
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_image_optimize.py -k optimize_image -v`
Expected: FAIL — `AttributeError: module 'kfxgen.image_optimize' has no attribute 'optimize_image'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to plugin/kfxgen/image_optimize.py

def optimize_image(data, *, max_dim=DEFAULT_MAX_DIM, jpeg_quality=DEFAULT_JPEG_QUALITY, log=None):
    """Downscale + recompress an over-size JPEG/PNG.

    Returns optimized bytes, or the original bytes unchanged when no
    optimization applies, calibre is unavailable, or anything fails.
    Never raises.
    """
    size = _read_image_size(data)
    if size is None:
        return data
    if max(size) <= max_dim:
        return data
    is_png = data[:8] == _PNG_SIG
    try:
        from calibre.utils.img import scale_image
    except Exception:
        if log:
            log.debug("  calibre.utils.img unavailable; leaving image at original size")
        return data
    try:
        # scale_image fits within the (width, height) box, preserving aspect.
        _fmt, out = scale_image(
            data, width=max_dim, height=max_dim,
            as_png=is_png, compression_quality=jpeg_quality,
        )
    except Exception as e:  # noqa: BLE001 - never fail a conversion over an image
        if log:
            log.warn(f"  image optimize failed ({e}); keeping original")
        return data
    if not out or len(out) >= len(data):
        return data
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_image_optimize.py -v`
Expected: PASS (all tests, Task 1 + Task 2).

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/image_optimize.py tests/unit/test_image_optimize.py
git commit -m "feat(images): optimize_image with calibre downscale + no-op fallback (#11)"
```

---

### Task 3: `optimize_images` cover + body wrapper

**Files:**
- Modify: `plugin/kfxgen/image_optimize.py`
- Test: `tests/unit/test_image_optimize.py`

**Interfaces:**
- Consumes: `optimize_image`, `_read_env_int`, `DEFAULT_MAX_DIM`, `DEFAULT_JPEG_QUALITY`.
- Produces: `optimize_images(cover_image: bytes | None, images: dict[str, bytes], log) -> tuple[bytes | None, dict[str, bytes]]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_image_optimize.py

def test_optimize_images_maps_all_and_handles_none_cover(monkeypatch):
    # Force the per-image optimizer to a deterministic stub.
    monkeypatch.setattr(io, "optimize_image",
                        lambda data, **k: b"OPT" + data[:1])
    cover, imgs = io.optimize_images(None, {"a.jpg": b"AAAA", "b.png": b"BBBB"}, _Log())
    assert cover is None
    assert imgs == {"a.jpg": b"OPTA", "b.png": b"OPTB"}


def test_optimize_images_optimizes_cover(monkeypatch):
    monkeypatch.setattr(io, "optimize_image", lambda data, **k: b"C")
    cover, imgs = io.optimize_images(b"COVERDATA", {}, _Log())
    assert cover == b"C"
    assert imgs == {}


def test_optimize_images_reads_env_overrides(monkeypatch):
    seen = {}
    monkeypatch.setenv("KFXGEN_IMAGE_MAX_DIM", "1600")
    monkeypatch.setenv("KFXGEN_IMAGE_QUALITY", "70")

    def spy(data, *, max_dim, jpeg_quality, log):
        seen["max_dim"] = max_dim
        seen["q"] = jpeg_quality
        return data

    monkeypatch.setattr(io, "optimize_image", spy)
    io.optimize_images(b"COVER", {"a.jpg": b"AAAA"}, _Log())
    assert seen["max_dim"] == 1600
    assert seen["q"] == 70
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_image_optimize.py -k optimize_images -v`
Expected: FAIL — `AttributeError: ... has no attribute 'optimize_images'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to plugin/kfxgen/image_optimize.py

_MIN_MAX_DIM, _MAX_MAX_DIM = 16, 20000


def optimize_images(cover_image, images, log):
    """Optimize the cover and every body image. Returns (cover, images)."""
    max_dim = _read_env_int("KFXGEN_IMAGE_MAX_DIM", DEFAULT_MAX_DIM,
                            _MIN_MAX_DIM, _MAX_MAX_DIM, log)
    quality = _read_env_int("KFXGEN_IMAGE_QUALITY", DEFAULT_JPEG_QUALITY,
                            1, 100, log)
    before = after = 0
    new_images = {}
    for href, data in images.items():
        before += len(data)
        opt = optimize_image(data, max_dim=max_dim, jpeg_quality=quality, log=log)
        after += len(opt)
        new_images[href] = opt
    new_cover = cover_image
    if cover_image:
        before += len(cover_image)
        new_cover = optimize_image(cover_image, max_dim=max_dim,
                                   jpeg_quality=quality, log=log)
        after += len(new_cover)
    if before and after < before:
        saved = before - after
        log.info(f"  Image optimization: {before:,} -> {after:,} bytes "
                 f"(saved {saved:,}, {round(100 * saved / before)}%)")
    return new_cover, new_images
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_image_optimize.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/image_optimize.py tests/unit/test_image_optimize.py
git commit -m "feat(images): optimize_images cover+body wrapper with env knobs (#11)"
```

---

### Task 4: Wire optimization into `convert_oeb_to_kfx`

**Files:**
- Modify: `plugin/kfxgen/converter.py` (insert after body-image extraction, ~line 923, inside `convert_oeb_to_kfx`)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `optimize_images` (Task 3); `opts.kfxgen_embed_original_images` (Task 5 defines the option; read defensively with `getattr`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_converter.py
import types as _types
from kfxgen import converter as _conv


class _OptsStub:
    def __init__(self, embed): self.kfxgen_embed_original_images = embed


class _Log2:
    def info(self, *a): pass
    def warn(self, *a): pass
    def debug(self, *a): pass
    def error(self, *a): pass


def _patch_pipeline(monkeypatch, captured):
    monkeypatch.setattr(_conv, "extract_metadata",
                        lambda *a, **k: {"title": "T", "author": "A",
                                         "language": "en", "publisher": "P",
                                         "issue_date": None})
    monkeypatch.setattr(_conv, "extract_cover_image", lambda *a, **k: (b"COVER", "c.jpg"))
    monkeypatch.setattr(_conv, "extract_images_from_oeb",
                        lambda *a, **k: {"x.jpg": b"XX"})
    monkeypatch.setattr(_conv, "extract_chapters_from_oeb",
                        lambda *a, **k: [{"text": "hi"}])

    class _Gen:
        def generate_full_book(self, **kw):
            captured["images"] = kw["images"]
            captured["cover"] = kw["cover_image"]
            # create the output file so the success branch passes
            with open(kw["output_path"], "wb") as f:
                f.write(b"KFX")
    monkeypatch.setattr(_conv, "NativeKFXGenerator", lambda: _Gen())


def test_optimization_runs_by_default(monkeypatch, tmp_path):
    captured = {}
    _patch_pipeline(monkeypatch, captured)
    called = {}
    monkeypatch.setattr(_conv, "optimize_images",
                        lambda cover, images, log: (called.setdefault("yes", True), (b"C2", {"x.jpg": b"Y"}))[1],
                        raising=False)
    out = tmp_path / "o.kfx"
    _conv.convert_oeb_to_kfx(object(), str(out), _OptsStub(False), _Log2())
    assert called.get("yes") is True
    assert captured["cover"] == b"C2"
    assert captured["images"] == {"x.jpg": b"Y"}


def test_optimization_skipped_when_embed_originals(monkeypatch, tmp_path):
    captured = {}
    _patch_pipeline(monkeypatch, captured)
    monkeypatch.setattr(_conv, "optimize_images",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
                        raising=False)
    out = tmp_path / "o.kfx"
    _conv.convert_oeb_to_kfx(object(), str(out), _OptsStub(True), _Log2())
    assert captured["images"] == {"x.jpg": b"XX"}  # originals untouched
    assert captured["cover"] == b"COVER"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -k optimization -v`
Expected: FAIL — `optimize_images` not imported in converter / not called (AttributeError or assertion).

- [ ] **Step 3: Write minimal implementation**

In `plugin/kfxgen/converter.py`, inside `convert_oeb_to_kfx`, immediately after the body-image extraction block (the `images = extract_images_from_oeb(...)` call, ~line 923) and before "Extract structured chapters", insert:

```python
    # Optimize over-size images unless the user opted to embed originals (#11).
    if getattr(opts, "kfxgen_embed_original_images", False):
        log.info("  Image optimization disabled (embed original images)")
    else:
        cover_image, images = optimize_images(cover_image, images, log)
```

And add the import near the top of `converter.py` with the other local imports:

```python
from .image_optimize import optimize_images
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py -v`
Expected: PASS (new tests + existing converter tests).

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/converter.py tests/unit/test_converter.py
git commit -m "feat(images): run image optimization in convert_oeb_to_kfx with escape hatch (#11)"
```

---

### Task 5: Expose the "Embed original images" Calibre option

**Files:**
- Modify: `plugin/__init__.py` (import + `options` attribute on `KFXGenOutputPlugin`)
- Test: `tests/unit/test_plugin_options.py`

**Interfaces:**
- Consumes: nothing.
- Produces: Calibre option `kfxgen_embed_original_images` (default False), surfaced in the GUI and as `--kfxgen-embed-original-images` on the CLI; read by Task 4.

Note: `plugin/__init__.py` imports `calibre.*`, which is unavailable in CI, so the test asserts on the **source text** rather than importing the module.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_plugin_options.py
from pathlib import Path

SRC = Path("plugin/__init__.py").read_text()


def test_imports_option_recommendation():
    assert "OptionRecommendation" in SRC


def test_defines_embed_original_images_option():
    assert "kfxgen_embed_original_images" in SRC
    # default must be False (optimization on by default)
    assert "recommended_value=False" in SRC


def test_option_has_help_text():
    assert "original resolution" in SRC.lower() or "embed" in SRC.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_plugin_options.py -v`
Expected: FAIL — `kfxgen_embed_original_images` / `OptionRecommendation` not present.

- [ ] **Step 3: Write minimal implementation**

In `plugin/__init__.py`, change the import line:

```python
from calibre.customize.conversion import OutputFormatPlugin, OptionRecommendation
```

Add the `options` attribute to `KFXGenOutputPlugin` (alongside `name`, `file_type`, etc.):

```python
    options = {
        OptionRecommendation(
            name="kfxgen_embed_original_images",
            recommended_value=False,
            help=(
                "Embed images at their original resolution instead of "
                "downscaling/recompressing them for Kindle. Produces much "
                "larger KFX files for illustrated books."
            ),
        ),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_plugin_options.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugin/__init__.py tests/unit/test_plugin_options.py
git commit -m "feat(images): add 'Embed original images' Calibre option (#11)"
```

---

### Task 6: Docs + full suite + CHANGELOG

**Files:**
- Modify: `README.md` (remove/adjust the image-size limitation now that it's addressed; document the option + env knobs)
- Modify: `CHANGELOG.md` (new version entry)
- Modify: `plugin/kfxgen/__init__.py` (version bump)

**Interfaces:** none.

- [ ] **Step 1: Run the full suite (no test to add; this is the regression gate)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, including the golden corpus (golden fixtures use <2048px images, so KFX output is byte-identical — proving no regression for normal books).

- [ ] **Step 2: Update README limitation + document the option**

In `README.md`, replace the image-size limitation bullet (the one linking #11) with a feature/usage note:

```markdown
- Images larger than 2048 px on the long edge are automatically downscaled and recompressed (JPEG quality 85) so illustrated books stay a reasonable size. To keep originals, enable **Embed original images** in the KFX output options (CLI: `--kfxgen-embed-original-images`). Tune with `KFXGEN_IMAGE_MAX_DIM` and `KFXGEN_IMAGE_QUALITY`.
```

- [ ] **Step 3: Bump version + CHANGELOG**

In `plugin/kfxgen/__init__.py` bump `version` (e.g. `(5, 3, 19)`). Prepend to `CHANGELOG.md`:

```markdown
## 5.3.19 — Automatic image optimization

Heavily illustrated books no longer produce huge KFX files. Images whose
longest edge exceeds 2048 px are downscaled and recompressed (JPEG quality
85; PNG kept as PNG) during conversion.

- On by default. Disable with the **Embed original images** output option
  (CLI `--kfxgen-embed-original-images`).
- Tunable via `KFXGEN_IMAGE_MAX_DIM` and `KFXGEN_IMAGE_QUALITY`.
- Closes #11.
```

- [ ] **Step 4: Run the suite once more**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md plugin/kfxgen/__init__.py
git commit -m "docs+release: automatic image optimization, v5.3.19 (#11)"
```

---

## Notes for the implementer

- The actual `calibre.utils.img.scale_image` resize cannot run in CI (no Calibre). Tests mock it; verify the real downscale on a Calibre install by converting an image-heavy book and confirming the KFX shrinks dramatically (the "What It's Like to Be a Bird" case went 308 MB at full-res).
- If `scale_image`'s keyword names differ in the target Calibre version, adjust the call in `optimize_image` only — the tests assert on the arguments we pass, so update both together.
- Do not modify the golden fixtures. Their images are small by design; that they stay byte-identical is the regression guard.
