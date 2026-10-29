"""A book with no page list gets approximate page numbers, placed as kfxlib's
`create_approximate_page_list` places them: a page at each section's start,
then one every 1,850 positions, moved back to the start of its word (#586).
A Paperwhite (5.19.2) showed kfxlib's pages on a kfxgen book, Go To Page
included (2026-10-29). A book's own page list still wins (#578)."""

import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402

pytestmark = [pytest.mark.tier2, pytest.mark.integration]

VENDOR_ZIP = REPO / "tests" / "fixtures" / "vendor" / "kfx_input_plugin.zip"


@pytest.fixture(scope="module")
def yj_book(tmp_path_factory):
    if not VENDOR_ZIP.exists():
        pytest.skip(f"Upstream kfxlib zip not found at {VENDOR_ZIP}")
    root = tmp_path_factory.mktemp("kfxlib_pages")
    with zipfile.ZipFile(VENDOR_ZIP) as zf:
        zf.extractall(root)
    paths = [str(root), str(root / "kfxlib" / "calibre-plugin-modules")]
    sys.path[:0] = paths
    try:
        import kfxlib.yj_book

        yield kfxlib.yj_book.YJ_Book
    finally:
        for p in paths:
            sys.path.remove(p)
        for name in [m for m in sys.modules if m.startswith("kfxlib")]:
            del sys.modules[name]


def _plain(x):
    return getattr(x, "value", x)


def _pages(navigation):
    """(label, eid, offset) of each entry of the $237 container."""
    for book_nav in navigation:
        for c in _plain(book_nav)["$392"]:
            c = _plain(c)
            if str(c.get("$235")) == "$237":
                return [
                    (
                        str(_plain(p)["$241"]["$244"]),
                        int(_plain(p)["$246"]["$155"]),
                        int(_plain(p)["$246"]["$143"]),
                    )
                    for p in c["$247"]
                ]
    return []


def _chapters():
    """Three chapters of long paragraphs, words of varied length so page
    breaks fall mid-word, a short one and a long run with no space."""
    words = " ".join(("w" * (1 + n % 9)) + str(n) for n in range(900))
    return [
        {
            "title": f"Chapter {i}",
            "text": words,
            "blocks": [{"text": f"P{i}.{k} " + words, "spans": []} for k in range(3)]
            + [{"text": f"Short {i}.", "spans": []}]
            # A run with no space longer than a page: the break can't move
            # back past the one before it.
            + [{"text": "Run " + "x" * 4000 + " end.", "spans": []}],
        }
        for i in range(3)
    ]


def _build(path, **kw):
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=_chapters(), output_path=str(path), **kw
    )
    return path


def test_pages_are_placed_as_kfxlib_places_them(tmp_path, yj_book):
    plain = _build(tmp_path / "plain.kfx")
    book = yj_book(str(plain))
    book.decode_book()
    book.create_approximate_page_list(0)
    want = _pages(book.fragments.get("$389", first=True).value)
    assert len(want) > 10  # the fixture spans many pages

    ours = _build(tmp_path / "pages.kfx", approximate_pages=True)
    (nav,) = by_type(load_fragments(ours), "$389")
    assert _pages(val(nav)) == want


def test_kfxlib_accepts_kfxgens_page_list(tmp_path, yj_book):
    """kfxlib sees the list as approximate and keeps it, as it keeps its
    own; and the book decodes without errors."""
    ours = _build(tmp_path / "pages.kfx", approximate_pages=True)
    book = yj_book(str(ours))
    book.decode_book()
    before = _pages(book.fragments.get("$389", first=True).value)
    book.create_approximate_page_list(0)
    assert _pages(book.fragments.get("$389", first=True).value) == before


def test_a_books_own_page_list_wins(tmp_path):
    chapters = _chapters()
    chapters[1]["anchor_keys"] = ["c.xhtml#p7"]
    path = tmp_path / "own.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=chapters,
        output_path=str(path),
        page_list=[("7", "c.xhtml#p7")],
        approximate_pages=True,
    )
    (nav,) = by_type(load_fragments(path), "$389")
    assert [label for label, _e, _o in _pages(val(nav))] == ["7"]


def test_no_pages_without_the_option(tmp_path):
    (nav,) = by_type(load_fragments(_build(tmp_path / "plain.kfx")), "$389")
    assert _pages(val(nav)) == []
