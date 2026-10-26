"""A file with a font's magic bytes and junk after them is skipped, not
embedded as a face (#471). The check reads the table directory: every
table inside the file, and the tables every font has."""

import struct

import pytest

from kfxgen.font_table import _check_sfnt, faces_from_rules
from tests._helpers import sfnt_of
from tests.unit.test_woff_fonts import _ttf

pytestmark = pytest.mark.unit


class _Log:
    def __init__(self):
        self.warns = []

    def warn(self, m):
        self.warns.append(m)

    def info(self, m):
        pass

    def debug(self, m):
        pass


def _faces(data):
    log = _Log()
    faces = faces_from_rules(
        [{"font-family": "Geo", "font-weight": "bold", "src": "url(g.ttf)"}],
        lambda href: data if href == "g.ttf" else None,
        log,
    )
    return faces, log.warns


def test_a_real_font_is_embedded():
    faces, warns = _faces(_ttf())
    assert len(faces) == 1 and warns == []


def test_junk_after_the_magic_is_skipped_and_named():
    # QA's #452 probe: TTF magic, then 8,000 bytes of junk.
    faces, warns = _faces(b"\x00\x01\x00\x00" + b"\x5a\x17" * 4000)
    assert faces == []
    assert any("'g.ttf'" in w for w in warns)


def _directory(tags, length=4, n=None):
    body = 12 + 16 * len(tags)
    return (
        b"\x00\x01\x00\x00"
        + struct.pack(">HHHH", len(tags) if n is None else n, 0, 0, 0)
        + b"".join(struct.pack(">4sLLL", t, 0, body, length) for t in tags)
        + bytes(4)
    )


ALL = (b"cmap", b"glyf", b"head", b"hhea", b"hmtx", b"maxp")


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b"\x00\x01\x00\x00\x00", "header"),
        (_directory(ALL, n=40), "directory"),
        (_directory(ALL, length=4000), "past the file's end"),
        (_directory(ALL[1:]), "'cmap'"),
        (_directory((b"cmap", b"head", b"hhea", b"hmtx", b"maxp")), "'glyf'"),
    ],
    ids=["short-header", "short-directory", "table-past-end", "no-cmap", "no-outlines"],
)
def test_an_unsound_directory_says_why(data, why):
    with pytest.raises(ValueError, match=why):
        _check_sfnt(data)


@pytest.mark.parametrize(
    "data",
    [sfnt_of(), _directory((b"CFF ",) + ALL[:1] + ALL[2:]), b"ttcf" + bytes(20)],
    ids=["glyf", "cff", "collection"],
)
def test_a_sound_directory_passes(data):
    _check_sfnt(data)
