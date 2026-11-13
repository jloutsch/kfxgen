"""A block picture is placed as its block places its text (#769).

Kindle Previewer 4.0.3 (probes PBI, PBJ, PBR, decoded): `$580` only when the
block is aligned, or the picture is `display: block` with auto side
margins; `$48` its own, its block's and its containers' left margins, plus
its block's first-line indent; `$50` the right margins. kfxgen centred every
block picture and wrote no margins. Through calibre in
tests/integration/test_calibre_block_picture_place.py.
"""

import pytest

from tests.unit.test_floats import _entries

pytestmark = pytest.mark.unit

CSS = {
    "ind": {"text-indent": "1.5em"},
    "c": {"text-align": "center"},
    "r": {"text-align": "right", "margin-right": "2em"},
    "auto": {"display": "block", "margin-left": "auto", "margin-right": "auto"},
    "inlauto": {"margin-left": "auto", "margin-right": "auto"},
    "ownml": {"margin-left": "2em"},
    "ml3": {"margin-left": "3em"},
}
IMG = '<img src="i.jpg" alt="a"/>'


def _img(cls):
    return f'<img src="i.jpg" alt="a" class="{cls}"/>'


def _styles(body, tmp_path):
    return [s for kind, s in _entries(body, CSS, tmp_path) if kind == "image"]


def _pct(style, key):
    v = style.get(key)
    return None if v is None else float(v["$307"])


@pytest.mark.parametrize(
    "body, align, left, right",
    [
        # Unaligned: no $580; its indent as $48 (PBI B01).
        (f'<p class="ind">{IMG}</p>', None, 4.688, None),
        (f"<p>{IMG}</p>", None, None, None),
        (f'<p class="c">{IMG}</p>', "$320", None, None),
        # Right, with its right margin (PBJ J12).
        (f'<p class="r">{IMG}</p>', "$61", None, 6.25),
        # display: block and auto margins centre it; inline they don't
        # (J04, J16).
        (f"<p>{_img('auto')}</p>", "$320", None, None),
        (f"<p>{_img('inlauto')}</p>", None, None, None),
        # Its own margin (J05), and its container's (PBI B03).
        (f"<p>{_img('ownml')}</p>", None, 6.25, None),
        (f'<div class="ml3"><p>{IMG}</p></div>', None, 9.375, None),
    ],
    ids=[
        "indent",
        "none",
        "centre",
        "right",
        "auto",
        "inline-auto",
        "own",
        "container",
    ],
)
def test_placed_as_its_block(tmp_path, body, align, left, right):
    (style,) = _styles(body, tmp_path)
    assert style.get("$580") == align
    assert (_pct(style, "$48"), _pct(style, "$50")) == (left, right)


def test_the_indent_is_the_first_lines_only(tmp_path):
    # PBJ J20: two pictures a line each, the indent on the first.
    first, second = _styles(f'<p class="ind">{IMG}<br/>{IMG}</p>', tmp_path)
    assert _pct(first, "$48") == 4.688
    assert "$48" not in second


def test_kfxgens_own_cover_page_stays_centred(tmp_path):
    # calibre takes a book's cover page out of the spine and kfxgen writes
    # its own; a source cover page is centred (pg37106's `text-align:
    # center`), so a cover narrower than the page stays in the middle.
    from kfxgen.native_generator import NativeKFXGenerator
    from tests._helpers import jpeg_of
    from tests._kfx_introspect import by_type, load_fragments, val

    out = tmp_path / "c.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [
            {
                "title": "One",
                "text": "Words.",
                "blocks": [{"text": "Words.", "spans": []}],
            }
        ],
        output_path=str(out),
        cover_image=jpeg_of(400, 600),
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (cover,) = [
        styles[str(e["$157"])]
        for st in by_type(frags, "$259")
        for e in val(st)["$146"]
        if str(e.get("$159")) == "$271"
    ]
    assert float(cover["$56"]["$307"]) < 100
    assert cover["$580"] == "$320"
