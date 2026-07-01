# Dynamic Section-Position Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `$260` section-position base dynamic so content and section eid ranges are disjoint by construction at any chapter count, while leaving normal books byte-identical (#30).

**Architecture:** Content positions are unchanged. After content assignment, compute `content_max` and set the section base to `max(SECTION_POS_BASE, content_max + SECTION_POS_STEP)` — so sections keep base `10000` when content fits under it, and relocate just above the content range only when content would otherwise overflow. The guarantee is asserted as content/section eid-set disjointness, replacing the fixed-threshold `xfail` gate from #23.

**Tech Stack:** Python 3.13, pytest. All production changes in `plugin/kfxgen/native_generator.py`; tests in `tests/unit/test_converter.py`.

## Global Constraints

- Run tests with `.venv/bin/python -m pytest` (the venv has the deps).
- Lint gate: `.venv/bin/python -m ruff check` AND `ruff format --check`, ruff pinned `0.15.1` (`.venv/bin/python -m ruff`; `.venv/bin/pip install ruff==0.15.1 -q` if the venv differs).
- Content positions and their assignment loop are UNCHANGED. `CONTENT_POS_BASE=1000`, `CONTENT_POS_STEP=2`, `SECTION_POS_BASE=10000`, `SECTION_POS_STEP=2` stay as the defaults/floor.
- Content eids are always even (base 1000 even, step 2), so `content_max + SECTION_POS_STEP` stays even-aligned — no explicit rounding needed.
- Normal books (content_max < SECTION_POS_BASE) MUST produce byte-identical output; golden fixtures are NOT regenerated.
- Branch: `feat/30-dynamic-section-base`. Commit after every task.

---

### Task 1: Dynamic section base + disjointness tests

**Files:**
- Modify: `plugin/kfxgen/native_generator.py` (add `_section_base` classmethod; use it in the `section_positions` assignment ~line 2443; update the constants comment ~2107-2120)
- Test: `tests/unit/test_converter.py`

**Interfaces:**
- Produces: `NativeKFXGenerator._section_base(content_max: int) -> int` — the section base for a book whose largest content eid is `content_max`. Returns `SECTION_POS_BASE` when `content_max < SECTION_POS_BASE`, else `content_max + SECTION_POS_STEP`.

- [ ] **Step 1: Write the failing unit tests for `_section_base`**

Add to `tests/unit/test_converter.py`. `NativeKFXGenerator` is only imported
locally inside other test methods in this file, so import it inside these tests
too (matching that pattern):

```python
class TestSectionBase:
    def test_normal_book_keeps_default_base(self):
        from kfxgen.native_generator import NativeKFXGenerator

        # content well under the floor -> sections stay at SECTION_POS_BASE
        assert NativeKFXGenerator._section_base(3398) == 10000
        assert NativeKFXGenerator._section_base(9998) == 10000

    def test_overflow_relocates_above_content(self):
        from kfxgen.native_generator import NativeKFXGenerator

        # content at/above the floor -> section base moves just above content_max
        assert NativeKFXGenerator._section_base(10000) == 10002
        assert NativeKFXGenerator._section_base(17798) == 17800

    def test_result_is_even_aligned(self):
        from kfxgen.native_generator import NativeKFXGenerator

        # content eids are always even; the relocated base stays even
        for cm in (10000, 10002, 12344, 17798):
            assert NativeKFXGenerator._section_base(cm) % 2 == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestSectionBase -v`
Expected: FAIL — `AttributeError: type object 'NativeKFXGenerator' has no attribute '_section_base'`.

- [ ] **Step 3: Implement `_section_base` and use it**

In `plugin/kfxgen/native_generator.py`, add the classmethod near the position constants (after line 2125, `SECTION_POS_STEP = 2`):

```python
    @classmethod
    def _section_base(cls, content_max):
        """Base position id for $260 sections.

        Sections start strictly above the content range so content and
        section eids never collide (#30). For normal books (content stays
        under SECTION_POS_BASE) this is the floor SECTION_POS_BASE, keeping
        output byte-identical; only books whose content would overflow the
        floor relocate their sections just above content_max. Content eids
        are always even, so content_max + SECTION_POS_STEP stays even-aligned.
        """
        return max(cls.SECTION_POS_BASE, content_max + cls.SECTION_POS_STEP)
```

Then change the `section_positions` assignment. Current code (~2440-2446):

```python
        # Section position IDs (for $260) — separate range, may arithmetically
        # collide with chunk positions for busy books (v5.2.0 had ~71 such
        # collisions on the test corpus and worked correctly).
        section_positions = [
            self.SECTION_POS_BASE + i * self.SECTION_POS_STEP
            for i in range(len(chapters))
        ]
```

Replace with (note `content_pos_id` is the next-unused value after the content
loop, so the last assigned content eid is `content_pos_id - CONTENT_POS_STEP`):

```python
        # Section position IDs (for $260). The base is dynamic (#30): sections
        # start strictly above the content range so content and section eids
        # are disjoint at any chapter count. Normal books keep SECTION_POS_BASE
        # (byte-identical); only overflow books relocate.
        content_max = content_pos_id - self.CONTENT_POS_STEP
        section_base = self._section_base(content_max)
        section_positions = [
            section_base + i * self.SECTION_POS_STEP
            for i in range(len(chapters))
        ]
```

- [ ] **Step 4: Update the stale constants comment**

In the comment block above the constants (~lines 2108-2120), the text says the
`10000+` section range is fixed and "Don't widen." Replace the bullet that reads:

```python
    #   - Despite (a), the values themselves matter. Pushing
    #     SECTION_POS_BASE up to 100000 broke Kindle's progress display
    #     ("at start of book" showed 100% complete). The 5-digit range
    #     v5.2.0 used (≤ 16000-ish for content, 10000+ for sections) is
    #     the known-good envelope. Don't widen.
```

with:

```python
    #   - Despite (a), the values themselves matter. Pushing
    #     SECTION_POS_BASE up to 100000 broke Kindle's progress display
    #     ("at start of book" showed 100% complete). Keep section eids in
    #     the low 5-digit range Kindle has been observed to handle.
    #   - The section base is dynamic (#30): SECTION_POS_BASE is the floor,
    #     but for books whose content overflows it the sections relocate to
    #     just above content_max (via _section_base) so content and section
    #     eids stay disjoint. Normal books are unaffected (stay at 10000).
```

- [ ] **Step 5: Run the `_section_base` tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py::TestSectionBase -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Rewrite the #23 scale gate to assert disjointness (remove xfail)**

Replace the whole `TestHighChapterCountScale` class (currently at
`tests/unit/test_converter.py:963`, the `@pytest.mark.xfail`-marked
`test_1200_chapter_book_converts_and_stays_in_envelope`) with:

```python
class TestHighChapterCountScale:
    """A large book (1200 chapters × several paragraphs) must keep content and
    section eid ranges disjoint by construction (#30) — no reliance on the
    old fixed 10000 boundary, which this book's content overflows."""

    def test_1200_chapter_book_has_disjoint_eid_ranges(self, tmp_path):
        from kfxgen.native_generator import NativeKFXGenerator
        from kfxgen.kfxlib_minimal.ion import IS
        from tests._kfx_introspect import by_type, val, load_fragments

        chapters = [
            {
                "title": f"Chapter {i}",
                "text": (
                    f"Chapter {i}\n\n"
                    f"First sentence of chapter {i}.\n\n"
                    f"Second sentence of chapter {i}.\n\n"
                    f"Third sentence of chapter {i}.\n\n"
                    f"Fourth sentence of chapter {i}.\n\n"
                    f"Fifth sentence of chapter {i}."
                ),
            }
            for i in range(1200)
        ]
        out = tmp_path / "scale.kfx"
        NativeKFXGenerator().generate_full_book(
            title="Scale", author="T", chapters=chapters, output_path=str(out)
        )
        frags = load_fragments(out)

        assert len(by_type(frags, "$260")) == 1200

        content_eids = set()
        for f in by_type(frags, "$259"):
            v = val(f)
            for e in v.get(IS("$146")) or v.get(IS("$181")) or []:
                if hasattr(e, "get") and e.get(IS("$155")) is not None:
                    content_eids.add(int(e.get(IS("$155"))))

        section_eids = set()
        for f in by_type(frags, "$260"):
            v = val(f)
            for e in v.get(IS("$141")) or []:
                if hasattr(e, "get") and e.get(IS("$155")) is not None:
                    section_eids.add(int(e.get(IS("$155"))))

        # Content pushes past the old 10000 floor at this scale...
        assert max(content_eids) >= NativeKFXGenerator.SECTION_POS_BASE
        # ...but content and section eid sets are still disjoint by construction.
        assert content_eids.isdisjoint(section_eids), (
            f"content/section eid overlap: "
            f"{sorted(content_eids & section_eids)[:10]}"
        )

        # #23 invariant still holds: every section eid is present in $265.
        pos_265 = set()
        for f in by_type(frags, "$265"):
            v = val(f)
            entries = v if isinstance(v, list) else v.get(IS("$181")) or []
            for e in entries:
                if hasattr(e, "get") and e.get(IS("$185")) is not None:
                    pos_265.add(int(e.get(IS("$185"))))
        missing = section_eids - pos_265
        assert not missing, f"section eids absent from $265: {sorted(missing)[:10]}"
```

- [ ] **Step 7: Run the scale test + full regression**

Run: `.venv/bin/python -m pytest tests/unit/test_converter.py tests/unit/test_position_map.py tests/integration/test_golden_corpus.py -q`
Expected: PASS. The scale test now passes (asserting disjointness, no xfail); the position-map tests (small fixtures) and golden bytes are unchanged.

- [ ] **Step 8: Lint + commit**

```bash
.venv/bin/python -m ruff check plugin/kfxgen/native_generator.py tests/unit/test_converter.py
.venv/bin/python -m ruff format plugin/kfxgen/native_generator.py tests/unit/test_converter.py
git add plugin/kfxgen/native_generator.py tests/unit/test_converter.py
git commit -m "feat: dynamic $260 section base for guaranteed eid disjointness (#30)"
```

---

### Task 2: Release prep + device gate

**Files:**
- Modify: `plugin/kfxgen/__init__.py` (version), `CHANGELOG.md`

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (12 tier-2 skip without the vendored zip is expected). The #30 scale test now passes and there is no longer an xfail for it. Fix nothing silently; if anything regresses, STOP and report.

- [ ] **Step 2: Golden corpus unchanged**

Run: `.venv/bin/python -m pytest tests/integration/test_golden_corpus.py -q -m "tier3 or tier3_strict"`
Expected: PASS with NO regeneration — golden inputs are normal-sized (content well under 10000), so section bases stay at 10000 and bytes are identical. If tier3_strict fails, STOP and report (it would mean a normal book changed, which violates the design's byte-identical guarantee — do NOT regenerate to paper over it).

- [ ] **Step 3: Version + CHANGELOG**

Bump `version` in `plugin/kfxgen/__init__.py` 5.3.22 → 5.3.23. Prepend a
`## 5.3.23 — Dynamic section-position base (#30)` entry to `CHANGELOG.md`
summarizing: content and section eid ranges are now disjoint by construction at
any chapter count; `SECTION_POS_BASE` is a floor and sections relocate just
above the content range only for books that would otherwise overflow; normal
books are byte-identical (no re-verification); the #23 scale gate now asserts
disjointness (xfail removed). Match the style of the existing top entries.

- [ ] **Step 4: Lint gate (pinned ruff)**

```bash
.venv/bin/python -m ruff check plugin/ tests/
.venv/bin/python -m ruff format --check plugin/ tests/
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: bump 5.3.23, CHANGELOG for #30 dynamic section base"
```

- [ ] **Step 6: Device verification gate (manual — STOP for the user)**

Build + install the branch plugin (`./build_plugin.sh --install`) and convert a
huge book (Complete Works of Shakespeare) via `ebook-convert` to a `.kfx`. Have
the user sideload it to a physical Kindle and confirm TOC navigation still works
now that the section eids for that book are relocated to ~20k. Normal books need
no re-verification (byte-identical). Do NOT mark #30 done until the user confirms
on-device.

---

## Self-Review

- **Spec coverage:** dynamic base + floor → Task 1 Step 3 (`_section_base` + assignment); disjointness-by-construction → Task 1 Steps 1/6; stated-invariant test change → Task 1 Step 6; byte-identical normal books → Task 2 Step 2 (golden unchanged); boundary/overflow → Task 1 Steps 1 (`10000`/`17798` cases) + 6; device gate → Task 2 Step 6; release → Task 2.
- **Placeholders:** none — every step has concrete code/commands.
- **Type consistency:** `_section_base(content_max: int) -> int` defined in Task 1 Step 3, exercised in Task 1 Steps 1 and 6; constant names (`SECTION_POS_BASE`, `SECTION_POS_STEP`, `CONTENT_POS_STEP`) match `native_generator.py`.
