"""Table rows and cells through calibre's real Stylizer (#222, #219).

Native tables are the default, with `--kfxgen-disable-native-tables` as the
opt-out. On the opt-out path each `<tr>` is its own paragraph, so a row takes
block CSS from its computed style, and `text-align`, `text-indent` and the font
properties are inherited from the table or whatever wraps it. Every other test
of that path runs without a Stylizer, so this converts books of table cases with
the real `ebook-convert` and reads each row's paragraph style back out of the
KFX. The row tests below therefore convert with the opt-out flag; the `native`
tests convert the same books without it and read the cell entries.

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

from tests._kfx_introspect import (  # noqa: E402
    by_type,
    iter_entries,
    load_fragments,
    val,
)
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
td.l { text-align: left; }
td.r { text-align: right; }
"""

_ROWS = "<tr><td>{c}a</td><td>{c}b</td></tr><tr><td>{c}c</td><td>{c}d</td></tr>"


def _rows(first, second):
    """`_ROWS` with each row's two cells opened by `first` and `second`."""
    return (
        f"<tr>{first}{{c}}a</td>{second}{{c}}b</td></tr>"
        f"<tr>{first}{{c}}c</td>{second}{{c}}d</td></tr>"
    )


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
    # A row takes its cells' alignment when they agree (#224).
    (
        "c1",
        "ATTRCENTER",
        f"<table>{_rows('<td align="center">', '<td align="center">')}</table>",
    ),
    (
        "c1",
        "CELLSLEFT",
        f'<table class="center">{_rows('<td class="l">', '<td class="l">')}</table>',
    ),
    (
        "c1",
        "MIXED",
        f'<table class="center">{_rows('<td class="l">', '<td class="r">')}</table>',
    ),
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
        for entry in iter_entries(val(story)["$146"]):
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


def _ebook_convert(calibre, epub, kfx, native):
    """Run `ebook-convert`; with `native` False, pass the native-table opt-out."""
    _, env, _ = calibre
    cmd = [EBOOK_CONVERT, str(epub), str(kfx)]
    if not native:
        cmd.append("--kfxgen-disable-native-tables")
    run = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr[-2000:]


def _convert(calibre, name, cases, css, body_attrs=None, fonts=(), native=False):
    """Convert a book of `cases` and split its paragraphs by case."""
    tmp, env, calibre_version = calibre
    epub = tmp / f"{name}.epub"
    _build_epub(epub, cases, css, body_attrs or {}, fonts)
    kfx = tmp / f"{name}.kfx"
    _ebook_convert(calibre, epub, kfx, native)
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
    """The row path: native tables switched off."""
    return _convert(calibre, "tables", CASES, CSS, {"c2": ' class="indent"'})


@pytest.fixture(scope="module")
def converted_native(calibre):
    """The same cases with native tables on, which is the default."""
    return _convert(
        calibre,
        "tables-native",
        CASES,
        CSS,
        {"c2": ' class="indent"'},
        native=True,
    )


@pytest.fixture(scope="module")
def converted_fonts(calibre):
    fonts = [name for name, _, _ in _FACES]
    return _convert(calibre, "tables-fonts", FONT_CASES, FONT_CSS, fonts=fonts)


@pytest.fixture(scope="module")
def converted_fonts_native(calibre):
    fonts = [name for name, _, _ in _FACES]
    return _convert(
        calibre,
        "tables-fonts-native",
        FONT_CASES,
        FONT_CSS,
        fonts=fonts,
        native=True,
    )


#: Note tables in both layouts. Notes 1-3: each anchor *after* its row, the
#: calibre MOBI→EPUB shape behind #223. Notes 4-6: each anchor *before* its row,
#: #225's reproduction. `rowN` is the row for note N.
def _note_row(n):
    return f'<tr><td><a href="ch.xhtml#r{n}">{n}.</a></td><td>Note {n} text.</td></tr>'


_NOTES_HTML = (
    "<h1>Notes</h1>"
    "<table>"
    + "".join(f'{_note_row(n)}<a id="n{n}"></a>' for n in (1, 2, 3))
    + "</table><p>Between the tables.</p><table>"
    + "".join(f'<a id="n{n}"></a>{_note_row(n)}' for n in (4, 5, 6))
    + "</table>"
)
_CHAPTER_HTML = (
    "<h1>Chapter</h1><p>"
    + " ".join(
        f'Claim {n}<a id="r{n}" href="notes.xhtml#n{n}"><sup>{n}</sup></a>.'
        for n in range(1, 7)
    )
    + "</p>"
)


def _page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{title}</title></head><body>{body}</body></html>"
    )


def _build_notes_epub(path):
    """Chapter + notes, with an NCX: without one calibre adds the note links
    to its generated TOC and the notes split into chapters (#225)."""
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        'version="2005-1"><head><meta name="dtb:uid" content="t"/></head>'
        "<docTitle><text>Notes</text></docTitle><navMap>"
        '<navPoint id="p1" playOrder="1"><navLabel><text>Chapter</text></navLabel>'
        '<content src="ch.xhtml"/></navPoint>'
        '<navPoint id="p2" playOrder="2"><navLabel><text>Notes</text></navLabel>'
        '<content src="notes.xhtml"/></navPoint></navMap></ncx>'
    )
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
            "</dc:identifier><dc:title>Note tables</dc:title><dc:language>en"
            "</dc:language></metadata><manifest>"
            '<item id="ch" href="ch.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="ch"/>'
            '<itemref idref="notes"/></spine></package>',
        )
        z.writestr("ch.xhtml", _page("Chapter", _CHAPTER_HTML))
        z.writestr("notes.xhtml", _page("Notes", _NOTES_HTML))
        z.writestr("toc.ncx", ncx)


def _link_landings(kfx):
    """For each link whose visible text is a bare number: (that number, the
    text at the position its anchor names).

    An anchor may name a container: a native table's row is a `$279` with no
    text of its own, and a link to a row lands on the row (#219). The reader
    shows the row from its first text leaf, so the landing text is the text
    leaves beneath the container joined by spaces, from the first leaf's offset:
    "1." and "Note 1 text." read as the row "1. Note 1 text.".
    """
    frags = load_fragments(kfx)
    content = {
        str(val(f)["name"]): list(val(f)["$146"]) for f in by_type(frags, "$145")
    }

    def text_of(entry):
        ref = entry.get("$145")
        return str(content[str(ref["name"])][int(ref["$403"])])

    entries, landings = {}, []
    for story in by_type(frags, "$259"):
        for entry in iter_entries(val(story)["$146"]):
            entries[int(entry["$155"])] = entry

    def landing_text(eid, offset):
        entry = entries[eid]
        if entry.get("$145") is None:
            leaves = [
                text_of(e)
                for e in iter_entries(entry.get("$146"))
                if e.get("$145") is not None
            ]
            return " ".join([leaves[0][offset:], *leaves[1:]])
        return text_of(entry)[offset:]

    anchors = {}
    for f in by_type(frags, "$266"):
        pos = val(f).get("$183")
        if pos is not None:
            anchors[str(val(f)["$180"])] = (int(pos["$155"]), int(pos.get("$143") or 0))
    for entry in entries.values():
        if entry.get("$145") is None:
            continue
        text = text_of(entry)
        for span in entry.get("$142") or []:
            if span.get("$179") is None:
                continue
            start = int(span["$143"])
            label = text[start : start + int(span["$144"])]
            if not label.isdigit():
                continue
            eid, offset = anchors[str(span["$179"])]
            landings.append((label, landing_text(eid, offset)))
    return sorted(landings)


@pytest.fixture(scope="module", params=["native", "rows"])
def note_landings(calibre, request):
    """The notes book, built as native tables and as rows."""
    tmp, env, calibre_version = calibre
    epub = tmp / "notes.epub"
    _build_notes_epub(epub)
    kfx = tmp / f"notes-{request.param}.kfx"
    _ebook_convert(calibre, epub, kfx, native=request.param == "native")
    return _link_landings(kfx), f"{calibre_version} ({request.param})"


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


@pytest.mark.parametrize(
    "cid, align",
    [("ATTRCENTER", "$320"), ("CELLSLEFT", "$59"), ("MIXED", "$320")],
)
def test_a_row_takes_its_cells_alignment_when_they_agree(converted, cid, align):
    # ATTRCENTER is pg22210's picture tables: calibre turns align="center"
    # into a computed text-align. CELLSLEFT is left cells in a centred table
    # (pg24855). MIXED cells disagree, so the row keeps the table's. (#224)
    case, v = _case(converted, cid)
    for text, style in case["rows"]:
        assert str(style.get("$34")) == align, (
            f"calibre {v}: {text!r} is {style.get('$34')}, expected {align}"
        )


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


def _native_case(converted, cid):
    """A native-table case: every cell is its own text entry, in row order."""
    by_case, calibre_version = converted
    case = by_case.get(cid)
    assert case, f"calibre {calibre_version}: case {cid} missing from the output"
    texts = [t for t, _ in case["rows"]]
    assert texts == [f"{cid}{x}" for x in "abcd"], (
        f"calibre {calibre_version}, {cid}: cells are not one entry each: {texts}"
    )
    return case, calibre_version


def test_native_centered_table_centers_its_cells(converted_native):
    case, v = _native_case(converted_native, "CENTER")
    for text, style in case["rows"]:
        assert str(style["$34"]) == "$320", f"calibre {v}: {text!r} is not centered"


@pytest.mark.parametrize("cid", ["DIVINDENT", "BODYINDENT"])
def test_native_cells_do_not_take_an_indent_from_a_wrapper(converted_native, cid):
    # Same rule as the row path: a cell is not an indented paragraph, and the
    # resolver reads `text-indent` from an element's own rules only.
    case, v = _native_case(converted_native, cid)
    assert _indent(case["control"]) > 0, (
        f"calibre {v}: control paragraph not indented, so the CSS never applied"
    )
    for text, style in case["rows"]:
        assert "$36" not in style, f"calibre {v}: {text!r} inherited the indent"


@pytest.mark.parametrize("cid", ["AUTO", "INSET"])
def test_native_cells_do_not_take_the_tables_margins(converted_native, cid):
    case, v = _native_case(converted_native, cid)
    if case["control"] is not None:
        assert case["control"].get("$48") != case["end"].get("$48"), (
            f"calibre {v}: control paragraph has no inset, so the CSS never applied"
        )
    for text, style in case["rows"]:
        for prop in ("$48", "$50"):
            assert prop not in style, (
                f"calibre {v}: {text!r} carries {prop} {style.get(prop)!r}"
            )


def test_native_bold_tables_cells_use_the_embedded_bold_face(converted_fonts_native):
    case, v = _native_case(converted_fonts_native, "BOLDTABLE")
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


def test_native_plain_tables_cells_use_the_books_regular_face(converted_fonts_native):
    case, v = _native_case(converted_fonts_native, "FONTPLAIN")
    regular = case["end"].get("$11")
    assert regular is not None, f"calibre {v}: an ordinary paragraph has no font"
    for text, style in case["rows"]:
        assert str(style.get("$13", "$350")) == "$350", (
            f"calibre {v}: {text!r} is not normal weight"
        )
        assert style.get("$11") == regular, (
            f"calibre {v}: {text!r} font {style.get('$11')!r}, "
            f"an ordinary paragraph uses {regular!r}"
        )


def test_each_note_link_lands_on_its_own_note(note_landings):
    # Both layouts: notes 1-3 with the anchor after the row (#223's book, where
    # links used to land on the next note), notes 4-6 with it before the row.
    landings, v = note_landings
    assert [label for label, _ in landings] == ["1", "2", "3", "4", "5", "6"], (
        f"calibre {v}: expected six note links, got {landings}"
    )
    for label, target in landings:
        assert target.startswith(f"{label}. Note {label} text."), (
            f"calibre {v}: note link {label} lands on {target[:40]!r}"
        )
