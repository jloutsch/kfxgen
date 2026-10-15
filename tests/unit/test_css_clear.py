"""CSS `clear` is written as Kindle Previewer writes it (#381).

kfxgen floats a picture (#243) but wrote nothing for `clear`, so a heading
or paragraph a book starts below a floated picture sat beside it on the
Voyage (5.13.6; Previewer's book puts it below). Kindle Previewer 4
(kpr4, decoded) writes `$628` on the cleared element's style, with the side
as written: `$421` both, `$59` left, `$61` right, whether or not a float
comes before it. An empty clearing element (`<div class="clear"></div>`)
passes its clear to the next block; a cleared `<div>` around a picture puts
it on the picture; a cleared `<hr>` keeps it on the rule; a `<br>` with
`clear` gets nothing.
"""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import WORDS, _blocks

_BORDER = {
    f"border-{side}-{k}": v
    for side in ("top", "right", "bottom", "left")
    for k, v in (("style", "solid"), ("width", "0.75"), ("color", "black"))
}
CSS = {
    "fl": {"float": "left", "width": "30%"},
    "cb": {"clear": "both"},
    "cl": {"clear": "left"},
    "cr": {"clear": "right"},
    "cn": {"clear": "none"},
    "bcb": {"clear": "both", **_BORDER},
    "hrcb": {"clear": "both", **_BORDER},
    "w20": {"width": "20%"},
}
PICTURE = '<img class="fl" src="i.jpg" alt="a"/>'


def _clears(body):
    """(type, clear) of each block the converter made."""
    return [(b.get("type", "text"), b.get("clear")) for b in _blocks(body, CSS)]


# --- The converter: which block takes the clear ------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cls", "side"), [("cb", "both"), ("cl", "left"), ("cr", "right")]
)
def test_a_cleared_paragraph_keeps_its_side(cls, side):
    body = f'<p>{PICTURE}Short.</p><p class="{cls}">{WORDS}</p>'
    assert _clears(body) == [("text", None), ("text", side)]


@pytest.mark.unit
def test_clear_none_is_nothing():
    assert _clears(f'<p class="cn">{WORDS}</p>') == [("text", None)]


@pytest.mark.unit
def test_an_empty_clearing_div_clears_the_next_block():
    body = f'<p>{PICTURE}Short.</p><div class="cb"></div><p>{WORDS}</p>'
    assert _clears(body) == [("text", None), ("text", "both")]


@pytest.mark.unit
def test_a_cleared_container_clears_its_first_block():
    body = f'<div class="cl"><p>{WORDS}</p><p>{WORDS}</p></div>'
    assert _clears(body) == [("text", "left"), ("text", None)]


@pytest.mark.unit
def test_a_cleared_br_is_nothing():
    """Previewer writes no `$628` for a `<br>` with `clear`."""
    body = f'<div><p>{PICTURE}Short.</p><br class="cb"/><p>{WORDS}</p></div>'
    assert [c for _t, c in _clears(body)] == [None, None]


@pytest.mark.unit
def test_a_cleared_rule_keeps_its_clear():
    body = f'<p>{WORDS}</p><hr class="hrcb"/><p>{WORDS}</p>'
    assert _clears(body) == [("text", None), ("rule", "both"), ("text", None)]


@pytest.mark.unit
def test_a_cleared_div_around_a_picture_clears_the_picture():
    body = f'<p>{WORDS}</p><div class="cb"><img class="w20" src="i.jpg" alt="a"/></div>'
    blocks = _blocks(body, CSS)
    assert blocks[-1]["text"].startswith("\x00IMG") and blocks[-1]["clear"] == "both"


@pytest.mark.unit
def test_a_cleared_bordered_box_clears_the_box():
    body = f'<div class="bcb"><p>{WORDS}</p><p>{WORDS}</p></div>'
    (box,) = _blocks(body, CSS)
    assert box["type"] == "box" and box["clear"] == "both"
    assert [b.get("clear") for b in box["blocks"]] == [None, None]


# --- The generator: `$628` on the cleared entry's style ----------------------


def _entries(body, tmp_path):
    """(type, style) of every storyline entry, nested ones too, with the
    text of text entries: [(type, style, text or None)]."""
    blocks = _blocks(body, CSS)
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "One", "text": "\n\n".join(b["text"] for b in blocks),
             "blocks": blocks}
        ],
        output_path=str(out),
        images={"i.jpg": jpeg_of(300, 600)},
    )  # fmt: skip
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = []

    def walk(entries):
        for e in entries:
            if isinstance(e, str):
                continue
            text = None
            if e.get("$145"):
                r = e["$145"]
                text = str(texts[str(r["name"])][r["$403"]])
            style = styles.get(str(e.get("$157")), {})
            found.append((str(e.get("$159")), style, text))
            walk(e.get("$146") or [])

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"])
    return found


def _clear_of(style):
    v = style.get("$628")
    return None if v is None else str(v)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cls", "symbol"), [("cb", "$421"), ("cl", "$59"), ("cr", "$61")]
)
def test_a_cleared_paragraph_after_a_float_has_628(tmp_path, cls, symbol):
    body = f'<p>{PICTURE}Short.</p><p class="{cls}">Below. {WORDS}</p>'
    found = _entries(body, tmp_path)
    (picture,) = [s for t, s, _ in found if t == "$271"]
    assert str(picture["$140"]) == "$59" and _clear_of(picture) is None
    by_text = {txt.split(".")[0]: s for t, s, txt in found if txt}
    assert _clear_of(by_text["Below"]) == symbol
    assert _clear_of(by_text["Short"]) is None


@pytest.mark.unit
def test_only_the_first_entry_of_a_cleared_block_has_628(tmp_path):
    """A picture splits the paragraph into three entries; the ones after it
    continue the paragraph."""
    body = (
        f'<p class="cb">First. <img class="w20" src="i.jpg" alt="a"/>After. {WORDS}</p>'
        f"<p>Next. {WORDS}</p>"
    )
    found = [
        (t, txt.split(".")[0] if txt else None, _clear_of(s))
        for t, s, txt in _entries(body, tmp_path)
        if t == "$271" or (txt or "").startswith(("First", "After", "Next"))
    ]
    assert found == [
        ("$269", "First", "$421"),
        ("$271", None, None),
        ("$269", "After", None),
        ("$269", "Next", None),
    ]


@pytest.mark.unit
def test_an_empty_clearing_div_clears_the_next_paragraph(tmp_path):
    body = f'<p>{PICTURE}Short.</p><div class="cb"></div><p>Below. {WORDS}</p>'
    by_text = {txt.split(".")[0]: s for _t, s, txt in _entries(body, tmp_path) if txt}
    assert _clear_of(by_text["Below"]) == "$421"


@pytest.mark.unit
def test_a_cleared_picture_has_628_and_an_uncleared_one_none(tmp_path):
    body = (
        f'<p>{WORDS}</p><div class="cb"><img class="w20" src="i.jpg" alt="a"/></div>'
        f'<p>{WORDS}</p><div><img class="w20" src="i.jpg" alt="b"/></div>'
    )
    pictures = [s for t, s, _ in _entries(body, tmp_path) if t == "$271"]
    assert [_clear_of(s) for s in pictures] == ["$421", None]
    # The same size either way.
    assert pictures[0]["$56"] == pictures[1]["$56"]


@pytest.mark.unit
def test_a_cleared_rule_has_628(tmp_path):
    body = f'<p>{WORDS}</p><hr class="hrcb"/><p>{WORDS}</p>'
    (rule,) = [s for t, s, _ in _entries(body, tmp_path) if t == "$596"]
    assert _clear_of(rule) == "$421"


@pytest.mark.unit
def test_a_cleared_box_has_628_on_the_box(tmp_path):
    body = f'<div class="bcb"><p>Inside. {WORDS}</p></div>'
    found = _entries(body, tmp_path)
    (box,) = [s for t, s, _ in found if t == "$270"]
    assert _clear_of(box) == "$421"
    (inside,) = [s for _t, s, txt in found if txt and txt.startswith("Inside")]
    assert _clear_of(inside) is None


@pytest.mark.unit
def test_a_book_with_no_clear_has_no_628(tmp_path):
    body = f'<p>{PICTURE}Short.</p><p>{WORDS}</p><hr class="hrcb"/>'
    body = body.replace("hrcb", "nothing")
    assert all("$628" not in s for _t, s, _ in _entries(body, tmp_path))


@pytest.mark.unit
def test_a_cleared_table_has_628_on_the_table(tmp_path):
    """Previewer writes it on the `$278` (#386 review). Cells of paragraphs
    are walked as the body is (#261); the table's clear waits past them."""
    body = (
        f'<p>{WORDS}</p><table class="cb"><tr><td><p>One cell</p><p>More</p></td>'
        "<td><p>Two cell</p></td></tr></table>"
    )
    found = _entries(body, tmp_path)
    (table,) = [s for t, s, _ in found if t == "$278"]
    assert _clear_of(table) == "$421"
    assert all(_clear_of(s) is None for t, s, _ in found if t != "$278")


@pytest.mark.unit
def test_a_cleared_linked_picture_has_628_on_the_link_container(tmp_path):
    """Previewer writes it on the `$269` holding the link, not the picture
    inside (#386 review)."""
    body = (
        f'<p>{WORDS}</p><div class="cb"><a href="c.xhtml#t"><img class="w20" '
        f'src="i.jpg" alt="a"/></a></div><p id="t">{WORDS}</p>'
    )
    found = _entries(body, tmp_path)
    i = next(n for n, (t, _s, _x) in enumerate(found) if t == "$271")
    link, picture = found[i - 1], found[i]
    assert link[0] == "$269" and _clear_of(link[1]) == "$421"
    assert _clear_of(picture[1]) is None
