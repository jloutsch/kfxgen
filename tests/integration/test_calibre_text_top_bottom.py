"""vertical-align text-top and text-bottom through calibre (#485): written
as Kindle Previewer 4.0.3 writes them, $44 $447 and $449 with no size
change, not as a 0.75rem superscript and subscript. super is unchanged.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_contents_links import _build  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    _ebook_convert,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BODY = "<h1>V</h1>" + "".join(
    f'<p>V{i} before <span style="vertical-align: {v}">{v.upper()}</span> after.</p>'
    for i, v in enumerate(["text-top", "text-bottom", "super"])
)


def test_text_top_and_bottom_as_previewer_writes_them(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "v485.epub", tmp / "v485.kfx"
    _build(epub, {"c.xhtml": BODY}, [("V", "c.xhtml")], "v485")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    runs = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                for sp in e.get("$142") or []:
                    word = text[int(sp["$143"]) : int(sp["$143"]) + int(sp["$144"])]
                    s = styles[str(sp["$157"])]
                    runs[word] = (str(s.get("$44")), "$16" in s)
    assert runs == {
        "TEXT-TOP": ("$447", False),
        "TEXT-BOTTOM": ("$449", False),
        "SUPER": ("$370", True),
    }, f"calibre {version}"
