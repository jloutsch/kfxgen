"""Line breaks and preformatted text (#202).

KFX keeps a paragraph's text preformatted: a line break inside a paragraph is a
newline character, and spaces are literal (KFX Input turns them back into <br>
and non-breaking spaces). kfxgen collapsed every run of whitespace, so <pre>
code lost its lines and indentation, and <br> either became a space or, with
no whitespace beside it, fused two words ("line oneline two").
"""

import sys
from pathlib import Path

import pytest
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kfxgen.converter import extract_blocks_from_html  # noqa: E402
from kfxgen.inline_style import FLAG_BOLD, FLAG_PRE, FLAG_PRE_LINE  # noqa: E402
from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.fixtures.epub_builder import EpubBuilder  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

pytestmark = [pytest.mark.tier1, pytest.mark.unit]

NB = " "


def _blocks(body, resolver=None):
    doc = etree.fromstring(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"{body}</body></html>".encode()
    )
    return extract_blocks_from_html(doc, style_resolver=resolver)


def _texts(body, resolver=None):
    return [b["text"] for b in _blocks(body, resolver)]


# ── <br> ─────────────────────────────────────────────────────────────────────


def test_br_between_words_is_a_line_break_not_a_fusion():
    assert _texts("<p>line one<br/>line two</p>") == ["line one\nline two"]


def test_whitespace_around_br_does_not_survive():
    assert _texts("<p>a <br/>\n   b</p>") == ["a\nb"]


def test_br_at_the_edges_of_a_block_adds_nothing():
    assert _texts("<p><br/>a<br/></p>") == ["a"]


def test_two_brs_leave_a_blank_line():
    assert _texts("<p>a<br/><br/>b</p>") == ["a\n\nb"]


def test_spans_stay_aligned_across_a_br():
    [block] = _blocks("<p><b>bold<br/>more</b> end</p>")
    assert block["text"] == "bold\nmore end"
    assert [(s, n) for s, n, f in block["spans"] if FLAG_BOLD in f] == [(0, 9)]


def test_an_anchor_after_a_br_points_at_the_second_line():
    from kfxgen.converter import _walk_inline
    from kfxgen.inline_style import normalize_runs_with_anchors

    doc = etree.fromstring(
        b'<p xmlns="http://www.w3.org/1999/xhtml">ab<br/><span id="x">cd</span></p>'
    )
    text, _spans, offsets = normalize_runs_with_anchors(_walk_inline(doc))
    assert text == "ab\ncd"
    assert offsets["x"] == 3


def test_a_list_marker_lands_on_the_first_line():
    assert _texts("<ol><li>a<br/>b</li></ol>") == ["1. a\nb"]


# ── ordinary text is unchanged ───────────────────────────────────────────────


def test_newlines_and_runs_of_spaces_in_ordinary_text_still_collapse():
    """Adversarial: the fix must not touch normal flow. Source line breaks
    are just whitespace there, as in any browser."""
    assert _texts("<p>one\n   two   three\n</p>") == ["one two three"]


def test_an_ordinary_block_is_not_marked_preformatted():
    [block] = _blocks("<p>x</p>")
    assert not block.get("preformatted")


# ── <pre> ────────────────────────────────────────────────────────────────────


def test_pre_keeps_its_lines_and_indentation():
    [block] = _blocks("<pre>def f():\n    return 1\n</pre>")
    assert block["text"] == f"def f():\n{NB * 4}return 1"
    assert block["preformatted"] is True


def test_the_newline_right_after_pre_is_dropped_as_html_does():
    assert _texts("<pre>\nx\n  y</pre>") == [f"x\n{NB * 2}y"]


def test_a_run_of_spaces_inside_a_line_is_kept():
    assert _texts("<pre>a  b c</pre>") == [f"a{NB * 2}b c"]


def test_a_tab_expands_to_the_next_multiple_of_eight():
    assert _texts("<pre>\tx\nab\ty</pre>") == [f"{NB * 8}x\nab{NB * 6}y"]


def test_blank_lines_inside_pre_are_kept():
    assert _texts("<pre>a\n\nb</pre>") == ["a\n\nb"]


def test_inline_markup_inside_pre_keeps_its_spans():
    [block] = _blocks("<pre><b>kw</b> x\n  y</pre>")
    assert block["text"] == f"kw x\n{NB * 2}y"
    assert [(s, n) for s, n, f in block["spans"] if FLAG_BOLD in f] == [(0, 2)]


def test_pre_is_a_block_of_its_own():
    assert _texts("<p>before</p><pre>code</pre><p>after</p>") == [
        "before",
        "code",
        "after",
    ]


def test_code_inside_pre_is_still_preformatted():
    assert _texts("<pre><code>a\n  b</code></pre>") == [f"a\n{NB * 2}b"]


# ── CSS white-space ──────────────────────────────────────────────────────────


def _ws(value):
    return lambda elem: {"white-space": value}


@pytest.mark.parametrize("mode", ["pre", "pre-wrap", "break-spaces"])
def test_css_white_space_that_preserves_spaces(mode):
    assert _texts("<p>x\n  y</p>", _ws(mode)) == [f"x\n{NB * 2}y"]


def test_css_pre_line_keeps_newlines_but_collapses_spaces():
    assert _texts("<p>x\n   y   z</p>", _ws("pre-line")) == ["x\ny z"]


def test_css_normal_collapses_even_inside_a_pre_tag_resolver_says_normal():
    """The computed style wins when there is one: a <pre> restyled to
    `white-space: normal` collapses, as a browser shows it."""
    assert _texts("<pre>x\n  y</pre>", _ws("normal")) == ["x y"]


# ── end to end ───────────────────────────────────────────────────────────────


def test_indentation_survives_to_the_container(tmp_path):
    """The generator strips each paragraph; a preformatted one must keep its
    first line's indentation, which is exactly what `strip()` would eat."""
    body = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>C</title></head>'
        b"<body><h1>C</h1><pre>    first\n  second</pre>"
        b"<p>poem line<br/>next line</p></body></html>"
    )
    epub = EpubBuilder().set_metadata(title="Pre", author="T").add_chapter("C", body)
    epub = epub.build(tmp_path, "pre")
    out = tmp_path / "pre.kfx"
    from kfxgen import converter

    class _Q:
        def __getattr__(self, _):
            return lambda *a, **k: None

    converter.convert_oeb_to_kfx(EpubAsOeb(str(epub)), str(out), None, _Q())
    texts = [
        str(t)
        for f in by_type(load_fragments(out), "$145")
        for k, x in val(f).items()
        if isinstance(x, list)
        for t in x
    ]
    assert f"{NB * 4}first\n{NB * 2}second" in texts
    assert "poem line\nnext line" in texts


def test_the_white_space_mode_never_becomes_a_style_span():
    """The mode rides on segment flags only to reach the normalizer. A plain
    code block must come out with no spans at all, and a styled one with
    spans that carry style flags only."""
    assert _blocks("<pre>a\n  b</pre>")[0]["spans"] == []
    assert _blocks("<p>x\n y</p>", _ws("pre-line"))[0]["spans"] == []
    [block] = _blocks("<pre><i>it</i> x</pre>")
    assert all(not (f & {FLAG_PRE, FLAG_PRE_LINE}) for _s, _n, f in block["spans"])
