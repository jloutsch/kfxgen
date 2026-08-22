#!/usr/bin/env python3
"""Fetch the public-domain corpus that `BASELINE.md` was measured against.

The books are not committed (~0.5 GB) and never should be. What is committed is
`corpus_ids.json`, so the *set* is reproducible even though its contents are not.
That manifest did not exist until 2026-08-21: `BASELINE.md` recorded titles only,
which is why 13 of the original 90 could not be recovered — their filenames were
abbreviated past the point where they match a catalog entry. The manifest holds
the 77 that were.

Usage:

    python research/fetch_corpus.py                  # fetch missing books
    python research/fetch_corpus.py --verify-only     # check what is on disk
    KFXGEN_CORPUS_DIR=research/gutenberg-top-90 pytest -m slow -k corpus

Two things worth knowing before running it:

* `www.gutenberg.org` is not used. It rate-limits and, at the time of writing,
  was returning 503 for every request. The official mirrors below serve the same
  files. Expect ~335 KB/s — the full set takes roughly half an hour.
* A 200 response is not proof of a book. Gutenberg mirrors answer with HTML
  error pages, and a truncated transfer still lands on disk looking plausible
  (a 24 MB EPUB arrived as a valid-looking 9 MB file during development, simply
  because the timeout cut it off). Every download is checked for zip validity,
  an EPUB container, and the image count `corpus_ids.json` expects. The image
  count is what proves it is the *right* book rather than merely a book.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
MANIFEST = HERE / "corpus_ids.json"
DEST = HERE / "gutenberg-top-90"

# Ordered by observed reliability. Both serve `cache/epub/<id>/pg<id>-images.epub`.
MIRRORS = (
    "https://gutenberg.pglaf.org",
    "http://aleph.gutenberg.org",
)

# Generous: the largest book in the set is ~85 MB at ~335 KB/s. Too small a
# value is the failure mode that produced a truncated file during development.
TIMEOUT_S = 600
# `.jfif` is here because leaving it out silently undercounts. pg12082 declares
# 436 images in its OPF and 9 of them use that extension, so an extension list
# without it reports 427 — which is what `baseline_runner.epub_image_count`
# does, and why BASELINE.md appeared to show kfxgen inventing 9 images out of
# nowhere. The generator was right and the counter was wrong. Prefer the OPF
# media-type when you can; this list is the zip-only fallback.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".jfif", ".png", ".gif", ".svg", ".webp")


def count_images(path: Path) -> int:
    with zipfile.ZipFile(path) as zf:
        return sum(1 for n in zf.namelist() if n.lower().endswith(IMAGE_SUFFIXES))


def inspect(path: Path, expected_images: int) -> tuple[bool, str]:
    """Return (ok, reason). Never raises — a corrupt file is an expected input."""
    if not path.exists():
        return False, "missing"
    if path.stat().st_size == 0:
        return False, "empty"
    try:
        if not zipfile.is_zipfile(path):
            return False, "not a zip (HTML error page?)"
        with zipfile.ZipFile(path) as zf:
            if "META-INF/container.xml" not in zf.namelist():
                return False, "zip but not an EPUB"
            bad = zf.testzip()
            if bad is not None:
                return False, f"corrupt member {bad} (truncated?)"
        got = count_images(path)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the sweep
        return False, f"{type(exc).__name__}: {exc}"
    if got != expected_images:
        return False, f"image count {got}, manifest says {expected_images}"
    return True, f"ok ({got} images)"


def download(book_id: int, dest: Path) -> tuple[bool, str]:
    name = f"pg{book_id}-images.epub"
    last = "no mirror tried"
    for base in MIRRORS:
        url = f"{base}/cache/epub/{book_id}/{name}"
        tmp = dest.with_suffix(".part")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "kfxgen-corpus-fetch"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                declared = resp.headers.get("Content-Length")
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
            # Content-Length is the only signal that separates "server closed
            # early" from "file ended". Without this check a short read is
            # indistinguishable from success.
            if declared is not None and tmp.stat().st_size != int(declared):
                last = f"short read: {tmp.stat().st_size} of {declared} bytes"
                tmp.unlink(missing_ok=True)
                continue
            tmp.replace(dest)
            return True, base.split("//")[-1]
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            tmp.unlink(missing_ok=True)
    return False, last


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--verify-only", action="store_true", help="check disk, download nothing"
    )
    ap.add_argument(
        "--dest", default=str(DEST), help=f"corpus directory (default: {DEST})"
    )
    args = ap.parse_args(argv)

    dest_dir = Path(args.dest)
    books = json.loads(MANIFEST.read_text())
    dest_dir.mkdir(parents=True, exist_ok=True)

    have, fetched, failed = 0, 0, []
    t0 = time.time()
    for i, book in enumerate(books, 1):
        bid, want = book["id"], book["epub_images"]
        path = dest_dir / f"pg{bid}.epub"

        ok, why = inspect(path, want)
        if ok:
            have += 1
            continue
        if args.verify_only:
            failed.append((bid, why))
            print(f"[{i:>2}/{len(books)}] pg{bid}: {why}", flush=True)
            continue

        if path.exists():
            print(f"[{i:>2}/{len(books)}] pg{bid}: refetching ({why})", flush=True)
            path.unlink()

        got, detail = download(bid, path)
        if not got:
            failed.append((bid, detail))
            print(f"[{i:>2}/{len(books)}] pg{bid}: FAILED {detail}", flush=True)
            continue

        ok, why = inspect(path, want)
        if ok:
            fetched += 1
            print(f"[{i:>2}/{len(books)}] pg{bid}: {why} via {detail}", flush=True)
        else:
            failed.append((bid, why))
            path.unlink(missing_ok=True)
            print(f"[{i:>2}/{len(books)}] pg{bid}: REJECTED {why}", flush=True)

    print(
        f"\nalready valid: {have} | fetched: {fetched} | failed: {len(failed)} "
        f"| {len(books)} in manifest | {time.time() - t0:.0f}s"
    )
    if failed:
        print("failures:")
        for bid, why in failed:
            print(f"  pg{bid}: {why}")
    print(f"\ncorpus dir: {dest_dir}")
    print(f"use it with: KFXGEN_CORPUS_DIR={dest_dir} pytest -m slow -k corpus")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
