"""Image resource names come from the book, not from iteration order (#189).

`img_0`, `img_1`, … used to be handed out in whatever order the caller's
`images` mapping yielded. That mapping is built by walking
`oeb_book.manifest`, and Calibre backs `Manifest` with a `set` — so the order
differs between processes. Converting one book twice through `ebook-convert`
gave `img_1` to a different picture each time and produced a different file:

    run 1   p1 p2 p3 p4   -> 0dcc3d6e3de7
    run 2   p1 p2 p3 p4   -> 0dcc3d6e3de7
    run 3   p3 p1 p2 p4   -> 034e4f0447d6

Nothing rendered wrongly — every `$259` entry referenced whichever name held
its own image — so this was invisible to a reader and visible to anyone
diffing output.

This is #96 one allocation over: that fixed the same drift in `$157` style
symbols, for the same reason, with the same remedy.

**Why this test and not the golden gate.** `tier3_strict` exists for byte
drift and could not see this. It builds through `EpubAsOeb`, the test shim,
which walks a zip's manifest in a fixed order — so the golden path is the one
path that cannot vary. Rather than put Calibre in CI to exercise the path that
can, these tests shuffle the mapping and assert the property directly: the
names must depend on the hrefs and on nothing else.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin"))

from kfxgen.kfxlib_minimal.ion import IS  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402
from tests._helpers import jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402

pytestmark = [pytest.mark.tier1, pytest.mark.unit]

#: Four distinct images, each a different size so they cannot be confused.
HREFS = ("images/p1.jpg", "images/p2.jpg", "images/p3.jpg", "images/p4.jpg")


def _images(order) -> dict[str, bytes]:
    """The same four images, inserted in the given order."""
    return {h: jpeg_of(300 + 10 * HREFS.index(h), 400) for h in order}


def _build(images: dict[str, bytes]) -> Path:
    text = "\n\n".join(f"\x00IMG\x01{h}\x01\x00" for h in HREFS)
    out = Path(tempfile.mkdtemp()) / "book.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "Pages", "text": text}],
        output_path=str(out),
        images=images,
    )
    return out


def _name_to_bytes(path: Path) -> dict[str, bytes]:
    """resource name -> the payload it holds, which is what must stay put."""
    frags = load_fragments(path)
    payloads = {
        str(f.fid).split("/")[-1]: bytes(val(f)) for f in by_type(frags, "$417")
    }
    return payloads


def test_resource_names_survive_a_shuffled_mapping():
    """The defect, in the form a reader of this file can check.

    Calibre's manifest is a set, so this shuffle is not a contrivance — it is
    what a second `ebook-convert` run actually produced.
    """
    forward = _build(_images(HREFS))
    shuffled = _build(
        _images(("images/p3.jpg", "images/p1.jpg", "images/p4.jpg", "images/p2.jpg"))
    )
    assert _name_to_bytes(forward) == _name_to_bytes(shuffled), (
        "the same four images, inserted in a different order, produced "
        "different name-to-payload assignments. Resource numbering must come "
        "from the hrefs, not from the mapping's iteration order (#189)."
    )


def test_the_whole_file_is_identical_under_a_shuffle():
    """Stronger, and the claim 5.7.2 actually makes.

    Name-to-payload stability is the mechanism; byte-identity is the promise.
    Asserted separately so a future change that keeps the mapping but moves
    something else still fails here.
    """
    a = _build(_images(HREFS)).read_bytes()
    b = _build(_images(tuple(reversed(HREFS)))).read_bytes()
    assert a == b, (
        "the same book built from the same images in a different insertion "
        "order produced different bytes — output is not reproducible (#189)"
    )


def test_numbering_is_contiguous_and_href_ordered():
    """`img_0` is the alphabetically first href, and there are no gaps.

    Pins the order itself rather than only its stability, so that a future
    change to *which* order cannot slip through as "still deterministic".
    """
    # Built from a shuffled mapping on purpose: with the input already in
    # href order this assertion holds whether or not anything sorts, and would
    # pass against the defect it exists to catch.
    frags = load_fragments(_build(_images(tuple(reversed(HREFS)))))
    by_name = {}
    for f in by_type(frags, "$164"):
        name = str(f.fid)
        if name.startswith("img_"):
            by_name[name] = val(f)
    assert sorted(by_name) == [f"img_{i}" for i in range(len(HREFS))], (
        f"resource names must be contiguous from img_0, got {sorted(by_name)}"
    )
    # img_N should carry the Nth href alphabetically: distinguishable because
    # each image was built at a different width.
    widths = [int(by_name[f"img_{i}"][IS("$422")]) for i in range(len(HREFS))]
    assert widths == sorted(widths), (
        f"img_0..img_N should follow href order, which here means ascending "
        f"width; got {widths}"
    )
