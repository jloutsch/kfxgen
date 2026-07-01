import pytest
from kfxgen.font_table import (
    Face,
    is_ttf_otf,
    normalize_family,
    parse_weight,
    parse_style,
)

pytestmark = pytest.mark.unit


def test_is_ttf_otf_accepts_truetype_and_opentype():
    assert is_ttf_otf(b"\x00\x01\x00\x00rest")  # TrueType
    assert is_ttf_otf(b"OTTOrest")  # OpenType/CFF
    assert is_ttf_otf(b"truerest")  # legacy TrueType
    assert is_ttf_otf(b"ttcfrest")  # TrueType collection


def test_is_ttf_otf_rejects_woff_and_junk():
    assert not is_ttf_otf(b"wOFFrest")  # WOFF
    assert not is_ttf_otf(b"wOF2rest")  # WOFF2
    assert not is_ttf_otf(b"\x89PNGabc")
    assert not is_ttf_otf(b"")
    assert not is_ttf_otf(b"ab")


def test_normalize_family_strips_quotes_and_lowercases():
    assert normalize_family('"Merriweather"') == "merriweather"
    assert normalize_family("  'Foo Bar' ") == "foo bar"
    assert normalize_family("Georgia") == "georgia"
    assert normalize_family(None) == ""


def test_parse_weight():
    assert parse_weight("bold") == 700
    assert parse_weight("normal") == 400
    assert parse_weight(None) == 400
    assert parse_weight("600") == 600
    assert parse_weight("700 ") == 700
    assert parse_weight("weird") == 400


def test_parse_style():
    assert parse_style("italic") is True
    assert parse_style("oblique") is True
    assert parse_style("normal") is False
    assert parse_style(None) is False


def test_face_is_a_dataclass():
    f = Face(
        css_family="foo",
        weight=700,
        italic=True,
        data=b"\x00\x01\x00\x00",
        emitted_family="foo-700i",
        location="resource/font0",
    )
    assert f.weight == 700 and f.italic is True
