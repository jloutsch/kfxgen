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


def _walk(nodes):
    for n in nodes:
        yield n
        if n[0] == "box":
            yield from _walk(n[3])


def _texts(node):
    if node[0] == "text":
        return node[1]
    if node[0] == "mixed":
        return "".join(p for p in node[1] if isinstance(p, str))
    if node[0] == "box":
        return "".join(_texts(k) for k in node[3])
    return ""


def test_a_linked_float_is_not_floated(tmp_path):
    """The Voyage drops a floated picture's link (#378), so a linked one is
    written unfloated, its link kept, as on main, where Previewer floats
    it (QA on #697). Alone, it doesn't make the paragraph a container."""
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p><a href='c.xhtml'><img src='b.jpg' alt='a' "
        "style='float:left;width:20%'/></a>L1 words after.</p>",
    )
    # No float container: the only $269 is the link's own (#355).
    assert not [n for n in story if n[0] == "box" and set(n[2]) <= {"$47", "$49"}]
    pictures = [n for n in _walk(story) if n[0] == "picture"] + [
        p
        for n in _walk(story)
        if n[0] == "mixed"
        for p in n[1]
        if not isinstance(p, str)
    ]
    assert pictures and all("$140" not in p[1] for p in pictures)


def test_a_linked_picture_among_floats_stays_unfloated(tmp_path):
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p>K words <img src='b.jpg' alt='f' style='float:left'/> "
        "and <a href='c.xhtml'><img src='b.jpg' alt='l' style='float:right;"
        "width:10%'/></a> linked.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    ((_m, parts),) = wrapper[3]
    floated = [p[1].get("$140") for p in parts if not isinstance(p, str)]
    assert floated == ["$59", None]


def test_the_container_takes_the_paragraphs_clear(tmp_path):
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p style='clear:both'>Q words <img src='b.jpg' alt='a' "
        "style='float:left'/> more.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    assert "$628" in wrapper[2]


def test_an_id_just_after_a_float_first_lands_on_the_words(tmp_path):
    """An id at the cut is the words', not the picture's, as Previewer puts
    it (QA on #697)."""
    from tests.unit.test_link_landings import _landings

    _story(
        tmp_path,
        "<h1>One</h1><p><img src='b.jpg' alt='a' style='float:left'/>"
        "<span id='t'>Text</span> here.</p><p><a href='c.xhtml#t'>To text</a></p>",
    )
    assert _landings(tmp_path / "f.kfx")["To text"] == "Text here."


def test_preformatted_text_is_not_wrapped(tmp_path):
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><pre>P words <img src='b.jpg' alt='a' style='float:left'/>"
        " more.</pre>",
    )
    assert not [n for n in story if n[0] == "box"]


def test_a_height_alone_gives_a_float_its_width(tmp_path):
    """Previewer: 3em high, 300 x 220 px: 65.45 px of 512, 12.784% (QA on
    #697)."""
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p>H words <img src='b.jpg' alt='a' "
        "style='float:left;height:3em'/> more.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    ((_m, parts),) = wrapper[3]
    assert parts[1][1]["$56"] == "12.7841$314"


@pytest.mark.parametrize(
    ("height", "expected"),
    [
        ("40px", {"$56": "6.39205$314"}),
        ("30pt", {"$56": "10.6534$314"}),
        ("50%", {"$57": "50$314"}),
    ],
    ids=["px", "pt", "percent"],
)
def test_a_floats_height_as_previewer_reads_it(tmp_path, height, expected):
    """Previewer (QA on #697): px at 0.45pt as #541 measured (40px high is
    6.392%), pt with 1em = 12pt, and a % height kept as a %, no width."""
    story, _ = _story(
        tmp_path,
        f"<h1>One</h1><p>H words <img src='b.jpg' alt='a' "
        f"style='float:left;height:{height}'/> more.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box"]
    ((_m, parts),) = wrapper[3]
    pic = parts[1][1]
    assert {k: pic.get(k) for k in ("$56", "$57")} == {
        "$56": expected.get("$56"),
        "$57": expected.get("$57"),
    }


def test_a_linked_float_first_keeps_the_next_float_in_the_words(tmp_path):
    """A linked float first ends the run of floats taken out ahead: the
    float after it stays in the one entry (QA on #697: dropping the link
    check gave the picture an entry of its own, 1.833lh above it)."""
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><p><a href='c.xhtml'><img src='b.jpg' alt='l' "
        "style='float:left;width:10%'/></a><img src='b.jpg' alt='f' "
        "style='float:right;width:10%'/>W words after.</p>",
    )
    (wrapper,) = [n for n in story if n[0] == "box" and set(n[2]) <= {"$47", "$49"}]
    assert [k[0] for k in wrapper[3]] == ["mixed"]


@pytest.mark.parametrize(
    "item",
    [
        "L1 item <img src='b.jpg' alt='a' style='float:left;width:20%'/> words.",
        "<img src='b.jpg' alt='a' style='float:right;width:20%'/>L2 lead item.",
    ],
    ids=["middle", "first"],
)
def test_a_list_items_float_is_unfloated_in_its_words(tmp_path, item):
    """Previewer floats nothing in a list item (#699): the picture stays in
    the item's words, unfloated, and the item isn't wrapped. kfxgen writes
    the bullet as text, so a float first stays after it in the words."""
    story, _ = _story(tmp_path, f"<h1>One</h1><ul><li>{item}</li></ul>")
    assert not [
        n for n in _walk(story) if n[0] == "box" and set(n[2]) <= {"$47", "$49"}
    ]
    (mixed,) = [n for n in _walk(story) if n[0] == "mixed"]
    (pic,) = [p for p in mixed[1] if not isinstance(p, str)]
    assert "$140" not in pic[1] and pic[1]["$56"] == "6.4$308"


def test_a_float_first_in_a_list_items_second_paragraph_is_unfloated(tmp_path):
    """A list item's later paragraph has no bullet before its float: the
    picture is an entry of its own, not floated (#699)."""
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><ul><li><p>L3 first.</p><p><img src='b.jpg' alt='a' "
        "style='float:left;width:20%'/>L3 second paragraph.</p></li></ul>",
    )
    pictures = [n for n in _walk(story) if n[0] == "picture"]
    assert pictures and all("$140" not in p[1] for p in pictures)
    assert not [
        n for n in _walk(story) if n[0] == "box" and set(n[2]) <= {"$47", "$49"}
    ]


def test_a_lead_float_in_a_spaced_list_paragraph_keeps_the_space(tmp_path):
    """The paragraph's own margin goes on a container round the picture and
    the words, as Previewer writes it; the words take none of their own (QA
    on #701: the space was lost)."""
    story, frags = _story(
        tmp_path,
        "<h1>One</h1><ul><li><p>L4 first.</p><p style='margin-top:2em'>"
        "<img src='b.jpg' alt='a' style='float:left;width:20%'/>L4 second.</p>"
        "</li></ul>",
    )
    (box,) = [n for n in _walk(story) if n[0] == "box" and "$47" in n[2]]
    assert box[2]["$47"] == "1.66667$310"
    assert [k[0] for k in box[3]] == ["picture", "text"]
    assert "$140" not in box[3][0][1]
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            e = getattr(e, "value", e)
            for kid in e.get("$146") or ():
                kid = getattr(kid, "value", kid)
                if hasattr(kid, "get") and kid.get("$145"):
                    assert "$47" not in {str(k) for k in styles[str(kid["$157"])]}


def test_a_picture_alone_in_a_list_item_is_unfloated(tmp_path):
    """QA on #701: Previewer floats nothing in a list item, a lone picture
    included."""
    story, _ = _story(
        tmp_path,
        "<h1>One</h1><ul><li><img src='b.jpg' alt='a' style='float:left;"
        "width:20%'/></li><li>Next item.</li></ul>",
    )
    pictures = [n for n in _walk(story) if n[0] == "picture"]
    assert pictures and all("$140" not in p[1] for p in pictures)
