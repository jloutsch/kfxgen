"""
Property-based fuzz for HTML text extraction (#124 / G2).

`extract_text_from_html` and its recursive helper `_walk_paragraph_with_imgs`
walk attacker-controlled OEB element trees (the parsed XHTML of an EPUB spine
item). The existing tests cover hand-picked shapes; nothing fuzzed malformed or
adversarial trees.

This module generates varied element trees and asserts the walk is robust:
it returns a `str`, terminates, and never raises an uncontrolled crash class
(RecursionError / MemoryError) or an unexpected uncaught exception.

Recursion bound (why this is safe, documented not fixed): the real attack
surface is *parsed* XHTML bytes, and lxml caps document nesting at 256
(`XMLSyntaxError: Excessive depth in document`) at parse time — before
`extract_text_from_html` ever runs. 256 frames is well under Python's 1000
recursion limit, so `_walk_paragraph_with_imgs` recursion cannot be driven to a
RecursionError by attacker bytes. This test builds trees within that realistic
bound. (A directly-constructed >256-deep tree is not reachable from EPUB input,
so it is out of the threat model.)

Tier-1 + unit (per #42 oracle hierarchy): in-process, gates every PR.
"""

from __future__ import annotations

import os
import sys

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.converter import extract_text_from_html  # noqa: E402

_XHTML = "{http://www.w3.org/1999/xhtml}"

# Tags the extractor branches on: block-level (paragraph boundaries), inline
# (recursed through), and img (token path). Mixing all three exercises every
# branch of extract_text_from_html / _walk_paragraph_with_imgs.
_BLOCK_TAGS = ["p", "div", "h1", "h2", "blockquote", "li", "section", "figure"]
_INLINE_TAGS = ["span", "b", "i", "em", "a", "strong"]

# Text that is valid in XML element bodies: lxml rejects NUL and other invalid
# XML control chars, so exclude surrogates and C0 controls except tab/newline.
_safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        min_codepoint=0x20,
        max_codepoint=0x2FFF,
    ),
    max_size=30,
)

_UNCONTROLLED = (RecursionError, MemoryError)


def _node_to_element(node):
    """Render a generated (kind, text, tail, children) node into an lxml
    element. `kind` is either a tag name or ('img', src, alt)."""
    kind, text, tail, children = node
    if isinstance(kind, tuple):  # img leaf
        _, src, alt = kind
        el = etree.Element(_XHTML + "img")
        el.set("src", src)
        el.set("alt", alt)
    else:
        el = etree.Element(_XHTML + kind)
        if text:
            el.text = text
        for child in children:
            el.append(_node_to_element(child))
    if tail:
        el.tail = tail
    return el


def _tree_strategy():
    """Recursive element-tree strategy. `max_leaves` keeps depth far under the
    lxml 256 bound while still nesting enough to exercise the recursive walk."""
    img_leaf = st.builds(
        lambda src, alt: (("img", src, alt), "", "", []),
        _safe_text,
        _safe_text,
    )
    text_leaf = st.builds(
        lambda tag, text, tail: (tag, text, tail, []),
        st.sampled_from(_BLOCK_TAGS + _INLINE_TAGS),
        _safe_text,
        _safe_text,
    )
    leaf = st.one_of(img_leaf, text_leaf)

    def extend(children):
        return st.builds(
            lambda tag, text, tail, kids: (tag, text, tail, kids),
            st.sampled_from(_BLOCK_TAGS + _INLINE_TAGS),
            _safe_text,
            _safe_text,
            st.lists(children, max_size=4),
        )

    return st.recursive(leaf, extend, max_leaves=40)


def _wrap_body(*children) -> etree._Element:
    html = etree.Element(_XHTML + "html")
    body = etree.SubElement(html, _XHTML + "body")
    for child in children:
        body.append(child)
    return html


_FUZZ = settings(
    max_examples=200,
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@pytest.mark.tier1
@pytest.mark.unit
class TestHtmlExtractionFuzz:
    @_FUZZ
    @given(node=_tree_strategy())
    def test_returns_str_without_crash(self, node):
        """Any generated tree extracts to a str, terminates, and never raises a
        crash-class or unexpected exception."""
        root = _wrap_body(_node_to_element(node))
        try:
            out = extract_text_from_html(root)
        except _UNCONTROLLED:
            raise
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"extract_text_from_html raised uncaught {exc!r}")
        assert isinstance(out, str)

    @_FUZZ
    @given(nodes=st.lists(_tree_strategy(), min_size=0, max_size=6))
    def test_multiple_top_level_blocks(self, nodes):
        root = _wrap_body(*[_node_to_element(n) for n in nodes])
        try:
            out = extract_text_from_html(root)
        except _UNCONTROLLED:
            raise
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"extract_text_from_html raised uncaught {exc!r}")
        assert isinstance(out, str)


@pytest.mark.tier1
@pytest.mark.unit
class TestHtmlExtractionEdgeCases:
    """Targeted shapes the fuzz won't reliably hit, plus the documented
    recursion bound."""

    def test_empty_body(self):
        assert extract_text_from_html(_wrap_body()) == ""

    def test_no_body_element_falls_back(self):
        # Root with no <body>: the extractor falls back to the element itself.
        el = etree.Element(_XHTML + "div")
        el.text = "loose text"
        out = extract_text_from_html(el)
        assert isinstance(out, str)
        assert "loose text" in out

    def test_bare_img_under_body(self):
        img = etree.Element(_XHTML + "img")
        img.set("src", "cover.jpg")
        img.set("alt", "the cover")
        out = extract_text_from_html(_wrap_body(img))
        # Emitted as an IMG token containing the src.
        assert "cover.jpg" in out

    def test_block_with_block_child_is_skipped_not_doubled(self):
        outer = etree.Element(_XHTML + "div")
        inner = etree.SubElement(outer, _XHTML + "p")
        inner.text = "only once"
        out = extract_text_from_html(_wrap_body(outer))
        assert out.count("only once") == 1

    def test_lxml_caps_parse_depth_at_256(self):
        """Pins the bound this module relies on: lxml refuses to *parse* a
        document nested past 256, so attacker bytes can never hand
        extract_text_from_html a tree deep enough to RecursionError."""
        deep = "<span>" * 400 + "x" + "</span>" * 400
        xml = (
            f'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>{deep}'
            "</p></body></html>"
        ).encode()
        with pytest.raises(etree.XMLSyntaxError, match="[Dd]epth"):
            etree.fromstring(xml)

    def test_realistic_max_depth_does_not_recurse_error(self):
        """A tree at the realistic parse-bound ceiling (well under 256) extracts
        without a RecursionError."""
        xml = (
            f'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>'
            f"{'<span>' * 200}deep{'</span>' * 200}</p></body></html>"
        ).encode()
        root = etree.fromstring(xml)
        out = extract_text_from_html(root)
        assert "deep" in out
