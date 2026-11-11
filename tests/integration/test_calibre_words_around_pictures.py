"""Words and pictures loose in a container that also holds blocks, through
calibre (#757). Kindle Previewer 4.0.3 (probe P757; QA's Q28 on #755):
`<div style="text-align: center">Before <img/> after<p>…</p></div>` is one
centred paragraph with the picture inline, sized in em (px / 16), in a
horizontal book and in a vertical book's horizontal block alike.

Needs calibre installed, so it is `slow`; skips when `ebook-convert` is not
found.
"""

import pytest

from tests._kfx_introspect import load_fragments
from tests.integration.test_calibre_table_rows import (  # noqa: F401
    _ebook_convert,
    calibre,
)
from tests.integration.test_calibre_vertical_writing import (
    REST,
    VERT,
    _pictures,
    _write,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BODY = (
    "<h1>One</h1><p>W00 A paragraph.</p>"
    '<div class="c hz">Before <img src="w.jpg" alt="P1"/> after<p>W01 Last.</p></div>'
    '<div class="c hz"><img src="w.jpg" alt="P2"/><p>W02 Last.</p></div>'
    '<div class="c hz">前<img src="w.jpg" alt="P3"/>後<p>W03 Last.</p></div>'
    "<p>W04 A paragraph.</p>"
)
CSS = " .c { text-align: center }"


def _book(calibre, name, vertical):  # noqa: F811
    from tests._helpers import decodable_jpeg_of

    tmp, _env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    css = (f"html {{ {VERT} }}" if vertical else "") + REST + CSS
    _write(
        epub,
        vertical,
        css,
        css,
        lang="ja" if vertical else "en",
        ch1=BODY,
        images=[("w.jpg", decodable_jpeg_of(200, 40))],
    )
    _ebook_convert(calibre, epub, kfx, True)
    return load_fragments(kfx)


@pytest.fixture(scope="module", params=["horizontal", "vertical"])
def book(request, calibre):  # noqa: F811
    return _book(calibre, f"w757{request.param[0]}", request.param == "vertical")


def _words(entry):
    return [str(x) for x in entry.get("$146") or () if isinstance(x, str)]


@pytest.mark.parametrize(
    "alt, words", [("P1", ["Before ", " after"]), ("P3", ["前", "後"])]
)
def test_words_and_a_picture_are_one_centred_paragraph(book, alt, words):
    # P757 R01, QA's Q28: the words on each side and the picture inline in
    # one `$269`, centred, the picture sized in em.
    entry, own, parent, around = _pictures(book)[alt]
    assert str(entry.get("$601")) == "$283"
    assert "$308" in own["$56"] and "$308" in own["$57"]
    assert _words(parent) == words
    assert around.get("$34") == "$320"


def test_a_picture_alone_in_the_run_is_unchanged(book):
    # P757 R09: no words beside it, so not inline among them.
    entry, own, parent, _around = _pictures(book)["P2"]
    assert parent is None or not _words(parent)
    assert "$308" not in own.get("$56", "")
