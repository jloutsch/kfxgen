"""font-size keywords through calibre, at Kindle Previewer's sizes (#729).

Probe PUNF's keyword spans, each run's `$16` as Previewer 4.0.3 writes it:
the CSS keyword sizes in px over 16. calibre rewrites each keyword with the
input profile before kfxgen sees it; kfxgen reads it back. With the kindle
input profile, xx-small and x-small come out the same and stay so.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_contents_pages import write_epub  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

KEYWORDS = ["xx-small", "x-small", "small", "large", "x-large", "xx-large"]
CSS = "".join(f" span.k{i} {{ font-size: {k} }}" for i, k in enumerate(KEYWORDS))
CSS += " p.raised::first-letter { font-size: xx-large }"
BODY = "<h1>Sizes</h1>" + "".join(
    f"<p>K{i:02d} {k}: <span class='k{i}'>SIZEDWORD</span> after.</p>"
    for i, k in enumerate(KEYWORDS)
)
#: A raised initial: Previewer 4.0.3 writes its xx-large as 2 too.
BODY += "<p class='raised'>Raised initial K10 in the words of a line.</p>"
#: Previewer 4.0.3's $16 for each, probe PUNF.
PREVIEWER = [0.5625, 0.625, 0.8125, 1.125, 1.5, 2.0, 2.0]


def _sizes(calibre, name, *args):  # noqa: F811
    tmp, env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    write_epub(
        epub,
        pages={"c.xhtml": BODY},
        toc=[("Sizes", "c.xhtml", 1)],
        css=CSS,
        ident=name,
    )
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx), *args],
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    out = []
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            for r in (e.get("$142") or ()) if hasattr(e, "get") else ():
                size = styles.get(str(r.get("$157")), {}).get("$16")
                if size is not None:
                    out.append(round(float(size["$307"]), 4))
    return out


def test_keywords_are_written_at_previewers_sizes(calibre):  # noqa: F811
    assert _sizes(calibre, "k729") == PREVIEWER


def test_with_another_output_profile_too(calibre):  # noqa: F811
    # The keywords are rewritten with the input profile, whatever the output.
    assert _sizes(calibre, "k729pw", "--output-profile", "kindle_pw3") == PREVIEWER


def test_the_kindle_input_profiles_shared_value_is_left(calibre):  # noqa: F811
    # xx-small and x-small are both 0.8rem there: 12.8 px, as before.
    assert _sizes(calibre, "k729in", "--input-profile", "kindle") == [
        0.8,
        0.8,
        *PREVIEWER[2:],
    ]
