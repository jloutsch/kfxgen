"""The book's contents and start landmarks (#579), as Kindle Previewer 4.0.3
writes them: `toc` and `bodymatter` from an EPUB 3 landmarks nav, else
`toc` and `text` from the OPF guide, each landing where its link points.
The end-to-end cases, each checked against Previewer's file, are in
tests/integration/test_calibre_landmarks.py."""

from types import SimpleNamespace

import pytest

from kfxgen import converter as _conv
from kfxgen.native_generator import NativeKFXGenerator
from tests._helpers import jpeg_of
from tests._kfx_introspect import by_type, iter_entries, load_fragments, val

pytestmark = pytest.mark.unit

OPS = 'xmlns:epub="http://www.idpf.org/2007/ops"'


def _book(tmp_path, guide="", nav=None, nav_href="nav.xhtml"):
    """An unpacked book with its OPF in OEBPS/, and a nav document if given."""
    (tmp_path / "OEBPS").mkdir(exist_ok=True)
    item = (
        f'<item id="nav" href="{nav_href}" media-type="application/xhtml+xml" '
        'properties="nav"/>'
        if nav is not None
        else ""
    )
    (tmp_path / "OEBPS" / "content.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        f'version="3.0"><manifest>{item}</manifest><spine/>'
        f"{f'<guide>{guide}</guide>' if guide else ''}</package>"
    )
    if nav is not None:
        path = tmp_path / "OEBPS" / nav_href
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" {OPS}>'
            f"<body>{nav}</body></html>"
        )
    return SimpleNamespace(
        container=SimpleNamespace(rootdir=str(tmp_path), opfname="OEBPS/content.opf")
    )


def _landmarks_nav(links):
    return (
        '<nav epub:type="landmarks"><ol>'
        + "".join(
            f'<li><a epub:type="{t}" href="{h}">{label}</a></li>'
            for t, h, label in links
        )
        + "</ol></nav>"
    )


def test_the_guides_contents_and_text_references(tmp_path):
    book = _book(
        tmp_path,
        '<reference type="cover" title="Cover" href="cover.xhtml"/>'
        '<reference type="toc" title="Contents" href="toc.xhtml"/>'
        '<reference type="text" title="Begin" href="c1.xhtml#start"/>'
        '<reference type="copyright-page" title="C" href="c.xhtml"/>',
    )
    assert _conv._book_landmarks(book) == [
        ("toc", "Contents", "toc.xhtml"),
        ("start", "Begin", "c1.xhtml#start"),
    ]


def test_a_landmarks_nav_wins_over_the_guide(tmp_path):
    book = _book(
        tmp_path,
        '<reference type="toc" title="Guide" href="other.xhtml"/>',
        _landmarks_nav(
            [("toc", "toc.xhtml", "Contents"), ("bodymatter", "c2.xhtml", "Start")]
        ),
    )
    assert _conv._book_landmarks(book) == [
        ("toc", "Contents", "toc.xhtml"),
        ("start", "Start", "c2.xhtml"),
    ]


def test_a_nav_in_its_own_folder_names_files_from_the_opfs(tmp_path):
    book = _book(
        tmp_path,
        nav=_landmarks_nav([("toc", "../Text/toc.xhtml#t", "Contents")]),
        nav_href="Nav/nav.xhtml",
    )
    assert _conv._book_landmarks(book) == [("toc", "Contents", "Text/toc.xhtml#t")]


def test_a_nav_with_no_landmarks_falls_back_to_the_guide(tmp_path):
    book = _book(
        tmp_path,
        '<reference type="toc" title="Contents" href="toc.xhtml"/>',
        '<nav epub:type="toc"><ol><li><a href="c1.xhtml">One</a></li></ol></nav>',
    )
    assert _conv._book_landmarks(book) == [("toc", "Contents", "toc.xhtml")]


def test_a_link_out_of_the_book_is_dropped(tmp_path):
    book = _book(
        tmp_path, '<reference type="toc" title="Contents" href="../../../x.xhtml"/>'
    )
    assert _conv._book_landmarks(book) == []


def test_no_unpacked_book_no_landmarks():
    assert _conv._book_landmarks(SimpleNamespace()) == []


def _written(tmp_path, landmarks, cover=None):
    out = tmp_path / "l.kfx"
    chapters = [
        {
            "title": f"Chapter {n}",
            "text": f"Words {n}.",
            "blocks": [{"text": f"Words {n}.", "spans": [], "anchor_keys": [key]}],
        }
        for n, key in ((1, "c1.xhtml#a"), (2, "c2.xhtml#b"))
    ]
    NativeKFXGenerator().generate_full_book(
        "T", "A", chapters, output_path=str(out), cover_image=cover, landmarks=landmarks
    )
    frags = load_fragments(out)
    text = {str(f.fid): list(val(f)["$146"]) for f in by_type(frags, "$145")}
    words = {}
    for st in by_type(frags, "$259"):
        for e in iter_entries(val(st)["$146"]):
            if hasattr(e, "get") and e.get("$145"):
                words[int(e["$155"])] = str(
                    text[str(e["$145"]["name"])][int(e["$145"]["$403"])]
                )
    (nav,) = by_type(frags, "$389")
    got = []
    for c in val(nav)[0]["$392"]:
        c = getattr(c, "value", c)
        if str(c["$235"]) == "$236":
            for u in c["$247"]:
                u = getattr(u, "value", u)
                got.append(
                    (
                        str(u["$238"]),
                        str(u["$241"]["$244"]),
                        words.get(int(u["$246"]["$155"])),
                    )
                )
    return got


def test_the_generator_writes_each_landmark_where_its_link_lands(tmp_path):
    got = _written(
        tmp_path,
        [
            ("toc", "Contents", "c1.xhtml"),
            ("start", "Start", "c2.xhtml#b"),
            ("toc", "Nowhere", "gone.xhtml"),
        ],
    )
    assert [g for g in got if g[0] != "$233"] == [
        ("$212", "Contents", "Words 1."),
        ("$396", "Start", "Words 2."),
    ]


def test_the_cover_landmark_comes_first(tmp_path):
    got = _written(tmp_path, [("toc", "Contents", "c1.xhtml")], cover=jpeg_of(600, 800))
    assert [g[:2] for g in got] == [("$233", "cover-nav-unit"), ("$212", "Contents")]


def test_a_landmark_inside_a_paragraph_keeps_its_offset(tmp_path):
    out = tmp_path / "o.kfx"
    block = {
        "text": "Before mid after.",
        "spans": [],
        "anchor_keys": ["c1.xhtml#mid"],
        "anchor_offsets": {"c1.xhtml#mid": 7},
    }
    NativeKFXGenerator().generate_full_book(
        "T",
        "A",
        [{"title": "One", "text": block["text"], "blocks": [block]}],
        output_path=str(out),
        landmarks=[("start", "Start", "c1.xhtml#mid")],
    )
    (nav,) = by_type(load_fragments(out), "$389")
    (unit,) = [
        getattr(u, "value", u)
        for c in val(nav)[0]["$392"]
        if str(getattr(c, "value", c)["$235"]) == "$236"
        for u in getattr(c, "value", c)["$247"]
    ]
    assert int(unit["$246"]["$143"]) == 7
