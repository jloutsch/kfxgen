"""A paragraph holding a floated picture and words, as Kindle Previewer 4.0.3
writes it (#565, probed): a plain container carrying the paragraph's space,
the pictures floated at its start as entries, then the rest as one entry with
the later floats among its words."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import decodable_jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val

pytestmark = pytest.mark.unit

WIDE = decodable_jpeg_of(300, 220)


def _resolver(e):
    return dict(
        (k.strip(), v.strip())
        for k, _, v in (d.partition(":") for d in (e.get("style") or "").split(";"))
        if v
    )


def _story(tmp_path, body):
    """The storyline as nested lists: ("box", style, [kids]) for a container
    of entries, ("text", str), ("mixed", [str or picture style]), ("picture",
    style). Plus the anchors as {name: (entry id, offset)}."""
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml", style_resolver=_resolver
    )
    out = tmp_path / "f.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "One", "text": "x", "blocks": blocks}],
        images={"b.jpg": WIDE},
        output_path=str(out),
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def style(e):
        s = styles.get(str(e.get("$157")), {})
        return {
            str(k): (f"{v['$307']}{v['$306']}" if "$307" in v else str(v))
            if hasattr(v, "get")
            else str(v)
            for k, v in s.items()
            if str(k) != "$173"
        }

    def node(e):
        e = getattr(e, "value", e)
        if e.get("$145"):
            ref = e["$145"]
            return ("text", str(content[str(ref["name"])][int(ref["$403"])]))
        kids = [getattr(k, "value", k) for k in e.get("$146") or ()]
        if kids and all(hasattr(k, "get") and k.get("$159") for k in kids):
            return ("box", str(e["$159"]), style(e), [node(k) for k in kids])
        if kids:
            return (
                "mixed",
                [str(k) if not hasattr(k, "get") else ("pic", style(k)) for k in kids],
            )
        return ("picture", style(e))

    story = [node(e) for st in by_type(frags, "$259") for e in val(st)["$146"]]
    return story, frags


def test_a_float_among_words_is_written_in_them(tmp_path):
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p>M2 words <img src='b.jpg' alt='a' style='float:left'/>"
        " more inline.</p><p>After.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    kind, ftype, wstyle, kids = wrapper
    # A plain container, as Previewer's: $269, the paragraph's space on it.
    assert ftype == "$269"
    assert set(wstyle) <= {"$47", "$49"}
    ((_m, parts),) = kids
    assert parts[0] == "M2 words "
    assert parts[1][0] == "pic"
    pic = parts[1][1]
    # Floated left, 300 px of a 512 px column, unsized: no $546, no height.
    assert pic["$140"] == "$59"
    assert pic["$56"] == "58.5938$314"
    assert "$546" not in pic and "$57" not in pic and "$65" not in pic
    # The space after a floated picture is dropped, as Previewer drops it.
    assert parts[2:] == ["more inline."]


@pytest.mark.parametrize(
    ("img", "expected"),
    [
        ("style='float:right;width:20%'", {"$140": "$61", "$56": "20$314"}),
        ("width='100' style='float:left'", {"$140": "$59", "$56": "19.5312$314"}),
        (
            "style='float:left;width:3em'",
            {"$140": "$59", "$56": "9.375$314", "$65": "100$314"},
        ),
    ],
    ids=["percent", "attribute", "em"],
)
def test_a_sized_float_among_words(tmp_path, img, expected):
    story, _ = _story(
        tmp_path, f"<h1>One</h1><p>F words <img src='b.jpg' alt='a' {img}/> more.</p>"
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    ((_m, parts),) = wrapper[3]
    pic = parts[1][1]
    assert {k: pic.get(k) for k in expected} == expected
    assert pic["$546"] == "$377"


def test_a_float_first_is_an_entry_in_the_container(tmp_path):
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p><img src='b.jpg' alt='a' style='float:left;width:20%'/>"
        "F1 starts with a float.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    assert [k[0] for k in wrapper[3]] == ["picture", "text"]
    assert wrapper[3][0][1]["$140"] == "$59"
    assert wrapper[3][1][1] == "F1 starts with a float."


def test_the_paragraphs_space_goes_on_the_container(tmp_path):
    story, frags = _story(
        tmp_path,
        "<h1>One</h1><p style='margin-top:2em;margin-bottom:1em'>Q words "
        "<img src='b.jpg' alt='a' style='float:left'/> more.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    assert wrapper[2] == {"$47": "1.66667$310", "$49": "0.833333$310"}
    # None on the entry inside.
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            e = getattr(e, "value", e)
            if str(e.get("$159")) == "$269" and e.get("$146"):
                for kid in e["$146"]:
                    kid = getattr(kid, "value", kid)
                    if hasattr(kid, "get") and kid.get("$146"):
                        own = styles.get(str(kid.get("$157")), {})
                        assert IS_47 not in {str(k) for k in own}


IS_47 = "$47"


def test_an_id_after_a_float_lands_on_its_word(tmp_path):
    """The dropped space moves the offsets after the picture: a link to the
    id on "more" lands on its first letter."""
    from tests.unit.test_link_landings import _landings

    _story(
        tmp_path,
        "<h1>One</h1><p>Ab <img src='b.jpg' alt='a' style='float:left'/> "
        "<span id='m'>more</span> words.</p><p><a href='c.xhtml#m'>To more</a></p>",
    )
    assert _landings(tmp_path / "f.kfx")["To more"] == "more words."


def test_a_picture_alone_still_floats_as_an_entry(tmp_path):
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p><img src='b.jpg' alt='a' style='float:left'/></p><p>Next.</p>",
    )
    assert not [n for n in story if n[0] == "box"]
    assert any(n[0] == "picture" and n[1].get("$140") == "$59" for n in story)


def test_a_cell_keeps_its_pictures_as_before(tmp_path):
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><table><tr><td>C words <img src='b.jpg' alt='a' "
        "style='float:left'/> more.</td><td>x</td></tr></table>",
    )
    assert not any(n[0] == "box" and n[1] == "$269" and n[2] for n in story)
