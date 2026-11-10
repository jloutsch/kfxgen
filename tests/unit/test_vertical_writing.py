"""Vertical writing (#247).

Kindle Previewer 4.0.3 (probe P247, decoded) writes a Japanese book set
`writing-mode: vertical-rl`, with an rtl spine, as:

- `$538`: `$560: $559` (vertical-rl) and `$192: $376` (direction ltr: the
  pages turn right to left because the writing mode ends in -rl, as kfxlib
  reads it), and no `$477`;
- `$585`: `jp-reflow-language` 1 and `jpvertical-reflow-language` 6;
- a file set horizontal-tb in it: `$560: $557` on its section's page
  template (P247f);
- with an ltr spine, the book horizontal and each vertical section's page
  template `$560: $559`, with `yj_mixed_writing_mode` 1 and
  `jpvertical-reflow-language` 4 (P247c);
- `text-orientation: upright` and `sideways` as a run's `$706`: `$779`
  and `$778` (P247 V03, V05).

The book's writing mode is set on <html> (P247a), on <body> (P247d) or as
`-epub-writing-mode` (P247e), which calibre reads as `writing-mode`.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, load_fragments, val
from tests.unit.test_floats import _resolver

XHTML = "http://www.w3.org/1999/xhtml"


def _book(tmp_path, chapters, **kw):
    out = tmp_path / "v.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(out), **kw
    )
    return load_fragments(out)


def _chapter(text, mode=None):
    block = {"text": text, "spans": []}
    if mode:
        block["writing_mode"] = mode
    return {"title": text, "text": text, "blocks": [block]}


def _doc(frags):
    (frag,) = by_type(frags, "$538")
    return val(frag)


def _features(frags):
    (frag,) = by_type(frags, "$585")
    return {
        str(f["$492"]): int(f["$589"]["version"]["$587"]) for f in val(frag)["$590"]
    }


def _section_modes(frags):
    """Each section's page template `$560`, or None, in reading order."""
    order = [str(n) for n in _doc(frags)["$169"][0]["$170"]]
    by_name = {str(f.fid): val(f) for f in by_type(frags, "$260")}
    out = []
    for name in order:
        (template,) = by_name[name]["$141"]
        mode = template.get("$560")
        out.append(None if mode is None else str(mode))
    return out


@pytest.mark.unit
def test_a_vertical_book_writes_its_writing_mode_on_the_document(tmp_path):
    frags = _book(
        tmp_path,
        [_chapter("縦書き", "vertical-rl"), _chapter("二章", "vertical-rl")],
        rtl=True,
        writing_mode="vertical-rl",
    )
    doc = _doc(frags)
    assert str(doc["$560"]) == "$559"
    # Direction ltr: Previewer writes $376, not the rtl spine's $375.
    assert str(doc["$192"]) == "$376"
    assert "$477" not in doc
    features = _features(frags)
    assert features["jp-reflow-language"] == 1
    assert features["jpvertical-reflow-language"] == 6
    assert "yj_mixed_writing_mode" not in features
    assert _section_modes(frags) == [None, None]


@pytest.mark.unit
def test_a_horizontal_book_is_unchanged(tmp_path):
    frags = _book(tmp_path, [_chapter("One"), _chapter("Two")], rtl=True)
    doc = _doc(frags)
    assert str(doc["$560"]) == "$557"
    assert str(doc["$192"]) == "$375"
    assert str(doc["$477"]) == "$56"
    features = _features(frags)
    assert not {"jp-reflow-language", "jpvertical-reflow-language"} & set(features)
    assert _section_modes(frags) == [None, None]


@pytest.mark.unit
def test_a_horizontal_file_in_a_vertical_book_is_set_on_its_section(tmp_path):
    # P247f: the second file horizontal-tb.
    frags = _book(
        tmp_path,
        [_chapter("縦書き", "vertical-rl"), _chapter("奥付", "horizontal-tb")],
        rtl=True,
        writing_mode="vertical-rl",
    )
    assert _section_modes(frags) == [None, "$557"]
    assert "yj_mixed_writing_mode" not in _features(frags)


@pytest.mark.unit
def test_a_vertical_file_in_a_horizontal_book_is_set_on_its_section(tmp_path):
    # P247c: vertical CSS with an ltr spine.
    frags = _book(
        tmp_path,
        [_chapter("縦書き", "vertical-rl"), _chapter("二章", "vertical-rl")],
    )
    doc = _doc(frags)
    assert str(doc["$560"]) == "$557" and str(doc["$192"]) == "$376"
    assert _section_modes(frags) == ["$559", "$559"]
    features = _features(frags)
    assert features["yj_mixed_writing_mode"] == 1
    assert features["jpvertical-reflow-language"] == 4
    assert features["jp-reflow-language"] == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("items", "rtl", "mode"),
    [
        ([("vertical-rl", 100)], True, "vertical-rl"),
        ([("vertical-rl", 100)], False, "horizontal-tb"),  # P247c
        ([("vertical-rl", 100), ("horizontal-tb", 10)], True, "vertical-rl"),
        ([("vertical-rl", 10), ("horizontal-tb", 100)], True, "horizontal-tb"),
        ([("horizontal-tb", 100)], True, "horizontal-tb"),
        ([(None, 100)], True, "horizontal-tb"),
    ],
)
def test_the_book_is_vertical_when_its_spine_is_rtl_and_most_text_is(items, rtl, mode):
    spine = [{"text": "x" * n, "writing_mode": m} for m, n in items]
    assert _conv._book_writing_mode(spine, rtl) == mode


@pytest.mark.unit
@pytest.mark.parametrize(
    ("css", "mode"),
    [
        ({"writing-mode": "vertical-rl"}, "vertical-rl"),
        ({"writing-mode": "VERTICAL-RL"}, "vertical-rl"),
        ({"writing-mode": "horizontal-tb"}, "horizontal-tb"),
        ({}, None),
    ],
)
def test_a_files_writing_mode_is_read_from_its_body(css, mode):
    root = etree.fromstring(
        f'<html xmlns="{XHTML}"><body class="b"><p>x</p></body></html>'
    )
    assert _conv._file_writing_mode(root, _resolver({"b": css})) == mode


# --- text-orientation on a run -------------------------------------------------


def _runs(tmp_path, body, css):
    xhtml = f'<html xmlns="{XHTML}"><body>{body}</body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=_resolver(css),
        native_tables=True,
        base_href="c.xhtml",
    )
    frags = _book(
        tmp_path,
        [{"title": "T", "text": blocks[0]["text"], "blocks": blocks}],
        rtl=True,
        writing_mode="vertical-rl",
    )
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []
    for f in by_type(frags, "$259"):
        for e in val(f)["$146"]:
            for r in e.get("$142") or ():
                st = styles.get(str(r.get("$157")), {})
                out.append((int(r["$143"]), int(r["$144"]), str(st.get("$706"))))
    return out


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "symbol"), [("upright", "$779"), ("sideways", "$778")]
)
def test_text_orientation_is_written_on_its_run(tmp_path, value, symbol):
    # P247 V03, V05.
    css = {"o": {"text-orientation": value}}
    runs = _runs(tmp_path, '<p>英字を<span class="o">ABC</span>と書く。</p>', css)
    assert (3, 3, symbol) in runs


@pytest.mark.unit
def test_latin_with_no_orientation_gets_no_run(tmp_path):
    # P247 V04: nothing.
    assert _runs(tmp_path, "<p>英字をそのまま Kindle と書く。</p>", {}) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("writing_mode", "declared"), [("vertical-rl", False), ("horizontal-tb", True)]
)
def test_a_vertical_book_declares_no_yj_ruby(tmp_path, writing_mode, declared):
    # P247a V06: the ruby is written, and no yj_ruby is declared; P247c,
    # horizontal with vertical sections, declares it.
    xhtml = f'<html xmlns="{XHTML}"><body><p><ruby>漢字<rt>かんじ</rt></ruby>です。</p></body></html>'
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()),
        style_resolver=_resolver({}),
        base_href="c.xhtml",
    )
    for b in blocks:
        b["writing_mode"] = "vertical-rl"
    frags = _book(
        tmp_path,
        [{"title": "T", "text": blocks[0]["text"], "blocks": blocks}],
        rtl=True,
        writing_mode=writing_mode,
    )
    assert ("yj_ruby" in _features(frags)) is declared
    assert by_type(frags, "$756"), "the ruby is written"
