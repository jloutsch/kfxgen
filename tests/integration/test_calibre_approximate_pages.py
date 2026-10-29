"""Approximate page numbers through calibre (#586): a book with no page list
gets kfxgen's APPROXIMATE_PAGE_LIST, numbered from 1; the per-conversion
`--kfxgen-disable-approximate-pages` option or the saved setting leaves it
out; a book's own page list is kept instead (#578).

Needs calibre, so it is `slow` and skips without `ebook-convert`."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_landmarks import CASES, _epub  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _plain(x):
    return getattr(x, "value", x)


def _page_list(kfx):
    """(name, labels) of the book's $237 container, or None."""
    for f in by_type(load_fragments(kfx), "$389"):
        for nav in val(f):
            for c in _plain(nav)["$392"]:
                c = _plain(c)
                if str(c.get("$235")) == "$237":
                    labels = [str(_plain(p)["$241"]["$244"]) for p in c["$247"]]
                    return str(c["$239"]), labels
    return None


def _convert(calibre, name, *flags):  # noqa: F811
    tmp, env, version = calibre
    guide, nav, _want = CASES["nav"]
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _epub(epub, name, guide, nav)
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx), *flags],
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr[-2000:]
    return _page_list(kfx), version


def test_by_default_a_book_without_pages_gets_approximate_ones(calibre):  # noqa: F811
    pages, version = _convert(calibre, "ap-default")
    assert pages is not None, f"calibre {version}"
    name, labels = pages
    assert name == "APPROXIMATE_PAGE_LIST", f"calibre {version}"
    assert labels == [str(n) for n in range(1, len(labels) + 1)], f"calibre {version}"


def test_the_option_leaves_them_out(calibre):  # noqa: F811
    pages, version = _convert(calibre, "ap-flag", "--kfxgen-disable-approximate-pages")
    assert pages is None, f"calibre {version}: {pages}"


def test_the_saved_setting_leaves_them_out(calibre):  # noqa: F811
    _tmp, env, _version = calibre
    path = Path(env["CALIBRE_CONFIG_DIRECTORY"]) / "plugins" / "kfxgen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_text() if path.exists() else None
    path.write_text(json.dumps({"disable_approximate_pages": True}))
    try:
        pages, version = _convert(calibre, "ap-pref")
    finally:
        if before is None:
            path.unlink()
        else:
            path.write_text(before)
    assert pages is None, f"calibre {version}: {pages}"
