"""A paragraph's line height, as Kindle Previewer 4.0.3 writes it (#463,
probed 2026-10-26): $42 is its line height as a factor of its own font
size, against the factor most of the book's text has, so the main text is
1lh; and its lh-unit top margin is measured in its own line height, so a
double-spaced paragraph keeps the same gap above it."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val


def _styles(tmp_path, paras):
    out = tmp_path / "l.kfx"
    blocks = [
        {"text": t, "spans": [], "block_style": {"line_height": lh}} for t, lh in paras
    ]
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": "x", "blocks": blocks}],
        output_path=str(out),
    )
    frags = load_fragments(out)
    content = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    styles = {
        str(f.fid): {str(k): v for k, v in val(f).items()}
        for f in by_type(frags, "$157")
    }
    got = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                text = str(content[str(e["$145"]["name"])][int(e["$145"]["$403"])])
                s = styles[str(e["$157"])]
                mt = s.get("$47")
                got[text] = (
                    float(str(s["$42"]["$307"])),
                    float(str(mt["$307"])) if mt is not None else None,
                )
    return got


@pytest.mark.unit
def test_a_wider_line_height_against_the_books_own(tmp_path):
    got = _styles(
        tmp_path,
        [("Main one.", 1.2), ("Main two.", 1.2), ("Main three.", 1.2), ("Wide.", 1.5)],
    )
    assert got["Main one."] == (1.0, 1.0)
    assert got["Wide."] == (1.25, 0.8)


@pytest.mark.unit
def test_the_books_own_line_height_is_1lh_whatever_it_is(tmp_path):
    got = _styles(tmp_path, [("A.", 1.5), ("B.", 1.5), ("C.", 3.0)])
    assert got["A."] == (1.0, 1.0)
    assert got["C."] == (2.0, 0.5)


@pytest.mark.unit
def test_a_tight_line_height_stops_at_0_6(tmp_path):
    got = _styles(tmp_path, [("Main one.", 1.5), ("Main two.", 1.5), ("Tight.", 0.75)])
    assert got["Tight."][0] == 0.6


@pytest.mark.unit
def test_no_line_height_known_writes_1lh(tmp_path):
    got = _styles(tmp_path, [("A.", None), ("B.", None)])
    assert got["A."] == (1.0, 1.0)
