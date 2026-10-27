"""The contents keep the book's nesting (#313).

A two-level contents (chapters, and the sections inside them) came out as one
flat list in the contents pane, and kfxgen's rebuilt Contents page listed
every entry at the same indent. Kindle Previewer 4 (kpr4, decoded) nests each
entry's children in a `$247` list inside its `$393` nav unit, as the NCX
nests its navPoints. kfxgen now does the same, and indents the Contents page
by level.
"""

import zipfile
from unittest.mock import MagicMock

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import NullLog
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

TOC = [
    {"title": "Chapter One", "href": "ch1.xhtml", "level": 0},
    {"title": "Section A", "href": "ch1.xhtml#s1", "level": 1},
    {"title": "Part A1", "href": "ch1.xhtml#s1a", "level": 2},
    {"title": "Section B", "href": "ch1.xhtml#s2", "level": 1},
    {"title": "Chapter Two", "href": "ch2.xhtml", "level": 0},
]
CH1 = (
    '<h1 id="c1">Chapter One</h1><p>Para one.</p>'
    '<h2 id="s1">Section A</h2><p>Under A.</p>'
    '<h3 id="s1a">Part A1</h3><p>Deeper.</p>'
    '<h2 id="s2">Section B</h2><p>Under B.</p>'
)
CH2 = '<h1 id="c2">Chapter Two</h1><p>Para two.</p>'


def _doc(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return etree.fromstring(xhtml.encode())


def _spine_item(href, body, breaks_known):
    blocks = _conv.extract_blocks_from_html(
        _doc(body),
        style_resolver=(lambda e: {}) if breaks_known else None,
        base_href=href,
    )
    item = {"href": href, "text": "", "blocks": blocks, "nav_listing_at": []}
    if breaks_known:
        item["breaks_known"] = True
    return item


def _chapters(breaks_known):
    spine = [
        _spine_item("ch1.xhtml", CH1, breaks_known),
        _spine_item("ch2.xhtml", CH2, breaks_known),
    ]
    return _conv._assemble_chapters_by_coordinate(spine, TOC, NullLog())


def _tree(frags):
    """The contents pane as nested (label, [children]) pairs."""

    def unit(u):
        u = getattr(u, "value", u)
        label = getattr(u["$241"], "value", u["$241"])
        kids = u.get("$247") or []
        return (str(label["$244"]), [unit(k) for k in kids])

    for f in by_type(frags, "$389"):
        for container in val(f)[0]["$392"]:
            c = getattr(container, "value", container)
            if str(c["$235"]) == "$212":
                return [unit(u) for u in c["$247"]]
    return None


NESTED = [
    (
        "Chapter One",
        [("Section A", [("Part A1", [])]), ("Section B", [])],
    ),
    ("Chapter Two", []),
]


def _book(chapters, path):
    g = NativeKFXGenerator()
    g.generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(path)
    )
    return load_fragments(path)


# --- The levels reach the chapters ----------------------------------------


@pytest.mark.unit
def test_sections_and_places_carry_their_level():
    """Sections inside the chapter's file, no page break: places (#363)."""
    (one, two) = _chapters(breaks_known=True)
    assert one["toc_level"] == 0 and two["toc_level"] == 0
    assert [(s["title"], s["level"]) for s in one["sub_entries"]] == [
        ("Section A", 1),
        ("Part A1", 2),
        ("Section B", 1),
    ]


@pytest.mark.unit
def test_each_chapter_carries_its_level_without_css():
    chapters = _chapters(breaks_known=False)
    assert [(c["title"], c["toc_level"]) for c in chapters] == [
        (e["title"], e["level"]) for e in TOC
    ]


# --- The contents pane nests ----------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("breaks_known", [True, False])
def test_the_contents_pane_nests_as_the_book_does(tmp_path, breaks_known):
    """Whether a section is a place in its chapter (#363) or a section of
    its own, it sits under its chapter."""
    frags = _book(_chapters(breaks_known), tmp_path / "n.kfx")
    assert _tree(frags) == NESTED


@pytest.mark.unit
def test_a_flat_contents_stays_flat(tmp_path):
    chapters = [
        {"title": "One", "text": "One\n\nx", "toc_level": 0},
        {"title": "Two", "text": "Two\n\ny", "toc_level": 0},
    ]
    frags = _book(chapters, tmp_path / "f.kfx")
    assert _tree(frags) == [("One", []), ("Two", [])]


@pytest.mark.unit
def test_an_entry_under_a_dropped_parent_nests_under_the_one_before():
    """The NCX's parent was left out (not in the spine, a footnote): the
    child goes under the nearest entry above it at a lower level."""
    from kfxgen.native_generator import _nest_nav_entries

    flat = [
        {"title": "Part", "level": 0},
        {"title": "Chapter", "level": 1},
        # A level-2 parent of "Deep" was dropped.
        {"title": "Deep", "level": 3},
        {"title": "Next", "level": 1},
        {"title": "Last", "level": 0},
    ]
    nested = _nest_nav_entries(flat)

    def shape(entries):
        return [(e["title"], shape(e.get("children") or [])) for e in entries]

    assert shape(nested) == [
        ("Part", [("Chapter", [("Deep", [])]), ("Next", [])]),
        ("Last", []),
    ]


@pytest.mark.unit
def test_a_book_starting_below_the_top_level_is_not_lost():
    from kfxgen.native_generator import _nest_nav_entries

    flat = [{"title": "A", "level": 2}, {"title": "B", "level": 0}]
    assert [e["title"] for e in _nest_nav_entries(flat)] == ["A", "B"]


@pytest.mark.unit
def test_nested_entries_keep_their_landing(tmp_path):
    frags = _book(_chapters(breaks_known=True), tmp_path / "n.kfx")
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    at = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            ref = e.get("$145")
            if ref:
                at[int(e["$155"])] = texts[str(ref["name"])][ref["$403"]]

    def walk(units, out):
        for u in units:
            u = getattr(u, "value", u)
            target = getattr(u["$246"], "value", u["$246"])
            label = str(getattr(u["$241"], "value", u["$241"])["$244"])
            out.append((label, at[int(target["$155"])][int(target["$143"]) :]))
            walk(u.get("$247") or [], out)
        return out

    for f in by_type(frags, "$389"):
        for c in val(f)[0]["$392"]:
            c = getattr(c, "value", c)
            if str(c["$235"]) == "$212":
                for label, text in walk(c["$247"], []):
                    assert text.startswith(label), (label, text)


# --- The rebuilt Contents page indents ------------------------------------


def _contents_links(frags):
    """(link text, margin-left of its paragraph) on the Contents page."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            ref = e.get("$145")
            runs = e.get("$142") or []
            if ref and any("$179" in r for r in runs):
                margin = styles[str(e["$157"])]["$48"]
                out.append(
                    (texts[str(ref["name"])][ref["$403"]], float(margin["$307"]))
                )
    return out


@pytest.mark.unit
def test_the_contents_page_indents_by_level(tmp_path):
    chapters = _chapters(breaks_known=True)
    contents = {"title": "Contents", "text": "Contents", "blocks": [], "toc_level": 0}
    chapters.insert(0, contents)
    _conv._rebuild_contents_page(contents, chapters, NullLog())
    links = dict(_contents_links(_book(chapters, tmp_path / "c.kfx")))
    assert links["Section A"] > links["Chapter One"]
    assert links["Part A1"] > links["Section A"]
    assert links["Section B"] == links["Section A"]
    assert links["Chapter Two"] == links["Chapter One"]


@pytest.mark.unit
def test_contents_levels_count_from_the_shallowest_listed():
    """A book whose contents all sit under one wrapper entry kfxgen leaves
    off the page (its title page) is not indented throughout."""
    chapters = [
        {"title": "Title Page", "text": "t", "toc_level": 0},
        {"title": "One", "text": "x", "toc_level": 1},
        {"title": "Two", "text": "y", "toc_level": 1},
    ]
    contents = {"title": "Contents", "text": "Contents", "blocks": [], "toc_level": 1}
    chapters.insert(1, contents)
    _conv._rebuild_contents_page(contents, chapters, NullLog())
    assert [(link["text"], link["level"]) for link in contents["toc_links"]] == [
        ("One", 0),
        ("Two", 0),
    ]


# --- End to end, through the converter ------------------------------------


def _epub(path):
    def page(body):
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            f"</head><body>{body}</body></html>"
        )

    def point(i, title, src, kids=""):
        return (
            f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{title}</text>'
            f'</navLabel><content src="{src}"/>{kids}</navPoint>'
        )

    nav = point(
        1,
        "Chapter One",
        "ch1.xhtml",
        point(2, "Section A", "ch1.xhtml#s1", point(3, "Part A1", "ch1.xhtml#s1a"))
        + point(4, "Section B", "ch1.xhtml#s2"),
    ) + point(5, "Chapter Two", "ch2.xhtml")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="id"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>N</dc:title>'
            '<dc:creator>A</dc:creator><dc:identifier id="id">n</dc:identifier>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="a" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="b" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="a"/><itemref idref="b"/>'
            "</spine></package>",
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>N</text></docTitle>'
            f"<navMap>{nav}</navMap></ncx>",
        )
        z.writestr("ch1.xhtml", page(CH1))
        z.writestr("ch2.xhtml", page(CH2))
    return path


@pytest.mark.unit
def test_converting_a_nested_contents_keeps_its_nesting(tmp_path):
    from tests.fixtures.oeb_shim import EpubAsOeb

    out = tmp_path / "n.kfx"
    _conv.convert_oeb_to_kfx(
        EpubAsOeb(str(_epub(tmp_path / "n.epub"))),
        str(out),
        opts=MagicMock(kfxgen_disable_native_tables=False),
        log=NullLog(),
    )
    assert _tree(load_fragments(out)) == NESTED


@pytest.mark.parametrize(
    ("toc", "subs"),
    [
        pytest.param(
            [
                {"title": "Chapter One", "href": "ch1.xhtml", "level": 0},
                {"title": "Twin", "href": "ch1.xhtml", "level": 0},
            ],
            [("Twin", None)],
            id="twin-of-the-section",
        ),
        pytest.param(
            [
                {"title": "Chapter One", "href": "ch1.xhtml", "level": 0},
                {"title": "Section A", "href": "ch1.xhtml#s1", "level": 1},
                {"title": "Twin", "href": "ch1.xhtml#s1", "level": 1},
                {"title": "Twin again", "href": "ch1.xhtml#s1", "level": 1},
            ],
            [
                ("Section A", "ch1.xhtml#s1"),
                ("Twin", "ch1.xhtml#s1"),
                ("Twin again", "ch1.xhtml#s1"),
            ],
            id="twin-of-a-place",
        ),
    ],
)
def test_a_second_entry_on_the_same_spot_lands_with_its_twin(toc, subs):
    # #459: it was dropped as "out of document order". It is now a place in
    # its twin's section, landing where the twin does: the section's start
    # (no key) for the section's own entry, the twin's key for a place.
    spine = [
        _spine_item("ch1.xhtml", CH1, True),
        _spine_item("ch2.xhtml", CH2, True),
    ]
    chapters = _conv._assemble_chapters_by_coordinate(spine, toc, NullLog())
    assert [c["title"] for c in chapters] == ["Chapter One"]
    assert [(s["title"], s["key"]) for s in chapters[0]["sub_entries"]] == subs


@pytest.mark.unit
def test_an_entry_out_of_document_order_is_kept_where_the_book_lists_it():
    # #484: it was dropped as "out of document order". It is now a place, in
    # the book's TOC order, landing on its own block; sections still follow
    # the document.
    spine = [
        _spine_item("ch1.xhtml", CH1, True),
        _spine_item("ch2.xhtml", CH2, True),
    ]
    toc = [
        {"title": "Chapter Two", "href": "ch2.xhtml", "level": 0},
        {"title": "Chapter One", "href": "ch1.xhtml", "level": 0},
    ]
    chapters = _conv._assemble_chapters_by_coordinate(spine, toc, NullLog())
    head, two = chapters
    assert head.get("_omit_from_toc") is True
    assert two["title"] == "Chapter Two"
    assert [(s["title"], s["key"]) for s in two["sub_entries"]] == [
        ("Chapter One", "ch1.xhtml")
    ]


@pytest.mark.unit
def test_a_last_entry_out_of_order_does_not_repeat_the_files_after_it():
    # #484 review: the book's last TOC entry pointing back to the first file
    # printed every later file a second time, as pages the TOC never listed
    # (one library book's text doubled).
    spine = [
        _spine_item("ch1.xhtml", CH1, True),
        _spine_item("ch2.xhtml", CH2, True),
        _spine_item(
            "ch3.xhtml", '<h1 id="c3">Chapter Three</h1><p>Para three.</p>', True
        ),
    ]
    for item in spine:
        item["text"] = "\n\n".join(b["text"] for b in item["blocks"])
    toc = [
        {"title": "Chapter One", "href": "ch1.xhtml", "level": 0},
        {"title": "Chapter Two", "href": "ch2.xhtml", "level": 0},
        {"title": "Chapter Three", "href": "ch3.xhtml", "level": 0},
        {"title": "Back to A", "href": "ch1.xhtml#s1", "level": 0},
    ]
    chapters = _conv._assemble_chapters_by_coordinate(spine, toc, NullLog())
    texts = [b["text"] for c in chapters for b in c["blocks"]]
    assert texts == [b["text"] for s in spine for b in s["blocks"]]
    assert [c["title"] for c in chapters] == [
        "Chapter One",
        "Chapter Two",
        "Chapter Three",
    ]
