"""A picture in a sized wrapper is drawn at the wrapper's width (#382).

`<div style="width: 40%"><img style="width: 100%"/><p>Caption.</p></div>`,
floated or not, drew its picture across the whole page: the wrapper's width
was dropped. Kindle Previewer 4 (kpr4, decoded) writes the wrapper as a
container at its width, holding the picture and the caption: a `$269` at
`$56: 40%`, centred (`$580`) when its side margins are `auto`, a 300px one
at 35.156%. A floated wrapper is a `$270` with `$140` (the Voyage, 5.13.6,
stacks Previewer's floated container rather than wrapping text beside it).
A picture with no width of its own fills the container.

A floated wrapper holding only the picture keeps #243's shape: the picture
itself floats at the wrapper's width, which the Voyage wraps text beside.
"""

import pytest

from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import WORDS, _blocks, _tokens
from kfxgen.native_generator import NativeKFXGenerator

CSS = {
    "w40": {"width": "40%"},
    "f40": {"width": "40%", "float": "left"},
    "c40": {"width": "40%", "margin-left": "auto", "margin-right": "auto"},
    "w300": {"width": "300px"},
    "full": {"width": "100%"},
    "half": {"width": "50%"},
}
#: A border as calibre's Stylizer reports it, per side.
_BORDER = {
    f"border-{side}-{k}": v
    for side in ("top", "right", "bottom", "left")
    for k, v in (("style", "solid"), ("width", "0.75"), ("color", "black"))
}
CSS["bw40"] = {"width": "40%", **_BORDER}
CSS["box"] = dict(_BORDER)

CAPTION = "Figure 1. A caption."


def _figure(cls, tag="div", img="full", caption=True):
    cap = f"<p>{CAPTION}</p>" if caption else ""
    return (
        f'<{tag} class="{cls}"><img class="{img}" src="i.jpg" alt="a"/>{cap}</{tag}>'
        f"<p>{WORDS}</p>"
    )


def _box(blocks):
    (box,) = [b for b in blocks if b.get("type") == "box"]
    return box


# --- The converter: a sized wrapper around a picture is a box ---------------


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["div", "figure"])
def test_a_sized_wrapper_around_a_picture_is_a_box_at_its_width(tag):
    box = _box(_blocks(_figure("w40", tag), CSS))
    assert box["box"]["width"] == 40.0
    assert box["box"]["float"] is None
    assert not box["box"]["centred"]
    assert box["box"]["border"] is None and box["box"]["background"] is None
    kids = box["blocks"]
    assert _tokens(kids[:1]) == [("w=100%", None)]
    assert kids[1]["text"] == CAPTION


@pytest.mark.unit
def test_a_floated_sized_wrapper_with_a_caption_is_a_floated_box():
    box = _box(_blocks(_figure("f40"), CSS))
    assert (box["box"]["width"], box["box"]["float"]) == (40.0, "left")
    assert _tokens(box["blocks"]) == [("w=100%", None)]


@pytest.mark.unit
def test_a_floated_wrapper_of_only_a_picture_keeps_the_floated_picture():
    """#243's shape, device-verified: no box, the picture floats at 40%."""
    blocks = _blocks(_figure("f40", caption=False), CSS)
    assert not [b for b in blocks if b.get("type") == "box"]
    assert _tokens(blocks) == [("w=40%", "left")]


@pytest.mark.unit
def test_a_sized_wrapper_of_only_a_picture_is_a_box():
    box = _box(_blocks(_figure("w40", caption=False), CSS))
    assert box["box"]["width"] == 40.0
    assert _tokens(box["blocks"]) == [("w=100%", None)]


@pytest.mark.unit
def test_auto_side_margins_centre_the_box():
    assert _box(_blocks(_figure("c40"), CSS))["box"]["centred"]


@pytest.mark.unit
def test_a_px_width_is_a_share_of_the_page_at_previewers_rate():
    """Previewer: 300px is 35.156 %, 0.45pt a px and 12pt an em."""
    assert _box(_blocks(_figure("w300"), CSS))["box"]["width"] == pytest.approx(
        35.15625
    )


@pytest.mark.unit
def test_a_picture_with_no_width_fills_its_box(tmp_path):
    """A small picture, which kfxgen would otherwise size from its pixels."""
    body = _figure("w40", img="none")
    ((_ftype, _style, kids),) = _containers(body, tmp_path, image=(120, 80))
    (picture,) = [s for k, s in kids if k == "image"]
    assert _pct(picture) == (100.0, "$314")


@pytest.mark.unit
def test_a_picture_keeps_its_own_share_of_the_box():
    box = _box(_blocks(_figure("w40", img="half"), CSS))
    assert _tokens(box["blocks"]) == [("w=50%", None)]


@pytest.mark.unit
def test_a_sized_wrapper_of_text_only_is_unchanged():
    blocks = _blocks(f'<div class="w40"><p>{CAPTION}</p></div><p>{WORDS}</p>', CSS)
    assert [b.get("type") for b in blocks] == [None, None]


@pytest.mark.unit
def test_a_bordered_sized_wrapper_is_one_box_with_both():
    box = _box(_blocks(_figure("bw40"), CSS))
    assert box["box"]["width"] == 40.0 and box["box"]["border"]


@pytest.mark.unit
def test_a_bordered_box_with_no_width_has_none():
    blocks = _blocks(f'<div class="box"><p>{CAPTION}</p></div>', CSS)
    assert _box(blocks)["box"]["width"] is None


# --- The generator: the box is a container at its width ---------------------


def _containers(body, tmp_path, image=(600, 400)):
    """[(container type, its style, [what is in it])] for each container
    holding a picture."""
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
        images={"i.jpg": jpeg_of(*image)},
    )  # fmt: skip
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = []

    def kind(e):
        if str(e.get("$159")) == "$271":
            return ("image", styles.get(str(e.get("$157")), {}))
        if e.get("$145"):
            r = e["$145"]
            return ("text", str(texts[str(r["name"])][r["$403"]]))
        return (str(e.get("$159")), None)

    def walk(entries):
        for e in entries:
            if isinstance(e, str):
                continue
            kids = [k for k in e.get("$146") or [] if not isinstance(k, str)]
            if any(str(k.get("$159")) == "$271" for k in kids):
                st = styles.get(str(e.get("$157")), {})
                found.append((str(e.get("$159")), st, [kind(k) for k in kids]))
            walk(kids)

    for st in by_type(frags, "$259"):
        walk(val(st)["$146"])
    return found


def _pct(style, key="$56"):
    return float(style[key]["$307"]), str(style[key]["$306"])


@pytest.mark.unit
def test_a_sized_box_is_a_container_at_its_width(tmp_path):
    ((ftype, style, kids),) = _containers(_figure("w40"), tmp_path)
    assert ftype == "$269"
    assert _pct(style) == (40.0, "$314")
    assert "$140" not in style and "$580" not in style
    (picture,) = [s for k, s in kids if k == "image"]
    assert _pct(picture) == (100.0, "$314")
    assert ("text", CAPTION) in kids


@pytest.mark.unit
def test_a_floated_box_is_a_floated_container(tmp_path):
    ((ftype, style, _kids),) = _containers(_figure("f40"), tmp_path)
    assert ftype == "$270"
    assert style["$140"] == "$59"
    assert _pct(style) == (40.0, "$314")


@pytest.mark.unit
def test_a_centred_box_is_centred(tmp_path):
    ((_ftype, style, _kids),) = _containers(_figure("c40"), tmp_path)
    assert style["$580"] == "$320"


@pytest.mark.unit
def test_a_bordered_box_keeps_its_type_and_takes_the_width(tmp_path):
    ((ftype, style, _kids),) = _containers(_figure("bw40"), tmp_path)
    assert ftype == "$270"
    assert _pct(style) == (40.0, "$314")


@pytest.mark.unit
@pytest.mark.parametrize("caption_first", [False, True], ids=["below", "above"])
def test_a_plain_box_has_only_its_width(tmp_path, caption_first):
    """Previewer's container for a sized wrapper has its width and no space
    or padding: those are for a box with a border (#238)."""
    cap, img = f"<p>{CAPTION}</p>", '<img class="full" src="i.jpg" alt="a"/>'
    inner = cap + img if caption_first else img + cap
    body = f'<div class="w40">{inner}</div><p>{WORDS}</p>'
    ((_ftype, style, _kids),) = _containers(body, tmp_path)
    assert set(style) <= {"$173", "$56"}


@pytest.mark.unit
def test_a_caption_beside_an_unsized_picture_keeps_its_styling(tmp_path):
    """pg28428: `<div class="figcenter" style="width: 350px">` holds the
    picture and `<span class="smcap">Fig. 16.</span>—…` as one paragraph.
    Filling the box once lengthened the picture's token, shifting the
    caption's runs: the small caps covered 1 character instead of 8."""
    css = dict(CSS, sc={"font-variant": "small-caps"})
    body = (
        '<div class="w300"><img src="i.jpg" alt="a"/>'
        '<span class="sc">Fig. 16.</span>—A caption.</div>'
        f"<p>{WORDS}</p>"
    )
    blocks = _blocks(body, css)
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "One", "text": "\n\n".join(b["text"] for b in blocks),
             "blocks": blocks}
        ],
        output_path=str(out),
        images={"i.jpg": jpeg_of(600, 400)},
    )  # fmt: skip
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    from tests._kfx_introspect import iter_entries

    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            r = e.get("$145")
            if r and str(texts[str(r["name"])][r["$403"]]).startswith("Fig. 16."):
                caps = [
                    (sp["$143"], sp["$144"])
                    for sp in e.get("$142") or []
                    if "$583" in styles.get(str(sp.get("$157")), {})
                ]
                assert caps == [(0, 8)]
                return
    raise AssertionError("no caption paragraph")
