import pytest
from kfxgen.font_table import (
    Face,
    FontTable,
    build_font_table,
    build_manifest_lookup,
    extract_src_urls,
    emitted_name,
    faces_from_rules,
    is_ttf_otf,
    normalize_family,
    _slug,
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


def _face(fam, w, it):
    return Face(
        css_family=fam,
        weight=w,
        italic=it,
        data=b"\x00\x01\x00\x00",
        emitted_family=f"{fam}-{w}{'i' if it else ''}",
        location="resource/x",
    )


def test_match_no_embedded_family_returns_none():
    t = FontTable([_face("foo", 400, False)])
    assert t.match(["bar", "serif"], bold=False, italic=False) == (None, False, False)
    assert t.match([], bold=True, italic=False) == (None, False, False)


def test_match_exact_regular():
    t = FontTable([_face("foo", 400, False)])
    assert t.match(["foo"], bold=False, italic=False) == ("foo-400", True, True)


def test_match_exact_bold_face():
    t = FontTable([_face("foo", 400, False), _face("foo", 700, False)])
    assert t.match(["foo"], bold=True, italic=False) == ("foo-700", True, True)


def test_match_bold_requested_only_regular_embedded_falls_back_synthetic():
    t = FontTable([_face("foo", 400, False)])
    # regular family applied, but weight not satisfied -> caller synthesizes bold
    assert t.match(["foo"], bold=True, italic=False) == ("foo-400", False, True)


def test_match_italic_requested_only_regular_embedded():
    t = FontTable([_face("foo", 400, False)])
    assert t.match(["foo"], bold=False, italic=True) == ("foo-400", True, False)


def test_match_exact_bold_italic():
    t = FontTable([_face("foo", 400, False), _face("foo", 700, True)])
    assert t.match(["foo"], bold=True, italic=True) == ("foo-700i", True, True)


def test_match_family_list_uses_first_embedded():
    t = FontTable([_face("bar", 400, False)])
    # "foo" not embedded, "bar" is -> use bar
    assert t.match(["foo", "bar"], bold=False, italic=False) == ("bar-400", True, True)


def test_match_family_with_only_bold_italic_face_no_double_bold():
    # Family embeds ONLY a bold-italic face; caller wants bold+upright.
    t = FontTable([_face("foo", 700, True)])
    fam, w_ok, s_ok = t.match(["foo"], bold=True, italic=False)
    assert fam == "foo-700i"
    assert w_ok is True  # face already bold -> do NOT faux-bold
    assert s_ok is False  # face is italic but upright requested -> flag mismatch


def test_match_family_with_only_bold_face_plain_text():
    # Family embeds ONLY a bold face; caller wants plain regular text.
    t = FontTable([_face("foo", 700, False)])
    fam, w_ok, s_ok = t.match(["foo"], bold=False, italic=False)
    assert fam == "foo-700"
    assert w_ok is False  # face is bold, regular requested -> mismatch signalled
    assert s_ok is True


# --- Task 4: build_font_table / build_manifest_lookup (Calibre integration) ---


class _Item:
    def __init__(self, href, data, media_type="application/font-sfnt"):
        self.href, self.data, self.media_type = href, data, media_type


class _Oeb:
    def __init__(self, items, spine):
        self.manifest = list(items)
        self.spine = spine


class _Stylizer:
    def __init__(self, rules):
        self.font_face_rules = rules


class _SpineItem:
    """A spine document item — carries parsed data + href like Calibre's."""

    def __init__(self, href="c.xhtml"):
        self.href, self.data = href, object()


def test_build_manifest_lookup_resolves_by_href_and_basename():
    oeb = _Oeb([_Item("OEBPS/fonts/x.ttf", b"\x00\x01\x00\x00zz")], spine=[])
    lookup = build_manifest_lookup(oeb)
    assert lookup("OEBPS/fonts/x.ttf")[:4] == b"\x00\x01\x00\x00"
    assert lookup("x.ttf")[:4] == b"\x00\x01\x00\x00"  # basename fallback
    assert lookup("nope.ttf") is None
    assert lookup("") is None


def test_build_font_table_aggregates_and_dedups_across_spine():
    font = _Item("fonts/f.ttf", b"\x00\x01\x00\x00yy")
    oeb = _Oeb([font], spine=[_SpineItem("c1.xhtml"), _SpineItem("c2.xhtml")])
    rules = [{"font-family": "Foo", "src": "url(fonts/f.ttf)"}]
    table = build_font_table(
        oeb, _Log(), stylizer_factory=lambda item: _Stylizer(rules)
    )
    # Same rule seen on both spine items -> deduped to one face.
    assert len(table.faces) == 1
    assert table.faces[0].css_family == "foo"


def test_build_font_table_empty_when_no_fonts():
    oeb = _Oeb([], spine=[object()])
    table = build_font_table(oeb, _Log(), stylizer_factory=lambda item: _Stylizer([]))
    assert table.faces == []


# --- #36: non-ASCII @font-face family names must yield distinct, stable slugs ---


def test_slug_ascii_names_unchanged():
    assert _slug("KoPubBatang") == "kopubbatang"
    assert _slug('"Merriweather"') == "merriweather"
    assert _slug("Times New Roman") == "times-new-roman"
    assert _slug("") == "font"


def test_slug_non_ascii_families_distinct_and_stable():
    a, b = _slug("명조"), _slug("고딕")  # Korean: Myeongjo (serif) / Gothic (sans)
    assert a != b  # was: both collapse to "font"
    assert a != "font" and b != "font"
    assert a == _slug("명조")  # deterministic across calls
    assert a.startswith("font-")


def test_slug_mixed_ascii_cjk_keeps_ascii_base_but_disambiguates():
    m1, m2 = _slug("KoPub 명조"), _slug("KoPub 고딕")
    assert m1 != m2
    assert m1.startswith("kopub-") and m2.startswith("kopub-")


def test_emitted_name_non_ascii_bases_are_distinct_not_counter_suffixed():
    taken = set()
    a = emitted_name("명조", 700, False, taken)
    b = emitted_name("고딕", 700, False, taken)
    assert a != b
    assert not b.endswith("-1")  # distinct by identity, not the dedup counter


# --- #41: single Stylizer builder; factory degrades cleanly without Calibre ---


def test_build_stylizer_and_factory_degrade_without_calibre():
    from kfxgen.font_table import _default_stylizer_factory

    class _Item:
        data = None
        href = "c.xhtml"

    log = _Log()
    make = _default_stylizer_factory(object(), log)
    # No Calibre in the test env -> build_stylizer raises -> factory returns None
    # and warns, rather than propagating.
    assert make(_Item()) is None
    assert log.warns


# --- #47: cap embedded font size (resource-exhaustion defense) ---


def test_faces_from_rules_skips_oversized_font():
    log = _Log()
    big = b"\x00\x01\x00\x00" + b"x" * 200  # valid TTF magic, 204 bytes
    rules = [{"font-family": "Foo", "src": "url(f.ttf)"}]
    manifest = _mk_manifest({"f.ttf": big})
    faces = faces_from_rules(rules, manifest, log, max_font_bytes=100)
    assert faces == []
    assert any(
        "large" in w.lower() or "exceed" in w.lower() or "cap" in w.lower()
        for w in log.warns
    )


def test_faces_from_rules_accepts_font_within_cap():
    log = _Log()
    ok = b"\x00\x01\x00\x00yy"
    faces = faces_from_rules(
        rules=[{"font-family": "Foo", "src": "url(f.ttf)"}],
        manifest_lookup=_mk_manifest({"f.ttf": ok}),
        log=log,
        max_font_bytes=1000,
    )
    assert len(faces) == 1


def test_default_max_font_bytes_allows_large_cjk_faces():
    # Must not reject legitimate large CJK fonts (tens of MB). Default cap must
    # sit well above a real CJK face.
    from kfxgen.font_table import MAX_FONT_BYTES

    assert MAX_FONT_BYTES >= 30 * 1024 * 1024
