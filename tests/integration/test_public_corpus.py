"""Structural regression sweep over a corpus of public-domain EPUBs.

Opt-in: set ``KFXGEN_CORPUS_DIR`` to a directory of ``.epub`` files (the
Gutenberg top-90 set is what this was built against). Skips cleanly when the
variable is unset, so it never blocks a normal test run or CI without a corpus.

Why this exists: the unit suite and the synthetic golden corpus both passed
while kfxgen was silently discarding 44% of one book's body text (#58) and
while *every* internal link it had ever emitted resolved against nothing (#51).
Neither shows up without running real books and checking invariants across
them. Each assertion here corresponds to a bug that actually shipped.

Two modes:

* invariants only (default) — properties that must hold for any book
* baseline diff (``KFXGEN_CORPUS_BASELINE=/path/to.json``) — per-book metrics
  compared against a recorded run, which is what catches silent text loss.
  Regenerate with ``KFXGEN_CORPUS_WRITE_BASELINE=1``. The baseline is keyed by
  filename and is a local artifact; it is not committed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from pathlib import Path

import pytest

from kfxgen import converter as conv
from kfxgen.kfxlib_minimal.ion import IS
from tests._kfx_introspect import by_type, load_fragments, val
from tests.fixtures.oeb_shim import EpubAsOeb

CORPUS_ENV = "KFXGEN_CORPUS_DIR"
BASELINE_ENV = "KFXGEN_CORPUS_BASELINE"
WRITE_ENV = "KFXGEN_CORPUS_WRITE_BASELINE"

#: Text that means a non-rendered navigation document leaked into the body (#60).
NAV_MARKERS = frozenset(
    {"Page List", "Navigation", "Begin Reading", "Table of Contents"}
)

#: The sentinel opening `_make_img_token`. An image token is an internal
#: placeholder: the generator is supposed to turn it into an image chunk, and
#: any that reaches emitted text is a control-character string printed to the
#: reader. 116 of them survived across all 77 books before #133.
IMG_TOKEN = "\x00IMG\x01"

#: A book may legitimately shrink slightly (deduplicated headings, #64) or grow
#: (recovered container text, #58). Only flag movement beyond this.
TEXT_DRIFT_TOLERANCE = 0.02


def _corpus_files():
    root = os.environ.get(CORPUS_ENV)
    if not root:
        return []
    return sorted(Path(root).glob("*.epub"))


def _silent_log():
    log = logging.getLogger("corpus")
    log.addHandler(logging.NullHandler())
    log.setLevel(logging.CRITICAL)
    for name in ("warn", "info", "error", "debug"):
        if not hasattr(log, name):
            setattr(log, name, lambda *a, **k: None)
    log.warn = log.warning
    return log


def _convert(epub_path, out_path):
    """Convert through the same entry point the Calibre plugin uses.

    This used to hand-roll a reduced pipeline: extract_metadata,
    extract_chapters_from_oeb, then generate_full_book(title, author, chapters).
    That path never passes images to the generator, so every book came out with
    zero `$164` resources no matter what the source held — the sweep could not
    see image handling at all, and an image assertion written against it would
    have been vacuous.

    `convert_oeb_to_kfx` is what `__init__.py` calls and what tier-2 already
    uses, so the sweep now enters the pipeline where the plugin does.

    Two gaps remain, so read results as "the real entry point" rather than "the
    full shipping path". `opts=None` (the convention in the other integration
    tests) makes Stylizer construction fail, so block CSS (#9) and `@font-face`
    extraction (#15) degrade rather than run. And `image_optimize` is a no-op
    outside Calibre, which is not importable here, so downscaling and
    recompression (#11, #55) are not exercised. Image *presence* is covered;
    image *processing* is not.
    """
    conv.convert_oeb_to_kfx(
        EpubAsOeb(str(epub_path)), str(out_path), opts=None, log=_silent_log()
    )
    return out_path


#: `<img src="...">` in the source. Attribute-order tolerant, quote-agnostic.
_IMG_SRC = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _inline_image_refs(epub_path) -> int:
    """Distinct manifest images the source displays with an `<img>` tag.

    Intersected with the manifest deliberately: an `<img>` pointing at a file
    the EPUB never ships is the source's defect, and counting it would blame
    the generator for a missing image it could not have emitted.
    """
    manifest_images = {
        item.href.split("/")[-1]
        for item in EpubAsOeb(str(epub_path)).manifest
        if (item.media_type or "").startswith("image/")
    }
    refs = set()
    with zipfile.ZipFile(str(epub_path)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith((".htm", ".html", ".xhtml")):
                continue
            for src in _IMG_SRC.findall(zf.read(name).decode("utf-8", "replace")):
                base = src.split("/")[-1].split("#")[0]
                if base in manifest_images:
                    refs.add(base)
    return len(refs)


def _metrics(kfx_path):
    frags = load_fragments(kfx_path)
    texts = [
        x
        for f in by_type(frags, "$145")
        for x in (val(f).get(IS("$146")) or [])
        if isinstance(x, str)
    ]
    anchors = set()
    unnamed = 0
    for f in by_type(frags, "$266"):
        v = val(f)
        name = v.get(IS("$180"))
        if name is None:
            unnamed += 1
        else:
            anchors.add(str(name))
    targets = []
    for f in by_type(frags, "$259"):
        for entry in val(f).get(IS("$146")) or []:
            for span in entry.get(IS("$142")) or []:
                if IS("$179") in span:
                    targets.append(str(span[IS("$179")]))
    # Images have two independent counts and both matter. `$164` fragments are
    # the resources carried in the container; `$175` refs are the places a
    # reader is told to draw one. #102 shipped with the first non-zero and the
    # second zero — every image present in the file, none of them on screen.
    # Counts an entry carrying $175 at either level. The earlier version only
    # looked at children once an entry had any, so a $175 on a parent that also
    # has $146 children read as zero — the nested $259 shape native_generator
    # explicitly contemplates (`:1607`). $181 was also in the descent and never
    # occurs inside a $259; it is dropped rather than left as decoration.
    shown = 0
    for f in by_type(frags, "$259"):
        for outer in val(f).get(IS("$146")) or []:
            if not hasattr(outer, "get"):
                continue
            if outer.get(IS("$175")) is not None:
                shown += 1
            for entry in outer.get(IS("$146")) or []:
                if hasattr(entry, "get") and entry.get(IS("$175")) is not None:
                    shown += 1

    return {
        "chars": sum(len(t) for t in texts),
        "blocks": len(texts),
        "anchors": len(anchors),
        "anchors_without_180": unnamed,
        "links": len(targets),
        "dangling": len(set(targets) - anchors),
        "nav_junk": sum(1 for t in texts if t.strip() in NAV_MARKERS),
        "raw_img_tokens": sum(1 for t in texts if IMG_TOKEN in t),
        "image_resources": len(by_type(frags, "$164")),
        "images_shown": shown,
    }


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not _corpus_files(), reason=f"{CORPUS_ENV} not set or empty")
@pytest.mark.parametrize("epub", _corpus_files(), ids=lambda p: p.stem[:40])
def test_corpus_book_invariants(epub, tmp_path):
    """Properties that must hold for every book, each tied to a shipped bug."""
    m = _metrics(_convert(epub, tmp_path / "out.kfx"))

    assert m["chars"] > 0, "book produced no text at all"
    # #51: an anchor with no $180 has no name for $179 to resolve against, so
    # every link pointing at it silently dies.
    assert m["anchors_without_180"] == 0, (
        f"{m['anchors_without_180']} $266 anchors are missing $180"
    )
    # #53/#62: a link must never reference an anchor that does not exist.
    assert m["dangling"] == 0, f"{m['dangling']} link targets resolve to nothing"
    # ...but that alone is nearly vacuous: kfxgen DROPS an unresolvable link
    # rather than emitting a dangling one, so the check above is satisfied by
    # emitting no links whatsoever. A book that declares internal targets must
    # actually produce spans for them (#79).
    if m["anchors"]:
        assert m["links"] > 0, (
            f"{m['anchors']} anchors emitted but zero link spans — every link "
            "was dropped, which 'dangling == 0' cannot see"
        )
    # #60: hidden page-list/landmarks navs are markup, never reading content.
    assert m["nav_junk"] == 0, f"{m['nav_junk']} navigation blocks leaked into the body"
    # #133: an image token in emitted text is a placeholder the generator
    # failed to resolve — the reader gets control characters. Its usual source
    # was a chapter *titled* with an image, which `_leading_chapter_title`
    # admitted because a token is short and has no whitespace. That title was
    # then emitted as a heading chunk and, via `_rebuild_contents_page`, as a
    # contents entry.
    assert m["raw_img_tokens"] == 0, (
        f"{m['raw_img_tokens']} unresolved image token(s) reached emitted text"
    )
    # Every image the source displays inline must survive into the container.
    #
    # This has to be measured against the EPUB, not within the KFX, and the
    # reason is worth stating: the #102 drop rule deletes any `$164` nothing
    # references (`native_generator.py:2211`), so a regression that dropped
    # body-image *refs* would also delete the orphaned resources it created,
    # erasing its own evidence. The KFX alone cannot tell "this book had no
    # body images" from "this book lost all of them".
    #
    # Restricted to `<img>` targets that exist in the manifest, so a source
    # pointing at a missing file is not counted against the generator.
    refs = _inline_image_refs(epub)
    assert m["image_resources"] >= refs, (
        f"source displays {refs} images inline but only {m['image_resources']} "
        f"$164 resources were emitted — {refs - m['image_resources']} are gone. "
        "See #113: an <img> preceded by a sibling caption div is dropped."
    )
    # A $175 ref with no resource behind it is the image analogue of a dangling
    # link. Cheap, and nothing else here would notice.
    if m["images_shown"]:
        assert m["image_resources"] > 0, (
            f"{m['images_shown']} image refs but no $164 resources to draw"
        )
    # #102 in the other direction: resources nothing draws. Note this one is
    # satisfied by the cover alone on a cover-only book — the synthetic cover
    # chapter always emits a $175 and the drop rule exempts `cover_img` — so it
    # guards the drop rule rather than body-image handling. The assertion above
    # is the one that covers body images.
    if m["image_resources"]:
        assert m["images_shown"] > 0, (
            f"{m['image_resources']} image resources emitted but zero $175 refs "
            "— every image is in the file and none of them is on screen"
        )


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not _corpus_files(), reason=f"{CORPUS_ENV} not set or empty")
def test_corpus_metrics_match_baseline(tmp_path):
    """Diff per-book metrics against a recorded run.

    This is the check that catches *silent* damage — text quietly disappearing
    or links quietly vanishing while every invariant above still holds.
    """
    baseline_path = os.environ.get(BASELINE_ENV)
    writing = os.environ.get(WRITE_ENV)
    if not baseline_path and not writing:
        pytest.skip(f"set {BASELINE_ENV} to compare, or {WRITE_ENV}=1 to record")

    current = {}
    for epub in _corpus_files():
        current[epub.name] = _metrics(_convert(epub, tmp_path / f"{epub.stem}.kfx"))

    if writing:
        target = Path(baseline_path or (Path(os.environ[CORPUS_ENV]) / "baseline.json"))
        target.write_text(json.dumps(current, indent=2, sort_keys=True))
        pytest.skip(f"baseline written to {target} ({len(current)} books)")

    baseline = json.loads(Path(baseline_path).read_text())
    problems = []
    for name, want in baseline.items():
        got = current.get(name)
        if got is None:
            problems.append(f"{name}: missing from this run")
            continue
        if want["chars"] and abs(got["chars"] - want["chars"]) / want["chars"] > (
            TEXT_DRIFT_TOLERANCE
        ):
            problems.append(
                f"{name}: text {want['chars']:,} -> {got['chars']:,} "
                f"({100 * (got['chars'] - want['chars']) / want['chars']:+.1f}%)"
            )
        if got["links"] < want["links"]:
            problems.append(f"{name}: links {want['links']} -> {got['links']}")
        if got["dangling"] > want["dangling"]:
            problems.append(f"{name}: dangling {want['dangling']} -> {got['dangling']}")
    assert not problems, "corpus drift:\n  " + "\n  ".join(problems[:20])
