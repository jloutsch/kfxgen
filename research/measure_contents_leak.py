#!/usr/bin/env python3
"""Measure two contents-page defects across the corpus (#132, #133, #136).

Written while reviewing #132, whose report keyed both its diagnosis and its
scope on `NAV_MARKERS` in `tests/integration/test_public_corpus.py`. That set
matches four literal strings, so it reported one failing book where there are
eight, and the one it reported was a false positive. These two measurements are
what replaced it.

Usage:

    python research/measure_contents_leak.py                 # both checks
    python research/measure_contents_leak.py --listings      # duplicate listings
    python research/measure_contents_leak.py --img-entries   # image-as-entry
    KFXGEN_CORPUS_DIR=/path/to/corpus python research/measure_contents_leak.py

Fetch the corpus first with `research/fetch_corpus.py`.


duplicate listings (`--listings`, #132)
---------------------------------------

Pulls every `<a>` label inside a `class~="toc"` element from the source EPUB,
converts, and finds the longest run of *consecutive* output blocks that are all
toc labels. A long run means the source listing survived into the body as a
listing.

Contiguity is the whole measurement. Asking "does this label appear anywhere in
the body" counts a real chapter heading as a leak, because a heading is exactly
what a toc label is a copy of. That inflated a first pass from 8 books to 10,
including two with no leak at all.

The run threshold cannot be 1, either. One corpus book has a three-entry
dramatis personae page whose entries coincide with toc labels — real content,
and the closest thing to a false positive this method has. `--min-run` defaults
to 4 to sit above it. A book that leaks a three-entry listing would be missed;
no such book is in the corpus, and the alternative (also requiring that the run
is not the chapter the entries point at) was not worth the complexity here.

Chapters carrying `toc_links` are skipped: that is kfxgen's own generated
contents page, not a leak of the source's.


image-as-entry (`--img-entries`, #133)
--------------------------------------

Scans generated contents pages for a `toc_links` entry whose label is an image
token rather than words. Every such entry renders the image inside the contents
listing.

This one needs no threshold and no source comparison — an image token in a link
label is unambiguously wrong.
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
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

DEFAULT_CORPUS = REPO / "research" / "gutenberg-top-90"

# `_make_img_token` builds "\x00IMG\x01<id>_<name>\x01\x00". Match the sentinel
# rather than importing the builder, so this keeps working if the payload
# format changes.
IMG_TOKEN = "\x00IMG\x01"

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


def _toc_labels(epub):
    """Link labels inside any `class~="toc"` element, plus the tags carrying it.

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
                found = [_norm("".join(a.itertext())) for a in elem.iter("a")]
                found = [f for f in found if f]
                if found:
                    labels += found
                    tags.add(elem.tag)
    return labels, tags


def _longest_run(blocks, labels):
    longest = run = 0
    for text in blocks:
        run = run + 1 if text in labels else 0
        longest = max(longest, run)
    return longest


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
    print(f"{'book':<10} {'tags':<10} {'entries':>7} {'run':>5}  {'chapter':<14}")
    print("-" * 56)
    leaks = 0
    for epub in epubs:
        labels, tags = _toc_labels(epub)
        if not labels:
            continue
        unique = set(labels)
        best_run, best_where = 0, "-"
        for index, chapter in enumerate(_chapters(epub)):
            if chapter.get("toc_links"):
                continue  # our own generated contents page, not the source's
            body = _body_blocks(chapter)
            run = _longest_run(body, unique)
            if run > best_run:
                # Index and size, never the title: see `_redact`.
                best_run, best_where = run, f"[{index}] {len(body)} blocks"
        flag = ""
        if best_run >= min_run:
            leaks += 1
            flag = "  <- leak"
        print(
            f"{epub.stem:<10} {','.join(sorted(tags)):<10} {len(unique):>7} "
            f"{best_run:>5}  {best_where:<14}{flag}"
        )
    print(f"\n{leaks} book(s) leak a duplicate contents listing (min-run {min_run})")
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
            imgs = sum(1 for link in links if IMG_TOKEN in link["text"])
            if not imgs:
                continue
            bad += 1
            heading = _redact(chapter["title"])[:22]
            print(f"{epub.stem:<10} {heading:<22} {len(links):>7} {imgs:>4}")
    print(f"\n{bad} of {pages} generated contents page(s) list an image as an entry")
    return bad


_IMG_TOKEN_DISPLAY = re.compile(re.escape(IMG_TOKEN) + r"[^\x01]*\x01\x00")

# Contents labels that are safe to print verbatim, because they name a
# structure rather than a work. Which of these a book uses is the whole point
# of #136 — "Table of Contents" is the one that trips `NAV_MARKERS`.
_SAFE_HEADINGS = frozenset({"contents", "table of contents", "contents."})


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
    text = _norm(_IMG_TOKEN_DISPLAY.sub("<img>", title or ""))
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
