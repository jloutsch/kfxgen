"""A hanging indent is kept when the paragraph's left margin covers it, as
Kindle Previewer 4.0.3 writes `text-indent: -1em; margin-left: 1em` (#465),
and still dropped when nothing does: alone, it pulled the first line off
the left edge and cut its first characters (#22)."""

import pytest

from kfxgen.inline_style import compute_block_style
from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


@pytest.mark.unit
@pytest.mark.parametrize(
    ("css", "indent"),
    [
        ({"text-indent": "-1em", "margin-left": "1em"}, ("-1", "$308")),
        ({"text-indent": "-16px", "margin-left": "1em"}, ("-16", "$319")),
        ({"text-indent": "-1em", "margin-left": "2em"}, ("-1", "$308")),
        ({"text-indent": "-1em"}, None),
        # Past its margin: clamped to it. Kindle Previewer 4.0.3 compares in
        # the % it writes, where 1em = 12pt and 1px = 0.45pt (#541 probes).
        ({"text-indent": "-2em", "margin-left": "1em"}, ("-1", "$308")),
        ({"text-indent": "-40px", "margin-left": "2em"}, ("-40", "$319")),
        ({"text-indent": "-1em", "margin-left": "20px"}, ("-20", "$319")),
        ({"text-indent": "-1em", "margin-left": "12pt"}, ("-1", "$308")),
        ({"text-indent": "-15pt", "margin-left": "1em"}, ("-1", "$308")),
        ({"text-indent": "-40px", "margin-left": "5%"}, ("-40", "$319")),
        ({"text-indent": "-60px", "margin-left": "5%"}, ("-5", "$314")),
        ({"text-indent": "-2em", "margin-left": "5%"}, ("-5", "$314")),
        ({"text-indent": "2em"}, ("2", "$308")),
    ],
    ids=[
        "covered",
        "px-under-em",
        "wider-margin",
        "alone",
        "past-margin",
        "px-under-em-margin",
        "em-past-px-margin",
        "pt-equal",
        "pt-past-em",
        "px-under-percent",
        "px-past-percent",
        "em-past-percent",
        "positive",
    ],
)
def test_a_negative_indent_is_kept_within_its_margin(css, indent):
    assert compute_block_style(css)["indent"] == indent


@pytest.mark.unit
def test_a_hanging_paragraph_keeps_its_space_above(tmp_path):
    """Only a first-line indent replaces the space between paragraphs."""
    block = {
        "text": "Hanging entry text.",
        "spans": [],
        "block_style": compute_block_style(
            {"text-indent": "-1em", "margin-left": "1em"}
        ),
    }
    out = tmp_path / "h.kfx"
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": "x", "blocks": [block]}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    (style,) = [
        styles[str(e["$157"])]
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get")
        and e.get("$145")
        and str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])]).startswith(
            "Hanging"
        )
    ]
    assert float(str(style["$36"]["$307"])) < 0
    assert "$47" in style
