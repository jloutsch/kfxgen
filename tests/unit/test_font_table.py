import pytest
from kfxgen.font_table import (
    Face,
    extract_src_urls,
    emitted_name,
    faces_from_rules,
    is_ttf_otf,
    normalize_family,
    parse_style,
    parse_weight,
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


class _Log:
    def __init__(self):
        self.warns = []

    def warn(self, m):
        self.warns.append(m)

    def info(self, m):
        pass

    warning = warn


def test_extract_src_urls_handles_bare_href_and_url_list():
    assert extract_src_urls("fonts/x.ttf") == ["fonts/x.ttf"]
    got = extract_src_urls("url(fonts/x.woff2) format('woff2'), url('fonts/x.ttf')")
    assert got == ["fonts/x.woff2", "fonts/x.ttf"]


def test_emitted_name_is_deterministic_and_unique():
    taken = set()
    a = emitted_name("Merriweather", 700, True, taken)
    assert a == "merriweather-700i"
    b = emitted_name("Merriweather", 700, True, taken)  # collision
    assert b != a and b.startswith("merriweather-700i")


def _mk_manifest(mapping):
    return lambda href: mapping.get(href)


def test_faces_from_rules_emits_ttf_prefers_over_woff2():
    log = _Log()
    rules = [
        {
            "font-family": '"Foo"',
            "font-weight": "bold",
            "font-style": "italic",
            "src": "url(f.woff2) format('woff2'), url(f.ttf)",
        }
    ]
    manifest = _mk_manifest({"f.woff2": b"wOF2xx", "f.ttf": b"\x00\x01\x00\x00yy"})
    faces = faces_from_rules(rules, manifest, log)
    assert len(faces) == 1
    f = faces[0]
    assert f.css_family == "foo" and f.weight == 700 and f.italic is True
    assert f.data[:4] == b"\x00\x01\x00\x00"
    assert f.emitted_family == "foo-700i" and f.location == "resource/font0"


def test_faces_from_rules_skips_when_only_woff_available():
    log = _Log()
    rules = [{"font-family": "Foo", "src": "url(f.woff2)"}]
    manifest = _mk_manifest({"f.woff2": b"wOF2xx"})
    faces = faces_from_rules(rules, manifest, log)
    assert faces == []
    assert any("woff" in w.lower() or "skip" in w.lower() for w in log.warns)


def test_faces_from_rules_dedups_same_href():
    log = _Log()
    rules = [
        {"font-family": "Foo", "src": "url(f.ttf)"},
        {"font-family": "Foo", "src": "url(f.ttf)"},
    ]
    manifest = _mk_manifest({"f.ttf": b"\x00\x01\x00\x00yy"})
    faces = faces_from_rules(rules, manifest, log)
    assert len(faces) == 1


def test_faces_from_rules_skips_rule_without_family():
    faces = faces_from_rules(
        [{"src": "url(f.ttf)"}], _mk_manifest({"f.ttf": b"\x00\x01\x00\x00"}), _Log()
    )
    assert faces == []


def test_faces_from_rules_reads_cssfontfacerule_style_objects():
    class _Style:
        def __init__(self, d):
            self._d = d

        def getPropertyValue(self, k):
            return self._d.get(k, "")

    class _Rule:  # mimics css_parser CSSFontFaceRule: has .style, no .get
        def __init__(self, d):
            self.style = _Style(d)

    log = _Log()
    rules = [
        _Rule(
            {
                "font-family": '"Fam"',
                "font-weight": "bold",
                "font-style": "italic",
                "src": "url(fonts/f.ttf)",
            }
        )
    ]
    manifest = _mk_manifest({"fonts/f.ttf": b"\x00\x01\x00\x00yy"})
    faces = faces_from_rules(rules, manifest, log)
    assert len(faces) == 1
    assert (
        faces[0].css_family == "fam"
        and faces[0].weight == 700
        and faces[0].italic is True
    )
