"""WOFF and WOFF2 fonts are unpacked and embedded (#245).

An @font-face whose only source was WOFF or WOFF2 was skipped, and the text
fell back to the Kindle's own font. calibre 9.16 passes WOFF through to the
plugin unchanged, and bundles fontTools and Brotli, so the plugin unpacks the
font to TTF/OTF. Kindle Previewer 4 embeds no font for a WOFF source.

A WOFF is compressed: a small file can claim to inflate to gigabytes. Each
WOFF table's zlib output, and a WOFF2's Brotli stream, are capped at their
declared lengths before fontTools decompresses anything, and the declared
total is held to the #47 size cap.
"""

import struct
import zlib
from io import BytesIO

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.woff2 import WOFF2DirectoryEntry, woff2DirectorySize

from kfxgen.font_table import faces_from_rules, is_ttf_otf, sfnt_from_woff

pytestmark = pytest.mark.unit

GLYPHS = [".notdef", "A", "B", "C"]


def _ttf(family="Probe"):
    """A small TrueType font with outlines, so WOFF2's glyf transform runs."""
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(GLYPHS)
    fb.setupCharacterMap({0x41: "A", 0x42: "B", 0x43: "C"})
    glyphs = {}
    for i, name in enumerate(GLYPHS):
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0))
        pen.lineTo((100 + i * 50, 0))
        pen.lineTo((100 + i * 50, 700))
        pen.closePath()
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (600, 0) for n in GLYPHS})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    out = BytesIO()
    fb.save(out)
    return out.getvalue()


def _flavoured(flavor, family="Probe"):
    font = TTFont(BytesIO(_ttf(family)))
    font.flavor = flavor
    out = BytesIO()
    font.save(out)
    return out.getvalue()


class _Log:
    def __init__(self):
        self.warns = []

    def warn(self, m):
        self.warns.append(m)

    def info(self, m):
        pass


def _faces(data, href="f.woff", **kw):
    log = _Log()
    rules = [{"font-family": "Probe", "src": f"url({href})"}]
    return faces_from_rules(rules, {href: data}.get, log, **kw), log


def _glyphs(data):
    return TTFont(BytesIO(data)).getGlyphOrder()


# --- Unpacking ---------------------------------------------------------------


def test_a_woff_face_is_embedded_as_truetype():
    (face,), log = _faces(_flavoured("woff"))
    assert is_ttf_otf(face.data)
    assert _glyphs(face.data) == GLYPHS
    assert log.warns == []


def test_a_woff2_face_is_embedded_as_truetype():
    pytest.importorskip("brotli")
    (face,), log = _faces(_flavoured("woff2"), href="f.woff2")
    assert is_ttf_otf(face.data)
    assert _glyphs(face.data) == GLYPHS
    assert log.warns == []


def test_a_truetype_source_is_still_preferred_to_a_woff_listed_first():
    log = _Log()
    ttf = _ttf()
    rules = [{"font-family": "Probe", "src": "url(f.woff), url(f.ttf)"}]
    manifest = {"f.woff": _flavoured("woff", family="Other"), "f.ttf": ttf}
    (face,) = faces_from_rules(rules, manifest.get, log)
    assert face.data == ttf


def test_a_font_repeated_in_every_chapter_is_unpacked_once(monkeypatch):
    """Rules gathered from each chapter repeat; one book had 168 for 5 fonts."""
    import kfxgen.font_table as ft

    calls = []
    real = ft.sfnt_from_woff

    def counted(data, max_bytes):
        calls.append(1)
        return real(data, max_bytes)

    monkeypatch.setattr(ft, "sfnt_from_woff", counted)
    rules = [{"font-family": "Probe", "src": "url(f.woff)"}] * 30
    faces = faces_from_rules(rules, {"f.woff": _flavoured("woff")}.get, _Log())
    assert len(faces) == 1
    assert len(calls) == 1


def test_a_damaged_woff_is_skipped_with_a_warning():
    data = _flavoured("woff")
    faces, log = _faces(data[:200])
    assert faces == []
    assert any("f.woff" in w for w in log.warns)


def test_the_size_cap_applies_after_unpacking():
    """#47's cap counts the unpacked font, not the smaller WOFF."""
    data = _flavoured("woff")
    unpacked = len(sfnt_from_woff(data, 10**6))
    assert len(data) < unpacked
    faces, log = _faces(data, max_font_bytes=unpacked - 1)
    assert faces == []
    assert log.warns


# --- Decompression is bounded -----------------------------------------------


def _woff_with_bomb_table(declared, actual):
    """A WOFF whose one table claims `declared` bytes and inflates to `actual`."""
    raw = zlib.compress(b"\0" * actual, 9)
    header_len, entry_len = 44, 20
    offset = header_len + entry_len
    total = offset + len(raw)
    header = struct.pack(
        ">4s4sLHHLHHLLLLL",
        b"wOFF", b"\x00\x01\x00\x00", total, 1, 0, 12 + 16 + declared,
        1, 0, 0, 0, 0, 0, 0,
    )  # fmt: skip
    entry = struct.pack(">4sLLLL", b"glyf", offset, len(raw), declared, 0)
    return header + entry + raw


def test_a_woff_table_inflating_past_its_length_is_rejected_unread(monkeypatch):
    """fontTools would zlib-inflate the whole table before comparing lengths."""
    # 20 MB of zeros compresses to about 20 KB, under the 100 KB declared.
    data = _woff_with_bomb_table(declared=100_000, actual=20 * 1024 * 1024)

    def unbounded(*a, **k):
        raise AssertionError("an unbounded zlib.decompress ran")

    inflated = []
    real = zlib.decompressobj

    class Measured:
        def __init__(self):
            self._d = real()

        def decompress(self, *a):
            out = self._d.decompress(*a)
            inflated.append(len(out))
            return out

    monkeypatch.setattr(zlib, "decompress", unbounded)
    monkeypatch.setattr(zlib, "decompressobj", Measured)
    with pytest.raises(ValueError, match="glyf"):
        sfnt_from_woff(data, 50 * 1024 * 1024)
    assert max(inflated) <= 100_001


def test_a_woff_declaring_more_than_the_cap_is_rejected_unread(monkeypatch):
    data = _woff_with_bomb_table(declared=2000, actual=2000)
    monkeypatch.setattr(zlib, "decompressobj", None)
    with pytest.raises(ValueError, match="cap"):
        sfnt_from_woff(data, 1000)


def _woff2_with_stream(stream):
    """A real WOFF2 with its Brotli stream replaced by `stream`."""
    data = _flavoured("woff2")
    # The stream starts where the table directory ends.
    f = BytesIO(data)
    f.seek(woff2DirectorySize)
    for _ in range(struct.unpack(">H", data[12:14])[0]):
        WOFF2DirectoryEntry().fromFile(f)
    directory_end = f.tell()
    rebuilt = data[:directory_end] + stream
    rebuilt = rebuilt[:8] + struct.pack(">L", len(rebuilt)) + rebuilt[12:]
    rebuilt = rebuilt[:20] + struct.pack(">L", len(stream)) + rebuilt[24:]
    return rebuilt


def test_a_woff2_stream_inflating_past_its_tables_is_rejected_unread(monkeypatch):
    brotli = pytest.importorskip("brotli")
    data = _woff2_with_stream(brotli.compress(b"\0" * (20 * 1024 * 1024)))

    def unbounded(*a, **k):
        raise AssertionError("an unbounded brotli.decompress ran")

    monkeypatch.setattr(brotli, "decompress", unbounded)
    with pytest.raises(ValueError, match="WOFF2"):
        sfnt_from_woff(data, 50 * 1024 * 1024)
