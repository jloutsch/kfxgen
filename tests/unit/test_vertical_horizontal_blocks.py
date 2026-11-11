"""A horizontal-tb block inside vertical-rl text (#247).

Kindle Previewer 4.0.3 writes it in a container of its own (probe P247h,
decoded): a `$269` whose style has `$560: $557` (horizontal-tb), `$56`
100%, `$478` and `$784` (fit flags kfxlib reads as fit_width and
fit_tight), around the block's paragraphs, with the block's margins on it
and kept where the book puts them, not moved as vertical text's are. A
<div> holding two paragraphs is one container around both (H03); a
bordered one is a `$270` (H11); kfxgen writes a filled one the same way. kfxgen's own 0.5% left margin is not
written in it (none of H01-H11 has one).

`mark_vertical_margins` marks the first horizontal-tb element of each
branch of a vertical-rl file and leaves it and all inside it without the
vertical marks, so their margins are physical; the walker wraps its blocks
in a box marked horizontal; the generator writes the box as above and
stops moving margins while it is open.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

XHTML = "http://www.w3.org/1999/xhtml"
HZ = "horizontal-tb"


class _Stylizer:
    """calibre's pre-flatten Stylizer as far as `mark_vertical_margins` reads
    it: no rules, and an element's writing mode by its tag or class."""

    rules = ()

    def __init__(self, modes):
        self._modes = modes

    def style(self, elem):
        keys = [elem.tag.split("}")[-1], *(elem.get("class") or "").split()]
        mode = next((self._modes[k] for k in keys if k in self._modes), None)
        return {"writing-mode": mode} if mode else {}


BODY = (
    '<p id="v">V</p><p id="h" class="hz">H01 <span id="s">s</span></p>'
    '<div id="d" class="hz"><p id="d1">H03</p><p id="d2" class="hz">H04</p></div>'
    '<p id="i">H10 <span id="is" class="hz">ABC</span></p>'
)


def _marks(modes):
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{BODY}</body></html>')
    _conv.mark_vertical_margins(root, _Stylizer(modes), select=lambda t: [])
    return {
        e.get("id"): (e.get(_conv._HORIZONTAL), e.get(_conv._V_AUTHOR) is not None)
        for e in root.iter()
        if isinstance(e.tag, str) and e.get("id")
    }


@pytest.mark.unit
def test_the_first_horizontal_element_of_a_branch_is_marked_and_nothing_in_it_is_vertical():
    assert _marks({"html": "vertical-rl", "hz": HZ}) == {
        "v": (None, True),
        "h": ("1", False),
        "s": (None, False),
        "d": ("1", False),
        "d1": (None, False),
        # Horizontal inside horizontal: the <div> is the block.
        "d2": (None, False),
        "i": (None, True),
        "is": ("1", False),
    }


@pytest.mark.unit
def test_a_horizontal_file_marks_nothing():
    assert set(_marks({"hz": HZ}).values()) == {(None, False)}


@pytest.mark.unit
def test_a_vertical_element_inside_a_horizontal_one_stays_horizontal_free():
    # vertical-rl again inside the horizontal <div>: vertical text, marked so.
    marks = _marks({"html": "vertical-rl", "hz": HZ, "p": "vertical-rl"})
    assert marks["d1"] == (None, True)


# --- The walker -----------------------------------------------------------------

CSS = {
    "hz": {"writing-mode": HZ},
    "bx": {"writing-mode": HZ, "background-color": "#eeeeee"},
    "mt": {"margin-top": "2em"},
    # Padding on the line's start and the top, physical in horizontal text.
    "pad": {"padding-top": "1em", "padding-left": "2em"},
    # A filled box: the fake stylesheet reads no border.
    "bxp": {"background-color": "#eeeeee", "padding-top": "1em", "padding-left": "2em"},
}


def _blocks(body):
    root = etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    for e in root.iter():
        if isinstance(e.tag, str):
            e.set(_conv._V_AUTHOR, "")
            e.set(_conv._V_UA, "margin-top:1em;margin-bottom:1em")
    for e in root.iter():
        if isinstance(e.tag, str) and (e.get("data-h") is not None):
            for x in e.iter():
                x.attrib.pop(_conv._V_AUTHOR, None)
                x.attrib.pop(_conv._V_UA, None)
            e.set(_conv._HORIZONTAL, "1")
    return _conv.extract_blocks_from_html(
        root, style_resolver=_resolver(CSS), native_tables=True, base_href="c.xhtml"
    )


def _shape(blocks):
    return [
        ("box", bool(b["box"].get("horizontal")), _shape(b["blocks"]))
        if b.get("type") == "box"
        else b["text"]
        for b in blocks
        if b.get("type") != "space"
    ]


@pytest.mark.unit
def test_a_horizontal_paragraph_is_a_horizontal_box_around_it():
    assert _shape(_blocks('<p>V</p><p data-h="">H01</p><p>V2</p>')) == [
        "V",
        ("box", True, ["H01"]),
        "V2",
    ]


@pytest.mark.unit
def test_a_horizontal_div_is_one_box_around_its_paragraphs():
    assert _shape(_blocks('<div data-h=""><p>H03</p><p>H04</p></div>')) == [
        ("box", True, ["H03", "H04"])
    ]


@pytest.mark.unit
def test_a_horizontal_heading_is_a_box_too():
    # H08: a heading is no box tag, but Previewer wraps it.
    assert _shape(_blocks('<h2 data-h="">H08</h2>')) == [("box", True, ["H08"])]


@pytest.mark.unit
def test_an_unmarked_paragraph_is_no_box():
    assert _shape(_blocks('<p class="hz">H01</p>')) == ["H01"]


# --- The generator ----------------------------------------------------------------

_SIDES = ("$47", "$48", "$49", "$50")


def _storyline(tmp_path, body, title=""):
    blocks = _blocks(body)
    for b in blocks:
        b["writing_mode"] = "vertical-rl"
    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": title,
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": blocks,
            }
        ],
        output_path=str(out),
        rtl=True,
        language="ja",
        writing_mode="vertical-rl",
    )
    frags = load_fragments(out)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (story,) = by_type(frags, "$259")

    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def entry(e):
        st = styles.get(str(e.get("$157")), {})
        ref = e.get("$145")
        return {
            "type": str(e["$159"]),
            "text": str(texts[str(ref["name"])][ref["$403"]]) if ref else None,
            "style": {str(k): v for k, v in st.items() if str(k) != "$173"},
            "kids": [entry(k) for k in e.get("$146") or () if hasattr(k, "get")],
        }

    return [entry(e) for e in val(story)["$146"]]


def _num(v):
    return float(v["$307"])


@pytest.mark.unit
def test_the_box_is_a_horizontal_container_at_the_pages_width(tmp_path):
    (box,) = _storyline(tmp_path, '<p data-h="">H01</p>')
    assert box["type"] == "$269"
    st = box["style"]
    assert str(st["$560"]) == "$557"
    assert _num(st["$56"]) == 100.0 and str(st["$56"]["$306"]) == "$314"
    assert st["$478"] is True and st["$784"] is True
    assert [k["type"] for k in box["kids"]] == ["$269"]


@pytest.mark.unit
def test_margins_in_it_stay_where_the_book_puts_them(tmp_path):
    # H05: margin-top 2em is the box's top, in line heights; a paragraph
    # beside it in vertical text has its space moved right and left.
    entries = _storyline(
        tmp_path,
        '<p class="mt">V</p><div data-h="" class="mt"><p class="mt">H</p></div>',
    )
    vertical, box = entries[-2], entries[-1]
    assert "$47" not in vertical["style"] and "$50" in vertical["style"]
    assert _num(box["style"]["$47"]) == pytest.approx(2 / 1.2, abs=1e-5)
    (inner,) = box["kids"]
    sides = {k for k in _SIDES if k in inner["style"]}
    assert "$50" not in sides and "$48" not in sides


@pytest.mark.unit
def test_kfxgens_own_left_margin_is_not_written_in_it(tmp_path):
    (box,) = _storyline(tmp_path, '<p data-h="">H01</p>')
    assert "$48" not in box["kids"][0]["style"]


@pytest.mark.unit
def test_a_filled_one_is_a_box_container(tmp_path):
    (box,) = _storyline(tmp_path, '<p data-h="" class="bx">H11</p>')
    assert box["type"] == "$270"
    assert str(box["style"]["$560"]) == "$557"


@pytest.mark.unit
def test_vertical_text_after_it_is_moved_again(tmp_path):
    entries = _storyline(tmp_path, '<p data-h="">H</p><p class="mt">V</p>')
    assert "$50" in entries[-1]["style"] and "$47" not in entries[-1]["style"]


def _padding(style):
    return {k for k in ("$52", "$53", "$54", "$55") if k in style}


def _unmoved(style):
    """Whether `pad`'s 1em top and 2em line start are where the book puts
    them: the top in line heights, the left a share of the page. Moved for
    vertical text, the top would be the right and the left the top."""
    top, left = style.get("$52"), style.get("$53")
    return (
        top is not None
        and left is not None
        and (str(top["$306"]), str(left["$306"])) == ("$310", "$314")
        and _num(top) == pytest.approx(1 / 1.2, abs=1e-5)
    )


def _walk_entries(entries):
    for e in entries:
        yield e
        yield from _walk_entries(e["kids"])


@pytest.mark.unit
def test_a_padded_one_with_no_border_or_fill_is_a_plain_container(tmp_path):
    # QA's P749d: Previewer writes a $269 for a padded horizontal block.
    (box,) = _storyline(tmp_path, '<p data-h="" class="pad">P01</p>')
    assert box["type"] == "$269"
    assert _padding(box["style"]) == {"$52", "$53"}


@pytest.mark.unit
def test_a_bordered_box_inside_it_keeps_its_padding_where_the_book_puts_it(
    tmp_path,
):
    # QA's P749 Q07: a box nested in a horizontal block is horizontal too.
    (outer,) = _storyline(
        tmp_path, '<div data-h=""><p>Q07</p><div class="bxp"><p>in</p></div></div>'
    )
    (inner,) = [k for k in outer["kids"] if k["type"] == "$270"]
    assert _unmoved(inner["style"]) and "$55" not in inner["style"]


def _cells(entries):
    return [e for e in _walk_entries(entries) if e["text"] in ("a", "b", "c", "d")]


@pytest.mark.unit
def test_a_table_inside_it_keeps_every_cells_padding_where_the_book_puts_it(
    tmp_path,
):
    # QA's P749 Q01: every row, not only the first.
    entries = _storyline(
        tmp_path,
        '<div data-h=""><p>Q01</p><table>'
        '<tr><td class="pad">a</td><td class="pad">b</td></tr>'
        '<tr><td class="pad">c</td><td class="pad">d</td></tr></table></div>',
    )
    cells = _cells(entries)
    assert [c["text"] for c in cells] == ["a", "b", "c", "d"]
    assert all(_unmoved(c["style"]) for c in cells), cells


@pytest.mark.unit
def test_a_horizontal_table_is_set_horizontal_itself_with_no_container(tmp_path):
    # QA's P749 Q02: $560 $557 on the $278, its cells' padding not moved.
    entries = _storyline(
        tmp_path,
        '<table data-h=""><tr><td class="pad">a</td><td class="pad">b</td></tr>'
        '<tr><td class="pad">c</td><td class="pad">d</td></tr></table>',
    )
    (table,) = [e for e in entries if e["type"] == "$278"]
    assert str(table["style"]["$560"]) == "$557"
    assert all(_unmoved(c["style"]) for c in _cells(entries))


@pytest.mark.unit
def test_vertical_text_after_a_horizontal_table_is_moved_again(tmp_path):
    entries = _storyline(
        tmp_path,
        '<table data-h=""><tr><td>a</td></tr></table><p class="mt">V</p>',
    )
    assert "$50" in entries[-1]["style"] and "$47" not in entries[-1]["style"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "label"),
    [
        # QA's P749b T and W: the label is the heading's first words.
        ('<div data-h=""><h2>T1 第四章、横</h2><p>ta 本文。</p></div>', "T1 第四章"),
        ('<h2 data-h="">W1 第六章、横</h2><p>Wa 本文。</p>', "W1 第六章"),
        # U: a label that is none of the heading's words.
        ('<div data-h=""><h2>U1 第五章、横</h2><p>ua 本文。</p></div>', "U 章"),
    ],
)
def test_a_chapter_opening_with_a_boxed_heading_prints_it_whole_in_the_box(
    tmp_path, body, label
):
    entries = _storyline(tmp_path, body, title=label)
    heading = body.split("<h2")[1].split(">", 1)[1].split("<")[0]
    texts = [e["text"] for e in _walk_entries(entries) if e["text"]]
    assert heading in texts
    assert label not in texts
    assert entries[0]["type"] in ("$269", "$270") and entries[0]["kids"]
    assert entries[0]["kids"][0]["text"] == heading


@pytest.mark.unit
def test_a_boxed_heading_that_is_the_label_is_still_kfxgens_heading_in_the_box(
    tmp_path,
):
    # QA's P749b S: the label is the whole heading. kfxgen's heading prints
    # in the box, as for any box opening with its chapter's title (#646).
    entries = _storyline(
        tmp_path,
        '<div data-h=""><h2>S1 第三章</h2><p>sa 本文。</p></div>',
        title="S1 第三章",
    )
    heading = entries[0]["kids"][0]
    assert heading["text"] == "S1 第三章"
    assert _num(heading["style"]["$16"]) == NativeKFXGenerator.HEADING_FONT_SIZE


@pytest.mark.unit
def test_a_horizontal_books_chapter_opening_with_a_boxed_heading_prints_it_whole(
    tmp_path,
):
    # QA's P749c: the same in a horizontal book, a filled box (the fake
    # stylesheet reads no border). Main printed the label "T1 Chapter four"
    # above the box and ", boxed" in it.
    xhtml = (
        f'<html xmlns="{XHTML}"><body><div class="bx">'
        "<h2>T1 Chapter four, boxed</h2><p>ta Text.</p></div></body></html>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=_resolver({"bx": {"background-color": "#eeeeee"}}),
        base_href="c.xhtml",
    )
    out = tmp_path / "c.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "T1 Chapter four",
                "text": "\n\n".join(b["text"] for b in blocks),
                "blocks": blocks,
            }
        ],
        output_path=str(out),
    )
    frags = load_fragments(out)
    texts = [str(t) for f in by_type(frags, "$145") for t in val(f)["$146"]]
    assert "T1 Chapter four, boxed" in texts
    assert "T1 Chapter four" not in texts and ", boxed" not in texts
