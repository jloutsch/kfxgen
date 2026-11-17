"""Rebuilding the contents page grows with the book's size, not its square
(#789), and pages that keep one dropped listing share its keys (#790).

Both were found porting converter.py to Rust. A real book is far smaller
than these shapes; they matter for very large or generated books."""

import time

import pytest

from kfxgen import converter as _conv
from kfxgen.inline_style import make_link_flag
from tests._helpers import NullLog
from tests.unit.test_toc_in_page_entries import _spine_item

pytestmark = pytest.mark.unit


def _book(n, heading):
    """#789's shape: every chapter has a title and two places,
    "Introduction" and "Part"; the "Contents" page alternates a link to a
    chapter and a heading."""
    blocks = [{"text": "Contents", "heading_level": 1}]
    chapters = []
    for i in range(n):
        title = f"Chapter {i}"
        link = frozenset({make_link_flag(f"c{i}.xhtml")})
        blocks.append({"text": title, "spans": [(0, len(title), link)]})
        blocks.append({"text": heading, "heading_level": 2})
        chapters.append(
            {
                "title": title,
                "text": f"{title} words.",
                "blocks": [{"text": f"{title} words."}],
                "sub_entries": [
                    {"title": "Introduction", "key": f"c{i}.xhtml#a"},
                    {"title": "Part", "key": f"c{i}.xhtml#b"},
                ],
            }
        )
    return [{"title": "Contents", "text": "", "blocks": blocks}, *chapters]


@pytest.mark.parametrize(
    "heading, lines",
    [
        # A heading the contents list: it prints as its entry's line.
        ("Introduction", 3),
        # One they don't: it prints before the next link's entry.
        ("Note", 4),
    ],
)
def test_a_contents_page_of_ten_thousand_chapters_rebuilds_in_seconds(heading, lines):
    # Before #789 each heading and link searched the whole list of entries,
    # and each printed heading was inserted into it: 5,000 chapters took
    # about 5 s, 10,000 about 20 s. Now it takes a fraction of a second.
    n = 10_000
    chapters = _book(n, heading)
    start = time.perf_counter()
    _conv._replace_title_page(chapters, {"title": "T"}, NullLog())
    assert time.perf_counter() - start < 5
    toc_links = chapters[0]["toc_links"]
    assert len(toc_links) == lines * n
    first = [(link["text"], link.get("heading_level")) for link in toc_links[:lines]]
    if heading == "Introduction":
        assert first == [("Chapter 0", None), ("Introduction", 2), ("Part", None)]
    else:
        assert first == [
            ("Chapter 0", None),
            ("Introduction", None),
            ("Part", None),
            ("Note", 2),
        ]


def test_a_second_link_with_the_same_words_is_the_next_entry():
    # Two entries named "Part": the page's second "Part" link is the second
    # entry, so the heading between the links prints between them (#618).
    link = frozenset({make_link_flag("c.xhtml")})
    contents = {
        "title": "Contents",
        "text": "",
        "blocks": [
            {"text": "Contents", "heading_level": 1},
            {"text": "Part", "spans": [(0, 4, link)]},
            {"text": "Note", "heading_level": 2},
            {"text": "Part", "spans": [(0, 4, link)]},
        ],
    }
    chapters = [
        contents,
        {"title": "Part", "text": "One."},
        {"title": "Part", "text": "Two."},
    ]
    _conv._rebuild_contents_page(contents, chapters, NullLog())
    assert [
        (t["text"], t.get("target_chapter_idx")) for t in contents["toc_links"]
    ] == [
        ("Part", 1),
        ("Note", None),
        ("Part", 2),
    ]


def _kept_listings(entries, ids):
    """#790's shape: `entries` contents entries titled "Copyright",
    alternately at the first id of a dropped listing of `ids` ids and at its
    file."""
    links = "".join(
        f'<p id="i{j}"><a href="b.xhtml">Line {j}</a></p>' for j in range(ids)
    )
    spine = [
        _spine_item("a.xhtml", f'<h1>Front</h1><div class="toc">{links}</div>'),
        _spine_item("b.xhtml", "<h1>B</h1><p>Body.</p>"),
    ]
    toc = [
        {"title": "Copyright", "href": "a.xhtml#i0" if k % 2 == 0 else "a.xhtml"}
        for k in range(entries)
    ] + [{"title": "B", "href": "b.xhtml"}]
    chapters = _conv._assemble_chapters_by_coordinate(spine, toc, NullLog())
    return [c["_listing_keys"] for c in chapters if "_listing_keys" in c]


def test_pages_keeping_one_listing_share_its_keys():
    # Before #790 each page built its own copy of the listing's keys:
    # pages × ids strings.
    kept = _kept_listings(200, 500)
    assert len(kept) == 100
    assert all(keys is kept[0] for keys in kept)
    # The listing's ids, sorted as they always were.
    assert kept[0] == [f"a.xhtml#{i}" for i in sorted(f"i{j}" for j in range(500))]
