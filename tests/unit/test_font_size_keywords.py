"""font-size keywords at Kindle Previewer's sizes (#729).

calibre rewrites a font-size keyword before kfxgen sees it: the input
profile's size for it over the profile's base size, in rem to one decimal
(`StylizerRules.flatten_style`). On the default profile `small` is 9 / 12,
written 0.8rem, which kfxgen read as 12.8 px; Previewer 4.0.3 writes the CSS
sizes, 13 px (probe PUNF, 2026-11-06). kfxgen now maps calibre's value back
to the keyword's size. A value two keywords share, and `medium` (1rem, as
authors write it), are left as they are. Through calibre:
tests/integration/test_calibre_font_size_keywords.py.
"""

from types import SimpleNamespace

import pytest

from kfxgen.converter import _calibre_keyword_px

DEFAULT = SimpleNamespace(
    fbase=12,
    fnames={
        "xx-small": 5.0,
        "x-small": 7.0,
        "small": 9.0,
        "medium": 12.0,
        "large": 13.5,
        "x-large": 17.0,
        "xx-large": 20.0,
    },
)
KINDLE = SimpleNamespace(
    fbase=16,
    fnames={
        "xx-small": 12.0,
        "x-small": 12.0,
        "small": 14.0,
        "medium": 16.0,
        "large": 18.0,
        "x-large": 20.0,
        "xx-large": 22.0,
    },
)


def _stylizer(profile):
    return SimpleNamespace(opts=SimpleNamespace(input_profile=profile))


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, px",
    [
        ("0.4rem", 9.0),
        ("0.6rem", 10.0),
        ("0.8rem", 13.0),
        ("1.1rem", 18.0),
        ("1.4rem", 24.0),
        ("1.7rem", 32.0),
        # medium is left: 1rem as authors write it, however it's spelled.
        ("1rem", None),
        ("1.0rem", None),
        ("0.5rem", None),
        ("13px", None),
    ],
)
def test_the_default_profiles_keywords_read_at_previewers_sizes(value, px):
    assert _calibre_keyword_px(_stylizer(DEFAULT), value) == px


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, px",
    [
        # xx-small and x-small are both 12 / 16: left as written.
        ("0.8rem", None),
        ("0.9rem", 13.0),
        ("1.1rem", 18.0),
        ("1.2rem", 24.0),
        ("1.4rem", 32.0),
    ],
)
def test_the_kindle_profiles_keywords_read_unless_two_share_a_value(value, px):
    assert _calibre_keyword_px(_stylizer(KINDLE), value) == px


@pytest.mark.unit
def test_without_a_profile_nothing_is_read_as_a_keyword():
    assert _calibre_keyword_px(SimpleNamespace(), "0.8rem") is None
