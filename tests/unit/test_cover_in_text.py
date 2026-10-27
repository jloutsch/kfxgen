"""The cover's picture, used in the text too, is shown there, sharing the
cover's resource rather than storing its bytes twice (#475)."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

COVER = jpeg_of(600, 800)
OTHER = jpeg_of(300, 200)


def _book(tmp_path, images):
    tok = "\x00IMG\x01{}\x01\x00".format
    text = f"A {tok('cover.jpg')} B {tok('b.jpg')} C"
    out = tmp_path / "c.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            # An opening page, so the cover page (#32) is still written.
            {
                "title": "Open",
                "text": "Open.",
                "blocks": [{"text": "Open.", "spans": []}],
            },
            {"title": "One", "text": text, "blocks": [{"text": text, "spans": []}]},
        ],
        cover_image=COVER,
        images=images,
        output_path=str(out),
    )
    frags = load_fragments(out)
    pictures = [
        str(e["$175"])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$175") is not None
    ]
    return pictures, sorted(str(f.fid) for f in by_type(frags, "$164"))


@pytest.mark.unit
def test_the_covers_picture_in_the_text_shares_its_resource(tmp_path):
    pictures, resources = _book(tmp_path, {"cover.jpg": COVER, "b.jpg": OTHER})
    # The cover page (#32), then the text's two pictures; one resource each.
    assert pictures == ["cover_img", "cover_img", "img_0"]
    assert resources == ["cover_img", "img_0"]


@pytest.mark.unit
def test_a_different_picture_named_like_the_cover_is_its_own(tmp_path):
    pictures, resources = _book(
        tmp_path, {"cover.jpg": jpeg_of(400, 400), "b.jpg": OTHER}
    )
    assert pictures == ["cover_img", "img_1", "img_0"]
    assert resources == ["cover_img", "img_0", "img_1"]


TOK = "\x00IMG\x01{}\x01\x00".format


def _first_page_book(tmp_path, blocks, href="cover.jpg"):
    """Each section's entries, a picture's resource or "text", for a book
    whose first page holds `blocks` (strings, or block dicts)."""
    blocks = [b if isinstance(b, dict) else {"text": b, "spans": []} for b in blocks]
    out = tmp_path / "f.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "Cover",
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": blocks,
            },
            {
                "title": "One",
                "text": "Body.",
                "blocks": [{"text": "Body.", "spans": []}],
            },
        ],
        cover_image=COVER,
        images={href: COVER},
        output_path=str(out),
    )
    return [
        [
            str(e["$175"]) if e.get("$175") is not None else "text"
            for e in iter_entries(val(st)["$146"])
            if hasattr(e, "get") and (e.get("$175") is not None or e.get("$145"))
        ]
        for st in by_type(load_fragments(out), "$259")
    ]


def _pictures(sections):
    return [e for s in sections for e in s if e != "text"]


@pytest.mark.unit
def test_a_first_page_showing_the_cover_after_text_shows_it_there(tmp_path):
    """kfxgen's cover page first, as Kindle Previewer 4.0.3's, then the
    page's text and its cover (#475, QA's c7 and c8 probes)."""
    sections = _first_page_book(tmp_path, [f"One Text. {TOK('cover.jpg')}"])
    assert _pictures(sections) == ["cover_img", "cover_img"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("href", "token"),
    [("cover.jpg", "cover.jpg"), ("ops/images/c.jpg", "../images/c.jpg")],
    ids=["plain", "relative"],
)
def test_a_first_page_opening_with_the_cover_alone_shows_it_once(tmp_path, href, token):
    """The book's own cover page, or a first page opening with the cover in
    a paragraph of its own: that picture is kfxgen's cover page's, which
    stays a page of its own, as on main and in Previewer. Printed again under
    it, the cover showed twice in a row (#475); with no cover page, the
    page's text ran on under the cover (#516 review). A relative href names
    the cover as the generator resolves it."""
    sections = _first_page_book(tmp_path, [TOK(token), "Words after it."], href)
    assert sections[0] == ["cover_img"]
    assert _pictures(sections) == ["cover_img"]


@pytest.mark.unit
def test_a_cover_sharing_its_paragraph_with_text_stays(tmp_path):
    """Kindle Previewer 4.0.3 prints it twice there: its cover page, then the
    page as written (QA's c1 probe on #516)."""
    sections = _first_page_book(tmp_path, [f"{TOK('cover.jpg')} Words after it."])
    assert sections[0] == ["cover_img"]
    assert _pictures(sections) == ["cover_img", "cover_img"]


@pytest.mark.unit
def test_a_first_page_opening_with_the_cover_in_a_box(tmp_path):
    """The cover inside a styled box (#238), as an SVG cover page reached
    one library book: the box's own blocks hold the picture, so it is left
    out there (#516 recheck)."""
    from tests.unit.test_native_generator import _box_block

    box = _box_block([{"text": TOK("cover.jpg"), "spans": []}])
    sections = _first_page_book(tmp_path, [box, "Words after it."])
    assert sections[0] == ["cover_img"]
    assert _pictures(sections) == ["cover_img"]


@pytest.mark.unit
@pytest.mark.parametrize("boxed", [False, True], ids=["plain", "box"])
def test_a_link_to_the_left_out_cover_still_lands(tmp_path, boxed):
    """A link to the id on the first page's cover picture: that picture is
    left out, and the link lands on what follows it, not dropped (#475; 2
    links in one library book, whose first page held a copy of the cover)."""
    from lxml import etree

    from kfxgen import converter as _conv
    from tests.unit.test_link_landings import _link

    pic = (
        '<div id="cv"><p><img src="cover.jpg"/></p></div>'
        if boxed
        else '<p id="cv"><img src="cover.jpg"/></p>'
    )
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"{pic}<p>Words after it.</p></body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )
    if boxed:
        from tests.unit.test_native_generator import _box_block

        box = _box_block([blocks[0]])
        box["anchor_keys"] = blocks[0]["anchor_keys"]
        blocks = [box, *blocks[1:]]
    out = tmp_path / "l.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "Cover", "text": "x", "blocks": blocks},
            {"title": "Links", "text": "x", "blocks": [_link("L1", "c.xhtml#cv")]},
        ],
        cover_image=COVER,
        images={"cover.jpg": COVER},
        output_path=str(out),
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get")
    ]
    anchors = {str(f.fid): val(f)["$183"]["$155"] for f in by_type(frags, "$266")}
    by_id = {e["$155"]: e for e in entries if "$155" in e}
    link = next(
        e
        for e in entries
        if e.get("$145")
        and str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])]) == "L1"
    )
    targets = [sp["$179"] for sp in link.get("$142") or [] if "$179" in sp]
    assert len(targets) == 1
    landing = by_id[anchors[str(targets[0])]]
    text = landing.get("$145")
    assert text and str(content[str(text["name"])][int(text["$403"])]) == (
        "Words after it."
    )
    assert [str(e["$175"]) for e in entries if e.get("$175") is not None] == [
        "cover_img"
    ]


@pytest.mark.unit
def test_a_box_holding_the_cover_and_more_keeps_the_rest(tmp_path):
    """Only the cover picture leaves the box: its other paragraphs print in
    it, once (#475)."""
    from tests.unit.test_native_generator import _box_block

    box = _box_block(
        [{"text": TOK("cover.jpg"), "spans": []}, {"text": "Inside.", "spans": []}]
    )
    sections = _first_page_book(tmp_path, [box, "Words after it."])
    assert sections[0] == ["cover_img"]
    assert "cover_img" not in sections[1]
    assert _pictures(sections) == ["cover_img"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "rest", [[], ["\xa0"], [" ", "\xa0"]], ids=["alone", "nbsp", "spaces"]
)
def test_a_first_page_of_only_the_cover_keeps_it(tmp_path, rest):
    """A first page holding only a copy of the cover, and at most spaces:
    left out, the page was a near-blank one of a lone NBSP (QA's c5 probe on
    #516). The copy stays shown there, as on main and in Kindle Previewer
    4.0.3 (#524)."""
    sections = _first_page_book(tmp_path, [TOK("cover.jpg"), *rest])
    assert _pictures(sections) == ["cover_img", "cover_img"]
