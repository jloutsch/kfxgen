# Native KFX Font Embedding (#15) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry a source EPUB's embedded `@font-face` TTF/OTF fonts through kfxgen's native KFX generator so text renders in those fonts on a Kindle, with full face matching (regular / bold / italic / bold-italic per run).

**Architecture:** A new isolated `font_table.py` builds a `FontTable` from Calibre's `Stylizer.font_face_rules` + OEB manifest bytes (carriage source of truth + `.match()` for application). The generator gains `$418` (raw font BLOB) and `$262` (`@font-face` decl) fragments — direct analogs of the existing image `$417`/`$164`. Application sets `$11` (font-family) on `$157` styles via `FontTable.match()`, resolved from block-level computed `font-family` + per-run bold/italic.

**Tech Stack:** Python 3.13, Calibre plugin runtime (frozen Python), pytest, ruff 0.15.1. No new dependencies.

## Global Constraints

- **No new runtime dependencies.** TTF/OTF only (magic-byte validated); WOFF/WOFF2/other → skip + warn. No `fonttools`/`brotli`.
- **CACE guardrail:** a book with no embeddable fonts MUST produce byte-identical KFX output vs. current `main`. Guaranteed by the `emitted_family is None` path.
- **No `kfxlib_minimal` change** — all font symbols (`$262 $418 $165 $11 $12 $13 $15 $350 $361 $382`) already resolve via the imported catalog.
- **Lint gate (both):** `.venv/bin/python -m ruff check` AND `.venv/bin/python -m ruff format --check` (ruff pinned 0.15.1).
- **Test runner:** `.venv/bin/python -m pytest`. Tier-2 (needs Calibre `Stylizer`) marked `@pytest.mark.integration`.
- **Emitted family names** are internal, deterministic, lowercased slugs shared identically between `$262.$11` and `$157.$11`.
- **Symbols:** weight `$361` (bold) / `$350` (normal); style `$382` (italic) / `$350` (normal). `$11`/`$165` emitted as plain strings (confirm in Task 0).

---

## Task 0: Phase-0 spike — verify `font_face_rules` shape (investigation, no production code)

**Files:**
- Create: `tools/inspect_font_face_rules.py` (throwaway diagnostic; committed for reproducibility)
- Modify: `docs/superpowers/specs/2026-07-01-native-font-embed-design.md` (pin confirmed constants)

**Goal:** Confirm the exact per-rule dict keys Calibre exposes and whether `src` is pre-resolved, before Tasks 2/4 depend on them. Also confirm Calibre de-obfuscates fonts on import.

- [ ] **Step 1: Write the inspection script**

```python
# tools/inspect_font_face_rules.py
"""Dump Calibre Stylizer.font_face_rules for a font-embedding EPUB.

Run with Calibre's bundled Python:
  /Applications/calibre.app/Contents/MacOS/calibre-debug tools/inspect_font_face_rules.py <book.epub>
"""
import sys
from calibre.ebooks.oeb.reader import OEBReader  # noqa: PLC0415
from calibre.ebooks.conversion.plumber import Plumber
from calibre.utils.logging import Log


def main(path):
    log = Log()
    plumber = Plumber(path, path + ".ignore.kfx", log)
    plumber.setup_options()
    oeb = plumber.input_plugin(open(path, "rb"), plumber.opts, "epub", log, "/tmp/fontspike")
    from calibre.ebooks.oeb.stylizer import Stylizer
    for item in oeb.spine:
        try:
            st = Stylizer(item.data, item.href, oeb, oeb.opts,
                          getattr(oeb.opts, "output_profile", None))
        except Exception as e:
            print("stylizer fail", item.href, e); continue
        rules = getattr(st, "font_face_rules", [])
        if rules:
            print("ITEM", item.href, "rules:", len(rules))
            for r in rules:
                print("  keys:", sorted(r.keys()) if hasattr(r, "keys") else type(r))
                print("  rule:", dict(r) if hasattr(r, "keys") else r)
            break
    print("manifest fonts:")
    for it in oeb.manifest:
        mt = (getattr(it, "media_type", "") or "").lower()
        if "font" in mt or (getattr(it, "href", "") or "").lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
            data = getattr(it, "data", b"") or b""
            print("  ", it.href, mt, "magic", bytes(data[:4]).hex() if data else "-")


if __name__ == "__main__":
    main(sys.argv[1])
```

- [ ] **Step 2: Run against a real font-embedding EPUB**

Run: `/Applications/calibre.app/Contents/MacOS/calibre-debug tools/inspect_font_face_rules.py <local font book>.epub`
Expected: prints each `@font-face` rule's keys (expect `font-family`, `src`, and optionally `font-weight`/`font-style`), the `src` value form (bare href vs. `url(...)`), and manifest font magic bytes (confirm TTF `00010000`/OTF `4f54544f` after Calibre de-obfuscation, NOT random-looking obfuscated bytes).

- [ ] **Step 3: Record findings in the spec**

Edit the design spec: under "Fragment shapes", add a `## Phase 0 confirmed` note recording (a) the exact rule dict keys, (b) whether `src` is a bare manifest href or a CSS `url(...)` string, (c) that manifest font bytes are de-obfuscated. Tasks 2 and 4 code against these.

- [ ] **Step 4: Commit**

```bash
git add tools/inspect_font_face_rules.py docs/superpowers/specs/2026-07-01-native-font-embed-design.md
git commit -m "spike: confirm Calibre font_face_rules shape (#15 Phase 0)"
```

> **Note for later tasks:** the code below assumes rules expose `.get('font-family')`, `.get('font-weight')`, `.get('font-style')`, `.get('src')`, and that `src` may be either a bare href or a `url(...)` string (both handled). If Task 0 shows different keys, adjust the accessors in Tasks 2/4 only — the `Face`/`FontTable`/fragment interfaces do not change.

---

## Task 1: `Face` + primitives in `font_table.py`

**Files:**
- Create: `plugin/kfxgen/font_table.py`
- Test: `plugin/tests/test_font_table.py`

**Interfaces:**
- Produces: `Face` (dataclass: `css_family: str`, `weight: int`, `italic: bool`, `data: bytes`, `emitted_family: str`, `location: str`); `is_ttf_otf(data: bytes) -> bool`; `normalize_family(name: str) -> str`; `parse_weight(v) -> int`; `parse_style(v) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# plugin/tests/test_font_table.py
import pytest
from kfxgen.font_table import (
    Face, is_ttf_otf, normalize_family, parse_weight, parse_style,
)

pytestmark = pytest.mark.unit


def test_is_ttf_otf_accepts_truetype_and_opentype():
    assert is_ttf_otf(b"\x00\x01\x00\x00rest")      # TrueType
    assert is_ttf_otf(b"OTTOrest")                   # OpenType/CFF
    assert is_ttf_otf(b"truerest")                   # legacy TrueType
    assert is_ttf_otf(b"ttcfrest")                   # TrueType collection


def test_is_ttf_otf_rejects_woff_and_junk():
    assert not is_ttf_otf(b"wOFFrest")   # WOFF
    assert not is_ttf_otf(b"wOF2rest")   # WOFF2
    assert not is_ttf_otf(b"\x89PNGabc")
    assert not is_ttf_otf(b"")
    assert not is_ttf_otf(b"ab")


def test_normalize_family_strips_quotes_and_lowercases():
    assert normalize_family('"Merriweather"') == "merriweather"
    assert normalize_family("  'Foo Bar' ") == "foo bar"
    assert normalize_family("Georgia") == "georgia"
    assert normalize_family(None) == ""


def test_parse_weight():
    assert parse_weight("bold") == 700
    assert parse_weight("normal") == 400
    assert parse_weight(None) == 400
    assert parse_weight("600") == 600
    assert parse_weight("700 ") == 700
    assert parse_weight("weird") == 400


def test_parse_style():
    assert parse_style("italic") is True
    assert parse_style("oblique") is True
    assert parse_style("normal") is False
    assert parse_style(None) is False


def test_face_is_a_dataclass():
    f = Face(css_family="foo", weight=700, italic=True,
             data=b"\x00\x01\x00\x00", emitted_family="foo-700i",
             location="resource/font0")
    assert f.weight == 700 and f.italic is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kfxgen.font_table'`

- [ ] **Step 3: Implement**

```python
# plugin/kfxgen/font_table.py
"""Build embedded-font resources (@font-face) for native KFX output (#15).

Isolated from the generator so the risky parsing/matching logic is unit-tested
without Calibre. Calibre only appears in build_font_table (Task 4).
"""
from dataclasses import dataclass

# TrueType/OpenType magic bytes. WOFF (wOFF) / WOFF2 (wOF2) are intentionally
# excluded — Kindle cannot embed them and decoding needs deps we don't carry.
_TTF_OTF_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


@dataclass
class Face:
    css_family: str      # normalized (lowercased, unquoted) source family
    weight: int          # 100..900; bold->700, normal->400
    italic: bool
    data: bytes          # raw TTF/OTF bytes
    emitted_family: str  # internal slug, shared by $262.$11 and $157.$11
    location: str        # $418 fid, e.g. "resource/font0"


def is_ttf_otf(data):
    return bool(data) and data[:4] in _TTF_OTF_MAGIC


def normalize_family(name):
    if not name:
        return ""
    return name.strip().strip("\"'").strip().lower()


def parse_weight(v):
    if v is None:
        return 400
    s = str(v).strip().lower()
    if s == "bold":
        return 700
    if s == "normal":
        return 400
    try:
        return int(s)
    except ValueError:
        return 400


def parse_style(v):
    return str(v or "").strip().lower() in ("italic", "oblique")
```

- [ ] **Step 4: Run to verify pass + lint**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -v && .venv/bin/python -m ruff check plugin/kfxgen/font_table.py && .venv/bin/python -m ruff format --check plugin/kfxgen/font_table.py`
Expected: all PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/font_table.py plugin/tests/test_font_table.py
git commit -m "feat: Face + font primitives for #15 font embedding"
```

---

## Task 2: `faces_from_rules` — rules + manifest → faces

**Files:**
- Modify: `plugin/kfxgen/font_table.py`
- Test: `plugin/tests/test_font_table.py`

**Interfaces:**
- Consumes: `Face`, `is_ttf_otf`, `normalize_family`, `parse_weight`, `parse_style` (Task 1).
- Produces: `extract_src_urls(src: str) -> list[str]`; `emitted_name(css_family, weight, italic, taken: set) -> str`; `faces_from_rules(rules: list, manifest_lookup, log) -> list[Face]` where `manifest_lookup(href) -> bytes | None`.

- [ ] **Step 1: Write the failing tests**

```python
# append to plugin/tests/test_font_table.py
from kfxgen.font_table import extract_src_urls, emitted_name, faces_from_rules


class _Log:
    def __init__(self): self.warns = []
    def warn(self, m): self.warns.append(m)
    def info(self, m): pass
    warning = warn


def test_extract_src_urls_handles_bare_href_and_url_list():
    assert extract_src_urls("fonts/x.ttf") == ["fonts/x.ttf"]
    got = extract_src_urls("url(fonts/x.woff2) format('woff2'), url('fonts/x.ttf')")
    assert got == ["fonts/x.woff2", "fonts/x.ttf"]


def test_emitted_name_is_deterministic_and_unique():
    taken = set()
    a = emitted_name("Merriweather", 700, True, taken)
    assert a == "merriweather-700i"
    b = emitted_name("Merriweather", 700, True, taken)  # collision
    assert b != a and b.startswith("merriweather-700i")


def _mk_manifest(mapping):
    return lambda href: mapping.get(href)


def test_faces_from_rules_emits_ttf_prefers_over_woff2():
    log = _Log()
    rules = [{
        "font-family": '"Foo"', "font-weight": "bold", "font-style": "italic",
        "src": "url(f.woff2) format('woff2'), url(f.ttf)",
    }]
    manifest = _mk_manifest({"f.woff2": b"wOF2xx", "f.ttf": b"\x00\x01\x00\x00yy"})
    faces = faces_from_rules(rules, manifest, log)
    assert len(faces) == 1
    f = faces[0]
    assert f.css_family == "foo" and f.weight == 700 and f.italic is True
    assert f.data[:4] == b"\x00\x01\x00\x00"
    assert f.emitted_family == "foo-700i" and f.location == "resource/font0"


def test_faces_from_rules_skips_when_only_woff_available():
    log = _Log()
    rules = [{"font-family": "Foo", "src": "url(f.woff2)"}]
    manifest = _mk_manifest({"f.woff2": b"wOF2xx"})
    faces = faces_from_rules(rules, manifest, log)
    assert faces == []
    assert any("woff" in w.lower() or "skip" in w.lower() for w in log.warns)


def test_faces_from_rules_dedups_same_href():
    log = _Log()
    rules = [
        {"font-family": "Foo", "src": "url(f.ttf)"},
        {"font-family": "Foo", "src": "url(f.ttf)"},
    ]
    manifest = _mk_manifest({"f.ttf": b"\x00\x01\x00\x00yy"})
    faces = faces_from_rules(rules, manifest, log)
    assert len(faces) == 1


def test_faces_from_rules_skips_rule_without_family():
    log = _Log()
    faces = faces_from_rules([{"src": "url(f.ttf)"}],
                             _mk_manifest({"f.ttf": b"\x00\x01\x00\x00"}), _Log())
    assert faces == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -k "src or emitted or faces_from" -v`
Expected: FAIL — `ImportError: cannot import name 'faces_from_rules'`

- [ ] **Step 3: Implement**

```python
# append to plugin/kfxgen/font_table.py
import re

_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


def extract_src_urls(src):
    """Return ordered candidate hrefs from a CSS `src` (or a bare href)."""
    if not src:
        return []
    s = str(src)
    urls = _URL_RE.findall(s)
    if urls:
        return [u.strip() for u in urls]
    return [s.strip()]  # bare href (some Calibre versions pre-resolve src)


def _slug(name):
    out = re.sub(r"[^a-z0-9]+", "-", normalize_family(name)).strip("-")
    return out or "font"


def emitted_name(css_family, weight, italic, taken):
    base = f"{_slug(css_family)}-{weight}{'i' if italic else ''}"
    name = base
    n = 1
    while name in taken:
        name = f"{base}-{n}"
        n += 1
    taken.add(name)
    return name


def faces_from_rules(rules, manifest_lookup, log):
    """Turn @font-face rule dicts + a manifest byte-lookup into Face objects.

    Skips (with a warning) rules whose family is missing or whose only
    resolvable src bytes are not TTF/OTF. Dedups by resolved href.
    """
    faces = []
    taken_names = set()
    seen_hrefs = set()
    idx = 0
    for rule in rules:
        get = rule.get if hasattr(rule, "get") else (lambda k: None)
        family = normalize_family(get("font-family"))
        if not family:
            continue
        weight = parse_weight(get("font-weight"))
        italic = parse_style(get("font-style"))
        chosen_href = None
        chosen_data = None
        for href in extract_src_urls(get("src")):
            data = manifest_lookup(href)
            if data and is_ttf_otf(data):
                chosen_href, chosen_data = href, data
                break
        if chosen_data is None:
            log.warn(
                f"  Skipping @font-face {family!r}: no embeddable TTF/OTF src "
                f"(WOFF/WOFF2/missing not supported)"
            )
            continue
        if chosen_href in seen_hrefs:
            continue
        seen_hrefs.add(chosen_href)
        faces.append(Face(
            css_family=family, weight=weight, italic=italic, data=chosen_data,
            emitted_family=emitted_name(family, weight, italic, taken_names),
            location=f"resource/font{idx}",
        ))
        idx += 1
    return faces
```

- [ ] **Step 4: Run to verify pass + lint**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -v && .venv/bin/python -m ruff check plugin/kfxgen/font_table.py && .venv/bin/python -m ruff format --check plugin/kfxgen/font_table.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/font_table.py plugin/tests/test_font_table.py
git commit -m "feat: faces_from_rules parsing + dedup for #15"
```

---

## Task 3: `FontTable` + `.match()`

**Files:**
- Modify: `plugin/kfxgen/font_table.py`
- Test: `plugin/tests/test_font_table.py`

**Interfaces:**
- Consumes: `Face` (Task 1).
- Produces: `FontTable(faces: list[Face])` with `.faces` and `.match(family_list: list[str], bold: bool, italic: bool) -> tuple[str | None, bool, bool]` returning `(emitted_family, weight_ok, style_ok)`. `weight_ok`/`style_ok` True means "nothing to synthesize".

- [ ] **Step 1: Write the failing tests**

```python
# append to plugin/tests/test_font_table.py
from kfxgen.font_table import FontTable


def _face(fam, w, it):
    return Face(css_family=fam, weight=w, italic=it, data=b"\x00\x01\x00\x00",
                emitted_family=f"{fam}-{w}{'i' if it else ''}",
                location="resource/x")


def test_match_no_embedded_family_returns_none():
    t = FontTable([_face("foo", 400, False)])
    assert t.match(["bar", "serif"], bold=False, italic=False) == (None, False, False)
    assert t.match([], bold=True, italic=False) == (None, False, False)


def test_match_exact_regular():
    t = FontTable([_face("foo", 400, False)])
    assert t.match(["foo"], bold=False, italic=False) == ("foo-400", True, True)


def test_match_exact_bold_face():
    t = FontTable([_face("foo", 400, False), _face("foo", 700, False)])
    assert t.match(["foo"], bold=True, italic=False) == ("foo-700", True, True)


def test_match_bold_requested_only_regular_embedded_falls_back_synthetic():
    t = FontTable([_face("foo", 400, False)])
    # regular family applied, but weight not satisfied -> caller synthesizes bold
    assert t.match(["foo"], bold=True, italic=False) == ("foo-400", False, True)


def test_match_italic_requested_only_regular_embedded():
    t = FontTable([_face("foo", 400, False)])
    assert t.match(["foo"], bold=False, italic=True) == ("foo-400", True, False)


def test_match_exact_bold_italic():
    t = FontTable([_face("foo", 400, False), _face("foo", 700, True)])
    assert t.match(["foo"], bold=True, italic=True) == ("foo-700i", True, True)


def test_match_family_list_uses_first_embedded():
    t = FontTable([_face("bar", 400, False)])
    # "foo" not embedded, "bar" is -> use bar
    assert t.match(["foo", "bar"], bold=False, italic=False) == ("bar-400", True, True)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -k match -v`
Expected: FAIL — `ImportError: cannot import name 'FontTable'`

- [ ] **Step 3: Implement**

```python
# append to plugin/kfxgen/font_table.py
class FontTable:
    """Embedded faces + face-matching for application (#15)."""

    def __init__(self, faces):
        self.faces = list(faces)
        self._by_family = {}
        for f in self.faces:
            self._by_family.setdefault(f.css_family, []).append(f)

    def match(self, family_list, bold, italic):
        """Resolve (family_list, bold, italic) to a face.

        Returns (emitted_family | None, weight_ok, style_ok). *_ok True means
        the chosen face already satisfies that axis, so the caller should NOT
        synthesize it. When no family is embedded, returns (None, False, False)
        so the caller reproduces today's synthetic behavior unchanged.
        """
        fam_faces = None
        for fam in (family_list or []):
            if fam in self._by_family:
                fam_faces = self._by_family[fam]
                break
        if not fam_faces:
            return (None, False, False)

        want_bold = bool(bold)
        want_italic = bool(italic)
        # Exact face: weight side (>=600 == bold) and italic side both match.
        for f in fam_faces:
            if (f.weight >= 600) == want_bold and f.italic == want_italic:
                return (f.emitted_family, True, True)
        # Fallback: regular face (weight<600, upright), else any face.
        regular = next(
            (f for f in fam_faces if f.weight < 600 and not f.italic),
            fam_faces[0],
        )
        return (regular.emitted_family, not want_bold, not want_italic)
```

- [ ] **Step 4: Run to verify pass + lint**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -v && .venv/bin/python -m ruff check plugin/kfxgen/font_table.py && .venv/bin/python -m ruff format --check plugin/kfxgen/font_table.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/font_table.py plugin/tests/test_font_table.py
git commit -m "feat: FontTable.match face matching for #15"
```

---

## Task 4: `build_font_table` — Calibre integration

**Files:**
- Modify: `plugin/kfxgen/font_table.py`
- Test: `plugin/tests/test_font_table.py`

**Interfaces:**
- Consumes: `faces_from_rules`, `FontTable`.
- Produces: `build_manifest_lookup(oeb_book) -> callable`; `build_font_table(oeb_book, log, stylizer_factory=None) -> FontTable`. `stylizer_factory(item) -> stylizer_or_None` is injectable for tests; default builds Calibre `Stylizer`.

- [ ] **Step 1: Write the failing tests** (no Calibre — inject fakes)

```python
# append to plugin/tests/test_font_table.py
from kfxgen.font_table import build_font_table, build_manifest_lookup


class _Item:
    def __init__(self, href, data, media_type="application/font-sfnt"):
        self.href, self.data, self.media_type = href, data, media_type


class _Manifest(list):
    pass


class _Oeb:
    def __init__(self, items, spine):
        self.manifest = _Manifest(items)
        self.spine = spine


class _Stylizer:
    def __init__(self, rules): self.font_face_rules = rules


def test_build_manifest_lookup_resolves_by_href_and_basename():
    oeb = _Oeb([_Item("OEBPS/fonts/x.ttf", b"\x00\x01\x00\x00zz")], spine=[])
    lookup = build_manifest_lookup(oeb)
    assert lookup("OEBPS/fonts/x.ttf")[:4] == b"\x00\x01\x00\x00"
    assert lookup("x.ttf")[:4] == b"\x00\x01\x00\x00"      # basename fallback
    assert lookup("fonts/x.ttf")[:4] == b"\x00\x01\x00\x00"  # tail fallback


def test_build_font_table_aggregates_and_dedups_across_spine():
    font = _Item("fonts/f.ttf", b"\x00\x01\x00\x00yy")
    oeb = _Oeb([font], spine=[object(), object()])
    rules = [{"font-family": "Foo", "src": "url(fonts/f.ttf)"}]
    table = build_font_table(oeb, _Log(), stylizer_factory=lambda item: _Stylizer(rules))
    # Same rule seen on both spine items -> deduped to one face.
    assert len(table.faces) == 1
    assert table.faces[0].css_family == "foo"


def test_build_font_table_empty_when_no_fonts():
    oeb = _Oeb([], spine=[object()])
    table = build_font_table(oeb, _Log(), stylizer_factory=lambda item: _Stylizer([]))
    assert table.faces == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -k "manifest_lookup or build_font_table" -v`
Expected: FAIL — `ImportError: cannot import name 'build_font_table'`

- [ ] **Step 3: Implement**

```python
# append to plugin/kfxgen/font_table.py
def build_manifest_lookup(oeb_book):
    """Return href -> bytes, with basename and path-tail fallbacks."""
    by_href = {}
    by_base = {}
    for item in getattr(oeb_book, "manifest", []) or []:
        href = getattr(item, "href", "") or ""
        data = getattr(item, "data", None)
        if not href or not isinstance(data, (bytes, bytearray)):
            continue
        b = bytes(data)
        by_href[href] = b
        by_base[href.rsplit("/", 1)[-1]] = b

    def lookup(href):
        if not href:
            return None
        if href in by_href:
            return by_href[href]
        base = href.rsplit("/", 1)[-1]
        return by_base.get(base)

    return lookup


def _default_stylizer_factory(oeb_book, log):
    def make(item):
        try:
            from calibre.ebooks.oeb.stylizer import Stylizer  # noqa: PLC0415
            profile = getattr(getattr(oeb_book, "opts", None), "output_profile", None)
            return Stylizer(item.data, item.href, oeb_book, oeb_book.opts, profile)
        except Exception as e:
            log.warning(f"  Stylizer unavailable for fonts ({e})")
            return None
    return make


def build_font_table(oeb_book, log, stylizer_factory=None):
    """Aggregate @font-face rules across the spine and build a FontTable.

    Fonts declared in shared CSS repeat per item; faces_from_rules dedups by
    resolved href. `stylizer_factory(item)` is injectable for tests.
    """
    make = stylizer_factory or _default_stylizer_factory(oeb_book, log)
    all_rules = []
    for item in getattr(oeb_book, "spine", []) or []:
        if not hasattr(item, "data") or getattr(item, "data", None) is None:
            continue
        st = make(item)
        if st is None:
            continue
        all_rules.extend(getattr(st, "font_face_rules", []) or [])
    lookup = build_manifest_lookup(oeb_book)
    faces = faces_from_rules(all_rules, lookup, log)
    if faces:
        log.info(f"  Embedded fonts: {len(faces)} face(s)")
    return FontTable(faces)
```

- [ ] **Step 4: Run + lint**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_table.py -v && .venv/bin/python -m ruff check plugin/kfxgen/font_table.py && .venv/bin/python -m ruff format --check plugin/kfxgen/font_table.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/font_table.py plugin/tests/test_font_table.py
git commit -m "feat: build_font_table Calibre integration for #15"
```

---

## Task 5: `build_fragment_418` + `build_fragment_262`

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (add methods near `build_fragment_417`, after line 327)
- Test: `plugin/tests/test_native_generator.py` (or the existing generator test module — match the repo's location)

**Interfaces:**
- Consumes: `Face` (for descriptor values via `weight`/`italic`).
- Produces: `NativeKFXGenerator.build_fragment_418(location_name, font_data) -> YJFragment`; `build_fragment_262(emitted_family, location_name, weight, italic) -> YJFragment`.

- [ ] **Step 1: Write the failing tests**

```python
# append to the generator test module
import pytest
from kfxgen.native_generator import NativeKFXGenerator
from kfxgen.kfxlib_minimal.ion import IonBLOB, IonSymbol

pytestmark = pytest.mark.unit


def _new_gen():
    g = NativeKFXGenerator()
    from kfxgen.native_generator import StandardSymbolTable
    g.symtab = StandardSymbolTable()
    return g


def test_build_fragment_418_is_raw_blob():
    g = _new_gen()
    frag = g.build_fragment_418("resource/font0", b"\x00\x01\x00\x00data")
    assert str(frag.ftype) == "$418"
    assert str(frag.fid) == "resource/font0"
    assert isinstance(frag.value, IonBLOB)
    assert bytes(frag.value) == b"\x00\x01\x00\x00data"


def test_build_fragment_262_regular_omits_descriptors():
    g = _new_gen()
    frag = g.build_fragment_262("foo-400", "resource/font0", weight=400, italic=False)
    assert str(frag.ftype) == "$262"
    v = frag.value
    assert v[IonSymbol("$11")] == "foo-400"
    assert v[IonSymbol("$165")] == "resource/font0"
    assert IonSymbol("$13") not in v   # normal weight omitted
    assert IonSymbol("$12") not in v   # upright omitted


def test_build_fragment_262_bold_italic_sets_descriptors():
    g = _new_gen()
    frag = g.build_fragment_262("foo-700i", "resource/font1", weight=700, italic=True)
    v = frag.value
    assert v[IonSymbol("$13")] == IonSymbol("$361")  # bold
    assert v[IonSymbol("$12")] == IonSymbol("$382")  # italic
```

> If the repo's Ion import path differs (check an existing generator test's imports), use that path. Do not invent one.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest <generator test module> -k "418 or 262" -v`
Expected: FAIL — `AttributeError: 'NativeKFXGenerator' object has no attribute 'build_fragment_418'`

- [ ] **Step 3: Implement** (insert after `build_fragment_417`, ~line 327)

```python
    def build_fragment_418(self, location_name, font_data):
        """Raw font BLOB ($418) — analog of $417 for images.

        The fid MUST match the $165 value of the paired $262 fragment.
        """
        self.symtab.create_local_symbol(location_name)
        return YJFragment(
            fid=IS(location_name), ftype=IS("$418"), value=IonBLOB(font_data)
        )

    def build_fragment_262(self, emitted_family, location_name, weight, italic):
        """@font-face declaration ($262) — analog of $164 for images.

        $11 (family) is the join key a $157 style sets to apply this face.
        $12/$13 (style/weight) reflect the actual face; omitted when default
        ($350), matching observed KDP output. $15 (stretch) omitted in v1.
        """
        self.symtab.create_local_symbol(emitted_family)
        self.symtab.create_local_symbol(location_name)
        value = IonStruct(
            IS("$11"), emitted_family,     # font-family (plain string)
            IS("$165"), location_name,     # location -> $418 fid (plain string)
        )
        if weight >= 600:
            value[IS("$13")] = IS("$361")  # font-weight: bold
        if italic:
            value[IS("$12")] = IS("$382")  # font-style: italic
        return YJFragment(fid=IS(emitted_family), ftype=IS("$262"), value=value)
```

- [ ] **Step 4: Run + lint**

Run: `.venv/bin/python -m pytest <generator test module> -k "418 or 262" -v && .venv/bin/python -m ruff check plugin/kfxgen/native_generator.py && .venv/bin/python -m ruff format --check plugin/kfxgen/native_generator.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/native_generator.py <generator test module>
git commit -m "feat: build_fragment_418/262 font fragments for #15"
```

---

## Task 6: Emit font fragments in `generate_full_book`

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`generate_full_book` signature ~1646-1658; emission after image loop ~1835; `self.font_table` init)
- Test: generator test module

**Interfaces:**
- Consumes: `FontTable.faces` (Task 3), `build_fragment_418/262` (Task 5).
- Produces: `generate_full_book(..., font_table=None)` param. Sets `self.font_table = font_table or FontTable([])`; appends one `$418` + one `$262` per face.

- [ ] **Step 1: Write the failing test**

```python
# append to generator test module
from kfxgen.font_table import FontTable, Face


def test_generate_full_book_emits_font_fragments():
    g = NativeKFXGenerator()
    face = Face(css_family="foo", weight=700, italic=False,
                data=b"\x00\x01\x00\x00fontbytes", emitted_family="foo-700",
                location="resource/font0")
    g.generate_full_book(
        title="T", author="A",
        chapters=[{"title": "C1", "text": "Hello world."}],
        font_table=FontTable([face]),
    )
    types = [str(f.ftype) for f in g.fragments]
    assert "$418" in types
    assert "$262" in types


def test_generate_full_book_no_font_table_emits_no_font_fragments():
    g = NativeKFXGenerator()
    g.generate_full_book(title="T", author="A",
                         chapters=[{"title": "C1", "text": "Hi."}])
    types = [str(f.ftype) for f in g.fragments]
    assert "$418" not in types and "$262" not in types
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest <generator test module> -k "emits_font or no_font_table" -v`
Expected: FAIL — `TypeError: generate_full_book() got an unexpected keyword argument 'font_table'`

- [ ] **Step 3: Implement**

Add `font_table=None,` to the `generate_full_book` signature (after `images=None,`, line 1657).

Add near the reset block (after line 1731 `self.field_403_counter = 10`):

```python
        from .font_table import FontTable  # noqa: PLC0415
        self.font_table = font_table if font_table is not None else FontTable([])
```

Add the emission loop immediately after the image emission block (after line 1834, before the cover-in-reading-flow block at 1836):

```python
        # Embedded fonts (#15): one $418 (bytes) + one $262 (@font-face) per
        # face, mirroring the image $417/$164 pair. Application (setting $11 on
        # $157 styles) happens later via self.font_table.match().
        for face in self.font_table.faces:
            self.fragments.append(
                self.build_fragment_418(face.location, face.data)
            )
            self.fragments.append(
                self.build_fragment_262(
                    face.emitted_family, face.location, face.weight, face.italic
                )
            )
```

- [ ] **Step 4: Run + lint + full suite (catch regressions early)**

Run: `.venv/bin/python -m pytest <generator test module> -k "emits_font or no_font_table" -v && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check plugin/kfxgen/native_generator.py && .venv/bin/python -m ruff format --check plugin/kfxgen/native_generator.py`
Expected: new tests PASS; full suite still 444 passed / 12 skipped

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/native_generator.py <generator test module>
git commit -m "feat: emit $418/$262 font fragments in generate_full_book (#15)"
```

---

## Task 7: `build_fragment_157` gains `font_family`

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (`build_fragment_157` signature ~937-951; add `$11` emission)
- Test: generator test module

**Interfaces:**
- Produces: `build_fragment_157(..., font_family=None)`. When `font_family` is a non-empty string, add `$11: <font_family>` (plain string) to the style value. `None` → identical output to today.

- [ ] **Step 1: Write the failing test**

```python
# append to generator test module
def test_build_fragment_157_sets_font_family():
    g = _new_gen()
    g.next_entity_id = 1
    frag = g.build_fragment_157(entity_name="s0", font_family="foo-400")
    assert frag.value[IonSymbol("$11")] == "foo-400"


def test_build_fragment_157_without_font_family_omits_11():
    g = _new_gen()
    g.next_entity_id = 1
    frag = g.build_fragment_157(entity_name="s0")
    assert IonSymbol("$11") not in frag.value
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest <generator test module> -k "157_sets_font or 157_without_font" -v`
Expected: FAIL — `TypeError: build_fragment_157() got an unexpected keyword argument 'font_family'`

- [ ] **Step 3: Implement**

Add `font_family=None,` to the `build_fragment_157` signature (after `margin_right=None,`, line 950).

Add after the `italic` block (after line 1059, `value[IS("$12")] = IS("$382")`):

```python
        # $11 = font-family (#15). Set only when an embedded face was matched;
        # absence preserves byte-identical output for non-font books.
        if font_family:
            value[IS("$11")] = font_family
```

- [ ] **Step 4: Run + lint**

Run: `.venv/bin/python -m pytest <generator test module> -k "157_sets_font or 157_without_font" -v && .venv/bin/python -m ruff check plugin/kfxgen/native_generator.py && .venv/bin/python -m ruff format --check plugin/kfxgen/native_generator.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/native_generator.py <generator test module>
git commit -m "feat: build_fragment_157 font_family ($11) param for #15"
```

---

## Task 8: Capture `font-family` in block style

**Files:**
- Modify: `plugin/kfxgen/inline_style.py` (`compute_block_style` ~107-128)
- Modify: `plugin/kfxgen/converter.py` (`_build_style_resolver.resolve` dict ~41-46)
- Test: `plugin/tests/test_inline_style.py`

**Interfaces:**
- Produces: `compute_block_style(css)` output now includes `"font_family": list[str]` (ordered, normalized, lowercased; `[]` when absent). Resolver dict includes `"font-family"`.

- [ ] **Step 1: Write the failing test**

```python
# append to plugin/tests/test_inline_style.py
from kfxgen.inline_style import compute_block_style


def test_compute_block_style_captures_font_family_list():
    bs = compute_block_style({"font-family": '"Merriweather", Georgia, serif'})
    assert bs["font_family"] == ["merriweather", "georgia", "serif"]


def test_compute_block_style_font_family_empty_when_absent():
    bs = compute_block_style({"text-align": "center"})
    assert bs["font_family"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest plugin/tests/test_inline_style.py -k font_family -v`
Expected: FAIL — `KeyError: 'font_family'`

- [ ] **Step 3: Implement**

In `inline_style.py`, add a helper and a field. Add near the top (after imports):

```python
def _parse_font_family(value):
    """Split a CSS font-family value into normalized, lowercased names."""
    if not value:
        return []
    out = []
    for part in str(value).split(","):
        name = part.strip().strip("\"'").strip().lower()
        if name:
            out.append(name)
    return out
```

In `compute_block_style`, add to the returned dict:

```python
        "font_family": _parse_font_family(css.get("font-family")),
```

In `converter.py` `_build_style_resolver.resolve`, add to the returned dict (after `"margin-right"`):

```python
                    "font-family": st.get("font-family"),
```

- [ ] **Step 4: Run + lint**

Run: `.venv/bin/python -m pytest plugin/tests/test_inline_style.py -v && .venv/bin/python -m ruff check plugin/kfxgen/inline_style.py plugin/kfxgen/converter.py && .venv/bin/python -m ruff format --check plugin/kfxgen/inline_style.py plugin/kfxgen/converter.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/inline_style.py plugin/kfxgen/converter.py plugin/tests/test_inline_style.py
git commit -m "feat: capture font-family in block style for #15"
```

---

## Task 9: Apply fonts in style allocation

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` — block body style (`_allocate_style("")` at 2537 and 2680), `_emphasis_style` (2613-2618), and thread the chunk family into emphasis.
- Test: generator test module

**Interfaces:**
- Consumes: `self.font_table.match` (Task 6), `build_fragment_157(font_family=...)` (Task 7), `block_style["font_family"]` (Task 8).
- Produces: text runs get `$11` set to the matched face; synthetic bold/italic gated by `weight_ok`/`style_ok`.

**Design note:** The body/block style and each emphasis span both need the family. The block family is `chunk["block_style"]["font_family"]`. `_emphasis_style` currently takes only `flags`; extend it to also take the family list so `(family, bold, italic)` resolves to one face.

- [ ] **Step 1: Write the failing test** (end-to-end via a fake font_table)

```python
# append to generator test module
def test_font_applied_to_body_and_emphasis_runs():
    from kfxgen.inline_style import FLAG_BOLD
    g = NativeKFXGenerator()
    face_r = Face("foo", 400, False, b"\x00\x01\x00\x00r", "foo-400", "resource/font0")
    face_b = Face("foo", 700, False, b"\x00\x01\x00\x00b", "foo-700", "resource/font1")
    # One paragraph in family "foo" with a bold span.
    chapter = {
        "title": "C1",
        "text": "Normal bold.",
        "chunks": [{
            "type": "text", "text": "Normal bold.",
            "block_style": {"font_family": ["foo"]},
            "spans": [(7, 4, frozenset({FLAG_BOLD}))],
        }],
    }
    g.generate_full_book(title="T", author="A", chapters=[chapter],
                         font_table=FontTable([face_r, face_b]))
    styles = [f for f in g.fragments if str(f.ftype) == "$157"]
    fams = {f.value[IonSymbol("$11")] for f in styles if IonSymbol("$11") in f.value}
    assert "foo-400" in fams   # body run in regular face
    assert "foo-700" in fams   # bold run in the real bold face (no synthetic $13)
```

> **Adapt to the real chunk shape.** Before writing this test, read how `generate_full_book` consumes chapters (the `all_chunks` / `chunk_chunk_ranges` build) and mirror the exact keys (`type`, `text`, `spans`, `block_style`). The assertion (matched `$11` families present) is the invariant; the input shape must match production.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest <generator test module> -k font_applied -v`
Expected: FAIL — `$11` families absent (application not wired)

- [ ] **Step 3: Implement**

Extend `_emphasis_style` (2613) to resolve family:

```python
        def _emphasis_style(flags, family_list):
            bold = FLAG_BOLD in flags
            italic = FLAG_ITALIC in flags
            fam, w_ok, s_ok = self.font_table.match(family_list, bold, italic)
            return _allocate_style(
                "_em",
                italic=italic and not s_ok,
                bold=bold and not w_ok,
                font_family=fam or "",
            )
```

At the emphasis-span build site (2685-2689), pass the chunk's family:

```python
                chunk_fam = (chunk.get("block_style") or {}).get("font_family", [])
                entry_emphasis_spans.append(
                    [
                        (s, length, _emphasis_style(flags, chunk_fam))
                        for (s, length, flags) in chunk_spans
                    ]
```

For the body block style (2670-2680), resolve the family (regular weight/style):

```python
                    bs = chunk.get("block_style") or {}
                    attrs = {"font_size": chapters[ch_idx].get("font_size", 1.0)}
                    if bs.get("align"):
                        attrs["align"] = bs["align"]
                    if bs.get("indent"):
                        attrs["text_indent"] = bs["indent"]
                    if bs.get("margin_left"):
                        attrs["margin_left"] = bs["margin_left"]
                    if bs.get("margin_right"):
                        attrs["margin_right"] = bs["margin_right"]
                    fam, _w, _s = self.font_table.match(
                        bs.get("font_family", []), bold=False, italic=False
                    )
                    if fam:
                        attrs["font_family"] = fam
                    entry_styles.append(_allocate_style("", **attrs))
```

Also update the per-chapter body style at 2537 so plain (unspanned) paragraphs that inherit the chapter's default get a family when the chapter has one. Leave 2537 unchanged in v1 (chapter default has no block_style); the per-chunk path at 2680 covers styled paragraphs. Document this in the commit body.

> `_allocate_style` already threads `**attrs` into `build_fragment_157` and includes them in its cache key (line 2522), so `font_family` participates in caching automatically — no cache change needed.

- [ ] **Step 4: Run + full suite (CACE check)**

Run: `.venv/bin/python -m pytest <generator test module> -k font_applied -v && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check plugin/kfxgen/native_generator.py && .venv/bin/python -m ruff format --check plugin/kfxgen/native_generator.py`
Expected: new test PASS; full suite unchanged (444 passed / 12 skipped) — proves no-font books untouched

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/native_generator.py <generator test module>
git commit -m "feat: apply matched fonts to body + emphasis $157 styles (#15)"
```

---

## Task 10: Wire `build_font_table` into the converter + byte-identical regression

**Files:**
- Modify: `plugin/kfxgen/converter.py` (build table ~1064; pass to `generate_full_book` ~1072)
- Test: `plugin/tests/` — a byte-identical golden test if the repo has a tier3/golden harness (see `test_*golden*`/`tier3`); otherwise a converter-level test asserting no `$418`/`$262` for a no-font book.

**Interfaces:**
- Consumes: `build_font_table` (Task 4), `generate_full_book(font_table=...)` (Task 6).

- [ ] **Step 1: Write the failing / guardrail test**

```python
# In the converter/golden test module. If a byte-stable golden harness exists
# (deterministic container id per #89), assert a no-font EPUB fixture converts
# byte-identically to its committed golden. Otherwise:
def test_no_font_book_emits_no_font_fragments_end_to_end():
    # Convert an existing no-font EPUB fixture through the full converter and
    # assert the generated fragments contain no $418/$262. Reuse whatever
    # fixture + conversion helper the existing converter tests use.
    ...
```

> Locate the existing converter/golden test module and its fixtures first; mirror its setup exactly rather than inventing a harness.

- [ ] **Step 2: Run to verify baseline**

Run: `.venv/bin/python -m pytest <converter/golden test module> -v`
Expected: baseline PASS (guardrail should already hold since Task 9 kept the suite green)

- [ ] **Step 3: Implement the wiring**

After chapter extraction (~line 1064), before the generate call:

```python
    # Build embedded-font table (#15). Empty (no faces) for books without
    # embeddable @font-face fonts, in which case output is unchanged.
    from .font_table import build_font_table  # noqa: PLC0415
    font_table = build_font_table(oeb_book, log)
```

Add `font_table=font_table,` to the `gen.generate_full_book(...)` call (after `issue_date=...`, line 1081).

- [ ] **Step 4: Run + full suite**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check plugin/kfxgen/converter.py && .venv/bin/python -m ruff format --check plugin/kfxgen/converter.py`
Expected: full suite green; no-font golden byte-identical

- [ ] **Step 5: Commit**

```bash
git add plugin/kfxgen/converter.py <converter/golden test module>
git commit -m "feat: wire font embedding into converter pipeline (#15)"
```

---

## Task 11: Integration test — real EPUB round-trip (tier-2)

**Files:**
- Create: `plugin/tests/fixtures/font_embed.epub` (tiny EPUB: one TTF `@font-face` applied to body text, plus a bold face)
- Test: `plugin/tests/test_font_integration.py`

**Interfaces:**
- Consumes: full converter path + `build_font_table` (needs Calibre `Stylizer` → `@pytest.mark.integration`).

- [ ] **Step 1: Build the fixture EPUB**

Create a minimal EPUB embedding a small open-license TTF (e.g. a single-weight display font + its bold) with CSS `@font-face` + `body { font-family: TestFont }` and a `<b>` run. Keep the font tiny (subset by hand or use a small existing OFL font). Document provenance in a `fixtures/README` line.

- [ ] **Step 2: Write the integration test**

```python
# plugin/tests/test_font_integration.py
import pytest

pytestmark = pytest.mark.integration


def test_font_embed_epub_round_trips_fonts():
    # Convert the fixture through the real pipeline (reuse the tier-2 conversion
    # helper the existing integration tests use). Assert:
    #  - generated fragments include >=1 $418 and >=1 $262
    #  - at least one $157 carries $11 matching a $262 $11
    # If the jhowell KFX Input decode helper is available (see existing tier-2
    # tests), additionally decode and assert the font fragments survive.
    ...
```

> Mirror the existing tier-2 integration test setup (they already gate on Calibre / the vendored `KFX Input.zip`). Do not build a new conversion harness.

- [ ] **Step 3: Run (tier-2)**

Run: `.venv/bin/python -m pytest plugin/tests/test_font_integration.py -v`
Expected: PASS where Calibre is available; SKIP in bare CI (consistent with existing tier-2)

- [ ] **Step 4: Commit**

```bash
git add plugin/tests/test_font_integration.py plugin/tests/fixtures/font_embed.epub
git commit -m "test: tier-2 integration for #15 font embedding"
```

---

## Phase 3: Device gate (manual — not a code task)

Build/convert an EPUB embedding a visually unmistakable TTF (distinct letterforms) as body text plus a real bold and a real italic face. `./build_plugin.sh --install`, convert, sideload to a physical Kindle. Confirm:

1. Body renders in the embedded face (not Kindle default).
2. Real bold and italic faces render distinctly (exact-match path).
3. Fallback case — a paragraph in a family that only ships a regular face, with a `<b>` run — shows faux bold (synthetic path).
4. A no-font book renders unchanged.
5. Decide `$593` capability flag: the #16 reference did not observe it as required. If fonts do NOT display, add a `$593` capability entry advertising font support and re-test. Record the outcome in `docs/kfx-embedded-fonts-reference.md`.

Keep device-test `.kfx` files in local scratch (git-ignored), per handoff convention.

## Phase 4: Ship

- Update `CHANGELOG.md` (new "Embedded font support (#15)" entry).
- Bump `plugin/kfxgen/__init__.py` version (5.3.23 → 5.4.0 — new feature).
- Run `/tech-debt-review` before merge.
- Update `docs/PROGRESS.md` handoff (mark #15 shipped, note device outcome + `$593` decision).
- Update `docs/kfx-embedded-fonts-reference.md` with any Phase-0/device findings.
- Squash-merge PR, delete branch.

---

## Self-review notes

- **Spec coverage:** carriage (Tasks 5-6), application/face-matching (Tasks 3, 7-9), TTF/OTF-only + skip-warn (Tasks 1-2), family capture block-level (Task 8), CACE byte-identical guardrail (Tasks 6, 9, 10), Calibre `font_face_rules` source (Tasks 0, 4), device gate + `$593` (Phase 3), deferred WOFF/subset/intra-block (documented, not implemented). All spec sections map to a task.
- **Type consistency:** `Face` fields, `FontTable.match -> (str|None, bool, bool)`, `build_fragment_262(emitted_family, location_name, weight, italic)`, `build_fragment_157(..., font_family=None)`, `_emphasis_style(flags, family_list)` used consistently across tasks.
- **Placeholder note:** Tasks 9-11 intentionally instruct the implementer to read the exact production chunk shape / existing test harness before finalizing the test body — because those shapes are established at runtime and must not be guessed. The invariants asserted are concrete; only the fixture/harness plumbing is delegated to the (verified) existing patterns.
