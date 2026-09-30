"""Table rows through calibre's real Stylizer (#222).

Since #219 each `<tr>` is its own paragraph, so a row takes block CSS from its
computed style, and `text-align`, `text-indent` and the font properties are
inherited from the table or whatever wraps it. Every other test of that path
runs without a Stylizer, so this converts books of table cases with the real
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
pre-#219 converter, where the wrapper *was* the paragraph, fails them. The
margin cases catch a row taking the `<table>` element's own style: a numeric
inset is needed for that, since `margin: auto` produces no value to leak.
Each of these has a control paragraph with its own rule; without it, "no
indent on the row" also passes when the stylesheet never loaded.

Block bold and italic apply only when the book embeds fonts
(`native_generator.py`, `if has_fonts`), so they are checked in a second book
that embeds the Charis SIL faces from `test_books/font-matching-test`. There a
row uses the book's font, and its bold face when the table's CSS is bold; on
`main` table text had no font family at all. In the first book, which embeds
nothing, CSS bold reaches no paragraph, table or not.
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
.inset { margin-left: 4em; }
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
    (
        "c1",
        "INSET",
        f'<p class="inset">INSET control</p><table class="inset">{_ROWS}</table>',
    ),
    ("c1", "PLAIN", f"<table>{_ROWS}</table>"),
    (
        "c2",
        "BODYINDENT",
        f'<p class="indent">BODYINDENT control</p><table>{_ROWS}</table>',
    ),
]

_FONT_DIR = REPO / "test_books/font-matching-test/OEBPS/fonts"
#: (file name, weight, style) of the faces the font book embeds.
_FACES = [
    ("CharisSILR.ttf", "normal", "normal"),
    ("CharisSILB.ttf", "bold", "normal"),
    ("CharisSILI.ttf", "normal", "italic"),
    ("CharisSILBI.ttf", "bold", "italic"),
]
FONT_CSS = (
    "".join(
        f'@font-face {{ font-family: "Charis SIL"; font-weight: {w}; '
        f'font-style: {s}; src: url("fonts/{name}"); }}\n'
        for name, w, s in _FACES
    )
    + 'body { font-family: "Charis SIL", serif; }\n.b { font-weight: bold; }\n'
)
FONT_CASES = [
    (
        "f1",
        "BOLDTABLE",
        f'<p class="b">BOLDTABLE control</p><table class="b">{_ROWS}</table>',
    ),
    ("f1", "FONTPLAIN", f"<table>{_ROWS}</table>"),
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


def _build_epub(path, cases, css, body_attrs, fonts=()):
    """A minimal EPUB: one XHTML file per distinct file key in `cases`."""
    by_file = {}
    for f, cid, html in cases:
        by_file.setdefault(f, []).append((cid, html))
    items = "".join(
        f'<item id="{f}" href="{f}.xhtml" media-type="application/xhtml+xml"/>'
        for f in by_file
    ) + "".join(
        f'<item id="font{i}" href="fonts/{name}" media-type="font/ttf"/>'
        for i, name in enumerate(fonts)
    )
    spine = "".join(f'<itemref idref="{f}"/>' for f in by_file)
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
            f"</dc:language></metadata><manifest>{items}"
            '<item id="s" href="s.css" media-type="text/css"/>'
            f"</manifest><spine>{spine}</spine></package>",
        )
        for f, file_cases in by_file.items():
            z.writestr(f"{f}.xhtml", _xhtml(body_attrs.get(f, ""), file_cases))
        z.writestr("s.css", css)
        for name in fonts:
            z.write(_FONT_DIR / name, f"fonts/{name}")


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
def calibre(tmp_path_factory):
    """A calibre config with this checkout's plugin installed, and its version."""
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
    version = re.search(
        r"ebook-convert \(calibre ([\d.]+)\)",
        subprocess.run(
            [EBOOK_CONVERT, "--version"], capture_output=True, text=True
        ).stdout,
    )
    return tmp, env, version.group(1) if version else "unknown"


def _convert(calibre, name, cases, css, body_attrs=None, fonts=()):
    """Convert a book of `cases` and split its paragraphs by case."""
    tmp, env, calibre_version = calibre
    epub = tmp / f"{name}.epub"
    _build_epub(epub, cases, css, body_attrs or {}, fonts)
    kfx = tmp / f"{name}.kfx"
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx)], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr[-2000:]
    by_case, current = {}, None
    for text, style in _paragraphs(kfx):
        if text in {cid for _, cid, _ in cases}:
            current = text
            by_case[current] = {"rows": [], "control": None, "end": None}
        elif current and text == f"END {current}":
            by_case[current]["end"] = style
            current = None
        elif current and text == f"{current} control":
            by_case[current]["control"] = style
        elif current:
            by_case[current]["rows"].append((text, style))
    return by_case, calibre_version


@pytest.fixture(scope="module")
def converted(calibre):
    return _convert(calibre, "tables", CASES, CSS, {"c2": ' class="indent"'})


@pytest.fixture(scope="module")
def converted_fonts(calibre):
    fonts = [name for name, _, _ in _FACES]
    return _convert(calibre, "tables-fonts", FONT_CASES, FONT_CSS, fonts=fonts)


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


@pytest.mark.parametrize("cid", ["AUTO", "INSET"])
def test_a_tables_margins_do_not_reach_rows(converted, cid):
    # Margins are not inherited, so a row keeps an ordinary paragraph's
    # margins. `margin: auto` is the common Gutenberg rule, but it produces no
    # value, so a row styled from the <table> element itself would look the
    # same; the numeric inset is what tells the two apart (6 corpus books have
    # one, e.g. pg21053 `table.poem { margin-left: 4em }`). The rows losing the
    # table's inset is recorded under #219, and `main` lost it too.
    case, v = _case(converted, cid)
    if case["control"] is not None:
        assert case["control"].get("$48") != case["end"].get("$48"), (
            f"calibre {v}: control paragraph has no inset, so the CSS never applied"
        )
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


def test_a_bold_tables_rows_use_the_embedded_bold_face(converted_fonts):
    case, v = _case(converted_fonts, "BOLDTABLE")
    control = case["control"]
    assert str(control["$13"]) == "$361" and control.get("$11") is not None, (
        f"calibre {v}: the bold control paragraph did not get the bold face"
    )
    for text, style in case["rows"]:
        assert str(style["$13"]) == "$361", f"calibre {v}: {text!r} is not bold"
        assert style.get("$11") == control["$11"], (
            f"calibre {v}: {text!r} font {style.get('$11')!r}, "
            f"the bold paragraph uses {control['$11']!r}"
        )


def test_a_plain_tables_rows_use_the_books_regular_face(converted_fonts):
    # On `main` table text had no font family, so it drew in the device's
    # default font even in a book that embeds its own.
    case, v = _case(converted_fonts, "FONTPLAIN")
    regular = case["end"].get("$11")
    assert regular is not None, f"calibre {v}: an ordinary paragraph has no font"
    for text, style in case["rows"]:
        assert str(style["$13"]) == "$350", f"calibre {v}: {text!r} is not normal"
        assert style.get("$11") == regular, (
            f"calibre {v}: {text!r} font {style.get('$11')!r}, "
            f"an ordinary paragraph uses {regular!r}"
        )
