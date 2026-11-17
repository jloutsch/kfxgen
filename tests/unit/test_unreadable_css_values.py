"""A CSS length, colour or width kfxgen can't read is read as none, and
costs nothing else (#792).

Each raised before: the spine loop then left the whole chapter file out.
Values that read before read the same. The calibre end-to-end case is in
tests/integration/test_calibre_unreadable_values.py."""

import pytest
from lxml import etree

from kfxgen import converter as _conv
from kfxgen import inline_style as _style

pytestmark = pytest.mark.unit

XH = "http://www.w3.org/1999/xhtml"


@pytest.mark.parametrize(
    "value",
    [
        "1ın",  # dotless i: re.I matched it as "in"
        "2İn",  # dotted capital I
        "9" * 400 + "in",  # infinite once read, then "inf" points
        "9" * 400 + "em",
        "9" * 308 + "in",  # finite, infinite in points
    ],
)
def test_an_unreadable_length_is_none(value):
    assert _style.parse_css_length(value) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1in", ("72", "$318")),
        ("2.5em", ("2.5", "$308")),
        ("-1em", None),
        ("0px", None),
    ],
)
def test_readable_lengths_read_as_before(value, expected):
    assert _style.parse_css_length(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "rgba(0,0,0,1.2.3)",
        "rgba(0,0,0,.)",
        "hsl(1.2.3,10%,10%)",
        "hsl(" + "9" * 400 + ",10%,10%)",
        "rgba(0,0,0," + "9" * 400 + ")",
    ],
)
def test_an_unreadable_colour_is_none(value):
    assert _style._rgb_alpha(value) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        # Channels past 255 are 255, however many digits (#792).
        ("rgb(" + "9" * 5000 + ",0,0)", (0xFF0000, 1.0)),
        ("rgb(" + "9" * 400 + "%,0,0)", (0xFF0000, 1.0)),
        ("rgb(300,0,0)", (0xFF0000, 1.0)),
        ("rgb(0255,0,0)", (0xFF0000, 1.0)),
        # Leading zeros are no digits past 255 (QA on #797).
        ("rgb(0012,0,0)", (0x0C0000, 1.0)),
        ("rgb(50%,0,0)", (0x800000, 1.0)),
        ("rgb(99.7%,0,0)", (0xFF0000, 1.0)),
        ("rgba(0,0,0,0.5)", (0x000000, 0.5)),
        ("hsl(120,100%,50%)", (0x00FF00, 1.0)),
    ],
)
def test_readable_colours_read_as_before(value, expected):
    assert _style._rgb_alpha(value) == expected


def test_unreadable_widths_and_paddings_are_none():
    assert _conv._padding_em("1.2.3px") is None
    assert _conv._padding_em(".") is None
    assert _conv._padding_em("2px") == pytest.approx(0.075)


@pytest.mark.parametrize(
    "body",
    [
        '<table width="1.2.3%"><tr><td>KEEPWORD a.</td><td>b</td></tr></table>',
        '<table><tr><td width="1.2.3%">KEEPWORD a.</td><td>b</td></tr></table>',
        '<table><colgroup><col width="1.2.3%"/><col/></colgroup>'
        "<tr><td>KEEPWORD a.</td><td>b</td></tr></table>",
        '<p style="text-indent: 1ın">KEEPWORD a.</p>',
    ],
    ids=["table-width", "cell-width", "col-width", "dotless-inch"],
)
def test_a_page_with_an_unreadable_value_keeps_its_text(body):
    def resolver(elem):
        style = elem.get("style") or ""
        return dict(
            (k.strip(), v.strip())
            for k, _, v in (d.partition(":") for d in style.split(";") if ":" in d)
        )

    root = etree.fromstring(f'<html xmlns="{XH}"><body>{body}</body></html>')
    blocks = _conv.extract_blocks_from_html(
        root, style_resolver=resolver, base_href="c.xhtml", native_tables=True
    )
    assert "KEEPWORD" in " ".join(b.get("text") or "" for b in blocks)


@pytest.mark.parametrize("height", [None, "3px"])
def test_a_hidden_rules_unreadable_margin_counts_as_none(height):
    css = {"margin-bottom": "2px"} | ({"height": height} if height else {})
    assert _conv._hidden_rule_em({**css, "margin-top": "1.2.3px"}) == (
        _conv._hidden_rule_em(css)
    )


@pytest.mark.parametrize(
    "attrs",
    [
        'width="' + "9" * 400 + '"',
        'height="' + "9" * 400 + '"',
        'width="1e9"',
        'width="' + "9" * 308 + 'em"',
        'width="1000000000"',
    ],
)
def test_a_picture_size_past_any_page_is_no_size(attrs):
    # 400 digits read as infinity, and the generator's IonDecimal failed
    # the whole book (QA on #797).
    img = etree.fromstring(f'<img xmlns="{XH}" src="p.png" {attrs}/>')
    assert _conv._img_size_hint(img) is None
    assert _conv._img_inline_extras(img) is None


@pytest.mark.parametrize(
    "attrs, hint, extras",
    [
        ('width="200"', "w=200", None),
        ('width="50%"', "w=50%", None),
        ('height="2em"', "h=2em", "h=2em"),
        ('width="999999999"', "w=999999999", None),
    ],
)
def test_picture_sizes_read_as_before(attrs, hint, extras):
    img = etree.fromstring(f'<img xmlns="{XH}" src="p.png" {attrs}/>')
    assert _conv._img_size_hint(img) == hint
    assert _conv._img_inline_extras(img) == extras
