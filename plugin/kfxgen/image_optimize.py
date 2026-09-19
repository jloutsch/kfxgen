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

#: Byte ceiling above which an image is re-encoded even when its pixel
#: dimensions are already acceptable (#55). Dimensions alone are a poor proxy
#: for weight: a 1161x1800 cover encoded near-lossless, with EXIF + Photoshop
#: + ICC baggage attached, ran to 2.25 MB and sailed through the dimension
#: gate untouched — as did every other image in that book, 7.7 MB in total.
#:
#: Measured against the reference corpus (212 images across three
#: Amazon-produced KFX files): the largest is 466 KB and not one exceeds
#: 512 KB. 1 MiB is therefore deliberately generous — roughly twice the
#: observed ceiling — so it catches the pathological tail without
#: re-compressing images that are merely large. Lower it via
#: KFXGEN_IMAGE_MAX_BYTES to track Amazon's own output more tightly.
DEFAULT_MAX_BYTES = 1024 * 1024

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# JPEG Start-Of-Frame markers that carry image dimensions.
_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


#: GIF's magic bytes, either version. Matched on the file rather than on the
#: manifest's media type, which is as trustworthy here as it is anywhere else.
GIF_SIGNATURES = (b"GIF87a", b"GIF89a")


def _read_image_size(data):
    """Return (width, height) for PNG, JPEG or GIF bytes, else None."""
    # GIF states its size in the header: little-endian, at offset 6. Here so
    # that a GIF can be measured before `gif_to_png` re-encodes it, and so
    # `optimize_image` sizes a converted page the same way it sizes any other.
    if len(data) >= 10 and data[:6] in GIF_SIGNATURES:
        w = int.from_bytes(data[6:8], "little")
        h = int.from_bytes(data[8:10], "little")
        return (w, h) if w and h else None
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
            seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
            if marker in _SOF_MARKERS:
                if i + 9 <= n:
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
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
        log.warn(
            f"  ignoring out-of-range {name}={n} (allowed {lo}-{hi}); using {default}"
        )
        return default
    return n


def optimize_image(
    data,
    *,
    max_dim=DEFAULT_MAX_DIM,
    max_bytes=DEFAULT_MAX_BYTES,
    jpeg_quality=DEFAULT_JPEG_QUALITY,
    log=None,
):
    """Downscale + recompress an over-size JPEG/PNG.

    Two independent triggers: pixel dimensions over `max_dim`, or encoded
    weight over `max_bytes`. An image can be well inside the dimension budget
    and still be far too heavy — near-lossless encoding plus EXIF/Photoshop/ICC
    baggage — which is the case the dimension gate alone missed entirely (#55).

    When only the byte trigger fires the image is re-encoded at its existing
    dimensions: it is too heavy, not too big, and shrinking it would lose
    detail for no reason.

    Returns optimized bytes, or the original bytes unchanged when no
    optimization applies, calibre is unavailable, or anything fails.
    Never raises.
    """
    size = _read_image_size(data)
    if size is None:
        return data
    over_dim = max(size) > max_dim
    over_bytes = len(data) > max_bytes
    if not over_dim and not over_bytes:
        return data
    # Fit into the dimension box only when the dimensions are the problem.
    target_w, target_h = (max_dim, max_dim) if over_dim else size
    is_png = data[:8] == _PNG_SIG
    try:
        from calibre.utils.img import scale_image
    except Exception:
        if log:
            log.debug("  calibre.utils.img unavailable; leaving image at original size")
        return data
    try:
        # scale_image fits within the (width, height) box, preserving aspect,
        # and returns (scaled_width, scaled_height, data) — a 3-tuple, not the
        # (fmt, data) pair an earlier version assumed. Unpacking it as 2 values
        # raised ValueError on every real call, so optimization silently fell
        # back to the original bytes for every image (#11).
        _w, _h, out = scale_image(
            data,
            width=target_w,
            height=target_h,
            as_png=is_png,
            compression_quality=jpeg_quality,
        )
    except Exception as e:  # noqa: BLE001 - never fail a conversion over an image
        if log:
            log.warn(f"  image optimize failed ({e}); keeping original")
        return data
    if not out or len(out) >= len(data):
        return data
    return out


def gif_to_png(data, log=None):
    """Re-encode a GIF as PNG, or None when that cannot be done here.

    kfxgen emits two image formats: `$285` JPEG and `$284` PNG. GIF has a
    symbol — `$286`, named in the generator's own format comment and listed
    among upstream kfxlib's fixed-layout formats — but nothing emits it, and
    whether a Kindle would render one from a sideloaded container is untested.

    So this converts instead of passing through. PNG is what a page scan needs
    anyway: both are lossless, so nothing is thrown away that the GIF still
    had, and PNG is a format this project has watched render on hardware.
    The cost is bytes — a scanned page as PNG is larger than as GIF — and
    `optimize_image` runs afterwards and will bring an oversized one down.

    Four books in a 226-EPUB library store every page this way and reach the
    reader with no pictures at all (#177). Against that, larger is not the
    expensive direction.

    Returns None when Calibre is not importable (tests, CI) or the re-encode
    fails, so the caller can fall back to skipping the image as it did before.
    """
    try:
        from calibre.utils.img import scale_image
    except Exception:
        if log:
            log.debug("  calibre.utils.img unavailable; cannot convert GIF")
        return None
    size = _read_image_size(data)
    if not size:
        if log:
            log.warn("  GIF has unreadable dimensions; leaving it unconverted")
        return None
    try:
        # Its own dimensions, so this step only changes the encoding. Any
        # downscaling is `optimize_image`'s decision and stays there.
        _w, _h, out = scale_image(data, width=size[0], height=size[1], as_png=True)
    except Exception as e:  # noqa: BLE001 - never fail a conversion over an image
        if log:
            log.warn(f"  GIF to PNG failed ({e}); skipping the image")
        return None
    if not out or out[:8] != _PNG_SIG:
        if log:
            log.warn("  GIF to PNG produced something that is not a PNG; skipping")
        return None
    return out


_MIN_MAX_DIM, _MAX_MAX_DIM = 16, 20000
# 1 KB floor (below it every image would be re-encoded pointlessly); 1 GB
# ceiling mirrors the guardrail style used for the decode-size cap.
_MIN_MAX_BYTES, _MAX_MAX_BYTES = 1024, 1024 * 1024 * 1024


def optimize_images(cover_image, images, log):
    """Optimize the cover and every body image. Returns (cover, images)."""
    max_dim = _read_env_int(
        "KFXGEN_IMAGE_MAX_DIM", DEFAULT_MAX_DIM, _MIN_MAX_DIM, _MAX_MAX_DIM, log
    )
    max_bytes = _read_env_int(
        "KFXGEN_IMAGE_MAX_BYTES", DEFAULT_MAX_BYTES, _MIN_MAX_BYTES, _MAX_MAX_BYTES, log
    )
    quality = _read_env_int("KFXGEN_IMAGE_QUALITY", DEFAULT_JPEG_QUALITY, 1, 100, log)
    before = after = 0
    new_images = {}
    for href, data in images.items():
        before += len(data)
        opt = optimize_image(
            data, max_dim=max_dim, max_bytes=max_bytes, jpeg_quality=quality, log=log
        )
        after += len(opt)
        new_images[href] = opt
    new_cover = cover_image
    if cover_image:
        before += len(cover_image)
        new_cover = optimize_image(
            cover_image,
            max_dim=max_dim,
            max_bytes=max_bytes,
            jpeg_quality=quality,
            log=log,
        )
        after += len(new_cover)
    if before and after < before:
        saved = before - after
        log.info(
            f"  Image optimization: {before:,} -> {after:,} bytes "
            f"(saved {saved:,}, {round(100 * saved / before)}%)"
        )
    return new_cover, new_images
