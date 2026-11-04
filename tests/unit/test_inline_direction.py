"""Inline direction: `<span dir>`, `<bdi>`, `<bdo>` and `lang` on a run (#710).

What Kindle Previewer 4.0.3 writes, read from its own output of #710's
probes I710 and K710 (2026-11-04):

- An inline element with a `dir`, a `lang`, or a `<bdi>`/`<bdo>` gets one
  run over its whole text carrying its own `$682`/`$674`/`$10`, even when
  they equal the paragraph's. Nothing comes from its parent.
- `dir` gives isolate (`$676`); `<bdi>` isolate, its direction from its text
  when it has no `dir`; `<bdo>` isolate-override (`$678`), with no `$682`
  when it has no `dir`. `dir="auto"` takes the text's direction.
- Runs inside it are layered on its run with their own keys only; an
  element around it is split, and its run carries that element's styles.

These run through the test shim, which reads no CSS: CSS `direction` and
`unicode-bidi` on a span are checked through real calibre in
tests/integration/test_calibre_direction.py.
"""

import pytest

from tests._kfx_introspect import by_type, val
from tests.unit.test_direction import AR, LAB, _convert

AR3 = "كلمات عربية"
KEYS = ("$10", "$682", "$674")
ISO_LTR = {"$682": "$376", "$674": "$676"}
ISO_RTL = {"$682": "$375", "$674": "$676"}


def _runs(frags):
    """{paragraph label: [(run text, {keys}), ...]} in file order, for runs
    in file order: their keys, `$12` (italic) and whether they link."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    found = {}

    def visit(nodes):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            ref = e.get("$145")
            if hasattr(ref, "get"):
                text = str(texts[str(ref["name"])][int(ref["$403"])])
                runs = []
                for r in e.get("$142") or ():
                    r = getattr(r, "value", r)
                    s = dict(styles.get(str(r.get("$157")), {}))
                    s.update(r)
                    a, n = int(s["$143"]), int(s["$144"])
                    keys = {k: str(s[k]) for k in KEYS if k in s}
                    for k in ("$12", "$13"):
                        if k in s:
                            keys[k] = str(s[k])
                    if "$179" in s:
                        keys["link"] = True
                    runs.append((text[a : a + n], keys))
                m = LAB.search(text)
                if m:
                    found[m.group(0)] = runs
            visit(e.get("$146") or ())

    for sl in by_type(frags, "$259"):
        visit(val(sl)["$146"])
    return found


def _book(tmp_path, body, rtl=True):
    attrs = ' dir="rtl" lang="ar"' if rtl else ' lang="en"'
    return _runs(_convert(tmp_path, body, html_attrs=attrs))


def _direction_runs(runs):
    return [
        (t, {k: v for k, v in keys.items() if k in KEYS})
        for t, keys in runs
        if any(k in keys for k in KEYS)
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("markup", "text", "keys"),
    [
        # I01, I02, I03, I13, I04, I12.
        ("<span dir='ltr'>LTRSPAN words</span>", "LTRSPAN words", ISO_LTR),
        ("<bdi>BDIENG words</bdi>", "BDIENG words", ISO_LTR),
        (
            "<bdo dir='rtl'>BDORTL words</bdo>",
            "BDORTL words",
            {"$682": "$375", "$674": "$678"},
        ),
        ("<bdo>BDONODIR words</bdo>", "BDONODIR words", {"$674": "$678"}),
        (
            f"<bdo dir='ltr'>{AR3} BDOLTR</bdo>",
            f"{AR3} BDOLTR",
            {"$682": "$376", "$674": "$678"},
        ),
        ("<span dir='auto'>AUTOSPAN words</span>", "AUTOSPAN words", ISO_LTR),
        # I10: the paragraph's own direction is written too.
        (f"<span dir='rtl'>{AR3} SAMEDIR</span>", f"{AR3} SAMEDIR", ISO_RTL),
        # I11, K06: <bdi> without dir takes its text's direction, ltr with no letter.
        (f"<bdi>{AR3} BDIAR</bdi>", f"{AR3} BDIAR", ISO_RTL),
        ("<bdi>1234</bdi>", "1234", ISO_LTR),
        # K04: its own dir wins over its text.
        (f"<bdi dir='ltr'>{AR3} BDIDIR</bdi>", f"{AR3} BDIDIR", ISO_LTR),
        # I05, I14, I23: lang, lower-cased, written when the paragraph's too.
        ("<span lang='en'>LANGEN words</span>", "LANGEN words", {"$10": "en"}),
        (
            "<span dir='ltr' lang='en-GB'>DIRLANG words</span>",
            "DIRLANG words",
            {**ISO_LTR, "$10": "en-gb"},
        ),
        (f"<span lang='ar'>{AR3} LANGSAME</span>", f"{AR3} LANGSAME", {"$10": "ar"}),
        # K15: an empty lang is none.
        ("<span dir='ltr' lang=''>EMPTYLANG</span>", "EMPTYLANG", ISO_LTR),
        # K16: the run takes the trailing space the normalizer keeps.
        ("<span dir='ltr'> SPACED </span>", "SPACED ", ISO_LTR),
    ],
)
def test_an_inline_elements_run_carries_its_own_keys(tmp_path, markup, text, keys):
    runs = _book(tmp_path, f"<p>R01 {AR} {markup} {AR3}</p>")["R01"]
    assert _direction_runs(runs) == [(text, keys)]


@pytest.mark.unit
def test_in_a_left_to_right_paragraph(tmp_path):
    body = (
        f"<p>J01 English <bdi>{AR3} BDIAREN</bdi> more.</p>"
        f"<p>J02 English <span dir='rtl'>{AR3} RTLSPAN</span> more.</p>"
        f"<p>J03 English <span lang='EN'>LANGUPPER</span> more.</p>"
        f"<p>J04 English <span xml:lang='ar'>{AR3} XMLLANG</span> more.</p>"
        f"<p>J05 English <span dir='auto'>{AR3} AUTOAR</span> more.</p>"
    )
    found = _book(tmp_path, body, rtl=False)
    assert _direction_runs(found["J01"]) == [(f"{AR3} BDIAREN", ISO_RTL)]
    assert _direction_runs(found["J02"]) == [(f"{AR3} RTLSPAN", ISO_RTL)]
    assert _direction_runs(found["J03"]) == [("LANGUPPER", {"$10": "en"})]
    assert _direction_runs(found["J04"]) == [(f"{AR3} XMLLANG", {"$10": "ar"})]
    # K01: dir="auto" reads the text, as <bdi> does.
    assert _direction_runs(found["J05"]) == [(f"{AR3} AUTOAR", ISO_RTL)]


@pytest.mark.unit
def test_a_span_with_no_dir_or_lang_writes_no_keys(tmp_path):
    runs = _book(tmp_path, f"<p>R01 {AR} <span>PLAIN</span> <i>ITAL</i> {AR3}</p>")[
        "R01"
    ]
    assert _direction_runs(runs) == []


@pytest.mark.unit
def test_nested_direction_runs_are_layered(tmp_path):
    # I09: the outer run covers the inner one, which carries only its own keys.
    runs = _book(
        tmp_path,
        f"<p>R01 {AR} <span dir='ltr'>OUTER <span dir='rtl'>{AR3} INNER</span> TAIL</span> {AR3}</p>",
    )["R01"]
    assert _direction_runs(runs) == [
        (f"OUTER {AR3} INNER TAIL", ISO_LTR),
        (f"{AR3} INNER", ISO_RTL),
    ]


@pytest.mark.unit
def test_emphasis_inside_a_direction_run_is_layered_on_it(tmp_path):
    # I21: one ltr run over the span; the italic run inside has no keys.
    runs = _book(
        tmp_path, f"<p>R01 {AR} <span dir='ltr'><i>ITALIC</i> words</span> {AR3}</p>"
    )["R01"]
    assert runs[0] == ("ITALIC words", ISO_LTR)
    assert runs[1][0] == "ITALIC" and "$12" in runs[1][1]
    assert not any(k in runs[1][1] for k in KEYS)
    assert len(runs) == 2


@pytest.mark.unit
def test_an_element_around_a_direction_run_is_split_around_it(tmp_path):
    # K08: <b>BOLDOUT <span dir>INBOLD</span> tail</b>: three runs, the
    # middle one bold with the direction keys.
    found = _runs(
        _convert(
            tmp_path,
            f"<p>R01 {AR} <b>BOLDOUT <span dir='ltr'>INBOLD</span> tail</b> {AR3}</p>",
            html_attrs=' dir="rtl" lang="ar"',
        )
    )
    assert [t for t, _ in found["R01"]] == ["BOLDOUT ", "INBOLD", " tail"]
    assert _direction_runs(found["R01"]) == [("INBOLD", ISO_LTR)]


@pytest.mark.unit
def test_links_and_direction(tmp_path):
    body = (
        f"<p>R01 {AR} <span dir='ltr'>SPANOUT <a href='#t'>INLINK</a> tail</span> {AR3}</p>"
        f"<p>R02 {AR} <a href='#t' dir='ltr'><i>LINKITAL</i> tail</a> {AR3}</p>"
        "<p id='t'>R03 target</p>"
    )
    found = _book(tmp_path, body)
    # K11: a link inside a direction run is its own run, with no keys.
    assert found["R01"] == [
        ("SPANOUT INLINK tail", ISO_LTR),
        ("INLINK", {"link": True}),
    ]
    # K12: a link with a dir: one run with the link and the keys, the
    # italic layered on it.
    assert found["R02"][0] == ("LINKITAL tail", {**ISO_LTR, "link": True})
    assert found["R02"][1][0] == "LINKITAL" and "link" not in found["R02"][1][1]


@pytest.mark.unit
def test_adjacent_spans_stay_two_runs(tmp_path):
    # K14.
    runs = _book(
        tmp_path,
        f"<p>R01 {AR} <span dir='ltr'>TWO</span><span dir='ltr'>ADJACENT</span> {AR3}</p>",
    )["R01"]
    assert _direction_runs(runs) == [("TWO", ISO_LTR), ("ADJACENT", ISO_LTR)]


@pytest.mark.unit
def test_headings_list_items_and_cells_get_the_run_too(tmp_path):
    # I24-I26.
    body = (
        f"<h2>H01 {AR} <span dir='ltr'>HEADDIR words</span></h2>"
        f"<ul><li>L01 {AR} <span dir='ltr'>LISTDIR words</span></li></ul>"
        f"<table><tr><td>T01 {AR} <span dir='ltr'>CELLDIR words</span></td></tr></table>"
    )
    found = _book(tmp_path, body)
    for label, text in (
        ("H01", "HEADDIR words"),
        ("L01", "LISTDIR words"),
        ("T01", "CELLDIR words"),
    ):
        runs = found[label]
        assert _direction_runs(runs) == [(text, ISO_LTR)], label


@pytest.mark.unit
def test_a_direction_only_run_carries_only_its_keys(tmp_path):
    # Previewer's run style for <span dir> holds the direction keys and no
    # weight or style, which would override a bold or italic paragraph's.
    runs = _book(
        tmp_path, f"<p>R01 {AR} <span dir='ltr'>LTRSPAN words</span> {AR3}</p>"
    )["R01"]
    assert runs == [("LTRSPAN words", ISO_LTR)]


@pytest.mark.unit
def test_an_inner_direction_run_carries_only_its_own_keys(tmp_path):
    # K40: the outer run takes the bold around it; the inner one, inside
    # the outer, doesn't repeat it.
    runs = _book(
        tmp_path,
        f"<p>R01 {AR} <b>BO <span dir='ltr'>XOUT <span dir='rtl'>{AR3} YIN</span> ZEND</span></b> {AR3}</p>",
    )["R01"]
    assert [t for t, _ in runs] == ["BO ", f"XOUT {AR3} YIN ZEND", f"{AR3} YIN"]
    assert "$13" in runs[1][1] and runs[2][1] == ISO_RTL


@pytest.mark.unit
def test_the_outer_run_comes_first_when_both_start_together(tmp_path):
    # K41: written outer first, so the inner one is layered on it.
    runs = _book(
        tmp_path,
        f"<p>R01 {AR} <span dir='ltr'><span dir='rtl'>{AR3} SAMESTART</span> after</span> {AR3}</p>",
    )["R01"]
    assert runs == [(f"{AR3} SAMESTART after", ISO_LTR), (f"{AR3} SAMESTART", ISO_RTL)]


@pytest.mark.unit
def test_a_language_alone_is_split_around_what_is_inside_it(tmp_path):
    # #710's P01, P03, P05, P06: a lang with no direction is a run style,
    # as bold is: the elements inside take it, an inner lang replaces it,
    # and a direction run inside carries it.
    body = (
        "<p>P01 Deutsch <span lang='fr'>AA <i>BB</i> CC</span> Ende.</p>"
        "<p>P03 Deutsch <span lang='fr'>AA <span lang='la'>BB</span> CC</span> Ende.</p>"
        f"<p>P05 Deutsch <span lang='fr'>AA <span dir='rtl'>{AR3}</span> CC</span> Ende.</p>"
        "<p>P02 Deutsch <span lang='fr' dir='ltr'>AA <i>BB</i> CC</span> Ende.</p>"
    )
    found = _runs(_convert(tmp_path, body, html_attrs=' lang="de"', language="de"))
    fr = {"$10": "fr"}
    assert _direction_runs(found["P01"]) == [("AA ", fr), ("BB", fr), (" CC", fr)]
    assert "$12" in found["P01"][1][1]
    assert _direction_runs(found["P03"]) == [
        ("AA ", fr),
        ("BB", {"$10": "la"}),
        (" CC", fr),
    ]
    assert _direction_runs(found["P05"]) == [
        ("AA ", fr),
        (AR3, {**fr, **ISO_RTL}),
        (" CC", fr),
    ]
    # With a direction it is one run over the whole span (P02).
    assert _direction_runs(found["P02"]) == [("AA BB CC", {**fr, **ISO_LTR})]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outer", "inner"),
    [
        ("fr", "la"),
        ("la", "fr"),
        ("it", "en"),
        ("en", "it"),
        ("de", "grc"),
        ("grc", "de"),
    ],
)
def test_an_inner_lang_replaces_the_outer(tmp_path, outer, inner):
    # P03. Several pairs: with both flags left on the run, which one was
    # read would follow the set's hash order.
    body = f"<p>P03 words <span lang='{outer}'>AA <span lang='{inner}'>BB</span> CC</span> end.</p>"
    found = _runs(_convert(tmp_path, body, html_attrs=' lang="ar"'))
    assert _direction_runs(found["P03"]) == [
        ("AA ", {"$10": outer}),
        ("BB", {"$10": inner}),
        (" CC", {"$10": outer}),
    ]
