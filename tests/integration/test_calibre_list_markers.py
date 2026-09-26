"""List markers through calibre's real Stylizer (#205).

The #201 fix takes a list item's marker style from calibre's computed CSS
whenever a Stylizer is available, which in the plugin is always. Every unit
test of that path uses a fake resolver, so this converts a book of list cases
with the real `ebook-convert` and reads the text back out of the KFX.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so like tier 2
(#99) this is local-only: a green CI says nothing about it. Last run against
calibre 9.14, where all cases below pass; set KFXGEN_EBOOK_CONVERT to point at
an `ebook-convert` that is not on PATH.
"""

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

_MAC_APP = Path("/Applications/calibre.app/Contents/MacOS")


def _tool(name):
    override = os.environ.get("KFXGEN_EBOOK_CONVERT")
    if override:
        candidate = Path(override).with_name(name)
        return str(candidate) if candidate.exists() else None
    return shutil.which(name) or (
        str(_MAC_APP / name) if (_MAC_APP / name).exists() else None
    )


EBOOK_CONVERT = _tool("ebook-convert")
CALIBRE_CUSTOMIZE = _tool("calibre-customize")

CSS = """
ol.none { list-style: none; }
ol.none2 { list-style-type: none; }
li.roman { list-style-type: upper-roman; }
li.block { display: block; }
ul.sq { list-style-type: square; }
ol.alpha-css { list-style-type: lower-alpha; }
ol.greek { list-style-type: lower-greek; }
ol.quoted { list-style-type: "\\2192  "; }
"""

#: (case id, markup, the paragraphs expected in the KFX).
CASES = [
    ("C01", "<ol><li>one</li><li>two</li></ol>", ["1. one", "2. two"]),
    ("C02", '<ol type="a"><li>one</li><li>two</li></ol>', ["a. one", "b. two"]),
    ("C03", '<ol type="I"><li>one</li><li>two</li></ol>', ["I. one", "II. two"]),
    ("C04", "<ul><li>one</li><li>two</li></ul>", ["• one", "• two"]),
    ("C05", "<ul><li>outer<ul><li>inner</li></ul></li></ul>", ["• outer", "• inner"]),
    ("C06", '<ol class="none"><li>one</li><li>two</li></ol>', ["one", "two"]),
    ("C07", '<ol class="none2"><li>one</li><li>two</li></ol>', ["one", "two"]),
    (
        "C08",
        '<ol><li class="roman">one</li><li class="roman">two</li></ol>',
        ["I. one", "II. two"],
    ),
    (
        "C09",
        '<ol><li class="block">one</li><li class="block">two</li></ol>',
        ["one", "two"],
    ),
    (
        "C10",
        '<ol class="none"><li>1. First</li><li>2. Second</li></ol>',
        ["1. First", "2. Second"],
    ),
    ("C11", '<ul class="sq"><li>one</li></ul>', ["• one"]),
    (
        "C12",
        '<ol class="alpha-css"><li>one</li><li>two</li></ol>',
        ["a. one", "b. two"],
    ),
    ("C13", '<ol start="5"><li>five</li><li>six</li></ol>', ["5. five", "6. six"]),
    (
        "C14",
        '<ol reversed="reversed"><li>three</li><li>two</li><li>one</li></ol>',
        ["3. three", "2. two", "1. one"],
    ),
    (
        "C15",
        '<ol><li>one</li><li value="10">ten</li><li>eleven</li></ol>',
        ["1. one", "10. ten", "11. eleven"],
    ),
    (
        "C16",
        '<ol><li id="n1"><a href="#r1">1</a>. The note text.</li>'
        '<li id="n2"><a href="#r2">2</a>. Another note.</li></ol>',
        ["1. The note text.", "2. Another note."],
    ),
    ("C17", "<div><li>orphan item</li></div>", ["• orphan item"]),
    ("C18", '<ol class="greek"><li>one</li><li>two</li></ol>', ["α. one", "β. two"]),
    ("C19", '<ol class="quoted"><li>one</li></ol>', ["→ one"]),
    (
        "C20",
        "<ol><li><p>para one</p><p>para two</p></li><li><p>next</p></li></ol>",
        ["1. para one", "para two", "2. next"],
    ),
    (
        "C21",
        "<ol><li>3 eggs</li><li>12 angry men</li><li>2 cups</li></ol>",
        ["1. 3 eggs", "2. 12 angry men", "3. 2 cups"],
    ),
    (
        "C22",
        '<ol><li><img src="p.png" alt=""/></li><li>after image</li></ol>',
        ["2. after image"],
    ),
]

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


def _build_epub(path):
    body = "".join(f"<h2>{cid}</h2>{html}<p>END {cid}</p>" for cid, html, _ in CASES)
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Lists</title>'
        '<link rel="stylesheet" type="text/css" href="s.css"/></head>'
        f"<body><h1>List cases</h1>{body}</body></html>"
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
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x'
            "</dc:identifier><dc:title>List cases</dc:title><dc:language>en"
            "</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="s" href="s.css" media-type="text/css"/>'
            '<item id="p" href="p.png" media-type="image/png"/>'
            '</manifest><spine><itemref idref="c"/></spine></package>',
        )
        z.writestr("c.xhtml", xhtml)
        z.writestr("s.css", CSS)
        z.writestr("p.png", _PNG)


def _build_plugin(path):
    """The plugin zip exactly as scripts/build_plugin.py lays it out."""
    plugin_dir = REPO / "plugin"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in plugin_dir.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                zf.write(f, f.relative_to(plugin_dir))


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    if not (EBOOK_CONVERT and CALIBRE_CUSTOMIZE):
        pytest.skip(
            "calibre not found (ebook-convert / calibre-customize); set "
            "KFXGEN_EBOOK_CONVERT to run the real-Stylizer list checks (#205)"
        )
    tmp = tmp_path_factory.mktemp("calibre_lists")
    config = tmp / "calibre-config"
    config.mkdir()
    env = dict(os.environ, CALIBRE_CONFIG_DIRECTORY=str(config))
    plugin = tmp / "kfxgen.zip"
    _build_plugin(plugin)
    subprocess.run(
        [CALIBRE_CUSTOMIZE, "-a", str(plugin)], env=env, check=True, capture_output=True
    )
    epub = tmp / "lists.epub"
    _build_epub(epub)
    kfx = tmp / "lists.kfx"
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
    paragraphs = [
        str(t)
        for f in by_type(load_fragments(kfx), "$145")
        for k, x in val(f).items()
        if isinstance(x, list)
        for t in x
    ]
    by_case, current = {}, None
    for p in paragraphs:
        if re.fullmatch(r"C\d\d", p):
            current = p
            by_case[current] = []
        elif p.startswith("END C"):
            current = None
        elif current:
            by_case[current].append(p)
    return by_case, version.group(1) if version else "unknown"


@pytest.mark.parametrize(
    "cid,expected", [(c, e) for c, _, e in CASES], ids=[c for c, _, _ in CASES]
)
def test_list_markers_through_the_real_stylizer(converted, cid, expected):
    by_case, calibre_version = converted
    assert by_case.get(cid) == expected, f"calibre {calibre_version}, case {cid}"
