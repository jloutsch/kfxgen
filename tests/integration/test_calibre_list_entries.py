"""Lists through calibre, written as Kindle Previewer 4.0.3 writes them (#768).

Previewer (probe S768, decoded) writes `<ul>`/`<ol>` as a `$276` entry whose
`$100` is the marker style (disc `$340`, square `$341`, circle `$342`,
decimal `$343`, lower-roman `$344`, upper-alpha `$347`), and each `<li>` as
a `$277`: the item's text entry itself, or a container of its blocks when it
holds more than one or a nested list (L3, L4, L10). A start is the list's
`$104` (L7), a `value` the item's (L9), markers inside its `$551 $552`
(L11). `list-style-type: none` is no list: a `$269` round the items with
`$48` 4.688%, the 40px padding (L5). kfxgen typed the markers into the text
("• ", "1. ") at the left margin; on the Paperwhite (5.19.2) and the Voyage
(5.13.6) Previewer's lists are indented with their markers drawn, nested
lists further.

Previewer counts a `reversed` list up (L8 on the Paperwhite shows 1, 2, 3),
so kfxgen keeps its typed 3, 2, 1 there.

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
from tests.unit.test_link_landings import _landings  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = " ".join(
    [
        "p { margin: 0.5em 0; text-indent: 1.5em }",
        "p.flat { text-indent: 0 }",
        "ul.none { list-style-type: none }",
        "ul.none0 { list-style-type: none; padding-left: 0 }",
        "ol.roman { list-style-type: lower-roman }",
        "ol.alpha { list-style-type: upper-alpha }",
        "ul.inside { list-style-position: inside }",
        "ul.square { list-style-type: square }",
        "ul.neg { list-style-type: none; margin-left: -2em }",
        "ul.small { list-style-type: none; padding-left: 1em; font-size: 0.83em }",
        "ol.armenian { list-style-type: armenian }",
    ]
)


def _items(tag, n=3):
    return "".join(f"<li>{tag} item {i}</li>" for i in range(1, n + 1))


CASES = {
    "L1": f"<ul>{_items('L1')}</ul>",
    "L2": f"<ol>{_items('L2')}</ol>",
    "L3": "<ul><li>L3 outer one</li><li>L3 outer two<ul><li>L3 inner one</li>"
    "<li>L3 inner two</li></ul></li><li>L3 outer three</li></ul>",
    "L4": "<ol><li>L4 outer one<ol><li>L4 inner one</li><li>L4 inner two</li></ol>"
    "</li><li>L4 outer two</li></ol>",
    "L5": f'<ul class="none">{_items("L5")}</ul>',
    "L5b": f'<ul class="none0">{_items("L5b")}</ul>',
    "L6": f'<ol class="roman">{_items("L6")}</ol>',
    "L7": f'<ol start="5">{_items("L7")}</ol>',
    "L8": f'<ol reversed="reversed">{_items("L8")}</ol>',
    "L9": '<ol><li>L9 item 1</li><li value="10">L9 item 2</li><li>L9 item 3</li></ol>',
    "L10": '<ol><li><p class="flat">L10 one a</p><p>L10 one b</p></li>'
    '<li><p class="flat">L10 two a</p><p>L10 two b</p></li></ol>',
    "L11": f'<ul class="inside">{_items("L11", 2)}</ul>',
    "L12": '<ol><li id="n1">L12 note one.</li><li id="n2">L12 note two.</li></ol>',
    "L13": f'<ol class="alpha">{_items("L13")}</ol>',
    "L15": f'<ul class="square">{_items("L15")}</ul>',
    # Items that type their own number keep kfxgen's text (#201).
    "L16": "<ol><li>1. L16 typed</li><li>2. L16 typed</li></ol>",
    # QA on #771 (Previewer 4.0.3, Q771 and Q771D, Q771R): unmarked lists
    # with a negative margin (D5) or their own type size (D8), and in rtl
    # text (B15); a marked rtl list (R01); an empty item (X26); start 0
    # (X27); armenian (X34).
    "L17": f'<ul class="neg">{_items("L17")}</ul>',
    "L18": f'<ul class="small">{_items("L18")}</ul>',
    "L19": f'<div dir="rtl"><ul class="none">{_items("L19")}</ul></div>',
    "L20": f'<div dir="rtl"><ul>{_items("L20", 2)}</ul></div>',
    "L21": "<ol><li>L21 item 1</li><li></li><li>L21 item 3</li></ol>",
    "L22": f'<ol start="0">{_items("L22", 2)}</ol>',
    "L23": f'<ol class="armenian">{_items("L23", 2)}</ol>',
}
BODY = (
    "<h1>Chapter</h1>"
    + "".join(f'<p class="flat">{k} before.</p>{html}' for k, html in CASES.items())
    + '<p class="flat">Z1 To <a href="c1.xhtml#n2">L12 note two</a>.</p>'
)


@pytest.fixture(scope="module")
def book(calibre):  # noqa: F811
    """(tree, kfx): the converted chapter as nested (type, keys, style,
    text, kids) entries."""
    tmp, _env, _version = calibre
    epub, kfx = tmp / "lists768.epub", tmp / "lists768.kfx"
    _write(epub, False, CSS, CSS, lang="en", ch1=BODY)
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}

    def node(e):
        ref = e.get("$145")
        own = styles.get(str(e.get("$157")), {})
        return (
            str(e["$159"]),
            {
                str(k): str(v)
                for k, v in e.items()
                if str(k) in ("$100", "$104", "$551")
            },
            {
                str(k): f"{v['$307']}{v['$306']}"
                if hasattr(v, "get") and "$307" in v
                else str(v)
                for k, v in own.items()
                if str(k) != "$173"
            },
            str(texts[str(ref["name"])][ref["$403"]]) if ref else "",
            [node(k) for k in e.get("$146") or () if hasattr(k, "get")],
        )

    tree = [node(e) for f in by_type(frags, "$259") for e in val(f)["$146"]]
    return tree, kfx


def _after(tree, case):
    """The entries between "<case> before." and the next case's line."""
    flat = [n for n in tree]
    i = next(i for i, n in enumerate(flat) if n[3].startswith(f"{case} before."))
    out = []
    for n in flat[i + 1 :]:
        if n[3].endswith(" before.") or n[3].startswith("Z1 "):
            break
        out.append(n)
    return out


def _shape(n):
    """(type, keys, text or [kids' shapes])."""
    return (n[0], n[1], n[3] if not n[4] else [_shape(k) for k in n[4]])


def _list(tree, case):
    (lst,) = _after(tree, case)
    return _shape(lst)


def _items_of(kind, tag, n=3, keys=None):
    return (
        "$276",
        keys or {"$100": kind},
        [("$277", {}, f"{tag} item {i}") for i in range(1, n + 1)],
    )


@pytest.mark.parametrize(
    "case, kind",
    [("L1", "$340"), ("L2", "$343"), ("L6", "$344"), ("L13", "$347"), ("L15", "$341")],
)
def test_a_list_is_a_276_of_277_items_with_its_marker_style(book, case, kind):
    assert _list(book[0], case) == _items_of(kind, case)


def test_a_nested_list_is_in_its_items_277_container(book):
    # S768 L3: the outer item holds its words and the inner circle list.
    assert _list(book[0], "L3") == (
        "$276",
        {"$100": "$340"},
        [
            ("$277", {}, "L3 outer one"),
            (
                "$277",
                {},
                [
                    ("$269", {}, "L3 outer two"),
                    (
                        "$276",
                        {"$100": "$342"},
                        [("$277", {}, "L3 inner one"), ("$277", {}, "L3 inner two")],
                    ),
                ],
            ),
            ("$277", {}, "L3 outer three"),
        ],
    )
    assert _list(book[0], "L4")[2][0] == (
        "$277",
        {},
        [
            ("$269", {}, "L4 outer one"),
            (
                "$276",
                {"$100": "$343"},
                [("$277", {}, "L4 inner one"), ("$277", {}, "L4 inner two")],
            ),
        ],
    )


def test_an_item_of_paragraphs_is_a_277_container_of_them(book):
    assert _list(book[0], "L10") == (
        "$276",
        {"$100": "$343"},
        [
            ("$277", {}, [("$269", {}, "L10 one a"), ("$269", {}, "L10 one b")]),
            ("$277", {}, [("$269", {}, "L10 two a"), ("$269", {}, "L10 two b")]),
        ],
    )


def test_a_start_a_value_and_markers_inside(book):
    tree = book[0]
    assert _list(tree, "L7") == _items_of(
        "$343", "L7", keys={"$100": "$343", "$104": "5"}
    )
    assert _list(tree, "L9")[2][1] == ("$277", {"$104": "10"}, "L9 item 2")
    assert _list(tree, "L11") == _items_of(
        "$340", "L11", 2, keys={"$100": "$340", "$551": "$552"}
    )


def test_a_list_with_no_markers_is_indented_by_its_padding(book):
    tree = book[0]
    (box,) = _after(tree, "L5")
    assert (box[0], box[2].get("$48")) == ("$269", "4.6875$314")
    assert [k[3] for k in box[4]] == ["L5 item 1", "L5 item 2", "L5 item 3"]
    (box,) = _after(tree, "L5b")
    assert "$48" not in box[2]


def _share(style):
    return round(float(style["$48"].removesuffix("$314")), 3)


def test_an_unmarked_lists_indent_takes_its_margin_and_its_own_type_size(book):
    # Previewer's `$48` is the margin and the padding: Q771D D5 -6.25 + 4.688,
    # D8 1em in 0.83em type, 3.125 x 0.83.
    tree = book[0]
    (box,) = _after(tree, "L17")
    assert (box[0], _share(box[2])) == ("$269", -1.562)
    (box,) = _after(tree, "L18")
    assert (box[0], _share(box[2])) == ("$269", 2.594)


def test_an_unmarked_list_in_rtl_text_has_no_container(book):
    # Q771 B15, R04: Previewer writes the items as plain paragraphs, with no
    # indent on either side: their side margins are a paragraph's.
    entries = _after(book[0], "L19")
    assert [(n[0], n[3]) for n in entries] == [
        ("$269", f"L19 item {i}") for i in (1, 2, 3)
    ]
    (before,) = [n for n in book[0] if n[3] == "L19 before."]
    for n in entries:
        assert [n[2].get(k) for k in ("$48", "$49")] == [
            before[2].get(k) for k in ("$48", "$49")
        ]


def test_a_marked_list_in_rtl_text_is_a_list_with_its_direction(book):
    # Q771R R01: `$682 $375` on the `$276`.
    (lst,) = _after(book[0], "L20")
    assert (lst[0], lst[2].get("$682")) == ("$276", "$375")
    assert [k[3] for k in lst[4]] == ["L20 item 1", "L20 item 2"]


def test_an_empty_item_start_0_and_armenian(book):
    tree = book[0]
    # X26: an empty `$277`, which shows its number.
    assert _list(tree, "L21") == (
        "$276",
        {"$100": "$343"},
        [("$277", {}, "L21 item 1"), ("$277", {}, ""), ("$277", {}, "L21 item 3")],
    )
    # X27: the start on the list only.
    assert _list(tree, "L22") == _items_of(
        "$343", "L22", 2, keys={"$100": "$343", "$104": "0"}
    )
    # X34: `$100 $794`.
    assert _list(tree, "L23") == _items_of("$794", "L23", 2)


def test_a_reversed_list_and_typed_numbers_keep_kfxgens_text(book):
    tree = book[0]
    assert [n[3] for n in _after(tree, "L8")] == [
        "3. L8 item 1",
        "2. L8 item 2",
        "1. L8 item 3",
    ]
    assert [(n[0], n[3]) for n in _after(tree, "L16")] == [
        ("$269", "1. L16 typed"),
        ("$269", "2. L16 typed"),
    ]


def test_a_link_to_an_item_lands_on_it(book):
    assert _landings(book[1])["L12 note two"] == "L12 note two."
