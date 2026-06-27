"""Baseline runner: convert each EPUB in gutenberg-top-90 and measure fidelity vs source.

Usage:
    python research/baseline_runner.py --smoke         # 5 diverse books
    python research/baseline_runner.py --all           # all 90
    python research/baseline_runner.py --files 02 05   # specific by prefix
"""

import argparse
import io
import json
import re
import sys
import time
import traceback
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT / "research"))

from convert_epub_to_kfx import (  # noqa: E402
    build_chapters,
    convert_epub_to_kfx,
    extract_cover_image,
    extract_epub_metadata,
    extract_epub_spine,
    extract_epub_toc,
)
from kfxgen.kfxlib_minimal.kfx_container import KfxContainer  # noqa: E402
from kfxgen.kfxlib_minimal.standard_symbols import StandardSymbolTable  # noqa: E402


class _BytesDatafile:
    def __init__(self, data: bytes):
        self._data = data

    def get_data(self) -> bytes:
        return self._data


def extract_kfx_text(kfx_path: Path) -> dict:
    """Read KFX back and pull paragraph text + image/section counts from fragments."""
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

EPUB_DIR = ROOT / "research" / "gutenberg-top-90"
OUT_DIR = ROOT / "research" / "gutenberg-top-90-baseline"

SMOKE_PICKS = [
    "02_Frankenstein.epub",
    "05_Romeo_and_Juliet.epub",
    "10_Alices_Adventures_in_Wonderland.epub",
    "12_Complete_Works_of_Shakespeare.epub",
    "20_Die_Traumdeutung.epub",
]


_IMG_TOKEN_RE = re.compile(r"\x00IMG\x01[^\x01]*\x01[^\x00]*\x00")


def visible_text(text: str) -> str:
    """Strip IMG placeholder tokens so word counts measure rendered content
    only — matches what the KFX writer emits as $145 paragraph strings."""
    return _IMG_TOKEN_RE.sub("", text or "")


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", visible_text(text)))


def epub_image_count(epub_path: Path) -> int:
    """Count image files inside the EPUB zip."""
    exts = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"}
    try:
        with zipfile.ZipFile(epub_path) as zf:
            return sum(1 for n in zf.namelist() if Path(n).suffix.lower() in exts)
    except Exception:
        return 0


def gather_source_metrics(epub_path: Path) -> dict:
    """Pull source-of-truth metrics from the EPUB before conversion."""
    metadata = extract_epub_metadata(epub_path)
    spine_items = extract_epub_spine(epub_path)
    toc = extract_epub_toc(epub_path)
    cover = extract_cover_image(epub_path)
    total_text = "\n".join(item.get("text", "") for item in spine_items)
    return {
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "language": metadata.get("language"),
        "spine_items": len(spine_items),
        "toc_entries": len(toc),
        "image_count_in_epub": epub_image_count(epub_path),
        "cover_present": cover is not None,
        "source_chars": len(total_text),
        "source_words": count_words(total_text),
    }


def gather_intermediate_metrics(epub_path: Path) -> dict:
    """Run the same EPUB→chapters pipeline the converter uses (no KFX gen yet)."""
    metadata = extract_epub_metadata(epub_path)
    spine_items = extract_epub_spine(epub_path)
    toc = extract_epub_toc(epub_path)
    chapters = build_chapters(spine_items, toc, metadata)
    total_chars = sum(len(ch.get("text", "")) for ch in chapters)
    total_words = sum(count_words(ch.get("text", "")) for ch in chapters)
    return {
        "chapter_count": len(chapters),
        "chapter_chars": total_chars,
        "chapter_words": total_words,
        "chapter_titles": [ch.get("title") for ch in chapters],
    }


def run_one(epub_path: Path, out_dir: Path) -> dict:
    """Convert one EPUB; capture metrics and any failures."""
    book_dir = out_dir / epub_path.stem
    book_dir.mkdir(parents=True, exist_ok=True)
    log_path = book_dir / "convert.log"
    metrics_path = book_dir / "metrics.json"

    result: dict = {
        "file": epub_path.name,
        "ok": False,
        "elapsed_s": None,
        "kfx_bytes": None,
        "error": None,
        "error_stage": None,
        "source": None,
        "intermediate": None,
        "kfx_readback": None,
    }

    t0 = time.monotonic()
    buf = io.StringIO()

    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            print(f"=== baseline_runner: {epub_path.name} ===")
            result["source"] = gather_source_metrics(epub_path)
            print(f"[source] {result['source']}")
            result["intermediate"] = gather_intermediate_metrics(epub_path)
            print(f"[intermediate] chapters={result['intermediate']['chapter_count']}")
            kfx_path_str = convert_epub_to_kfx(epub_path, output_dir=book_dir)
            kfx_path = Path(kfx_path_str)
            if kfx_path.exists():
                result["kfx_bytes"] = kfx_path.stat().st_size
                result["ok"] = True
                print("[readback] parsing generated KFX...")
                result["kfx_readback"] = extract_kfx_text(kfx_path)
                print(f"[readback] {result['kfx_readback']}")
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        if result["source"] is None:
            result["error_stage"] = "source_metrics"
        elif result["intermediate"] is None:
            result["error_stage"] = "build_chapters"
        else:
            result["error_stage"] = "kfx_generation"
        buf.write("\n--- TRACEBACK ---\n")
        buf.write(traceback.format_exc())

    result["elapsed_s"] = round(time.monotonic() - t0, 2)
    log_path.write_text(buf.getvalue())
    metrics_path.write_text(json.dumps(result, indent=2, default=str))
    return result


def pct(num, denom):
    if not denom:
        return None
    return round(100.0 * num / denom, 1)


def build_report(results: list, out_path: Path) -> None:
    lines = []
    lines.append("# Gutenberg Top-90 Baseline\n")
    total = len(results)
    ok = sum(1 for r in results if r["ok"])
    lines.append(f"Run: {total} books — passed: **{ok}** / failed: **{total - ok}**\n")
    lines.append("")
    lines.append("## Summary table\n")
    lines.append(
        "| # | File | Status | KFX KB | s | Src words | Chapters | KFX paras | Mid retention | KFX retention | Spine | TOC | EPUB imgs | KFX imgs | Notes |"
    )
    lines.append("|---|------|--------|--------|---|-----------|----------|-----------|----------------|----------------|-------|-----|-----------|----------|-------|")

    for i, r in enumerate(results, 1):
        src = r.get("source") or {}
        mid = r.get("intermediate") or {}
        rb = r.get("kfx_readback") or {}
        status = "✅" if r["ok"] else "❌"
        kfx_kb = f"{r['kfx_bytes'] // 1024}" if r.get("kfx_bytes") else "—"

        mid_retention = pct(mid.get("chapter_words", 0), src.get("source_words", 0))
        mid_ret_s = f"{mid_retention}%" if mid_retention is not None else "—"

        if rb.get("ok"):
            kfx_retention = pct(rb.get("kfx_words", 0), src.get("source_words", 0))
            kfx_ret_s = f"{kfx_retention}%" if kfx_retention is not None else "—"
            kfx_paras = rb.get("paragraph_count", "—")
            kfx_imgs = rb.get("image_resources", "—")
        else:
            kfx_retention = None
            kfx_ret_s = "readback FAIL"
            kfx_paras = "—"
            kfx_imgs = "—"

        notes = []
        if not r["ok"]:
            notes.append(f"FAIL @ {r.get('error_stage')}: {r.get('error','')[:60]}")
        else:
            if mid_retention is not None and mid_retention < 90:
                notes.append("intermediate loss")
            # Flag only KFX drops vs intermediate — KFX > intermediate is
            # expected when chapter title headings add words; only KFX < mid
            # is real content loss in the writer.
            if kfx_retention is not None and mid_retention is not None and (mid_retention - kfx_retention) > 2:
                notes.append("KFX<intermediate")
            if src.get("toc_entries", 0) and mid.get("chapter_count", 0) < src["toc_entries"] * 0.5:
                notes.append("ch<<toc")
        notes_s = "; ".join(notes)

        lines.append(
            f"| {i} | {r['file']} | {status} | {kfx_kb} | {r['elapsed_s']} | "
            f"{src.get('source_words','—')} | {mid.get('chapter_count','—')} | {kfx_paras} | "
            f"{mid_ret_s} | {kfx_ret_s} | "
            f"{src.get('spine_items','—')} | {src.get('toc_entries','—')} | "
            f"{src.get('image_count_in_epub','—')} | {kfx_imgs} | {notes_s} |"
        )

    lines.append("")
    lines.append("## Failures\n")
    fails = [r for r in results if not r["ok"]]
    if not fails:
        lines.append("None.\n")
    else:
        for r in fails:
            lines.append(f"### {r['file']}")
            lines.append(f"- Stage: `{r.get('error_stage')}`")
            lines.append(f"- Error: `{r.get('error')}`")
            lines.append(f"- Log: `{(OUT_DIR / Path(r['file']).stem / 'convert.log').relative_to(ROOT)}`")
            lines.append("")

    out_path.write_text("\n".join(lines))


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--smoke", action="store_true", help="Run 5 diverse picks")
    g.add_argument("--all", action="store_true", help="Run all 90")
    g.add_argument("--files", nargs="+", help="Run files matching these prefixes (e.g. 02 05)")
    args = p.parse_args()

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

    print(f"Running {len(targets)} book(s)...")
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
