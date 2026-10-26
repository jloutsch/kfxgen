"""A link whose target differs from the declared id only in capitals still
lands: "C2.XHTML#t" on c2.xhtml#t, as calibre passes a link written that
way (#476). An exact match wins over one in other capitals."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests.unit.test_link_landings import _landings, _link
from tests.unit.test_native_generator import _para


def _book(tmp_path, links, targets):
    out = tmp_path / "b.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[
            {"title": "One", "text": "x", "blocks": links},
            {"title": "Two", "text": "x", "blocks": targets},
        ],
        output_path=str(out),
    )
    return _landings(out)


@pytest.mark.unit
def test_a_link_in_other_capitals_lands(tmp_path):
    landed = _book(
        tmp_path,
        [_link("L1", "C2.XHTML#t"), _link("L2", "C2.XHTML")],
        [_para("Two"), _para("Target two.", ["c2.xhtml#t"])],
    )
    assert landed["L1"] == "Target two."
    assert "L2" in landed


@pytest.mark.unit
def test_an_exact_match_wins(tmp_path):
    landed = _book(
        tmp_path,
        # "#Note" sorts before "#note", so it is the one the fold keeps.
        [_link("L1", "c2.xhtml#note")],
        [_para("Other.", ["c2.xhtml#Note"]), _para("Exact.", ["c2.xhtml#note"])],
    )
    assert landed["L1"] == "Exact."
