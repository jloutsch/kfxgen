"""Ruby annotations: furigana and pinyin (#236).

kfxgen walked `<ruby>` as inline text, so `漢<rt>kan</rt>字<rt>ji</rt>` read
"漢kan字ji". KFX has ruby of its own, and Kindle Previewer 4.0.3 writes it
(kpr4, decoded probes on #236): the paragraph's text holds only the base,
"漢字"; an event over the base names a `$756` ruby fragment (`$757`) and an
entry in it (`$758`); the entry holds the reading, styled at 0.5em with a
normal line height; and `$585` declares `yj_ruby` 1.0. `<rp>` is dropped.
An event covers the whole base: emphasis on all of it goes on that event,
emphasis on part of it is an event of its own. Previewer reads `<rtc>` as
ordinary text, and so does kfxgen.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import FLAG_ITALIC, flag_value
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _blocks(body):
    """The body's blocks, with no style resolver: tags keep their meaning."""
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), native_tables=True, base_href="c.xhtml"
    )


def _rubies(body):
    """(text, [(start, length, reading)]) of the body's first block."""
    (block, *_rest) = _blocks(body)
    out = []
    for s, n, flags in block.get("spans") or []:
        ruby = flag_value(flags, "ruby")
        if ruby is not None:
            if out and out[-1][3] == ruby[0] and out[-1][0] + out[-1][1] == s:
                out[-1] = (out[-1][0], out[-1][1] + n, ruby[1], ruby[0])
            else:
                out.append((s, n, ruby[1], ruby[0]))
    return block["text"], [r[:3] for r in out]


# --- The converter: the base is the text, the reading a flag on it ----------


@pytest.mark.unit
def test_a_reading_per_character_is_one_ruby_each():
    assert _rubies("<p><ruby>漢<rt>kan</rt>字<rt>ji</rt></ruby></p>") == (
        "漢字",
        [(0, 1, "kan"), (1, 1, "ji")],
    )


@pytest.mark.unit
def test_one_reading_covers_its_whole_base():
    assert _rubies("<p><ruby>漢字<rt>かんじ</rt></ruby></p>") == (
        "漢字",
        [(0, 2, "かんじ")],
    )


@pytest.mark.unit
def test_rp_is_dropped():
    assert _rubies("<p><ruby>漢字<rp>(</rp><rt>かんじ</rt><rp>)</rp></ruby></p>") == (
        "漢字",
        [(0, 2, "かんじ")],
    )


@pytest.mark.unit
def test_an_rb_base_is_read():
    assert _rubies("<p><ruby><rb>東京</rb><rt>とうきょう</rt></ruby></p>") == (
        "東京",
        [(0, 2, "とうきょう")],
    )


@pytest.mark.unit
def test_text_around_a_ruby_has_no_reading():
    assert _rubies("<p>Before <ruby>北京<rt>Běijīng</rt></ruby> after.</p>") == (
        "Before 北京 after.",
        [(7, 2, "Běijīng")],
    )


@pytest.mark.unit
def test_two_rubies_with_one_reading_stay_two():
    text, rubies = _rubies(
        "<p><ruby>は<rt>ha</rt></ruby><ruby>は<rt>ha</rt></ruby></p>"
    )
    assert (text, rubies) == ("はは", [(0, 1, "ha"), (1, 1, "ha")])


@pytest.mark.unit
def test_a_reading_with_markup_is_its_text():
    assert _rubies("<p><ruby>漢字<rt><i>かん</i>じ</rt></ruby></p>") == (
        "漢字",
        [(0, 2, "かんじ")],
    )


@pytest.mark.unit
def test_an_empty_reading_leaves_the_base_plain():
    assert _rubies("<p><ruby>字<rt></rt></ruby></p>") == ("字", [])


@pytest.mark.unit
def test_rtc_is_text_as_in_previewer():
    assert _rubies("<p><ruby>漢字<rtc><rt>かんじ</rt></rtc></ruby></p>") == (
        "漢字かんじ",
        [],
    )


@pytest.mark.unit
def test_a_ruby_keeps_its_emphasis():
    (block,) = _blocks("<p><i><ruby>体<rt>たい</rt></ruby></i>と</p>")
    ((s, n, flags),) = block["spans"]
    assert (s, n) == (0, 1)
    assert flag_value(flags, "ruby")[1] == "たい"
    assert FLAG_ITALIC in flags


# --- The generator: Previewer's shapes --------------------------------------


def _book(*bodies, tmp_path):
    """A book of one chapter per body."""
    out = tmp_path / "r.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": f"Ch{i}", "text": "x", "blocks": _blocks(body)}
            for i, body in enumerate(bodies)
        ],
        output_path=str(out),
    )
    return load_fragments(out)


def _events(frags):
    """{paragraph text: its $142 events as dicts}, and the ruby fragments."""
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if e.get("$145"):
                ref = e["$145"]
                text = str(texts[str(ref["name"])][ref["$403"]])
                out[text] = [dict(ev) for ev in e.get("$142") or []]
    return out, {str(f.fid): val(f) for f in by_type(frags, "$756")}


def _readings(frags):
    """{(ruby fragment, ruby id): reading entry}."""
    _events_, rubies = _events(frags)
    return {
        (name, int(r["$758"])): r for name, frag in rubies.items() for r in frag["$146"]
    }


@pytest.mark.unit
def test_the_base_event_names_its_reading(tmp_path):
    frags = _book("<p>R2: <ruby>漢字<rt>かんじ</rt></ruby></p>", tmp_path=tmp_path)
    events, rubies = _events(frags)
    ((name, frag),) = rubies.items()
    assert str(frag["$757"]) == name
    (ev,) = events["R2: 漢字"]
    assert (ev["$143"], ev["$144"], str(ev["$757"])) == (4, 2, name)
    assert str(_readings(frags)[(name, ev["$758"])]["$145"]) == "かんじ"


@pytest.mark.unit
def test_each_character_reading_has_its_own_entry(tmp_path):
    frags = _book("<p><ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby></p>", tmp_path=tmp_path)
    events, _ = _events(frags)
    readings = _readings(frags)
    got = [
        (ev["$143"], ev["$144"], str(readings[(str(ev["$757"]), ev["$758"])]["$145"]))
        for ev in events["漢字"]
    ]
    assert got == [(0, 1, "かん"), (1, 1, "じ")]
    assert len({ev["$758"] for ev in events["漢字"]}) == 2


@pytest.mark.unit
def test_a_reading_is_half_size_at_normal_line_height(tmp_path):
    frags = _book("<p><ruby>漢字<rt>かんじ</rt></ruby></p>", tmp_path=tmp_path)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    (entry,) = _readings(frags).values()
    style = styles[str(entry["$157"])]
    assert (float(style["$16"]["$307"]), str(style["$16"]["$306"])) == (0.5, "$308")
    assert str(style["$42"]) == "$383"
    assert str(entry["$159"]) == "$269"


@pytest.mark.unit
def test_reading_ids_are_unique_and_not_positions(tmp_path):
    frags = _book(
        "<p><ruby>明日<rt>あした</rt></ruby>は<ruby>晴<rt>は</rt></ruby>れ</p>"
        "<p><ruby>漢字<rt>かんじ</rt></ruby></p>",
        tmp_path=tmp_path,
    )
    readings = _readings(frags)
    assert len(readings) == 3
    eids = [int(r["$155"]) for r in readings.values()]
    positions = set()
    for f in by_type(frags, "$264"):
        for group in val(f):
            positions.update(int(x) for x in group["$181"])
    assert len(set(eids)) == 3 and not positions & set(eids)
    for f in by_type(frags, "$259"):
        for e in iter_entries(val(f)["$146"]):
            assert int(e.get("$155", -1)) not in eids


@pytest.mark.unit
def test_a_ruby_moves_no_entry_off_its_position(tmp_path):
    frags = _book(
        "<p>One</p><p><ruby>漢字<rt>かんじ</rt></ruby></p><p>Three</p>"
        "<table><tr><td><ruby>表<rt>ひょう</rt></ruby></td><td>x</td></tr></table>",
        tmp_path=tmp_path,
    )
    positions = set()
    for f in by_type(frags, "$264"):
        for group in val(f):
            positions.update(int(x) for x in group["$181"])
    entries = [
        int(e["$155"])
        for f in by_type(frags, "$259")
        for e in iter_entries(val(f)["$146"])
        if "$155" in e
    ]
    assert len(entries) == len(set(entries))
    assert set(entries) <= positions


@pytest.mark.unit
def test_the_book_declares_yj_ruby(tmp_path):
    frags = _book("<p><ruby>漢字<rt>かんじ</rt></ruby></p>", tmp_path=tmp_path)
    (features,) = by_type(frags, "$585")
    got = {
        str(f["$492"]): (
            int(f["$589"]["version"]["$587"]),
            int(f["$589"]["version"]["$588"]),
        )
        for f in val(features)["$590"]
    }
    assert got["yj_ruby"] == (1, 0)


@pytest.mark.unit
def test_a_book_with_no_ruby_writes_no_ruby(tmp_path):
    frags = _book("<p>漢字 <i>かんじ</i></p>", tmp_path=tmp_path)
    (features,) = by_type(frags, "$585")
    assert "yj_ruby" not in {str(f["$492"]) for f in val(features)["$590"]}
    assert not by_type(frags, "$756")


@pytest.mark.unit
def test_emphasis_on_the_whole_base_goes_on_its_event(tmp_path):
    frags = _book("<p><b><ruby>太<rt>ふと</rt></ruby>字</b></p>", tmp_path=tmp_path)
    events, _ = _events(frags)
    first, second = events["太字"]
    assert (first["$143"], first["$144"]) == (0, 1)
    assert "$757" in first and "$157" in first
    assert (second["$143"], second["$144"]) == (1, 1)
    assert "$757" not in second and "$157" in second


@pytest.mark.unit
def test_emphasis_on_part_of_a_base_is_an_event_of_its_own(tmp_path):
    frags = _book("<p><ruby><i>漢</i>字<rt>かんじ</rt></ruby></p>", tmp_path=tmp_path)
    events, _ = _events(frags)
    ruby, italic = events["漢字"]
    assert (ruby["$143"], ruby["$144"]) == (0, 2)
    assert "$757" in ruby and "$157" not in ruby
    assert (italic["$143"], italic["$144"]) == (0, 1)
    assert "$757" not in italic and "$157" in italic


@pytest.mark.unit
def test_a_ruby_in_a_link_keeps_the_link(tmp_path):
    frags = _book(
        '<p><a href="c.xhtml#t">リンク<ruby>先<rt>さき</rt></ruby></a></p><p id="t">T</p>',
        tmp_path=tmp_path,
    )
    events, _ = _events(frags)
    linked = [ev for ev in events["リンク先"] if "$179" in ev]
    assert linked and sum(ev["$144"] for ev in linked) == 4
    (ruby,) = [ev for ev in events["リンク先"] if "$757" in ev]
    assert (ruby["$143"], ruby["$144"]) == (3, 1)


@pytest.mark.unit
def test_a_ruby_in_a_table_cell_is_written(tmp_path):
    frags = _book(
        "<table><tr><td><ruby>表<rt>ひょう</rt></ruby></td><td>x</td></tr></table>",
        tmp_path=tmp_path,
    )
    events, _ = _events(frags)
    (ruby,) = events["表"]
    assert "$757" in ruby
    assert [str(r["$145"]) for r in _readings(frags).values()] == ["ひょう"]


@pytest.mark.unit
def test_each_chapter_has_its_own_ruby_fragment_numbered_from_one(tmp_path):
    frags = _book(
        "<p><ruby>一<rt>いち</rt></ruby><ruby>二<rt>に</rt></ruby></p>",
        "<p>No ruby here.</p>",
        "<p><ruby>三<rt>さん</rt></ruby></p>",
        tmp_path=tmp_path,
    )
    rubies = {str(f.fid): val(f) for f in by_type(frags, "$756")}
    assert sorted(
        [str(r["$145"]) for r in frag["$146"]] for frag in rubies.values()
    ) == [["いち", "に"], ["さん"]]
    for frag in rubies.values():
        assert [int(r["$758"]) for r in frag["$146"]] == list(
            range(1, len(frag["$146"]) + 1)
        )
    named = []
    for st in by_type(frags, "$259"):
        used = {
            str(ev["$757"])
            for e in iter_entries(val(st)["$146"])
            for ev in e.get("$142") or []
            if "$757" in ev
        }
        if used:
            named.append(used)
    assert len(named) == 2 and all(len(u) == 1 for u in named)
    assert {n for u in named for n in u} == set(rubies)


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        '<a href="c.xhtml#t"><ruby>先<rt>さき</rt></ruby></a>',
        '<ruby><a href="c.xhtml#t">先</a><rt>さき</rt></ruby>',
        '<a href="c.xhtml#t"><i><ruby>先<rt>さき</rt></ruby></i></a>',
    ],
)
def test_a_link_over_the_whole_base_goes_on_its_event(tmp_path, html):
    frags = _book(f'<p>{html}</p><p id="t">T</p>', tmp_path=tmp_path)
    events, _ = _events(frags)
    (ev,) = events["先"]
    assert (ev["$143"], ev["$144"]) == (0, 1)
    assert "$757" in ev and "$179" in ev


@pytest.mark.unit
def test_each_ruby_fragment_is_in_both_entity_maps(tmp_path):
    # Previewer's file, converted to KFX, lists its $756 in the $270 map and
    # in the $419 index, as every fragment kfxgen writes is listed.
    frags = _book(
        "<p><ruby>一<rt>いち</rt></ruby></p>",
        "<p><ruby>二<rt>に</rt></ruby></p>",
        tmp_path=tmp_path,
    )
    names = {str(f.fid) for f in by_type(frags, "$756")}
    assert len(names) == 2
    (index,) = by_type(frags, "$419")
    assert names <= {str(n) for n in val(index)["$252"][0]["$181"]}
    (container,) = by_type(frags, "$270")
    assert sum(1 for kind, _id in val(container)["$181"] if int(kind) == 756) == 2


@pytest.mark.unit
def test_a_heading_with_ruby_prints_once_under_a_flattened_title(tmp_path):
    # calibre's TOC label runs each reading into its base, as the book's own
    # navigation does (pg34084); the heading prints once, with its ruby.
    out = tmp_path / "h.kfx"
    blocks = _blocks(
        "<h1><ruby>法螺<rt>ほら</rt></ruby><ruby>男爵<rt>だんしやく</rt></ruby></h1>"
        "<p>本文。</p>"
    )
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "法螺ほら男爵だんしやく", "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    events, _ = _events(frags)
    assert list(events) == ["法螺男爵", "本文。"]
    readings = _readings(frags)
    assert [
        (ev["$143"], ev["$144"], str(readings[str(ev["$757"]), ev["$758"]]["$145"]))
        for ev in events["法螺男爵"]
        if "$757" in ev
    ] == [(0, 2, "ほら"), (2, 2, "だんしやく")]


@pytest.mark.unit
def test_a_chapter_of_only_a_ruby_heading_keeps_its_readings(tmp_path):
    # pg34084's title page: the chapter is its heading alone.
    out = tmp_path / "h.kfx"
    blocks = _blocks("<h1><ruby>法螺<rt>ほら</rt></ruby>男爵</h1>")
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "法螺ほら男爵", "text": "法螺男爵", "blocks": blocks},
            {"title": "Two", "text": "y", "blocks": _blocks("<p>Body.</p>")},
        ],
        output_path=str(out),
    )
    frags = load_fragments(out)
    events, _ = _events(frags)
    assert "法螺ほら男爵" not in events
    readings = _readings(frags)
    assert [
        (ev["$143"], ev["$144"], str(readings[str(ev["$757"]), ev["$758"]]["$145"]))
        for ev in events["法螺男爵"]
        if "$757" in ev
    ] == [(0, 2, "ほら")]
