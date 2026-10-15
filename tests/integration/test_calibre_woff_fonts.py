"""WOFF and WOFF2 fonts through calibre's real pipeline (#245).

calibre passes a WOFF or WOFF2 font to the plugin unchanged, and the plugin
unpacks it with calibre's own fontTools and Brotli. This converts a book of
four faces, two WOFF and two WOFF2, and checks each is embedded as
TrueType.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only. Last run against calibre 9.16.0, where every case passes.
"""

import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from fontTools.ttLib import TTFont  # noqa: E402

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)
from tests.unit.test_woff_fonts import GLYPHS, _flavoured  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.integration]

#: (file, flavor, weight, style)
FACES = [
    ("r.woff", "woff", "normal", "normal"),
    ("i.woff", "woff", "normal", "italic"),
    ("b.woff2", "woff2", "bold", "normal"),
    ("bi.woff2", "woff2", "bold", "italic"),
]


def _build(path):
    rules = "".join(
        f'@font-face {{ font-family: "Probe"; font-weight: {w}; font-style: {s}; '
        f'src: url("{name}") format("{flavor}"); }}'
        for name, flavor, w, s in FACES
    )
    css = rules + 'body { font-family: "Probe", serif; } .b { font-weight: bold; }'
    body = (
        "<p>ABC regular.</p><p><i>ABC italic.</i></p>"
        '<p class="b">ABC bold.</p><p class="b"><i>ABC bold italic.</i></p>'
    )
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>W</title><link rel="stylesheet" type="text/css" href="s.css"/>'
        f"</head><body>{body}</body></html>"
    )
    items = "".join(
        f'<item id="f{i}" href="{name}" media-type="font/{flavor}"/>'
        for i, (name, flavor, _, _) in enumerate(FACES)
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
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">w'
            "</dc:identifier><dc:title>WOFF cases</dc:title><dc:language>en"
            '</dc:language></metadata><manifest><item id="c" href="c.xhtml" '
            'media-type="application/xhtml+xml"/><item id="s" href="s.css" '
            f'media-type="text/css"/>{items}</manifest><spine><itemref idref="c"/>'
            "</spine></package>",
        )
        z.writestr("c.xhtml", xhtml)
        z.writestr("s.css", css)
        for name, flavor, _, _ in FACES:
            z.writestr(name, _flavoured(flavor))


@pytest.fixture(scope="module")
def converted(calibre):  # noqa: F811
    """(the $262 declarations, the $418 font bytes, the log), and calibre's
    version."""
    pytest.importorskip("brotli")  # to write the WOFF2 faces
    tmp, env, version = calibre
    epub, kfx = tmp / "woff.epub", tmp / "woff.kfx"
    _build(epub)
    run = subprocess.run(
        [EBOOK_CONVERT, str(epub), str(kfx)], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0 and kfx.exists(), run.stdout[-2000:] + run.stderr
    frags = load_fragments(kfx)
    decls = [val(f) for f in by_type(frags, "$262")]
    fonts = {str(f.fid): bytes(val(f)) for f in by_type(frags, "$418")}
    return decls, fonts, run.stdout + run.stderr, version


def test_every_woff_face_is_embedded(converted):
    decls, _, log, version = converted
    assert len(decls) == 4, f"calibre {version}"
    assert "Skipping @font-face" not in log, f"calibre {version}"


def test_each_face_is_truetype_with_its_glyphs(converted):
    decls, fonts, _, version = converted
    assert decls, f"calibre {version}"
    for d in decls:
        data = fonts[str(d["$165"])]
        assert data[:4] == b"\x00\x01\x00\x00", f"calibre {version}"
        assert TTFont(BytesIO(data)).getGlyphOrder() == GLYPHS, f"calibre {version}"


def test_bold_and_italic_faces_are_told_apart(converted):
    decls, _, _, version = converted
    shapes = sorted((str(d.get("$13")), str(d.get("$12"))) for d in decls)
    assert len(set(shapes)) == 4, f"calibre {version}: {shapes}"
