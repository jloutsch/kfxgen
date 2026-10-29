"""Text before a book's first TOC entry gets no TOC entry, as Kindle
Previewer 4.0.3 writes it (#558, my probes 2026-10-29): title-page text in
the file whose Contents starts partway through, or a title page in a file of
its own. kfxgen listed it when its first line read as a heading (#143), and
its rebuilt Contents page listed it too. Its text still prints, once."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_part_headings import (  # noqa: E402
    _book_epub,
)
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _plain(x):
    return getattr(x, "value", x)


def _toc_and_text(kfx):
    frags = load_fragments(kfx)
    labels = []

    def walk(units):
        for u in units or ():
            u = _plain(u)
            labels.append(str(_plain(_plain(u["$241"])["$244"])))
            walk(u.get("$247"))

    for f in by_type(frags, "$389"):
        for nav in val(f):
            for c in _plain(nav).get("$392") or ():
                c = _plain(c)
                if str(c.get("$235")) == "$212":
                    walk(c.get("$247"))
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    words = [
        str(text[str(e["$145"]["name"])][int(e["$145"]["$403"])])
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]
    return labels, words


def _a(f, t):
    return f'<p><a href="{f}">{t}</a></p>'


def test_title_page_text_before_a_contents_mid_file_is_not_listed(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "c558.epub", tmp / "c558.kfx"
    _book_epub(
        epub,
        "C558",
        "<p>T1 The Book of Probes</p><p>T2 by kfxgen tests</p>"
        '<h2 id="toc">Contents</h2>'
        + _a("contents.xhtml#fw", "Foreword")
        + _a("c1.xhtml", "Chapter One")
        + '<h2 id="fw">Foreword</h2><p>F1 the foreword text.</p>',
        [("c1.xhtml", "Chapter One")],
        [
            ("t", "contents.xhtml#toc", "Contents", []),
            ("f", "contents.xhtml#fw", "Foreword", []),
            ("c1", "c1.xhtml", "Chapter One", []),
        ],
    )
    _ebook_convert(calibre, epub, kfx, True)
    labels, words = _toc_and_text(kfx)
    assert labels == ["Contents", "Foreword", "Chapter One"], f"calibre {version}"
    # Previewer's text, in order: the title page once, the Contents lines
    # without it.
    assert words[:6] == [
        "T1 The Book of Probes",
        "T2 by kfxgen tests",
        "Contents",
        "Foreword",
        "Chapter One",
        "Foreword",
    ], f"calibre {version}: {words[:8]}"


def test_a_title_page_file_before_the_first_entry_is_not_listed(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "s558.epub", tmp / "s558.kfx"
    _book_epub(
        epub,
        "S558",
        "<p>T3 The Separate Title Page</p><p>T4 by kfxgen tests</p>",
        [
            ("toc.xhtml", "Contents"),
            ("c1.xhtml", "Chapter One"),
            ("c2.xhtml", "Chapter Two"),
        ],
        [
            ("t", "toc.xhtml", "Contents", []),
            ("c1", "c1.xhtml", "Chapter One", []),
            ("c2", "c2.xhtml", "Chapter Two", []),
        ],
    )
    _ebook_convert(calibre, epub, kfx, True)
    labels, words = _toc_and_text(kfx)
    assert labels == ["Contents", "Chapter One", "Chapter Two"], f"calibre {version}"
    assert words.count("T3 The Separate Title Page") == 1, f"calibre {version}"
