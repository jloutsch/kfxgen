"""Table rows through calibre's real Stylizer (#222).

Since #219 each `<tr>` is its own paragraph, so a row takes block CSS from its
computed style, and `text-align`, `text-indent` and the font properties are
inherited from the table or whatever wraps it. Every other test of that path
runs without a Stylizer, so this converts a book of table cases with the real
`ebook-convert` and reads each row's paragraph style back out of the KFX.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so like #205 and
tier 2 (#99) this is local-only: a green CI says nothing about it. Last run
against calibre 9.14.0, where every case below passes.

Rows are not indented by an indent on the table's wrapper or on <body>, for
two reasons, both checked. kfxgen's resolver reads `text-indent` from an
element's own rules only (`Style.get`), for every paragraph. And with an
inheritance-aware read instead, calibre 9.14 still computes 0 for a table row,
while a plain paragraph in the same wrapper gets the inherited 18px. What the
indent cases catch is a row taking its wrapper's style some other way — the
pre-#219 converter, where the wrapper *was* the paragraph, fails them. Each has
a control paragraph with its own indent rule; without it, "no indent on the
row" also passes when the stylesheet never loaded.

Not covered: a table's `font-weight` / `font-style`. CSS bold and italic
reach no paragraph in kfxgen output, table or not (a `p { font-weight: bold }`
paragraph is normal weight too), so a case here would pin that limitation
rather than table behaviour.
"""

import os
import re
import subprocess
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_list_markers import (  # noqa: E402
    CALIBRE_CUSTOMIZE,
    EBOOK_CONVERT,
    _build_plugin,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

CSS = """
table.center { text-align: center; }
.indent { text-indent: 1.5em; }
table.auto { margin-left: auto; margin-right: auto; }
"""

_ROWS = "<tr><td>{c}a</td><td>{c}b</td></tr><tr><td>{c}c</td><td>{c}d</td></tr>"

#: (file, case id, markup). Each case's rows read "<id>a <id>b" / "<id>c <id>d".
#: The body-indent case is in its own file because the rule is on <body>.
CASES = [
    ("c1", "CENTER", f'<table class="center">{_ROWS}</table>'),
    (
        "c1",
        "DIVINDENT",
        '<div class="indent"><p class="indent">DIVINDENT control</p>'
        f"<table>{_ROWS}</table></div>",
    ),
    ("c1", "AUTO", f'<table class="auto">{_ROWS}</table>'),
    ("c1", "PLAIN", f"<table>{_ROWS}</table>"),
    (
        "c2",
        "BODYINDENT",
        f'<p class="indent">BODYINDENT control</p><table>{_ROWS}</table>',
    ),
]


def _xhtml(body_attr, cases):
    body = "".join(
        f"<h2>{cid}</h2>{html.format(c=cid)}<p>END {cid}</p>" for cid, html in cases
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Tables</title>'
        '<link rel="stylesheet" type="text/css" href="s.css"/></head>'
        f"<body{body_attr}><h1>Table cases</h1>{body}</body></html>"
    )


def _build_epub(path):
    by_file = {}
    for f, cid, html in CASES:
        by_file.setdefault(f, []).append((cid, html))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="i"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">t'
            "</dc:identifier><dc:title>Table cases</dc:title><dc:language>en"
            "</dc:language></metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="s" href="s.css" media-type="text/css"/>'
            '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine>'
            "</package>",
        )
        z.writestr("c1.xhtml", _xhtml("", by_file["c1"]))
        z.writestr("c2.xhtml", _xhtml(' class="indent"', by_file["c2"]))
        z.writestr("s.css", CSS)


def _paragraphs(kfx):
    """Every text entry in storyline order, as (text, style struct)."""
    frags = load_fragments(kfx)
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }
    out = []
    for story in by_type(frags, "$259"):
        for entry in val(story)["$146"]:
            ref = entry.get("$145")
            if ref is not None:
                text = str(content[str(ref["name"])][int(ref["$403"])])
                out.append((text, styles[str(entry["$157"])]))
    return out


def _indent(style):
    return Decimal(str(style["$36"]["$307"]))


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        pytest.skip(
            "calibre not found (ebook-convert / calibre-customize); set "
            "KFXGEN_EBOOK_CONVERT to run the real-Stylizer table checks (#222)"
        )
    tmp = tmp_path_factory.mktemp("calibre_tables")
    config = tmp / "calibre-config"
    config.mkdir()
    env = dict(os.environ, CALIBRE_CONFIG_DIRECTORY=str(config))
    plugin = tmp / "kfxgen.zip"
    _build_plugin(plugin)
    subprocess.run(
        [CALIBRE_CUSTOMIZE, "-a", str(plugin)], env=env, check=True, capture_output=True
    )
    epub = tmp / "tables.epub"
    _build_epub(epub)
    kfx = tmp / "tables.kfx"
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx)], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr[-2000:]
    version = re.search(
        r"ebook-convert \(calibre ([\d.]+)\)",
        subprocess.run(
            [EBOOK_CONVERT, "--version"], capture_output=True, text=True
        ).stdout,
    )
    by_case, current = {}, None
    for text, style in _paragraphs(kfx):
        if text in {cid for _, cid, _ in CASES}:
            current = text
            by_case[current] = {"rows": [], "control": None, "end": None}
        elif current and text == f"END {current}":
            by_case[current]["end"] = style
            current = None
        elif current and text == f"{current} control":
            by_case[current]["control"] = style
        elif current:
            by_case[current]["rows"].append((text, style))
    return by_case, version.group(1) if version else "unknown"


def _case(converted, cid):
    by_case, calibre_version = converted
    case = by_case.get(cid)
    assert case, f"calibre {calibre_version}: case {cid} missing from the output"
    texts = [t for t, _ in case["rows"]]
    assert texts == [f"{cid}a {cid}b", f"{cid}c {cid}d"], (
        f"calibre {calibre_version}, {cid}: rows are not one paragraph each: {texts}"
    )
    return case, calibre_version


def test_a_centered_table_centers_its_rows(converted):
    case, v = _case(converted, "CENTER")
    for text, style in case["rows"]:
        assert str(style["$34"]) == "$320", f"calibre {v}: {text!r} is not centered"


def test_an_indent_on_a_wrapping_div_does_not_indent_rows(converted):
    # Table cells don't lay out as indented paragraphs, so the source shows
    # no indent on a row. Before #219 the div was the paragraph — the table
    # sat inside it as inline text — and the whole table took its indent.
    case, v = _case(converted, "DIVINDENT")
    assert _indent(case["control"]) > 0, (
        f"calibre {v}: control paragraph not indented, so the CSS never applied"
    )
    for text, style in case["rows"]:
        assert _indent(style) == 0, f"calibre {v}: {text!r} inherited the indent"


def test_an_indent_on_body_does_not_indent_rows(converted):
    case, v = _case(converted, "BODYINDENT")
    assert _indent(case["control"]) > 0, (
        f"calibre {v}: control paragraph not indented, so the CSS never applied"
    )
    for text, style in case["rows"]:
        assert _indent(style) == 0, f"calibre {v}: {text!r} inherited the indent"


def test_auto_margins_on_a_table_do_not_reach_rows(converted):
    # `table { margin: auto }` is common in Gutenberg CSS. Margins are not
    # inherited, so a row keeps an ordinary paragraph's margins.
    case, v = _case(converted, "AUTO")
    for text, style in case["rows"]:
        for prop in ("$48", "$50"):
            assert style.get(prop) == case["end"].get(prop), (
                f"calibre {v}: {text!r} {prop} is {style.get(prop)!r}, "
                f"an ordinary paragraph has {case['end'].get(prop)!r}"
            )


def test_rows_of_an_unstyled_table_are_styled_like_a_paragraph(converted):
    case, v = _case(converted, "PLAIN")
    for text, style in case["rows"]:
        assert style == case["end"], f"calibre {v}: {text!r} carries extra style"
