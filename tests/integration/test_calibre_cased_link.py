"""A link written in other capitals doesn't print its chapter twice,
through calibre (#476).

On a file system that ignores case (macOS, Windows), a link to
"C2.XHTML#t" for c2.xhtml makes calibre add the file to the spine again
under that name, and kfxgen printed chapter Two twice. Kindle Previewer
4.0.3 prints it once (QA's #452 probe). The link still lands on it.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. On a case-sensitive file system calibre can't find C2.XHTML,
adds nothing, and the test passes either way.
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

FILES = {
    "c1.xhtml": '<h1>One</h1><p>See <a href="C2.XHTML#t">L1</a>.</p>',
    "c2.xhtml": '<h1>Two</h1><p id="t">Target two.</p>',
}
TOC = [("One", "c1.xhtml"), ("Two", "c2.xhtml")]


def test_a_cased_link_prints_its_chapter_once(calibre):  # noqa: F811
    tmp, _env, version = calibre
    epub, kfx = tmp / "case476.epub", tmp / "case476.kfx"
    _build(epub, FILES, TOC, "case476")
    _ebook_convert(calibre, epub, kfx, True)
    frags = load_fragments(kfx)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    entries = [
        e
        for st in by_type(frags, "$259")
        for e in iter_entries(val(st)["$146"])
        if hasattr(e, "get") and e.get("$145")
    ]

    def text(e):
        r = e["$145"]
        return str(content[str(r["name"])][int(r["$403"])])

    texts = [text(e) for e in entries]
    assert texts.count("Target two.") == 1, f"calibre {version}: {texts}"
    by_id = {e["$155"]: e for e in entries}
    anchors = {str(f.fid): val(f)["$183"]["$155"] for f in by_type(frags, "$266")}
    link = next(e for e in entries if text(e) == "See L1.")
    targets = [sp["$179"] for sp in link.get("$142") or [] if "$179" in sp]
    assert [text(by_id[anchors[str(t)]]) for t in targets] == ["Target two."]
