#!/usr/bin/env python3
"""Measure two contents-page defects across the corpus (#132, #133, #136).

Written while reviewing #132, whose report keyed both its diagnosis and its
scope on `NAV_MARKERS` in `tests/integration/test_public_corpus.py`. That set
matches four literal strings, so it reported one failing book where there are
nine, and the one it reported was a false positive. These two measurements are
what replaced it.

Usage:

    python research/measure_contents_leak.py                 # both checks
    python research/measure_contents_leak.py --listings      # duplicate listings
    python research/measure_contents_leak.py --img-entries   # image-as-entry
    KFXGEN_CORPUS_DIR=/path/to/corpus python research/measure_contents_leak.py

Fetch the corpus first with `research/fetch_corpus.py`.

Output names books by Gutenberg id. An id identifies a book as precisely as its
title does, so treat the tables as corpus diagnostics rather than as something
to paste somewhere public. What is deliberately kept out is free text: chapter
titles are usually the book's own title or its author's name, so `_redact`
prints only recognised structural labels.


duplicate listings (`--listings`, #132)
---------------------------------------

Collects the entry labels inside every `class~="toc"` element in the source,
converts, and looks for that listing surviving into the body. Current reading:
**9 of 77 books**.

Two signals, because the listing arrives in two shapes.

`run` — the longest stretch of *consecutive* blocks that are each a toc label.
Contiguity is what makes this a measurement: asking "does this label appear
anywhere in the body" counts a real chapter heading as a leak, because a
heading is exactly what a toc label copies. That inflated a first pass to 10
books, two of which have no leak at all.

`fused` — the most labels found inside a *single* block. A table listing never
produces a run: `td`/`th` are absent from `extract_blocks_from_html`'s
`block_tags`, so kfxgen walks a whole `<table>` as one container and every cell
lands in one paragraph. One book's entire contents table is a single
1551-character block. Run-based detection alone called all three table books
clean, which is how the first measurement said seven instead of nine.

`fused` is judged as a fraction of the listing (`_FUSED_FRACTION`), not as a
count. Prose mentions the odd section name — one book scatters 4-6 hits across
eleven unrelated chapters — while the two real fused listings score 27/27 and
40/79. Noise sits at or below 10%, signal at or above 51%.

`--min-run` defaults to 4. One book has a three-entry dramatis personae page
whose entries coincide with toc labels; that is real content and the closest
thing to a false positive here. A book leaking a three-entry listing would be
missed, and none is in the corpus.

Labels shorter than `_MIN_LABEL_LEN`, or made only of digits and roman
numerals, are dropped — they match body text by coincidence. A listing whose
entries are bare numerals is therefore invisible to this, which is a real gap
rather than a claim of zero.

Chapters carrying `toc_links` are skipped: that is kfxgen's own generated
contents page, not a leak of the source's.


image-as-entry (`--img-entries`, #133)
--------------------------------------

Scans generated contents pages for a `toc_links` entry labelled with an image
token rather than words. Needs no threshold and no source comparison — an
image in a link label is unambiguously wrong.

**This reads `0 of 39` once the #133 fix is in, and that is the pass
condition, not a broken check.** It read `39 of 39` before. The permanent
regression gate is the `raw_img_tokens` invariant in
`tests/integration/test_public_corpus.py`, which is stricter: it counts tokens
anywhere in emitted text, including the chapter headings this check cannot see.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from lxml import etree  # noqa: E402

from kfxgen import converter as conv  # noqa: E402
from kfxgen._img_tokens import IMG_TOKEN_RE  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

DEFAULT_CORPUS = REPO / "research" / "gutenberg-top-90"

HTML_SUFFIXES = (".xhtml", ".html", ".htm")


def _silent_log():
    """A logger shaped like Calibre's, quiet enough to sweep 77 books."""
    log = logging.getLogger("measure_contents_leak")
    log.addHandler(logging.NullHandler())
    log.setLevel(logging.CRITICAL)
    for name in ("warn", "info", "error", "debug"):
        if not hasattr(log, name):
            setattr(log, name, lambda *a, **k: None)
    log.warn = log.warning
    return log


def _norm(text):
    """Collapse whitespace so source markup and output blocks compare equal."""
    return " ".join((text or "").split())


def _chapters(epub):
    log = _silent_log()
    oeb = EpubAsOeb(str(epub))
    return conv.extract_chapters_from_oeb(oeb, log, conv.extract_metadata(oeb, log))


#: A label this short, or made only of digits and roman numerals, matches body
#: text by coincidence. "3", "I.", "PAGE" are entry decoration, not entries.
_MIN_LABEL_LEN = 4
_NUMERIC_LABEL = re.compile(r"^[\dIVXLCDMivxlcdm.,;:\s()\[\]-]+$")

#: Share of a listing that must appear inside one block to count as fused.
_FUSED_FRACTION = 0.33


def _entry_texts(elem):
    """Candidate entry labels inside one `class~="toc"` element.

    Reading `<a>` labels is wrong for a table listing: the link is on the page
    -number cell, so a 13-entry table yields `1, 48, 91, 135, …` and the
    comparison runs a set of numerals against body text. That both misses every
    real entry and invites false positives from any run of numeric blocks.

    So take the granularity the markup actually uses — cells for a table, items
    for a list, links otherwise — and drop anything too short or too numeric to
    be an entry.
    """
    if any(True for _ in elem.iter("tr")):
        raw = [c for row in elem.iter("tr") for c in row.iter("td", "th")]
    elif any(True for _ in elem.iter("li")):
        raw = list(elem.iter("li"))
    else:
        raw = list(elem.iter("a"))
    out = []
    for node in raw:
        text = _norm("".join(node.itertext()))
        if len(text) >= _MIN_LABEL_LEN and not _NUMERIC_LABEL.match(text):
            out.append(text)
    return out


def _toc_labels(epub):
    """Entry labels inside any `class~="toc"` element, plus the tags carrying it.

    The class sits at wildly different granularity across the corpus: one
    `<div>` wrapping the whole listing in one book, one `<p>` per entry in
    seven others, a `<table>` in three. Collecting labels rather than
    containers is what makes the measurement indifferent to which.
    """
    labels = []
    tags = set()
    with zipfile.ZipFile(str(epub)) as zf:
        for name in sorted(zf.namelist()):
            if not name.lower().endswith(HTML_SUFFIXES):
                continue
            try:
                tree = etree.fromstring(zf.read(name), etree.HTMLParser())
            except etree.XMLSyntaxError:
                continue
            if tree is None:
                continue
            for elem in tree.iter():
                if not isinstance(elem.tag, str):
                    continue
                if "toc" not in (elem.get("class") or "").lower().split():
                    continue
                found = _entry_texts(elem)
                if found:
                    labels += found
                    tags.add(elem.tag)
    return labels, tags


def _longest_run(blocks, labels):
    """Longest stretch of consecutive blocks that are each a toc label."""
    longest = run = 0
    for text in blocks:
        run = run + 1 if text in labels else 0
        longest = max(longest, run)
    return longest


def _most_fused(blocks, labels):
    """Most distinct labels found inside any single block.

    A table listing never produces a run, because kfxgen has no table
    structure: `td`/`th` are absent from `extract_blocks_from_html`'s
    `block_tags`, so a whole `<table>` is walked as one container and every
    cell lands in one paragraph (the `_CELL_TAGS` separator in converter.py
    exists precisely because of that). One corpus book's entire contents table
    arrives as a single 1551-character block.

    Counting labels *within* a block catches that shape. Run-based detection
    alone reported all three table books as clean, which is how the first
    measurement said seven leaks instead of nine.
    """
    most = 0
    for text in blocks:
        if len(text) < _MIN_LABEL_LEN:
            continue
        found = sum(1 for label in labels if label in text)
        most = max(most, found)
    return most


def _body_blocks(chapter):
    """A chapter's block texts in reading order.

    Falls back to splitting `text` for chapters the converter left unblocked;
    `_replace_title_page` drops `blocks` on the pages it rewrites.
    """
    blocks = chapter.get("blocks")
    if blocks:
        texts = [_norm(b.get("text")) for b in blocks]
    else:
        texts = [_norm(line) for line in (chapter.get("text") or "").split("\n")]
    return [t for t in texts if t]


def measure_listings(epubs, min_run):
    """Report books whose source contents listing survived into the body."""
    print(
        f"{'book':<10} {'tags':<10} {'entries':>7} {'run':>5} {'fused':>6}  "
        f"{'chapter':<14}"
    )
    print("-" * 64)
    leaks = 0
    for epub in epubs:
        labels, tags = _toc_labels(epub)
        if not labels:
            continue
        unique = set(labels)
        best_run = best_fused = 0
        best_where = "-"
        for index, chapter in enumerate(_chapters(epub)):
            if chapter.get("toc_links"):
                continue  # our own generated contents page, not the source's
            body = _body_blocks(chapter)
            run = _longest_run(body, unique)
            fused = _most_fused(body, unique)
            if max(run, fused) > max(best_run, best_fused):
                # Index and size, never the title: see `_redact`.
                best_where = f"[{index}] {len(body)} blocks"
            best_run, best_fused = max(best_run, run), max(best_fused, fused)
        # The fused signal is judged as a *fraction* of the listing, not as a
        # count. Prose mentions the odd section name: one corpus book scatters
        # 4-6 hits across eleven unrelated chapters, which a flat threshold of
        # 4 calls a leak eleven times over. A real fused listing is nothing
        # like that — the two in the corpus score 27/27 and 40/79, so the gap
        # between noise (<=10%) and signal (>=51%) is wide enough to sit in.
        fused_leak = (
            best_fused >= min_run and best_fused >= len(unique) * _FUSED_FRACTION
        )
        flag = ""
        if best_run >= min_run or fused_leak:
            leaks += 1
            flag = "  <- leak"
        print(
            f"{epub.stem:<10} {','.join(sorted(tags)):<10} {len(unique):>7} "
            f"{best_run:>5} {best_fused:>6}  {best_where:<14}{flag}"
        )
    print(f"\n{leaks} book(s) leak a duplicate contents listing (threshold {min_run})")
    return leaks


def measure_img_entries(epubs):
    """Report generated contents pages that list an image as an entry."""
    print(f"{'book':<10} {'heading':<22} {'entries':>7} {'img':>4}")
    print("-" * 48)
    pages = bad = 0
    for epub in epubs:
        for chapter in _chapters(epub):
            links = chapter.get("toc_links")
            if not links:
                continue
            pages += 1
            imgs = sum(1 for link in links if IMG_TOKEN_RE.search(link["text"]))
            if not imgs:
                continue
            bad += 1
            heading = _redact(chapter["title"])[:22]
            print(f"{epub.stem:<10} {heading:<22} {len(links):>7} {imgs:>4}")
    print(f"\n{bad} of {pages} generated contents page(s) list an image as an entry")
    return bad


# Contents labels that are safe to print verbatim, because they name a
# structure rather than a work. Which of these a book uses is the whole point
# of #136 — "Table of Contents" is the one that trips `NAV_MARKERS`.
_SAFE_HEADINGS = frozenset({"contents", "table of contents", "contents.", "<img>"})


def _redact(title):
    """Keep book titles and author names out of committed output.

    This repo is public and the corpus is real books. A chapter title is very
    often the book's own title or its author's name — printing one verbatim
    names the book, which is exactly what happened once already while
    reviewing #132. Truncating is not enough: four words of a title still
    identifies it.

    So this is an allowlist, not a filter. Anything not recognised as a
    structural label prints as a placeholder.
    """
    text = _norm(IMG_TOKEN_RE.sub("<img>", title or ""))
    if not text:
        return "-"
    return text if text.lower() in _SAFE_HEADINGS else "<other>"


def _corpus(arg):
    root = Path(arg or os.environ.get("KFXGEN_CORPUS_DIR") or DEFAULT_CORPUS)
    if not root.is_dir():
        sys.exit(f"corpus not found: {root}\nfetch it with research/fetch_corpus.py")
    epubs = sorted(root.glob("*.epub"))
    if not epubs:
        sys.exit(f"no .epub files in {root}")
    return epubs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", help="corpus dir (default $KFXGEN_CORPUS_DIR)")
    parser.add_argument(
        "--listings", action="store_true", help="duplicate contents listings (#132)"
    )
    parser.add_argument(
        "--img-entries", action="store_true", help="image as a contents entry (#133)"
    )
    parser.add_argument(
        "--min-run",
        type=int,
        default=4,
        help="consecutive toc labels that count as a leak (default 4)",
    )
    args = parser.parse_args()

    both = not (args.listings or args.img_entries)
    epubs = _corpus(args.corpus)
    print(f"{len(epubs)} books in corpus\n")

    if args.listings or both:
        measure_listings(epubs, args.min_run)
        if both:
            print()
    if args.img_entries or both:
        measure_img_entries(epubs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
