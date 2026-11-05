"""A chapter whose page is only its heading (#717).

pg100's title page for each play is one file holding a heading and a rule:
`<div class="chapter"><h2>ALL’S WELL THAT ENDS WELL</h2><hr/></div>`.
kfxgen prints the chapter's title as a heading of its own and puts the book
heading's style on it (#490), but only when text was left after cutting the
title; on a page of the title alone the heading kept kfxgen's style (left,
bold, 1.2em), and the rule under it was dropped as a chapter's last rule
(#230), though #230 keeps a rule under the heading. Kindle Previewer 4.0.3
prints the heading in the book's style and the rule under it.

Through the shim no CSS is read, so the rule is checked here and the style
in tests/integration/test_calibre_heading_only_page.py.
"""

import pytest

from tests._kfx_introspect import by_type, val
from tests.unit.test_contents_pages import _convert

CSS = (
    "h1, h2, h3, h4, h5 { text-align: center; font-weight: normal; line-height: 1.5 }"
    " h2 { font-size: 150%; margin-top: 2em; margin-bottom: 1em }"
    " hr { width: 80%; margin-top: 2em; margin-bottom: 2em }"
    " div.chapter { margin-top: 4em }"
    " div.box { border: 1px solid black; padding: 0.5em }"
)
PAGES = {
    "c0.xhtml": "<h1 id='w'>The Works</h1><p>Front words.</p>",
    "t1.xhtml": "<div class='chapter'><h2 id='t1'>RULED TITLE</h2><hr/></div>",
    "s1.xhtml": "<h3 id='s1'>Scene one</h3><p>Words one.</p><hr/>",
    "t2.xhtml": "<div class='chapter'><h2 id='t2'>BARE TITLE</h2></div>",
    "s2.xhtml": "<h3 id='s2'>Scene two</h3><p>Words two.</p>",
    "t3.xhtml": "<div class='box'><h2 id='t3'>BOXED TITLE</h2></div><hr/>",
    "s3.xhtml": "<h3 id='s3'>Scene three</h3><p>Words three.</p>",
    "t4.xhtml": "<h2 id='t4'>PLAIN TITLE</h2>",
    "s4.xhtml": "<h3 id='s4'>Scene four</h3><p>Words four.</p>",
}
TOC = [("The Works", "c0.xhtml#w", 0)]
for _n, _name in enumerate(("RULED", "BARE", "BOXED", "PLAIN"), 1):
    TOC += [
        (f"{_name} TITLE", f"t{_n}.xhtml#t{_n}", 0),
        (f"Scene {('one', 'two', 'three', 'four')[_n - 1]}", f"s{_n}.xhtml#s{_n}", 1),
    ]


def entries(frags):
    """[[(text, or $596 / $270, {its style and own keys}), ...] per
    section], nested entries flattened in reading order."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []

    def visit(nodes, into):
        for e in nodes:
            e = getattr(e, "value", e)
            if not hasattr(e, "get"):
                continue
            s = dict(styles.get(str(e.get("$157")), {}))
            s.update(e)
            ref = e.get("$145")
            if hasattr(ref, "get"):
                label = str(texts[str(ref["name"])][int(ref["$403"])]).strip()
            else:
                label = str(e.get("$159"))
            into.append((label, {str(k): v for k, v in s.items() if str(k) != "$146"}))
            visit(e.get("$146") or (), into)

    for sl in by_type(frags, "$259"):
        out.append([])
        visit(val(sl)["$146"], out[-1])
    return out


def section_of(frags, title):
    (section,) = [s for s in entries(frags) if any(t == title for t, _ in s)]
    return [t for t, _ in section]


@pytest.mark.unit
@pytest.mark.parametrize(
    "title, expected",
    [
        ("RULED TITLE", ["RULED TITLE", "$596"]),
        ("BARE TITLE", ["BARE TITLE"]),
        ("PLAIN TITLE", ["PLAIN TITLE"]),
        # A chapter's last rule is still left out (#230).
        ("Scene one", ["Scene one", "Words one."]),
    ],
)
def test_the_rule_under_a_title_alone_is_kept(tmp_path, title, expected):
    frags = _convert(tmp_path, pages=PAGES, toc=TOC)
    assert section_of(frags, title) == expected
