"""Tier-2 integration for #15 embedded-font carriage + application.

Packs the committed four-face fixture (test_books/font-matching-test — real
Charis SIL R/B/I/BI under the OFL) into an .epub, wraps it with the
Calibre-free EpubAsOeb shim, and drives the real generator. It verifies the
end-to-end path this environment can run without Calibre:

  real .epub -> real font bytes (manifest) -> build_font_table -> $418/$262
  fragments (+ linkage) -> $11 applied to $157 styles with the real slugs.

The @font-face *rule* parsing and per-element computed font-family are
Calibre's Stylizer (covered by the Phase-0 spike + the manual device gate),
so the rules are injected here and the applied chapter carries block_style
directly — the same internal contract extract_chapters_from_oeb produces.
"""

import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.font_table import build_font_table  # noqa: E402
from kfxgen.inline_style import FLAG_BOLD, FLAG_ITALIC  # noqa: E402
from kfxgen.kfxlib_minimal.ion import IS  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402
from tests.fixtures.oeb_shim import EpubAsOeb  # noqa: E402

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO / "test_books" / "font-matching-test"

# The fixture's four @font-face rules (family "Charis SIL"). In production the
# Calibre Stylizer parses these from chapter1.xhtml's <style>; injected here so
# the test runs without Calibre.
_RULES = [
    {
        "font-family": "Charis SIL",
        "font-weight": "normal",
        "font-style": "normal",
        "src": "url(fonts/CharisSILR.ttf)",
    },
    {
        "font-family": "Charis SIL",
        "font-weight": "bold",
        "font-style": "normal",
        "src": "url(fonts/CharisSILB.ttf)",
    },
    {
        "font-family": "Charis SIL",
        "font-weight": "normal",
        "font-style": "italic",
        "src": "url(fonts/CharisSILI.ttf)",
    },
    {
        "font-family": "Charis SIL",
        "font-weight": "bold",
        "font-style": "italic",
        "src": "url(fonts/CharisSILBI.ttf)",
    },
]


class _NullLog:
    def info(self, *a):
        pass

    def warn(self, *a):
        pass

    warning = warn


class _Stylizer:
    def __init__(self, rules):
        self.font_face_rules = rules


def _pack_epub(src_dir, out_path):
    """Zip an unpacked EPUB dir, mimetype first and stored (spec requirement)."""
    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr(
            "mimetype",
            (src_dir / "mimetype").read_bytes(),
            compress_type=zipfile.ZIP_STORED,
        )
        for p in sorted(src_dir.rglob("*")):
            if p.is_file() and p.name != "mimetype":
                zf.write(
                    p,
                    p.relative_to(src_dir).as_posix(),
                    compress_type=zipfile.ZIP_DEFLATED,
                )


@pytest.fixture(scope="module")
def font_table(tmp_path_factory):
    if not _FIXTURE.is_dir():
        pytest.skip(f"font fixture missing: {_FIXTURE}")
    epub = tmp_path_factory.mktemp("font_epub") / "font-matching-test.epub"
    _pack_epub(_FIXTURE, epub)
    oeb = EpubAsOeb(epub)
    return build_font_table(
        oeb, _NullLog(), stylizer_factory=lambda item: _Stylizer(_RULES)
    )


def test_real_epub_yields_four_faces_with_ttf_bytes(font_table):
    faces = font_table.faces
    assert len(faces) == 4
    # All four faces carry real TrueType bytes (magic 0x00010000).
    assert all(f.data[:4] == b"\x00\x01\x00\x00" for f in faces)
    assert {f.emitted_family for f in faces} == {
        "charis-sil-400",
        "charis-sil-700",
        "charis-sil-400i",
        "charis-sil-700i",
    }


def test_font_fragments_emitted_and_linked(font_table):
    g = NativeKFXGenerator()
    g.generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "C1", "text": "Hello."}],
        font_table=font_table,
    )
    raw = {f.fid: f for f in g.fragments if str(f.ftype) == "$418"}
    decls = [f for f in g.fragments if str(f.ftype) == "$262"]
    assert len(raw) == 4 and len(decls) == 4
    # Each $262 links to a real $418 raw-font fragment via $165 -> fid, and its
    # $11 family is one of the emitted slugs.
    slugs = {f.emitted_family for f in font_table.faces}
    for d in decls:
        assert d.value[IS("$11")] in slugs
        assert IS(d.value[IS("$165")]) in raw


def test_font_applied_to_styles_with_real_slugs(font_table):
    # A paragraph in "Charis SIL" with a bold span, carrying block_style as
    # extract_chapters_from_oeb would. Body resolves to the real regular slug;
    # the bold span resolves to the real bold slug (no synthetic weight).
    g = NativeKFXGenerator()
    chapter = {
        "title": "C1",
        "text": "Normal bold.",
        "blocks": [
            {
                "text": "Normal bold.",
                "spans": [(7, 4, frozenset({FLAG_BOLD}))],
                "block_style": {"font_family": ["charis sil"]},
            }
        ],
    }
    g.generate_full_book(
        title="T", author="A", chapters=[chapter], font_table=font_table
    )
    styles = [f for f in g.fragments if str(f.ftype) == "$157"]
    fams = {f.value[IS("$11")] for f in styles if IS("$11") in f.value}
    assert "charis-sil-400" in fams
    assert "charis-sil-700" in fams


def _styles_by_family(g):
    out = {}
    for f in g.fragments:
        if str(f.ftype) == "$157" and IS("$11") in f.value:
            out.setdefault(f.value[IS("$11")], []).append(f.value)
    return out


def test_span_face_descriptor_matches_262(font_table):
    # #50: a bold/italic span selecting a real face must carry the matching
    # weight/style descriptor on its $157 ($13=$361 / $12=$382), so the Kindle
    # resolves the same face the $262 declares. Suppressing them (normal weight)
    # left the on-device reader unable to find the face.
    g = NativeKFXGenerator()
    chapter = {
        "title": "C1",
        "text": "Bold italic.",
        "blocks": [
            {
                "text": "Bold italic.",
                "spans": [
                    (0, 4, frozenset({FLAG_BOLD})),
                    (5, 6, frozenset({FLAG_ITALIC})),
                ],
                "block_style": {"font_family": ["charis sil"]},
            }
        ],
    }
    g.generate_full_book(
        title="T", author="A", chapters=[chapter], font_table=font_table
    )
    by_fam = _styles_by_family(g)
    assert by_fam.get("charis-sil-700"), "bold span should reference the bold face"
    for v in by_fam["charis-sil-700"]:
        assert IS("$13") in v and v[IS("$13")] == IS("$361")
    assert by_fam.get("charis-sil-400i"), "italic span should reference the italic face"
    for v in by_fam["charis-sil-400i"]:
        assert IS("$12") in v and v[IS("$12")] == IS("$382")


def test_block_bold_face_descriptor_matches_262(font_table):
    # #50: block-level CSS bold (font-weight on the paragraph) selecting the real
    # bold face must carry $13=$361 on the entry $157. This is the path that
    # broke on-device for the four-face book — the face was assigned but the
    # applied style had normal weight, so it never matched.
    g = NativeKFXGenerator()
    chapter = {
        "title": "C1",
        "text": "Bold para.",
        "blocks": [
            {
                "text": "Bold para.",
                "spans": [],
                "block_style": {"font_family": ["charis sil"], "bold": True},
            }
        ],
    }
    g.generate_full_book(
        title="T", author="A", chapters=[chapter], font_table=font_table
    )
    by_fam = _styles_by_family(g)
    assert by_fam.get("charis-sil-700"), "bold paragraph should reference the bold face"
    for v in by_fam["charis-sil-700"]:
        assert IS("$13") in v and v[IS("$13")] == IS("$361")
