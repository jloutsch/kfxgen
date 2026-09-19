"""Pictures printed on a replaced front-matter page survive the replacement (#178).

`_replace_title_page` rewrites a title page's body to the book's title and
author, and a half-title's to the title alone. The reason is that the text on
those pages states worse than the metadata does — a TOC label like "Title Page"
is structural, not content. That is a statement about the text and none at all
about what the publisher printed alongside it.

Until this, the pictures went with it: the whole scanned page in a page-scan
book, the vignette or series device above the title in an ordinary illustrated
one. Measured across a 226-book library, 181 books lost 314 images.

#117 already made this call for the contents page, and the generator reads
`preserved_images` outside its `toc_links` branch precisely so a page with no
links can use it. This is that mechanism applied one page earlier.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin"))

from kfxgen import converter  # noqa: E402
from tests._helpers import NullLog, jpeg_of  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

pytestmark = [pytest.mark.tier1, pytest.mark.unit]

METADATA = {"title": "A Title", "author": "An Author"}


def _image_page(*hrefs: str) -> bytes:
    body = "".join(f'<p><img src="{h}" alt=""/></p>' for h in hrefs)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<body>{body}</body></html>"
    ).encode()


def _book(tmp_path: Path, label: str, page: bytes, images: tuple[str, ...]) -> Path:
    builder = (
        EpubBuilder()
        .set_metadata(title="A Title", author="An Author")
        .add_chapter(label, page)
        .add_chapter("Chapter One", "Body text.\n\nA second paragraph.")
    )
    for i, href in enumerate(images):
        builder = builder.add_manifest_item(
            item_id=f"i{i}", href=href, media_type="image/jpeg", data=jpeg_of(600, 800)
        )
    return builder.build(tmp_path, "book")


def _chapters(epub: Path, metadata=METADATA):
    return converter.extract_chapters_from_oeb(
        EpubAsOeb(str(epub)), NullLog(), metadata=metadata
    )


def _front(chapters, label):
    return next(c for c in chapters if c["title"].lower().startswith(label.lower()[:5]))


class TestReplacedPagesKeepTheirPictures:
    def test_a_title_page_keeps_what_was_printed_on_it(self, tmp_path):
        epub = _book(
            tmp_path,
            "Title Page",
            _image_page("images/a.jpeg", "images/b.jpeg"),
            ("images/a.jpeg", "images/b.jpeg"),
        )
        ch = _front(_chapters(epub), "Title Page")
        assert ch["text"] == "A Title\n\nby\n\nAn Author", (
            "the replacement text is the point of the feature and must stay"
        )
        assert len(ch.get("preserved_images") or []) == 2

    def test_a_half_title_keeps_them_too(self, tmp_path):
        epub = _book(
            tmp_path, "Half Title", _image_page("images/a.jpeg"), ("images/a.jpeg",)
        )
        ch = _front(_chapters(epub), "Half Title")
        assert ch["text"] == "A Title", "half-title convention is title only"
        assert len(ch.get("preserved_images") or []) == 1

    def test_a_page_with_no_pictures_gains_no_key(self, tmp_path):
        """An empty list would make every replaced page look like it kept something."""
        epub = _book(
            tmp_path,
            "Title Page",
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            b"<p>Some title page prose.</p></body></html>",
            (),
        )
        ch = _front(_chapters(epub), "Title Page")
        assert "preserved_images" not in ch

    def test_without_metadata_nothing_is_replaced_and_nothing_is_kept(self, tmp_path):
        """The replacement only runs with metadata; the tokens stay in the body."""
        epub = _book(
            tmp_path, "Title Page", _image_page("images/a.jpeg"), ("images/a.jpeg",)
        )
        ch = _front(_chapters(epub, metadata=None), "Title Page")
        assert len(re.findall("\x00IMG", ch["text"])) == 1
        assert "preserved_images" not in ch

    def test_the_pictures_reach_the_container(self, tmp_path):
        """The end that matters: a reader sees them.

        Asserted through a real conversion rather than on the chapter dict,
        because `preserved_images` is a promise the generator has to keep —
        #117's comment records that the `toc_links` branch once dropped these
        again after the converter had carefully collected them.
        """
        epub = _book(
            tmp_path,
            "Title Page",
            _image_page("images/a.jpeg", "images/b.jpeg"),
            ("images/a.jpeg", "images/b.jpeg"),
        )
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "book.kfx"
            converter.convert_oeb_to_kfx(
                EpubAsOeb(str(epub)), str(out), opts=None, log=NullLog()
            )
            frags = load_fragments(out)
        assert len(by_type(frags, "$164")) == 2, (
            "both title-page images must survive as resources — #102 prunes any "
            "resource nothing displays, so a lost reference erases its own evidence"
        )
