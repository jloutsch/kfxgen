"""Margins and paddings keyed by block (#712).

`extract_blocks_from_html` kept each block's margins and paddings in dicts
keyed by `id(block)`. An address is unique only while its object is alive: a
block dict replaced and freed left its address to the next one made, which
then read the old block's margins. Spacing changed from one conversion to
the next, and with unrelated code changes (QA's library A/B on #711).

`_ById` holds each key, so no address is reused while it is in use.
"""

import gc

import pytest

from kfxgen.converter import _ById


@pytest.mark.unit
def test_a_new_block_never_reads_a_freed_blocks_value():
    margins = _ById()
    for n in range(5000):
        block = {"text": f"line {n}"}
        assert margins.get(block) is None, f"block {n} read another block's margins"
        margins.setdefault(block, [float(n), 0.0])
        del block
        if n % 500 == 0:
            gc.collect()


@pytest.mark.unit
def test_it_reads_back_what_was_set():
    pads = _ById()
    a, b = {"text": "a"}, {"text": "a"}  # equal, but two blocks
    pads[a] = [1.0, 2.0, 0.0, 0.0]
    assert pads[a] == [1.0, 2.0, 0.0, 0.0]
    assert pads.get(b) is None
    assert pads.get(b, (0.0,) * 4) == (0.0,) * 4
    assert pads.setdefault(a, [9.0] * 4) is pads[a]
