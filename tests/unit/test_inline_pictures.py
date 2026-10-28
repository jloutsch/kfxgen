"""A picture inside a paragraph's words stays in the paragraph (#529), as
Kindle Previewer 4.0.3 writes it: one entry whose content is the text and
the picture together, the picture sized at its pixels / 16 in em, and
counted as one character by the paragraph's runs. kfxgen cut the paragraph
at the picture into three entries and sized the picture as one alone."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import decodable_jpeg_of
from tests._kfx_introspect import by_type, load_fragments, val

pytestmark = pytest.mark.unit

SIGN = decodable_jpeg_of(48, 48)
WIDE = decodable_jpeg_of(300, 220)


def _book(tmp_path, body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        base_href="c.xhtml",
        # The style attribute as calibre's Stylizer folds it in.
        style_resolver=lambda e: dict(
            (k.strip(), v.strip())
            for k, _, v in (d.partition(":") for d in (e.get("style") or "").split(";"))
            if v
        ),
    )
    out = tmp_path / "i.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "One", "text": "x", "blocks": blocks}],
        images={"s.jpg": SIGN, "b.jpg": WIDE},
        output_path=str(out),
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = []
    for st in by_type(frags, "$259"):
        for e in val(st)["$146"]:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            if e.get("$145"):
                ref = e["$145"]
                entries.append(
                    ("text", str(content[str(ref["name"])][int(ref["$403"])]))
                )
            elif e.get("$146") and str(e.get("$159")) == "$269":
                parts = []
                for part in e["$146"]:
                    part = getattr(part, "value", part)
                    if hasattr(part, "get"):
                        st_ = styles[str(part["$157"])]
                        cap = st_.get("$65")
                        parts.append(
                            (
                                str(part["$175"]),
                                str(st_["$56"]["$307"]) + str(st_["$56"]["$306"]),
                                str(st_["$57"]["$307"]) + str(st_["$57"]["$306"]),
                            )
                            + ((str(cap["$307"]) + str(cap["$306"]),) if cap else ())
                        )
                    else:
                        parts.append(str(part))
                entries.append(("mixed", parts, e))
            elif e.get("$175") is not None:
                entries.append(("picture", str(e["$175"])))
    return entries


def _mixed(entries):
    return [e for e in entries if e[0] == "mixed"]


@pytest.mark.parametrize(
    ("body", "parts"),
    [
        (
            "<p>A sign <img src='s.jpg' alt='x'/> in a line.</p>",
            ["A sign ", ("img_1", "3$308", "3$308"), " in a line."],
        ),
        (
            "<p><img src='s.jpg' alt='x'/> starts with it.</p>",
            [("img_1", "3$308", "3$308"), " starts with it."],
        ),
        (
            "<p>Ends with it <img src='s.jpg' alt='x'/></p>",
            ["Ends with it ", ("img_1", "3$308", "3$308")],
        ),
        (
            "<p>Two <img src='s.jpg' alt='x'/> and <img src='b.jpg' alt='y'/> here.</p>",
            [
                "Two ",
                ("img_1", "3$308", "3$308"),
                " and ",
                ("img_0", "18.75$308", "13.75$308"),
                " here.",
            ],
        ),
    ],
    ids=["middle", "start", "end", "two"],
)
def test_a_picture_in_a_paragraphs_words_stays_in_it(tmp_path, body, parts):
    entries = _book(tmp_path, f"<h1>One</h1>{body}")
    (mixed,) = _mixed(entries)
    assert mixed[1] == parts
    assert not [e for e in entries if e[0] == "picture"]


def test_a_link_over_the_picture_counts_it_as_one_character(tmp_path):
    """Previewer's run for 'linked <picture> picture' is 7 + 1 + 8 = 16 long."""
    entries = _book(
        tmp_path,
        "<h1>One</h1><p>A <a href='#t'>linked <img src='s.jpg' alt='x'/> picture</a>"
        " here.</p><p id='t'>Target.</p>",
    )
    (mixed,) = _mixed(entries)
    runs = [(int(r["$143"]), int(r["$144"])) for r in mixed[2].get("$142") or []]
    assert (2, 16) in runs


def test_a_picture_alone_in_its_paragraph_is_unchanged(tmp_path):
    entries = _book(
        tmp_path, "<h1>One</h1><p><img src='b.jpg' alt='y'/></p><p>After.</p>"
    )
    assert not _mixed(entries)
    assert ("picture", "img_0") in entries


@pytest.mark.parametrize(
    ("img", "size"),
    [
        ("style='width:20%'", ("6.4$308", "4.69333$308", None)),
        ("style='width:2em'", ("2$308", "1.46667$308", "100$314")),
        ("style='width:100px'", ("3.75$308", "2.75$308", None)),
        ("width='50'", ("1.875$308", "1.375$308", None)),
        # A height alone: kept, the width following the shape (QA on #564).
        ("style='height:2em'", ("2.72727$308", "2$308", None)),
        ("height='30'", ("1.53409$308", "1.125$308", None)),
    ],
    ids=["percent", "em", "px", "attribute", "height-em", "height-attribute"],
)
def test_a_width_the_markup_gives_is_kept_as_previewer_converts_it(tmp_path, img, size):
    """Kindle Previewer 4.0.3 (probed): a % of its 512 px column, em as
    written with a 100% cap, px at 0.45pt with 1em = 12pt; the height
    follows the picture's shape (300 x 220)."""
    entries = _book(
        tmp_path, f"<h1>One</h1><p>Q <img src='b.jpg' {img} alt='x'/> after.</p>"
    )
    (mixed,) = _mixed(entries)
    assert mixed[1][1] == ("img_0", *(v for v in size if v))


def test_a_picture_alone_on_its_line_is_cut_out_as_before(tmp_path):
    """`<img/><br/>caption`: Kindle Previewer 4.0.3 writes the picture as an
    entry of its own, then the text (probed)."""
    entries = _book(
        tmp_path, "<h1>One</h1><div><img src='b.jpg' alt='y'/><br/>A caption.</div>"
    )
    assert not _mixed(entries)
    assert ("picture", "img_0") in entries


def test_the_paragraph_resumes_one_character_after_each_picture(tmp_path):
    """The position map ($265): each picture takes one offset, and the
    paragraph's next record starts right after it, at the next character,
    as for a drawn equation's own positions (#235)."""
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>One</h1>'
        "<p>Two <img src='s.jpg' alt='x'/> and <img src='b.jpg' alt='y'/> here.</p>"
        "<p>Next.</p></body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )
    out = tmp_path / "p.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "One", "text": "x", "blocks": blocks}],
        images={"s.jpg": SIGN, "b.jpg": WIDE},
        output_path=str(out),
    )
    frags = load_fragments(out)
    (mixed,) = [
        getattr(e, "value", e)
        for st in by_type(frags, "$259")
        for e in val(st)["$146"]
        if getattr(e, "value", e).get("$146")
    ]
    pictures = [int(p["$155"]) for p in mixed["$146"] if hasattr(p, "get")]
    records = [
        (int(r["$184"]), int(r["$185"]), int(r.get("$143") or 0))
        for f in by_type(frags, "$265")
        for r in val(f)
    ]
    para = int(mixed["$155"])
    start = next(o for o, p, k in records if p == para and k == 0)
    # "Two " is 4 characters, " and " 5: pictures at 4 and 10.
    assert [o - start for o, p, _k in records if p in pictures] == [4, 10]
    assert (start + 5, para, 5) in records
    assert (start + 11, para, 11) in records


def _picture_style(tmp_path, img):
    """{property: value} of the inline picture's style, `img` its markup."""
    _book(tmp_path, f"<h1>One</h1><p>Q <img src='b.jpg' {img} alt='x'/> after.</p>")
    frags = load_fragments(tmp_path / "i.kfx")
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (style,) = [
        styles[str(part["$157"])]
        for st in by_type(frags, "$259")
        for e in val(st)["$146"]
        for part in (getattr(e, "value", e).get("$146") or ())
        if hasattr(part, "get") and str(part.get("$159")) == "$271"
    ]

    def show(v):
        return f"{v['$307']}{v['$306']}" if hasattr(v, "get") else str(v)

    return {str(k): show(v) for k, v in style.items() if str(k) != "$173"}


@pytest.mark.parametrize(
    ("img", "size"),
    [
        ("style='width:2em;height:1em'", {"$56": "2$308", "$57": "1$308"}),
        ("width='40' height='40'", {"$56": "1.5$308", "$57": "1.5$308"}),
    ],
    ids=["em", "attributes"],
)
def test_a_width_and_height_given_are_both_kept(tmp_path, img, size):
    """Kindle Previewer 4.0.3 stretches the picture to both, its shape aside
    (#565): 2 x 1em, not 2 x 1.467em."""
    style = _picture_style(tmp_path, img)
    assert {k: style[k] for k in size} == size


@pytest.mark.parametrize(
    ("img", "marked"),
    [
        ("", False),
        ("style='width:2em'", True),
        ("height='30'", True),
        ("width='40' height='40'", True),
        ("style='vertical-align:middle'", False),
    ],
    ids=["own-size", "width", "height", "both", "align-only"],
)
def test_a_picture_the_markup_sizes_is_marked(tmp_path, img, marked):
    """Previewer writes `$546: $377` on a picture the markup sizes, and on
    no other (#565, probed)."""
    assert (_picture_style(tmp_path, img).get("$546") == "$377") is marked


@pytest.mark.parametrize(
    ("value", "symbol"),
    [
        ("middle", "$320"),
        ("top", "$58"),
        ("bottom", "$60"),
        ("text-top", "$447"),
        ("text-bottom", "$449"),
        ("baseline", "$350"),
        ("super", "$370"),
        ("sub", "$371"),
    ],
)
def test_vertical_align_is_kept(tmp_path, value, symbol):
    """As Previewer writes it, `$44` (#565, probed with all eight)."""
    style = _picture_style(tmp_path, f"style='vertical-align:{value}'")
    assert style.get("$44") == symbol


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # #565 item 4, Kindle Previewer 4.0.3 (probed): a line of only a
        # picture ends the entry after it; the picture stays in the words
        # before it.
        (
            "<p>M3 start <img src='s.jpg' alt='a'/> mid<br/>"
            "<img src='s.jpg' alt='b'/><br/>end.</p>",
            [
                ["M3 start ", "pic", " mid\n", "pic"],
                "end.",
            ],
        ),
        # The first line alone: a picture entry, then the words, as #529.
        (
            "<p><img src='b.jpg' alt='a'/><br/>caption <img src='s.jpg' alt='b'/>"
            " inline.</p>",
            ["picture", ["caption ", "pic", " inline."]],
        ),
    ],
    ids=["after-words", "first-line"],
)
def test_a_line_of_only_a_picture_ends_the_entry(tmp_path, body, expected):
    entries = _book(tmp_path, f"<h1>One</h1>{body}<p>After.</p>")[1:-1]
    got = [
        [p if isinstance(p, str) else "pic" for p in e[1]]
        if e[0] == "mixed"
        else (e[1] if e[0] == "text" else "picture")
        for e in entries
    ]
    assert got == expected


def test_a_run_after_the_cut_stays_on_its_words(tmp_path):
    """The second entry's italic run is moved by the cut, not left at the
    paragraph's offsets (#565)."""
    _book(
        tmp_path,
        "<h1>One</h1><p>Start <img src='s.jpg' alt='a'/> mid<br/>"
        "<img src='s.jpg' alt='b'/><br/>an <i>end</i>.</p>",
    )
    frags = load_fragments(tmp_path / "i.kfx")
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    (runs,) = [
        [
            str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])[
                int(r["$143"]) : int(r["$143"]) + int(r["$144"])
            ]
            for r in e.get("$142") or ()
        ]
        for st in by_type(frags, "$259")
        for e in (getattr(x, "value", x) for x in val(st)["$146"])
        if e.get("$145")
        and str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])]) == "an end."
    ]
    assert runs == ["end"]


def test_a_link_to_the_words_after_the_cut_lands_on_them(tmp_path):
    """An id in the entry after the cut keeps its place in it (#565)."""
    from tests.unit.test_link_landings import _landings

    _book(
        tmp_path,
        "<h1>One</h1><p>Start <img src='s.jpg' alt='a'/> mid<br/>"
        "<img src='s.jpg' alt='b'/><br/>an <span id='e'>end</span>.</p>"
        "<p><a href='c.xhtml#e'>To end</a></p>",
    )
    # From the word "end" on, not the entry's start.
    assert _landings(tmp_path / "i.kfx")["To end"] == "end."


def test_an_id_at_the_paragraphs_end_lands_there_after_the_cut(tmp_path):
    """An id after the last word (#487) is at the paragraph's end, which no
    piece held, and fell back to the first piece's start (QA on #590). It
    lands on the last character, as on main and in Previewer's file."""
    from tests.unit.test_link_landings import _landings

    _book(
        tmp_path,
        "<h1>One</h1><p>E1 words<br/><img src='s.jpg' alt='a'/><br/>"
        "caption ends here<a id='e1end'></a></p>"
        "<p><a href='c.xhtml#e1end'>To end</a></p>",
    )
    assert _landings(tmp_path / "i.kfx")["To end"] == "e"


def test_preformatted_text_with_a_picture_line_is_not_cut(tmp_path):
    """Previewer keeps `<pre>` whole, a picture line and all (QA on #590)."""
    entries = _book(
        tmp_path,
        "<h1>One</h1><pre>P1 code line\n<img src='s.jpg' alt='a'/>\nP1 more code</pre>",
    )
    (mixed,) = _mixed(entries)
    assert [p if isinstance(p, str) else "pic" for p in mixed[1]] == [
        "P1 code line\n",
        "pic",
        "\nP1 more code",
    ]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "<p>W1 words<br/><br/><img src='b.jpg' alt='a'/></p>",
            ["W1 words", "picture"],
        ),
        (
            "<h1>W2 heading<br/><br/><img src='b.jpg' alt='a'/></h1>",
            ["W2 heading", "picture"],
        ),
        # One <br/>: the picture stays in the words, as before.
        (
            "<p>W3 words<br/><img src='s.jpg' alt='a'/></p>",
            [["W3 words\n", "pic"]],
        ),
    ],
    ids=["paragraph", "heading", "one-break"],
)
def test_a_picture_line_after_an_empty_line_is_its_own_entry(tmp_path, body, expected):
    """Previewer cuts before a line of only a picture when an empty line comes
    before it: the words, then the picture as an entry (QA on #590, 2 library
    books)."""
    entries = _book(tmp_path, f"<h1>One</h1>{body}<p>After.</p>")[1:-1]
    got = [
        [p if isinstance(p, str) else "pic" for p in e[1]]
        if e[0] == "mixed"
        else (e[1] if e[0] == "text" else "picture")
        for e in entries
    ]
    assert got == expected


def test_an_id_just_before_a_picture_after_an_empty_line_lands_on_it(tmp_path):
    """Previewer lands a link to an id just before the picture on the
    picture's own entry; kfxgen put it at the text piece's end, past its
    stripped line breaks, and it fell back to the text's start (#592)."""
    from tests.unit.test_link_landings import _landings

    _book(
        tmp_path,
        "<h1>One</h1><p>W words<br/><br/><a id='x'></a>"
        "<img src='b.jpg' alt='a'/></p><p><a href='c.xhtml#x'>To x</a></p>",
    )
    # The picture's entry, which holds no text.
    assert _landings(tmp_path / "i.kfx")["To x"] == ""
