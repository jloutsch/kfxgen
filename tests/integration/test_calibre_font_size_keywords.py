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


def _sizes(calibre, name, *args, body=BODY, css=CSS):  # noqa: F811
    tmp, env, _version = calibre
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    write_epub(
        epub,
        pages={"c.xhtml": body},
        toc=[("Sizes", "c.xhtml", 1)],
        css=css,
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


def test_an_authors_rem_under_a_10px_root_is_its_own(calibre):  # noqa: F811
    # QA on #731, probe P731b: under html { font-size: 62.5% } an author's
    # 1.1rem and 1.7rem, calibre's strings for large and xx-large, are 11
    # and 17 px in Previewer 4.0.3 (0.6875, 1.0625), not the keywords' sizes.
    body = (
        "<h1>Root</h1><p>R01 <span class='a'>ONE</span> and"
        " <span class='b'>TWO</span> after.</p>"
    )
    css = (
        "html { font-size: 62.5% } p { font-size: 1.6rem }"
        " span.a { font-size: 1.1rem } span.b { font-size: 1.7rem }"
    )
    sizes = _sizes(calibre, "k729root", body=body, css=css)
    assert sizes[-2:] == [0.6875, 1.0625]


def test_an_initials_rem_under_a_10px_root_is_its_own(calibre):  # noqa: F811
    # QA on #731, probe P731d: the first letter's 1.7rem under a 10 px root
    # is 17 px in Previewer 4.0.3 (1.0625), not xx-large's 2.
    body = "<h1>Root</h1><p class='raised'>Raised initial R02 in the words.</p>"
    css = (
        ":root { font-size: 62.5% } p { font-size: 1.6rem }"
        " p.raised::first-letter { font-size: 1.7rem }"
    )
    assert _sizes(calibre, "k729fl", body=body, css=css)[-1:] == [1.0625]
