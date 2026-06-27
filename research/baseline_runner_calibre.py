"""Calibre-plugin baseline: invoke ebook-convert per book and capture metrics.

Mirrors research/baseline_runner.py but uses Calibre's OEB pipeline + the
installed kfxgen output plugin instead of calling the native generator
directly. Output reports live alongside the direct-Python baseline so the
two pipelines can be compared.

Usage:
    python research/baseline_runner_calibre.py --smoke
    python research/baseline_runner_calibre.py --all
    python research/baseline_runner_calibre.py --files 02 05
"""

import argparse
import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT / "research"))

from convert_epub_to_kfx import (  # noqa: E402
    extract_cover_image,
    extract_epub_body_images,
    extract_epub_metadata,
    extract_epub_spine,
    extract_epub_toc,
)
from kfxgen.kfxlib_minimal.kfx_container import KfxContainer  # noqa: E402
from kfxgen.kfxlib_minimal.standard_symbols import StandardSymbolTable  # noqa: E402

EBOOK_CONVERT = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
EPUB_DIR = ROOT / "research" / "gutenberg-top-90"
OUT_DIR = ROOT / "research" / "gutenberg-top-90-baseline-calibre"

SMOKE_PICKS = [
    "02_Frankenstein.epub",
    "05_Romeo_and_Juliet.epub",
    "10_Alices_Adventures_in_Wonderland.epub",
    "28_The_2006_CIA_World_Factbook.epub",  # the headline broken case
    "54_Color_Images_from_Mars_Rovers.epub",  # image-heavy
]

_IMG_TOKEN_RE = re.compile(r"\x00IMG\x01[^\x01]*\x01[^\x00]*\x00")


def visible_text(text: str) -> str:
    return _IMG_TOKEN_RE.sub("", text or "")


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", visible_text(text)))


def epub_image_count(epub_path: Path) -> int:
    exts = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"}
    try:
        with zipfile.ZipFile(epub_path) as zf:
            return sum(1 for n in zf.namelist() if Path(n).suffix.lower() in exts)
    except Exception:
        return 0


def gather_source_metrics(epub_path: Path) -> dict:
    metadata = extract_epub_metadata(epub_path)
    spine_items = extract_epub_spine(epub_path)
    toc = extract_epub_toc(epub_path)
    cover = extract_cover_image(epub_path)
    body_images = extract_epub_body_images(epub_path, exclude_data=cover)
    total_text = "\n".join(item.get("text", "") for item in spine_items)
    return {
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "language": metadata.get("language"),
        "spine_items": len(spine_items),
        "toc_entries": len(toc),
        "image_count_in_epub": epub_image_count(epub_path),
        "body_image_count_in_manifest": len(body_images),
        "cover_present": cover is not None,
        "source_chars": len(total_text),
        "source_words": count_words(total_text),
    }


class _BytesDatafile:
    def __init__(self, data: bytes):
        self._data = data

    def get_data(self) -> bytes:
        return self._data


def extract_kfx_text(kfx_path: Path) -> dict:
    try:
        data = kfx_path.read_bytes()
        symtab = StandardSymbolTable()
        container = KfxContainer(symtab, datafile=_BytesDatafile(data))
        container.deserialize(ignore_drm=True)
        fragments = container.get_fragments()
        paragraphs: list[str] = []
        section_count = 0
        content_fragment_count = 0
        image_resource_count = 0
        for frag in fragments:
            ftype = str(frag.ftype) if frag.ftype else ""
            if ftype == "$145":
                content_fragment_count += 1
                value = frag.value
                strings = value.get("$146", []) if hasattr(value, "get") else []
                for s in strings:
                    if isinstance(s, str):
                        paragraphs.append(s)
            elif ftype == "$260":
                section_count += 1
            elif ftype == "$164":
                image_resource_count += 1
        full_text = "\n".join(paragraphs)
        return {
            "ok": True,
            "paragraph_count": len(paragraphs),
            "kfx_chars": len(full_text),
            "kfx_words": count_words(full_text),
            "section_count": section_count,
            "content_fragments": content_fragment_count,
            "image_resources": image_resource_count,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def run_one(epub_path: Path, out_dir: Path, timeout_s: int = 600) -> dict:
    book_dir = out_dir / epub_path.stem
    book_dir.mkdir(parents=True, exist_ok=True)
    log_path = book_dir / "convert.log"
    metrics_path = book_dir / "metrics.json"
    kfx_path = book_dir / (epub_path.stem + ".kfx")

    result: dict = {
        "file": epub_path.name,
        "ok": False,
        "elapsed_s": None,
        "kfx_bytes": None,
        "exit_code": None,
        "error": None,
        "source": None,
        "kfx_readback": None,
    }

    t0 = time.monotonic()
    try:
        result["source"] = gather_source_metrics(epub_path)
    except Exception as e:
        result["error"] = f"source_metrics: {type(e).__name__}: {e}"

    cmd = [EBOOK_CONVERT, str(epub_path), str(kfx_path)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s
        )
        result["exit_code"] = proc.returncode
        log_path.write_text(
            f"$ {' '.join(cmd)}\n\n--- STDOUT ---\n{proc.stdout}\n\n--- STDERR ---\n{proc.stderr}\n"
        )
        if proc.returncode == 0 and kfx_path.exists():
            result["kfx_bytes"] = kfx_path.stat().st_size
            result["ok"] = True
            result["kfx_readback"] = extract_kfx_text(kfx_path)
        elif proc.returncode != 0:
            result["error"] = (
                f"ebook-convert exit {proc.returncode}: "
                f"{(proc.stderr or proc.stdout)[-200:].strip()}"
            )
    except subprocess.TimeoutExpired:
        result["error"] = f"timeout after {timeout_s}s"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    result["elapsed_s"] = round(time.monotonic() - t0, 2)
    metrics_path.write_text(json.dumps(result, indent=2, default=str))
    return result


def pct(num, denom):
    if not denom:
        return None
    return round(100.0 * num / denom, 1)


def build_report(results: list, out_path: Path) -> None:
    lines = []
    lines.append("# Gutenberg Top-90 Baseline — Calibre plugin path\n")
    total = len(results)
    ok = sum(1 for r in results if r["ok"])
    lines.append(
        "Invocation: `ebook-convert <input.epub> <output.kfx>` via Calibre 9.8.0 + kfxgen plugin.\n"
    )
    lines.append(f"Run: {total} books — passed: **{ok}** / failed: **{total - ok}**\n")
    lines.append("")
    lines.append("## Summary table\n")
    lines.append(
        "| # | File | Status | KFX KB | s | Src words | KFX paras | KFX retention | Spine | TOC | EPUB body imgs | KFX imgs | Notes |"
    )
    lines.append("|---|------|--------|--------|---|-----------|-----------|----------------|-------|-----|----------------|----------|-------|")
    for i, r in enumerate(results, 1):
        src = r.get("source") or {}
        rb = r.get("kfx_readback") or {}
        status = "✅" if r["ok"] else "❌"
        kfx_kb = f"{r['kfx_bytes'] // 1024}" if r.get("kfx_bytes") else "—"
        if rb.get("ok"):
            kfx_retention = pct(rb.get("kfx_words", 0), src.get("source_words", 0))
            kfx_ret_s = f"{kfx_retention}%" if kfx_retention is not None else "—"
            kfx_paras = rb.get("paragraph_count", "—")
            kfx_imgs = rb.get("image_resources", "—")
        else:
            kfx_retention = None
            kfx_ret_s = "readback FAIL" if r["ok"] else "—"
            kfx_paras = "—"
            kfx_imgs = "—"
        notes = []
        if not r["ok"]:
            notes.append(f"FAIL: {(r.get('error') or '')[:80]}")
        else:
            if kfx_retention is not None and kfx_retention < 90:
                notes.append("low retention")
        notes_s = "; ".join(notes)
        lines.append(
            f"| {i} | {r['file']} | {status} | {kfx_kb} | {r['elapsed_s']} | "
            f"{src.get('source_words','—')} | {kfx_paras} | {kfx_ret_s} | "
            f"{src.get('spine_items','—')} | {src.get('toc_entries','—')} | "
            f"{src.get('body_image_count_in_manifest','—')} | {kfx_imgs} | {notes_s} |"
        )
    lines.append("")
    lines.append("## Failures\n")
    fails = [r for r in results if not r["ok"]]
    if not fails:
        lines.append("None.\n")
    else:
        for r in fails:
            lines.append(f"### {r['file']}")
            lines.append(f"- Exit code: `{r.get('exit_code')}`")
            lines.append(f"- Error: `{r.get('error')}`")
            lines.append(
                f"- Log: `{(OUT_DIR / Path(r['file']).stem / 'convert.log').relative_to(ROOT)}`"
            )
            lines.append("")
    out_path.write_text("\n".join(lines))


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--smoke", action="store_true")
    g.add_argument("--all", action="store_true")
    g.add_argument("--files", nargs="+")
    args = p.parse_args()

    if not Path(EBOOK_CONVERT).exists():
        print(f"ebook-convert not found at {EBOOK_CONVERT}", file=sys.stderr)
        sys.exit(2)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_epubs = sorted(EPUB_DIR.glob("*.epub"))
    if args.smoke:
        targets = [EPUB_DIR / n for n in SMOKE_PICKS]
    elif args.all:
        targets = all_epubs
    else:
        prefixes = tuple(args.files)
        targets = [p for p in all_epubs if p.name.startswith(prefixes)]

    targets = [t for t in targets if t.exists()]
    if not targets:
        print("No EPUBs matched.", file=sys.stderr)
        sys.exit(2)

    print(f"Running {len(targets)} book(s) via ebook-convert...")
    results = []
    for i, ep in enumerate(targets, 1):
        print(f"  [{i}/{len(targets)}] {ep.name} ...", flush=True)
        r = run_one(ep, OUT_DIR)
        flag = "OK " if r["ok"] else "FAIL"
        kb = f"{r['kfx_bytes']//1024}KB" if r.get("kfx_bytes") else "—"
        print(f"      {flag} {kb} in {r['elapsed_s']}s")
        results.append(r)

    report = OUT_DIR / "BASELINE.md"
    build_report(results, report)
    print(f"\nReport: {report}")


if __name__ == "__main__":
    main()
