"""A comma inside a quoted font name doesn't split it (#487):
`"Foo, Bar", serif` is two names, not three."""

import pytest

from kfxgen.inline_style import _parse_font_family


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "names"),
    [
        ('"Foo, Bar", serif', ["foo, bar", "serif"]),
        ("'Foo, Bar', serif", ["foo, bar", "serif"]),
        ("Georgia, 'Times New Roman', serif", ["georgia", "times new roman", "serif"]),
        ("  Plain  ", ["plain"]),
        ("", []),
    ],
)
def test_font_family_names(value, names):
    assert _parse_font_family(value) == names
