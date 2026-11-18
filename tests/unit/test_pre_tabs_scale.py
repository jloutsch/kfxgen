"""A tab in `white-space: pre` text finds its tab stop from where the line
starts, kept as the text is read, not by walking back to the last line
break: a line of n tabs took time n squared (#793)."""

import time

import pytest

from kfxgen import inline_style as _style

pytestmark = pytest.mark.unit

PRE = frozenset({_style.FLAG_PRE})


def test_a_line_of_twenty_thousand_tabs_reads_in_seconds():
    # Before #793: 5,000 tabs took 2 s, 10,000 8.5 s, 20,000 40 s.
    start = time.perf_counter()
    text, _spans, _anchors = _style.normalize_runs_with_anchors([("\t" * 20_000, PRE)])
    assert time.perf_counter() - start < 3
    # Leading spaces are stripped; nothing else is left.
    assert text == ""


@pytest.mark.parametrize(
    "segments",
    [
        [("abc\tX\nab\tY", PRE)],
        # The break in its own segment, and a hard break.
        [("abc\tX", PRE), ("\n", PRE), ("ab\tY", PRE)],
        [("abc\tX" + _style.HARD_BREAK + "ab\tY", PRE)],
        # Spaces before the break are dropped first.
        [("abc\tX   \nab\tY", PRE)],
    ],
    ids=["one-segment", "break-segment", "hard-break", "trailing-spaces"],
)
def test_a_tab_after_a_line_break_counts_from_that_line(segments):
    text, _spans, _anchors = _style.normalize_runs_with_anchors(segments)
    first, second = text.split("\n")
    assert first.index("X") == 8
    assert second.index("Y") == 8
