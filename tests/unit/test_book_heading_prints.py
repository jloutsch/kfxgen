"""A section opening with the book's own heading prints that heading alone,
as Kindle Previewer 4.0.3 prints it; the TOC label stays in the TOC (#481)."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _blocks(body):
    xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return _conv.extract_blocks_from_html(
        etree.fromstring(xhtml.encode()), base_href="c.xhtml"
    )


def _book(tmp_path, label, body):
    from kfxgen.native_generator import NativeKFXGenerator

    out = tmp_path / "h.kfx"
    chapters = [
        {"title": label, "text": "x", "blocks": _blocks(body)},
        {"title": "Next", "text": "x", "blocks": _blocks("<p>Next body.</p>")},
    ]
    NativeKFXGenerator().generate_full_book(
        title="T", author="A", chapters=chapters, output_path=str(out)
    )
    return load_fragments(out)


def _lines(frags):
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                ref = e["$145"]
                out.append(str(texts[str(ref["name"])][ref["$403"]]))
    return out


def _nav_labels(frags):
    out = []
    for f in by_type(frags, "$389"):
        for c in val(f)[0]["$392"]:
            c = getattr(c, "value", c)
            if str(c["$235"]) == "$212":
                for u in c["$247"]:
                    u = getattr(u, "value", u)
                    out.append(str(getattr(u["$241"], "value", u["$241"])["$244"]))
    return out


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "body", "lines"),
    [
        # The label and the heading differ: the heading alone prints.
        ("Chapter One", "<h1>Chapter 1</h1><p>Body.</p>", ["Chapter 1", "Body."]),
        ("Prologue", "<h2>A Prologue</h2><p>Body.</p>", ["A Prologue", "Body."]),
        ("Chapter III: The Third", "<h1>III.</h1><p>Body.</p>", ["III.", "Body."]),
        # The heading extends its label: it is not split.
        (
            "Chapter 5",
            "<h1>Chapter 5: The End</h1><p>Body.</p>",
            ["Chapter 5: The End", "Body."],
        ),
        # A rule above the heading is passed over.
        ("Chapter One", "<hr/><h1>Chapter 1</h1><p>Body.</p>", ["Chapter 1", "Body."]),
        # Equal, as before (#62).
        ("Chapter 1", "<h1>Chapter 1</h1><p>Body.</p>", ["Chapter 1", "Body."]),
        # A paragraph is not the book's heading: the label prints above it.
        (
            "Chapter One",
            "<p>Chapter 1</p><p>Body.</p>",
            ["Chapter One", "Chapter 1", "Body."],
        ),
    ],
)
def test_a_section_opening_with_its_heading_prints_it_alone(
    tmp_path, label, body, lines
):
    frags = _book(tmp_path, label, body)
    assert _lines(frags)[: len(lines)] == lines


@pytest.mark.unit
def test_the_label_stays_in_the_toc(tmp_path):
    frags = _book(tmp_path, "Chapter One", "<h1>Chapter 1</h1><p>Body.</p>")
    assert _nav_labels(frags) == ["Chapter One", "Next"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("heading", "lines"),
    [
        ("\x00IMG\x01images/x.jpg\x01\x00", ["Chapter One", "[PIC]", "Body."]),
        (
            "Map \x00IMG\x01images/x.jpg\x01\x00",
            ["Chapter One", "Map", "[PIC]", "Body."],
        ),
    ],
    ids=["picture", "words-and-picture"],
)
def test_a_heading_holding_a_picture_keeps_it(tmp_path, heading, lines):
    """The picture prints as a picture, under the label as before, not as
    the heading's text."""
    from kfxgen.native_generator import NativeKFXGenerator
    from tests._helpers import MINIMAL_JPEG

    out = tmp_path / "p.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {
                "title": "Chapter One",
                "text": f"{heading}\n\nBody.",
                "blocks": [
                    {"text": heading, "spans": [], "heading": True},
                    {"text": "Body.", "spans": []},
                ],
            },
            {"title": "Two", "text": "Two\n\nB.", "blocks": _blocks("<p>B.</p>")},
        ],
        images={"images/x.jpg": MINIMAL_JPEG},
        output_path=str(out),
    )
    frags = load_fragments(out)
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    seq = []
    for st in by_type(frags, "$259"):
        if str(st.fid) != "l0":
            continue
        for e in iter_entries(val(st)["$146"]):
            if not hasattr(e, "get"):
                continue
            if e.get("$175") is not None:
                seq.append("[PIC]")
            elif e.get("$145"):
                seq.append(str(texts[str(e["$145"]["name"])][e["$145"]["$403"]]))
    assert seq == lines
