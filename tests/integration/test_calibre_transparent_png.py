"""A PNG with transparent pixels reaches the book drawn onto white and still
a PNG, through calibre (#783): with optimisation on, with original pictures
embedded, and as the cover. Kindle Previewer 4.0.3 draws them onto white too
(a half-transparent (0, 0, 200) came out (127, 127, 227)), then writes a
JPEG; kfxgen keeps the PNG. An opaque PNG is written as the book has it.

It needs calibre installed, so it is `slow` (run with `pytest -m slow`) and
skips when `ebook-convert` is not found. CI has no calibre, so this is
local-only.
"""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugin"))
sys.path.insert(0, str(REPO))

from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.integration.test_calibre_table_rows import (  # noqa: E402
    EBOOK_CONVERT,
    calibre,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

GREEN, BLUE = (0, 255, 0), (0, 0, 200)


def _png(img, **kw):
    out = io.BytesIO()
    img.save(out, "PNG", **kw)
    return out.getvalue()


def _halves(mode, left, right):
    """A 40 x 20 picture, `left` on its left half and `right` on its right."""
    img = Image.new(mode, (40, 20), right)
    img.paste(left, (0, 0, 20, 20))
    return img


def _palette():
    img = _halves("P", 0, 1)
    img.putpalette(list(GREEN + BLUE) + [0] * 762)
    return _png(img, transparency=0)


PICTURES = {
    # Green hidden under the transparent half: a picture that lost its
    # alpha shows green, one drawn onto white shows white.
    "a.png": _png(_halves("RGBA", GREEN + (0,), BLUE + (128,))),
    "g.png": _png(_halves("LA", (0, 0), (60, 255))),
    "p.png": _palette(),
    "t.png": _png(_halves("RGB", GREEN, BLUE), transparency=GREEN),
    "o.png": _png(_halves("RGB", GREEN, BLUE)),
    "f.png": _png(_halves("RGBA", GREEN + (255,), BLUE + (255,))),
}


def _epub(path, cover):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        items = "".join(
            f'<item id="{n[0]}" href="{n}" media-type="image/png"/>' for n in PICTURES
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="u"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="u">t783</dc:identifier><dc:title>T</dc:title>'
            "<dc:language>en</dc:language>"
            + ('<meta name="cover" content="a"/>' if cover else "")
            + "</metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            f"{items}"
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx"><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            'version="2005-1"><head/><docTitle><text>T</text></docTitle><navMap>'
            '<navPoint id="n" playOrder="1"><navLabel><text>One</text></navLabel>'
            '<content src="c.xhtml"/></navPoint></navMap></ncx>',
        )
        body = "".join(
            f"<p>{n[0]} text.</p><p><img src='{n}' alt='{n[0]}'/></p>" for n in PICTURES
        )
        z.writestr(
            "c.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
            f"<body><h1>One</h1>{body}</body></html>",
        )
        for name, data in PICTURES.items():
            z.writestr(name, data)


def _pictures(kfx):
    """{alt text: (format symbol, picture bytes)} for each picture shown."""
    frags = load_fragments(kfx)
    raw = {str(f.fid): bytes(val(f)) for f in by_type(frags, "$417")}
    res = {str(f.fid): val(f) for f in by_type(frags, "$164")}
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if e.get("$175") is not None:
                r = res[str(e["$175"])]
                got[str(e["$584"])] = (str(r["$161"]), raw[str(r["$165"])])
    return got, len(raw)


def _near(got, want):
    return all(abs(a - b) <= 1 for a, b in zip(got, want))


@pytest.mark.parametrize(
    ("embed", "cover"),
    [(False, False), (True, False), (False, True)],
    ids=["optimised", "embed-originals", "cover"],
)
def test_a_transparent_png_is_drawn_onto_white(calibre, embed, cover):  # noqa: F811
    tmp, env, version = calibre
    name = f"t783{embed}{cover}"
    epub, kfx = tmp / f"{name}.epub", tmp / f"{name}.kfx"
    _epub(epub, cover)
    cmd = [EBOOK_CONVERT, str(epub), str(kfx)]
    if embed:
        cmd.append("--kfxgen-embed-original-images")
    run = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    got, stored = _pictures(kfx)
    where = f"calibre {version}, {name}"
    # The cover's picture is shown from the cover's resource, once (#475).
    assert stored == len(PICTURES), f"{where}: {stored} pictures stored"
    for alt, (fmt, data) in got.items():
        assert fmt == "$284" and data[:4] == b"\x89PNG", f"{where}: {alt} {fmt}"
    pics = {alt: Image.open(io.BytesIO(data)) for alt, (_, data) in got.items()}
    # Drawn onto white, no transparency left.
    for alt in "agpt":
        assert "transparency" not in pics[alt].info, f"{where}: {alt}"
        assert pics[alt].mode in ("RGB", "L", "P"), f"{where}: {alt} {pics[alt].mode}"
        left = pics[alt].convert("RGB").getpixel((5, 10))
        assert left == (255, 255, 255), f"{where}: {alt} left {left}"
    right = pics["a"].convert("RGB").getpixel((35, 10))
    assert _near(right, (127, 127, 227)), f"{where}: a right {right}"
    # A grey picture stays grey, a palette one a palette.
    assert pics["g"].mode == "L", f"{where}: g {pics['g'].mode}"
    assert pics["g"].getpixel((35, 10)) == 60, f"{where}"
    assert pics["p"].mode == "P", f"{where}: p {pics['p'].mode}"
    assert pics["p"].convert("RGB").getpixel((35, 10)) == BLUE, f"{where}"
    assert pics["t"].convert("RGB").getpixel((35, 10)) == BLUE, f"{where}"
    # Opaque ones, an alpha channel with no transparent pixel among them,
    # are written as the book has them.
    assert got["o"][1] == PICTURES["o.png"], where
    assert got["f"][1] == PICTURES["f.png"], where
