"""Pop-up footnote marks, as Kindle Previewer writes them (#445).

Previewer 4.0.3 marks a link with `epub:type="noteref"` whose target is, or
sits inside, an `epub:type="footnote"` element: the link's span gets
`$616: $617` and the entry the link lands on gets `$615: $281`. An endnote,
a `role` note, a plain link to a footnote and a noteref to a plain paragraph
get neither. Measured on eleven shapes (#445).
"""

from __future__ import annotations

from unittest import mock

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.inline_style import NOTEREF_FLAG, make_link_flag
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

pytestmark = [pytest.mark.unit]

X = (
    '<html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops"><body>{}</body></html>'
)


def _flags_of_links(body):
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(X.format(body)), base_href="c.xhtml"
    )
    return [
        flags
        for b in blocks
        for _s, _length, flags in b.get("spans") or ()
        if any(isinstance(f, tuple) and f[0] == "link" for f in flags)
    ]


def test_a_noteref_link_is_flagged_and_a_plain_link_is_not():
    (noteref,) = _flags_of_links('<p>a<a epub:type="noteref" href="#n1">1</a></p>')
    (plain,) = _flags_of_links('<p>a<a href="#n1">1</a></p>')
    assert NOTEREF_FLAG in noteref
    assert NOTEREF_FLAG not in plain


def test_a_plain_link_inside_a_noteref_is_not_flagged():
    # A link inside a link (the WordDumb plugin writes it, #317): the inner
    # one is the link, and it isn't a noteref.
    flags = _flags_of_links(
        '<p>a<a epub:type="noteref" href="#n1">x<a href="#p1">y</a></a></p>'
    )
    targets = {
        next(f[1] for f in fl if isinstance(f, tuple) and f[0] == "link"): fl
        for fl in flags
    }
    assert NOTEREF_FLAG in targets["c.xhtml#n1"]
    assert NOTEREF_FLAG not in targets["c.xhtml#p1"]


def test_footnote_keys_are_the_ids_on_and_inside_footnotes():
    body = (
        '<aside epub:type="footnote" id="a"><p id="a1">x</p></aside>'
        '<p epub:type="footnote" id="e">y</p>'
        '<aside epub:type="endnote" id="f"><p id="f1">z</p></aside>'
        '<div role="doc-footnote" id="c">w</div>'
        '<p id="plain">v</p>'
    )
    item = mock.Mock(href="text/n.xhtml", data=etree.fromstring(X.format(body)))
    keys = _conv._footnote_keys(mock.Mock(spine=[item]))
    assert keys == {"text/n.xhtml#a", "text/n.xhtml#a1", "text/n.xhtml#e"}


def _para(text, spans=(), keys=()):
    return {
        "text": text,
        "spans": list(spans),
        "anchor_keys": list(keys),
        "anchor_offsets": dict.fromkeys(keys, 0),
    }


def test_a_noteref_to_a_footnote_is_marked_on_the_link_and_the_note(tmp_path):
    link = make_link_flag("c.xhtml#n1")
    plain_to_other = make_link_flag("c.xhtml#p1")
    chapters = [
        {
            "title": "One",
            "text": "x",
            "blocks": [
                _para("text1 more", [(4, 1, frozenset({link, NOTEREF_FLAG}))]),
                _para("plain2 more", [(5, 1, frozenset({plain_to_other}))]),
                _para("noteref3", [(7, 1, frozenset({plain_to_other, NOTEREF_FLAG}))]),
                _para("Note one.", keys=["c.xhtml#n1"]),
                _para("Plain target.", keys=["c.xhtml#p1"]),
            ],
        }
    ]
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=chapters,
        output_path=str(out),
        footnote_keys={"c.xhtml#n1"},
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    marks = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if not (hasattr(e, "get") and e.get("$145")):
                continue
            t = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
            spans = [
                str(sp["$616"]) if "$616" in sp else None
                for sp in e.get("$142") or []
                if "$179" in sp
            ]
            marks[t] = (str(e["$615"]) if "$615" in e else None, spans)
    assert marks["text1 more"] == (None, ["$617"])
    assert marks["plain2 more"] == (None, [None])
    # A noteref to something that isn't a footnote: an ordinary link.
    assert marks["noteref3"] == (None, [None])
    assert marks["Note one."] == ("$281", [])
    assert marks["Plain target."] == (None, [])
