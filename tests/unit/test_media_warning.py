"""Read-aloud narration, audio and video are said to be left out (#248).

kfxgen writes no audio or video. A read-aloud book pairs its pages with
narration through media overlays: a `media-overlay` link on each page's
manifest item to a SMIL file of audio clips. calibre's EPUB input drops
those links and the SMIL files from its manifest, so kfxgen reads the
book's own OPF, which calibre leaves unpacked in its container folder.
`<audio>` and `<video>` elements reach kfxgen in the pages, and their
fallback text is written as before."""

import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kfxgen import converter as _conv
from tests._helpers import NullLog

X = (
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
    "<head><title>{0}</title></head><body>{1}</body></html>"
)
AUDIO = (
    "<audio src='a.mp3' controls='controls'>Your reading system does not support "
    "audio.</audio><audio src='a.mp3'/><video src='v.mp4'><p>No video.</p></video>"
)
STYLE_ONLY = "<meta property='media:active-class'>-epub-media-overlay-active</meta>"


def _epub(path, overlays=False, audio=False, meta=""):
    """A three-page EPUB 3: pages 1 and 2 narrated when `overlays`, page 3
    holding two audio clips and a video when `audio`."""
    pages = {
        "p1": "<h1>One</h1><p id='s1'>The cat sat.</p>",
        "p2": "<h1>Two</h1><p id='s2'>The dog ran.</p>",
        "p3": "<h1>Three</h1><p>Before.</p>"
        + (AUDIO if audio else "")
        + "<p>After.</p>",
    }
    man = "".join(
        f'<item id="{p}" href="{p}.xhtml" media-type="application/xhtml+xml"'
        + (f' media-overlay="m{p}"' if overlays and p != "p3" else "")
        + "/>"
        for p in pages
    )
    if overlays:
        man += (
            '<item id="mp1" href="p1.smil" media-type="application/smil+xml"/>'
            '<item id="mp2" href="p2.smil" media-type="application/smil+xml"/>'
            '<item id="a" href="a.mp3" media-type="audio/mpeg"/>'
        )
    man += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">ra</dc:identifier><dc:title>R</dc:title>'
        f"<dc:creator>A</dc:creator><dc:language>en</dc:language>{meta}</metadata>"
        f'<manifest>{man}</manifest><spine toc="ncx">'
        + "".join(f'<itemref idref="{p}"/>' for p in pages)
        + "</spine></package>"
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head/><docTitle><text>R</text></docTitle><navMap>'
        + "".join(
            f'<navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{p}</text>'
            f'</navLabel><content src="{p}.xhtml"/></navPoint>'
            for i, p in enumerate(pages)
        )
        + "</navMap></ncx>"
    )
    smil = (
        '<smil xmlns="http://www.w3.org/ns/SMIL" version="3.0"><body><par id="x">'
        '<text src="{0}.xhtml#{1}"/><audio src="a.mp3" clipBegin="0s" clipEnd="1s"/>'
        "</par></body></smil>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OPS/c.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("OPS/c.opf", opf)
        z.writestr("OPS/toc.ncx", ncx)
        for p, body in pages.items():
            z.writestr(f"OPS/{p}.xhtml", X.format(p, body))
        if overlays:
            z.writestr("OPS/p1.smil", smil.format("p1", "s1"))
            z.writestr("OPS/p2.smil", smil.format("p2", "s2"))
    return path


def _book(tmp_path, unpacked=True, **kw):
    """The book as calibre hands it over: its pages, and its container
    folder, where calibre unpacked the EPUB."""
    from tests.fixtures.oeb_shim import EpubAsOeb

    epub = _epub(tmp_path / "ra.epub", **kw)
    book = EpubAsOeb(str(epub))
    if unpacked:
        folder = tmp_path / "unpacked"
        zipfile.ZipFile(epub).extractall(folder)
        book.container = SimpleNamespace(rootdir=str(folder))
    return book


def _said(book):
    log = MagicMock()
    _conv._warn_media(book, log)
    return [c.args[0] for c in log.warn.call_args_list]


@pytest.mark.unit
def test_narration_is_said_to_be_left_out(tmp_path):
    assert _said(_book(tmp_path, overlays=True)) == [
        "  Left out, since kfxgen writes no audio or video: this book's read-aloud "
        "narration. The text and pictures are converted (#248)"
    ]


@pytest.mark.unit
def test_audio_and_video_clips_are_counted(tmp_path):
    assert _said(_book(tmp_path, audio=True)) == [
        "  Left out, since kfxgen writes no audio or video: 3 audio or video clips "
        "in 1 file. The text and pictures are converted, with each clip's fallback "
        "text (#248)"
    ]


@pytest.mark.unit
def test_narration_and_clips_are_said_once(tmp_path):
    assert _said(_book(tmp_path, overlays=True, audio=True)) == [
        "  Left out, since kfxgen writes no audio or video: this book's read-aloud "
        "narration and 3 audio or video clips in 1 file. The text and pictures are "
        "converted, with each clip's fallback text (#248)"
    ]


@pytest.mark.unit
def test_a_book_without_media_says_nothing(tmp_path):
    assert _said(_book(tmp_path)) == []


@pytest.mark.unit
def test_an_overlay_style_without_overlays_says_nothing(tmp_path):
    """Two library books declare the overlay highlight class and have no
    narration."""
    assert _said(_book(tmp_path, meta=STYLE_ONLY)) == []


@pytest.mark.unit
def test_a_book_with_no_unpacked_folder_still_counts_clips(tmp_path):
    """A source that isn't an EPUB has no container folder to read."""
    assert _said(_book(tmp_path, unpacked=False, overlays=True)) == []
    assert len(_said(_book(tmp_path, unpacked=False, audio=True))) == 1


@pytest.mark.unit
def test_a_folder_with_no_container_file_still_counts_clips(tmp_path):
    """calibre unpacks a book that isn't an EPUB without a container.xml."""
    book = _book(tmp_path, audio=True)
    (tmp_path / "unpacked" / "META-INF" / "container.xml").unlink()
    assert len(_said(book)) == 1


@pytest.mark.unit
def test_an_opf_outside_the_folder_is_not_read(tmp_path):
    """container.xml names the OPF; a path out of the folder is ignored."""
    book = _book(tmp_path)
    narrated = _epub(tmp_path / "other.epub", overlays=True)
    (tmp_path / "c.opf").write_bytes(zipfile.ZipFile(narrated).read("OPS/c.opf"))
    container = tmp_path / "unpacked" / "META-INF" / "container.xml"
    container.write_text(container.read_text().replace("OPS/c.opf", "../c.opf"))
    assert _said(book) == []


@pytest.mark.unit
def test_an_audio_element_with_no_fallback_writes_no_block():
    from lxml import etree

    page = X.format(
        "p", "<p>Before.</p><audio src='a.mp3' controls='controls'/><p>After.</p>"
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(page.encode()), base_href="p.xhtml"
    )
    assert [b["text"] for b in blocks] == ["Before.", "After."]


@pytest.mark.unit
def test_converting_a_read_aloud_book_says_so(tmp_path):
    log = MagicMock(wraps=NullLog())
    _conv.convert_oeb_to_kfx(
        _book(tmp_path, overlays=True),
        str(tmp_path / "ra.kfx"),
        opts=MagicMock(
            kfxgen_disable_native_tables=False, kfxgen_embed_original_images=False
        ),
        log=log,
    )
    said = [c.args[0] for c in log.warn.call_args_list if "#248" in c.args[0]]
    assert len(said) == 1


@pytest.mark.unit
def test_a_page_that_does_not_parse_is_skipped(tmp_path):
    """The converter skips a page whose XHTML doesn't parse (#73); counting
    clips skips it too, and counts the others."""
    from tests.fixtures.epub_builder import EpubBuilder
    from tests.fixtures.oeb_shim import EpubAsOeb

    b = EpubBuilder().set_metadata(title="S", author="A")
    b.add_chapter("One", X.format("One", f"<p>Before.</p>{AUDIO}").encode())
    b.add_chapter("Two", X.format("Two", "<p>Broken.</body>").encode())
    book = EpubAsOeb(str(b.build(tmp_path, "m")))
    assert _said(book) == [
        "  Left out, since kfxgen writes no audio or video: 3 audio or video clips "
        "in 1 file. The text and pictures are converted, with each clip's fallback "
        "text (#248)"
    ]
