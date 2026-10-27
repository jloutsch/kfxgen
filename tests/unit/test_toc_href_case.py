"""A TOC href naming a spine file only up to letter case or %-encoding is
read in the spine's spelling (#549). The OPF said `01_cover.xhtml`, the NCX
`01_Cover.xhtml`: calibre added the second spelling as a file of its own,
#476 dropped it as the same file, and every TOC entry pointing at it was
dropped as "not in spine"."""

from types import SimpleNamespace

import pytest

from kfxgen import converter as _conv

pytestmark = pytest.mark.unit


def _respelled(spine, hrefs):
    entries = [{"title": "T", "href": h, "level": 0} for h in hrefs]
    _conv._respell_toc_hrefs(entries, [SimpleNamespace(href=h) for h in spine])
    return [e["href"] for e in entries]


def test_a_tocs_case_is_the_spines():
    got = _respelled(
        ["x/01_cover.xhtml", "x/one.xhtml", "x/two%20b.xhtml"],
        ["x/01_Cover.xhtml", "x/ONE.xhtml#s1", "Two B.xhtml"],
    )
    assert got == ["x/01_cover.xhtml", "x/one.xhtml#s1", "x/two%20b.xhtml"]


@pytest.mark.parametrize(
    ("spine", "href"),
    [
        (["one.xhtml"], "one.xhtml#a"),  # already the spine's
        (["a/One.xhtml", "b/one.xhtml"], "ONE.xhtml"),  # two files fit
        (["one.xhtml"], "two.xhtml"),  # none fits
    ],
    ids=["exact", "ambiguous", "missing"],
)
def test_other_hrefs_are_left_as_given(spine, href):
    assert _respelled(spine, [href]) == [href]
