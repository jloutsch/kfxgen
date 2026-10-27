"""A book heading printed in place of kfxgen's label keeps the book's own
style, as Kindle Previewer 4.0.3 prints it (#490): QA's `<h1>` "Chapter 1"
set at 1em, normal weight, left-aligned, came out bold, 1.2rem and
justified, kfxgen's own heading style. A label kfxgen prints itself keeps
kfxgen's style."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _heading_style(tmp_path, blocks, title="Chapter One"):
    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": title, "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                if text.startswith("Chapter"):
                    return text, styles[str(e["$157"])]
    return None


def _block(text, **bs):
    return {"text": text, "spans": [], "heading": True, "block_style": bs}


@pytest.mark.unit
def test_the_books_heading_keeps_its_own_style(tmp_path):
    text, style = _heading_style(
        tmp_path,
        [
            _block("Chapter 1", bold=False, align="left", font_size=None),
            {"text": "Body.", "spans": []},
        ],
    )
    assert text == "Chapter 1"
    assert str(style["$13"]) != "$361"  # not bold
    assert "$16" not in style  # the main text size
    assert str(style["$34"]) == "$59"  # left


@pytest.mark.unit
def test_a_bold_book_heading_stays_bold(tmp_path):
    text, style = _heading_style(
        tmp_path,
        [
            _block("Chapter 1", bold=True, align="center"),
            {"text": "Body.", "spans": []},
        ],
    )
    assert str(style["$13"]) == "$361"
    assert str(style["$34"]) == "$320"


@pytest.mark.unit
def test_kfxgens_own_label_keeps_kfxgens_style(tmp_path):
    text, style = _heading_style(tmp_path, [{"text": "Body.", "spans": []}])
    assert text == "Chapter One"
    assert str(style["$13"]) == "$361"
    assert "$16" in style


@pytest.mark.unit
def test_a_book_heading_with_no_alignment_writes_none(tmp_path):
    """Previewer writes no $34 for a plain h1 or h2 (probed 2026-10-26)."""
    _text, style = _heading_style(
        tmp_path,
        [
            _block("Chapter 1", bold=True, font_size=None),
            {"text": "Body.", "spans": []},
        ],
    )
    assert "$34" not in style
