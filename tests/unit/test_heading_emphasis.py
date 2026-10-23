"""A chapter heading keeps the emphasis inside the book's heading (#366).

kfxgen prints each chapter's heading from its contents label and cuts the
book's matching heading from the body. The label is text only, so
`<h1>The <i>Harbour</i></h1>` became a bold "The Harbour" with no italic,
whether the italic came from a tag or a CSS class.

When the cut removes one block whose text is the title, its runs now go on
kfxgen's heading. A run's style names its whole face (#50), so an italic
word in the bold heading is bold and italic.
"""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _book(tmp_path, chapters):
    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(out)
    )
    return load_fragments(out)


def _entries(frags):
    """[(text, entry $157, [(run text, run $157)])] in reading order."""
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            ref = e.get("$145")
            if not ref:
                continue
            text = texts[str(ref["name"])][ref["$403"]]
            runs = [
                (
                    text[r["$143"] : r["$143"] + r["$144"]],
                    styles.get(str(r.get("$157")), {}),
                )
                for r in e.get("$142") or []
            ]
            out.append((text, styles.get(str(e.get("$157")), {}), runs))
    return out


def _bold(s):
    return str(s.get("$13")) == "$361"


def _italic(s):
    return str(s.get("$12")) == "$382"


def _chapter(title, body_html, resolver=None):
    xhtml = (
        f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body_html}</body></html>'
    )
    blocks = _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), style_resolver=resolver
    )
    return {
        "title": title,
        "text": "\n\n".join(b["text"] for b in blocks),
        "blocks": blocks,
    }


def _heading(frags, title):
    found = [e for e in _entries(frags) if e[0] == title]
    assert len(found) == 1, f"{title!r} printed {len(found)} times"
    return found[0]


@pytest.mark.unit
def test_an_italic_word_in_a_heading_stays_italic(tmp_path):
    ch = _chapter(
        "The Harbour", "<h1>The <i>Harbour</i></h1><p>First chapter text.</p>"
    )
    _, style, runs = _heading(_book(tmp_path, [ch]), "The Harbour")
    assert _bold(style)
    (word,) = [s for t, s in runs if t == "Harbour"]
    assert _italic(word) and _bold(word)


@pytest.mark.unit
def test_a_css_italic_word_in_a_heading_stays_italic(tmp_path):
    def resolve(elem):
        # calibre reports every element's computed font-style.
        return {"font-style": "italic" if elem.get("class") == "si" else "normal"}

    ch = _chapter(
        "The Storm",
        '<h1>The <span class="si">Storm</span></h1><p>Second chapter text.</p>',
        resolver=resolve,
    )
    _, _, runs = _heading(_book(tmp_path, [ch]), "The Storm")
    (word,) = [s for t, s in runs if t == "Storm"]
    assert _italic(word)


@pytest.mark.unit
def test_a_label_in_other_capitals_keeps_the_emphasis(tmp_path):
    """The book's heading prints, in its own capitals and with its runs, in
    place of the label (#409; the label printed until then)."""
    ch = _chapter("THE HARBOUR", "<h1>The <i>Harbour</i></h1><p>Text.</p>")
    _, _, runs = _heading(_book(tmp_path, [ch]), "The Harbour")
    (word,) = [s for t, s in runs if t == "Harbour"]
    assert _italic(word)


@pytest.mark.unit
def test_a_plain_heading_has_no_runs(tmp_path):
    ch = _chapter("The Tide", "<h1>The Tide</h1><p>Tide text.</p>")
    _, _, runs = _heading(_book(tmp_path, [ch]), "The Tide")
    assert runs == []


@pytest.mark.unit
def test_a_block_longer_than_the_title_keeps_its_own_runs(tmp_path):
    """Only a block that is exactly the title gives its runs to the heading;
    a longer one keeps its words and runs, as before."""
    ch = _chapter("The Harbour", "<p>The Harbour and the <i>boats</i>.</p>")
    frags = _book(tmp_path, [ch])
    _, _, runs = _heading(frags, "The Harbour")
    assert runs == []
    (rest,) = [e for e in _entries(frags) if "boats" in e[0]]
    (word,) = [s for t, s in rest[2] if t == "boats"]
    assert _italic(word)


@pytest.mark.unit
def test_an_unlisted_sub_heading_keeps_its_emphasis(tmp_path):
    ch = _chapter(
        "The Tide",
        "<h1>The Tide</h1><p>Tide text.</p><h2>A <i>Sub</i> heading</h2><p>More.</p>",
    )
    (sub,) = [e for e in _entries(_book(tmp_path, [ch])) if e[0] == "A Sub heading"]
    (word,) = [s for t, s in sub[2] if t == "Sub"]
    assert _italic(word)


@pytest.mark.unit
def test_spaces_around_the_books_heading_do_not_shift_the_emphasis(tmp_path):
    """No-break spaces are kept as written (#202); the title cut strips them,
    so the runs move by the leading ones and stop at the title's end."""
    ch = _chapter(
        "The Harbour", "<h1>&#160;&#160;The <i>Harbour&#160;</i></h1><p>Text.</p>"
    )
    frags = _book(tmp_path, [ch])
    _, _, runs = _heading(frags, "The Harbour")
    assert [(t, _italic(s)) for t, s in runs] == [("Harbour", True)]
    # No run reaches past the heading's text.
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            ref = e.get("$145")
            if ref and texts[str(ref["name"])][ref["$403"]] == "The Harbour":
                for r in e.get("$142") or []:
                    assert r["$143"] + r["$144"] <= len("The Harbour")
