"""Pictures side by side, through calibre (#762).

Kindle Previewer 4.0.3 (probe PSS, decoded, an English book): two or more
pictures in one run with nothing but spaces between are one paragraph
holding them inline (`$601 $283`), sized in em (px / 16): in a `<p>` (P01)
or a `<div>` (P02), one of them linked (P07, the link a run over it),
centred (P08, `$34 $320`), loose before a caption (P11). A `<br/>` between
them keeps each a block (P04), as do separate `<div>`s (P10). kfxgen wrote
each as a block.

It needs calibre installed, so it is `slow` and skips when `ebook-convert`
is not found.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)
from tests.integration.test_calibre_vertical_writing import _write  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _img(alt, src="w.jpg"):
    return f'<img src="{src}" alt="{alt}"/>'


BODY = (
    "<h1>Chapter</h1><p>V00 A paragraph.</p>"
    f"<p>{_img('P01a')}{_img('P01b', 'n.jpg')}</p>"
    f"<div>{_img('P02a')}{_img('P02b', 'n.jpg')}</div>"
    f"<div>{_img('P04a')}<br/>{_img('P04b', 'n.jpg')}</div>"
    f'<div><a href="c2.xhtml#t">{_img("P07a")}</a>{_img("P07b", "n.jpg")}</div>'
    f'<div style="text-align: center">{_img("P08a", "n.jpg")}{_img("P08b", "n.jpg")}</div>'
    f"<div>{_img('P10a', 'n.jpg')}</div><div>{_img('P10b', 'n.jpg')}</div>"
    f"<div>{_img('P11a', 'n.jpg')}{_img('P11b', 'n.jpg')}<p>A caption below.</p></div>"
    "<p>V99 A paragraph.</p>"
)


@pytest.fixture(scope="module")
def pictures(calibre):  # noqa: F811
    """{alt: (the picture entry, its style, the entry holding it or None,
    that entry's style)}."""
    from tests._helpers import decodable_jpeg_of

    tmp, _env, _version = calibre
    epub, kfx = tmp / "pss.epub", tmp / "pss.kfx"
    _write(
        epub,
        False,
        "",
        "",
        lang="en",
        ch1=BODY,
        images=[
            ("w.jpg", decodable_jpeg_of(400, 100)),
            ("n.jpg", decodable_jpeg_of(200, 40)),
        ],
    )
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}

    def style(e):
        own = styles.get(str(e.get("$157")), {}) if e is not None else {}
        return {str(k): str(v) for k, v in own.items() if str(k) != "$173"}

    out = {}

    def walk(entries, parent):
        for e in entries:
            if not hasattr(e, "get"):
                continue
            if str(e.get("$159")) == "$271":
                out[str(e.get("$584"))] = (e, style(e), parent, style(parent))
            walk(e.get("$146") or [], e)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"], None)
    return out


@pytest.mark.parametrize("pair", ["P01", "P02", "P07", "P08", "P11"])
def test_pictures_together_are_inline_in_one_paragraph(pictures, pair):
    first, second = pictures[f"{pair}a"], pictures[f"{pair}b"]
    for entry, own, _parent, _around in (first, second):
        assert str(entry.get("$601")) == "$283"
        assert "$308" in own["$56"] and "$308" in own["$57"]
    assert first[2] is not None and first[2] is second[2]


def test_their_size_is_their_pixels_in_em(pictures):
    # P01: 400 x 100 px is 25em x 6.25em; 200 x 40 px is 12.5em x 2.5em.
    _e, own, _p, _a = pictures["P01a"]
    assert "'25'" in own["$56"] and "'6.25'" in own["$57"]
    _e, own, _p, _a = pictures["P01b"]
    assert "'12.5'" in own["$56"] and "'2.5'" in own["$57"]


def test_a_linked_one_keeps_its_link_and_a_centred_line_its_alignment(pictures):
    # P07: the link a run over the first picture; P08: `$34 $320`.
    _e, _own, parent, _around = pictures["P07a"]
    (run,) = parent["$142"]
    assert (run["$143"], run["$144"]) == (0, 1) and "$179" in run
    _e, _own, _parent, around = pictures["P08a"]
    assert around.get("$34") == "$320"


@pytest.mark.parametrize("pair", ["P04", "P10"])
def test_a_line_break_or_separate_blocks_keep_each_a_block(pictures, pair):
    for side in "ab":
        entry, _own, _parent, _around = pictures[f"{pair}{side}"]
        assert entry.get("$601") is None
