# Image optimization for KFX output — design

Issue: [#11](https://github.com/jloutsch/kfxgen/issues/11)
Date: 2026-06-27

## Problem

kfxgen embeds body images and the cover as raw source bytes — no downscaling
or recompression anywhere in `converter.py` / `native_generator.py`. Heavily
illustrated books therefore produce enormous KFX files: a real conversion of a
469-image book (v5.3.18) yielded a 308 MB KFX. That is impractical to sideload
and store, and some Kindles handle very large KFX poorly.

## Goal

Downscale and recompress over-size images during conversion so image-heavy
books produce device-friendly KFX, on by default, with an escape hatch to keep
originals. Bounded to what KFX/Kindle actually need; no attempt to support
arbitrary image pipelines.

## Scope

In scope (this change):
- The **Calibre plugin path** only. It runs inside Calibre, so
  `calibre.utils.img` is available — no new dependency.
- Body images and the cover image.

Out of scope (deferred, noted in the issue):
- The direct-Python research path (`research/`), which would need Pillow.
- Converting opaque PNG → JPEG.
- Re-encoding images that are already within the dimension limit.

## Decisions

| Decision | Choice |
|---|---|
| Default behavior | **On by default**, with an escape hatch to embed originals. |
| Trigger | **Dimension-only.** Act on an image when its longest edge exceeds `max_dim`. Images at or under the limit are passed through untouched (no quality loss). |
| Max long edge (`max_dim`) | **2048 px** default. |
| JPEG quality | **85** default. |
| Format handling | JPEG over the limit → downscale + re-encode JPEG at quality. PNG over the limit → downscale, keep PNG (preserves transparency / line art). |
| Escape hatch | Calibre GUI checkbox **"Embed original images"** (`OptionRecommendation`, default `False`), read from `opts`. |
| Numeric knobs | Env vars `KFXGEN_IMAGE_MAX_DIM` (default 2048) and `KFXGEN_IMAGE_QUALITY` (default 85), validated like `KFXGEN_MAX_DECODE_SIZE` (positive int, sane bounds, fall back to default on invalid). |

## Architecture

New module `plugin/kfxgen/image_optimize.py`:

- `_read_image_size(data: bytes) -> tuple[int, int] | None`
  Pure-Python JPEG/PNG header parse to get `(width, height)`. No Calibre
  dependency, so it is fully unit-testable in CI. Returns `None` if the size
  can't be determined (caller then leaves the image untouched).

- `optimize_image(data: bytes, *, max_dim: int = 2048, jpeg_quality: int = 85, log) -> bytes`
  1. Read size via `_read_image_size`. If unknown or longest edge ≤ `max_dim`,
     return `data` unchanged.
  2. Otherwise compute the target size (scale longest edge to `max_dim`,
     preserve aspect ratio), then **lazy-import** `calibre.utils.img` and use it
     to decode → scale → re-encode (JPEG at `jpeg_quality`; PNG stays PNG).
  3. If `calibre.utils.img` is unavailable (e.g. CI / non-Calibre runtime) or
     any step raises, log and **return the original bytes** — never fail the
     conversion. Also return the original if the "optimized" result is somehow
     larger than the input.

Integration point: a distinct stage in `convert_oeb_to_kfx` (`converter.py`).
After body images and the cover are extracted (and magic-byte validated as
today), map `optimize_image` over the body-image dict values and the cover
bytes, unless the escape hatch is set. The existing extractor functions
(`extract_inline_images`, `extract_cover_image`, `_get_cover_image_data`) stay
pure and unchanged — optimization is a separate, testable step.

The plugin wrapper (`plugin/__init__.py`) gains:
```python
from calibre.customize.conversion import OutputFormatPlugin, OptionRecommendation
...
options = {
    OptionRecommendation(
        name='kfxgen_embed_original_images',
        recommended_value=False,
        help='Embed images at their original resolution instead of '
             'downscaling/recompressing them for Kindle. Produces much '
             'larger KFX files for illustrated books.'),
}
```
The value arrives as `opts.kfxgen_embed_original_images`; the converter reads it
with `getattr(opts, 'kfxgen_embed_original_images', False)` so non-Calibre
callers and tests work without the attribute.

## Error handling

- Decode/scale/encode failure → warn, keep original bytes.
- `calibre.utils.img` missing → debug log, keep original bytes (no-op).
- Invalid env-var values → warn, use defaults.
- Magic-byte validation (#46) remains upstream, unchanged. Optimization only
  ever sees already-validated JPEG/PNG bytes.

## Testing (TDD)

Unit (`tests/unit/test_image_optimize.py`), all CI-runnable without Calibre:
- `_read_image_size`: correct dimensions for known JPEG and PNG fixtures;
  `None` for truncated/garbage input.
- `optimize_image` decision logic:
  - image ≤ `max_dim` → returned unchanged (identity).
  - `calibre.utils.img` unavailable → returned unchanged (monkeypatch the
    import to fail), no exception.
  - over `max_dim` with `calibre.utils.img` mocked → scale called with the
    expected target dimensions; format preserved (JPEG stays JPEG, PNG stays
    PNG); JPEG path passes `jpeg_quality`.
  - env-var overrides parsed and clamped like `MAX_DECODE_SIZE`.
- Escape hatch: `convert_oeb_to_kfx` skips optimization when
  `opts.kfxgen_embed_original_images` is true (verified via a spy/mock on
  `optimize_image`).

Golden/regression:
- Existing golden KFX fixtures use tiny (<2048) synthetic images, so they remain
  **byte-identical** — no golden regression.
- Add a synthetic oversized image (longest edge > 2048) and assert the optimized
  output is smaller and within the limit. Where actual scaling needs Calibre,
  drive it through the mock; otherwise assert the no-op path.

## Acceptance criteria

- A book whose images exceed 2048 px produces a KFX dramatically smaller than
  today, at default settings, with no conversion failure.
- The "Embed original images" checkbox (GUI), or its Calibre CLI equivalent
  `--kfxgen-embed-original-images`, restores the current full-resolution
  behavior.
- `KFXGEN_IMAGE_MAX_DIM` / `KFXGEN_IMAGE_QUALITY` change the limits.
- All existing tests still pass; golden fixtures unchanged.
