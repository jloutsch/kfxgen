"""A run carrying two links (#759).

A run's flags are a frozenset, so with two link flags in it, "the first"
was string-hash order and changed with PYTHONHASHSEED: the same book could
give different bytes, and a picture under such a run follow either link.
No book is known to give a run two links (nested <a> keeps the inner one,
#317); the generator still has to choose the same one every time. It takes
the smallest target.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kfxgen.inline_style import link_target, make_link_flag

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
TWO = frozenset({make_link_flag("c.xhtml#b"), make_link_flag("c.xhtml#a")})


def test_link_target_takes_the_smallest_of_two():
    assert link_target(TWO) == "c.xhtml#a"
    assert link_target(frozenset({make_link_flag("c.xhtml#b")})) == "c.xhtml#b"
    assert link_target(frozenset()) is None


def _build(out):
    """A book with a run in two links, and a picture under such a run."""
    from kfxgen.converter import _make_img_token
    from kfxgen.native_generator import NativeKFXGenerator
    from tests._helpers import jpeg_of

    pic = _make_img_token("p.jpg", "", None)

    def para(text, spans=(), **extra):
        return {"text": text, "spans": list(spans), **extra}

    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "Chapter",
                "text": "x",
                "blocks": [
                    para("Two links here.", [(0, 9, TWO)]),
                    # Link a runs on past b: whether a's run is one layer or
                    # two depended on which link the set gave last.
                    para(
                        "Both then one.",
                        [(0, 4, TWO), (4, 5, frozenset({make_link_flag("c.xhtml#a")}))],
                    ),
                    para(pic, [(0, len(pic), TWO)]),
                    para("Target A.", anchor_keys=["c.xhtml#a"]),
                    para("Target B.", anchor_keys=["c.xhtml#b"]),
                ],
            }
        ],
        images={"p.jpg": jpeg_of(40, 30)},
        output_path=str(out),
    )


def test_the_book_is_the_same_under_every_hash_seed(tmp_path):
    digests = set()
    for seed in range(8):
        out = tmp_path / f"s{seed}.kfx"
        run = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path[:0] = ['plugin', '.'];"
                "from tests.unit.test_two_links import _build;"
                f"_build({str(out)!r})",
            ],
            cwd=REPO,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stderr[-2000:]
        digests.add(hashlib.sha1(out.read_bytes()).hexdigest())
    assert len(digests) == 1


def test_both_follow_the_smallest_target(tmp_path):
    from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

    out = tmp_path / "b.kfx"
    _build(out)
    frags = load_fragments(out)
    anchors = {str(f.fid): int(val(f)["$183"]["$155"]) for f in by_type(frags, "$266")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    by_pos, landed = {}, []
    for story in by_type(frags, "$259"):
        for e in iter_entries(val(story)["$146"]):
            ref = e.get("$145")
            if ref:
                by_pos[int(e["$155"])] = str(texts[str(ref["name"])][ref["$403"]])
            for span in e.get("$142") or ():
                if "$179" in span:
                    landed.append(anchors[str(span["$179"])])
    assert landed
    assert {by_pos[p] for p in landed} == {"Target A."}
