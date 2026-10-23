"""Bold faces in header cells and weight-600 faces (#50, #282).

The Kindle matches a run to an embedded face by family, weight and style,
so a style must name the face its text is drawn in and say its weight
(#50). Three mutations the #282 recheck found every test still passes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.font_table import Face, FontTable  # noqa: E402
from kfxgen.inline_style import FLAG_ITALIC  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val  # noqa: E402
from tests.unit.test_native_generator import _tiny_table  # noqa: E402

pytestmark = pytest.mark.unit

FOO = {"font_family": ["foo"], "bold": False, "italic": False}


def _faces(*weights):
    return FontTable(
        [
            Face(
                "foo",
                w,
                False,
                b"\x00\x01\x00\x00" + bytes([i]),
                f"foo-{w}",
                f"resource/font{i}",
            )
            for i, w in enumerate(weights)
        ]
    )


def _write(tmp_path, blocks, font_table=None):
    out = tmp_path / "f.kfx"
    NativeKFXGenerator().generate_full_book(
        title="T",
        author="A",
        chapters=[{"title": "C", "text": "x", "blocks": blocks}],
        font_table=font_table,
        output_path=str(out),
    )
    return load_fragments(out)


def _styles_by_text(frags):
    """{text: its style} for each text entry, and {run text: its style}."""
    texts = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {str(f.fid): val(f) for f in by_type(frags, "$157")}
    entries, runs = {}, {}
    for story in by_type(frags, "$259"):
        for e in iter_entries(val(story)["$146"]):
            ref = e.get("$145")
            if not ref:
                continue
            text = str(texts[str(ref["name"])][ref["$403"]])
            entries[text] = styles.get(str(e.get("$157")), {})
            for r in e.get("$142") or ():
                start = int(r["$143"])
                runs[text[start : start + int(r["$144"])]] = styles.get(
                    str(r.get("$157")), {}
                )
    return entries, runs


def _header_table(rows):
    table = _tiny_table(rows)
    for row in table["table"]["rows"]:
        for cell in row["cells"]:
            cell["block_style"] = dict(FOO)
    for cell in table["table"]["rows"][0]["cells"]:
        cell["header"] = True
    return table


def test_a_header_cell_names_the_bold_face(tmp_path):
    frags = _write(tmp_path, [_header_table([["Head"], ["Body"]])], _faces(400, 700))
    entries, _ = _styles_by_text(frags)
    assert str(entries["Head"].get("$11")) == "foo-700"
    assert str(entries["Body"].get("$11")) == "foo-400"


def test_a_weight_600_face_is_declared_bold(tmp_path):
    frags = _write(tmp_path, [{"text": "Text.", "spans": []}], _faces(600))
    (face,) = by_type(frags, "$262")
    assert str(val(face).get("$13")) == "$361"


def test_an_italic_run_in_a_header_cells_paragraph_stays_bold(tmp_path):
    # Without embedded fonts too: the header makes the paragraph bold, and a
    # run's style names its whole face, so it says bold as well (#219, #261).
    table = _header_table([["Head note"], ["Body"]])
    cell = table["table"]["rows"][0]["cells"][0]
    cell["paragraphs"] = [
        {
            "text": "Head note",
            "spans": [(5, 4, frozenset({FLAG_ITALIC}))],
            "anchor_keys": [],
            "anchor_offsets": {},
        }
    ]
    _, runs = _styles_by_text(_write(tmp_path, [table]))
    assert str(runs["note"].get("$12")) == "$382"
    assert str(runs["note"].get("$13")) == "$361"
