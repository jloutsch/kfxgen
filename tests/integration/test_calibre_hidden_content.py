"""Hidden content through calibre's real Stylizer (#232, #256).

Content the book hides, with the `hidden` attribute or `display: none`, was
printed when it sat inside a paragraph, a heading or a table row: mostly
page markers, so "harbour12" appeared mid-sentence. A block hidden by a
stylesheet class was printed too. Hidden content now gives only its ids,
which keep their place so links to a hidden page marker still land.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = ".hide { display: none; }\n"

#: (file, case id, markup, the paragraphs expected between the case's
#: heading and its END line).
CASES = [
    (
        "c1",
        "PAGEMARK",
        '<p>The ship left the harbour<a id="page12" hidden="hidden">12</a> at dawn.</p>',
        ["The ship left the harbour at dawn."],
    ),
    (
        "c1",
        "PAGEBREAK",
        '<p>It reached the cape<span epub:type="pagebreak" id="p13" hidden="">13'
        "</span> by noon.</p>",
        ["It reached the cape by noon."],
    ),
    (
        "c1",
        "SPANHIDDEN",
        '<p>A note was kept<span hidden="hidden">internal marker</span> in the log.</p>',
        ["A note was kept in the log."],
    ),
    (
        "c1",
        "INLINESTYLE",
        '<p>Visible <span style="display:none">css-hidden</span> words.</p>',
        ["Visible words."],
    ),
    (
        "c1",
        "CLASSSPAN",
        '<p>Visible <span class="hide">answer</span> words.</p>',
        ["Visible words."],
    ),
    (
        "c1",
        "CLASSBLOCK",
        '<p>Question.</p><p class="hide" id="answer">Answer: 42.</p><p>Next.</p>',
        ["Question.", "Next."],
    ),
    (
        "c1",
        "LINK",
        '<p>See <a href="chapter_1.xhtml#page12">page 12</a>.</p>',
        ["See page 12."],
    ),
]


def _build(tmp):
    """One chapter, with an NCX: without one calibre builds its own TOC from
    the book's links, and the link case became a chapter heading."""
    body = "".join(f"<h2>{cid}</h2>{html}<p>END {cid}</p>" for _, cid, html, _ in CASES)
    page = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>Hidden</title><style>{CSS}</style></head>"
        f"<body><h1>Hidden cases</h1>{body}</body></html>"
    )
    builder = EpubBuilder().set_metadata(title="Hidden", author="kfxgen tests")
    builder.add_chapter("Hidden cases", page.encode("utf-8"))
    return builder.build(tmp, "hidden")


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    tmp, _, version = calibre
    epub = _build(tmp)
    kfx = tmp / "hidden.kfx"
    _ebook_convert(calibre, epub, kfx, native=True)
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    anchors = {str(f.fid): val(f)["$183"] for f in by_type(frags, "$266")}
    entries = [
        e for st in by_type(frags, "$259") for e in iter_entries(val(st)["$146"])
    ]
    texts = {}
    by_case, current = {}, None
    for e in entries:
        if "$145" not in e:
            continue
        text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        texts[int(e["$155"])] = text
        if text in {c[1] for c in CASES}:
            current = text
            by_case[current] = []
        elif current and text == f"END {current}":
            current = None
        elif current:
            by_case[current].append((text, e))
    return by_case, anchors, texts, version


@pytest.mark.parametrize(
    "cid,expected", [(c[1], c[3]) for c in CASES], ids=[c[1] for c in CASES]
)
def test_hidden_content_through_the_real_stylizer(converted, cid, expected):
    by_case, _, _, version = converted
    assert [t for t, _ in by_case[cid]] == expected, f"calibre {version}, case {cid}"


def test_a_link_to_a_hidden_page_marker_lands_at_its_place(converted):
    by_case, anchors, texts, version = converted
    ((_, link_entry),) = by_case["LINK"]
    (span,) = [sp for sp in link_entry.get("$142") or [] if "$179" in sp]
    target = anchors[str(span["$179"])]
    assert texts[int(target["$155"])] == "The ship left the harbour at dawn.", version
    assert int(target.get("$143", 0)) == len("The ship left the harbour"), version
