"""The page width Kindle Previewer 4.0.3 measures a percentage against.

In a horizontal book it is 384pt, a 512px column (#541; pictures #467,
probed 1 to 400 px; it was 496 in Previewer 3.106, #145). In a vertical
book it is 480pt, a 640px column, in vertical text, in horizontal blocks and
in horizontal files alike (P247j, #247): 1em is 2.5% there, not 3.125%, and a
400px picture 62.5%, not 78.125%. `convert` sets it for one conversion with
`page_width`; everything else reads it.
"""

import contextlib
import contextvars

HORIZONTAL_PT = 384.0
VERTICAL_PT = 480.0

_PAGE_PT = contextvars.ContextVar("kfxgen_page_pt", default=HORIZONTAL_PT)


def page_pt():
    """The page width in pt, 1px being 0.75pt of it."""
    return _PAGE_PT.get()


def column_px():
    """The page width in CSS px: 512 in a horizontal book, 640 in a vertical
    one."""
    return page_pt() * 4 / 3


def em_percent():
    """One em, 12pt, as a % of the page: 3.125, or 2.5 in a vertical book."""
    return 1200 / page_pt()


@contextlib.contextmanager
def page_width(pt):
    """Measure percentages against `pt` inside the block."""
    token = _PAGE_PT.set(pt)
    try:
        yield
    finally:
        _PAGE_PT.reset(token)
