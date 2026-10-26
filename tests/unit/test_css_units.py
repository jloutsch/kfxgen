"""CSS lengths in in, cm and ex are read, as Kindle Previewer 4.0.3 reads
them (#486): 1in is 72pt, 1cm is 10mm, 1ex is 0.44em (QA's #452 probe:
text-indent 2ex is 0.88em, margin 1in 18.75% = 6em). A length with a space
before its unit is invalid CSS and ignored, as browsers and Previewer
ignore it. text-align start and end are left and right, -webkit-center is
centre, as Previewer maps them."""

import pytest

from kfxgen import inline_style as ist


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "parsed"),
    [
        ("1in", ("72", "$318")),
        ("0.5in", ("36", "$318")),
        ("1cm", ("10", "$316")),
        ("2.5cm", ("25", "$316")),
        ("2ex", ("0.88", "$308")),
        ("1EX", ("0.44", "$308")),
        # A space before the unit isn't a length.
        ("2 em", None),
        ("1 in", None),
        # Unchanged.
        ("1.5em", ("1.5", "$308")),
        ("3mm", ("3", "$316")),
    ],
)
def test_lengths_in_in_cm_and_ex(value, parsed):
    assert ist.parse_css_length(value) == parsed


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "align"),
    [
        ("start", "left"),
        ("end", "right"),
        ("-webkit-center", "center"),
        ("left", "left"),
        ("inherit", None),
    ],
)
def test_logical_text_align(value, align):
    assert ist.compute_block_style({"text-align": value})["align"] == align
