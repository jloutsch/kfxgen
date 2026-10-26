"""A box's fill read from the `background:` shorthand, which calibre's
Stylizer leaves unexpanded (#464)."""

import pytest

from kfxgen.converter import _box_background


@pytest.mark.unit
@pytest.mark.parametrize(
    ("css", "fill"),
    [
        ({"background": "#eeeeee"}, 0xFFEEEEEE),
        ({"background": "url(a.png) no-repeat top left #ddeeff"}, 0xFFDDEEFF),
        ({"background": "rgb(221, 238, 255) url(a.png)"}, 0xFFDDEEFF),
        ({"background": "none"}, None),
        ({"background": "url(a.png)"}, None),
        ({"background": "transparent"}, None),
        # The longhand, when set, is the fill.
        ({"background-color": "#112233", "background": "#eeeeee"}, 0xFF112233),
        ({"background-color": "transparent", "background": "#eeeeee"}, 0xFFEEEEEE),
    ],
)
def test_a_shorthand_fill(css, fill):
    assert _box_background(css) == fill
