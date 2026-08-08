# How kfxgen builds a KFX file

This is the document to read before changing anything in `native_generator.py`.
It explains the pipeline, what the numbered symbols mean, and which mistakes
have already been made and paid for.

**It is not a format specification.** KFX is closed. Everything here was
recovered by generating files, diffing them against Amazon-produced and
Calibre-produced KFX, and reading the result on a physical Kindle. Where a
claim has not been confirmed on a device, it says so.

The most important habit this document can teach: **an absence of evidence is
not evidence of absence.** Twice now a note recorded "no reference file does
X", that negative was believed, and the resulting bug survived for weeks. Both
are described under [Pitfalls](#pitfalls).

---

## 1. The pipeline

```
EPUB
  │  Calibre unpacks and parses; kfxgen never parses EPUB itself
  ▼
OEB (Calibre's object model)
  │  converter.py: extract_chapters_from_oeb
  ▼
Chapters  ──  {title, blocks: [{text, spans, block_style, anchor_keys, ...}]}
  │  converter.py: extract_blocks_from_html + normalize_runs
  ▼
Chunks    ──  one per paragraph (or per CHUNK_SIZE slice of a long one)
  │  native_generator.py: _build_chapter_content
  ▼
Fragments ──  $145 text, $259 flow, $265 positions, $266 anchors, $157 styles…
  │  native_generator.py: build_fragment_*
  ▼
Container ──  Ion-encoded .kfx
```

### What each stage must preserve

| Stage | Invariant | Breaks as |
|---|---|---|
| HTML → blocks | Every id that something links to survives on some block | Dead internal links |
| blocks → chunks | A block's anchors follow the piece that contains them | Links land in the wrong place |
| chunks → positions | Every chunk gets a unique position id, in reading order | Navigation snaps elsewhere |
| positions → `$265` | Char offsets match the concatenated `$145` text exactly | Jumps land off by the drift |
| everything → container | Every referenced symbol is defined | Upstream decoders reject the file |

Text is the spine of all of it. A paragraph becomes a chunk, a chunk becomes a
`$259` entry, that entry names a position, and that position is what anchors,
the TOC, and the reading-progress map all point at.

---

## 2. The fragment graph

```
$260 section ──▶ $259 storyline ──┬──▶ $145 content (the actual strings)
                                  ├──▶ $157 style   (per entry, and per span)
                                  ├──▶ $164 ──▶ $417  image metadata / bytes
                                  └──▶ $142 span ──▶ $179 ──▶ $266 anchor
                                                                  │
$265 position map ◀───────────────────────────────────────────────┘
   ▲                                          ($266 names a position;
   │                                           $265 maps that position
$264 section → positions                       to a character offset)
$550 all positions
```

Read that right-hand chain carefully, because every internal link in the book
depends on it: a `$142` span carries `$179`, which names a `$266` by name, which
carries a position, which must appear in `$265`. Break any link in that chain
and the reader silently renders the run as plain text or jumps somewhere else.

### The fragment builders

| Fragment | Purpose |
|---|---|
| `$145` | Content strings — the book's text, as a list under `$146` |
| `$157` | Style definition (font size, weight, alignment, baseline shift…) |
| `$164` / `$417` | Image resource metadata / raw bytes |
| `$418` / `$262` | Font blob / `@font-face` declaration — the font analogues of the two above |
| `$258` | Reading order metadata |
| `$259` | Storyline / flow map — the entries that make up a chapter |
| `$260` | Section. `$260.fid` **must** equal `$260.$174` |
| `$264` | Position index — section to its position ids |
| `$265` | Position index table — position id to character offset |
| `$266` | Anchor / bookmark — a named target for a link |
| `$270` | Container info |
| `$389` | Navigation / table of contents |
| `$419` | Entity index / container map |
| `$490` | Book metadata |
| `$538` | Document data / reading order |
| `$585` | Content features |

---

## 3. Symbols, and how much to trust each one

The bundled `yj_symbol_catalog.py` names almost nothing — 284 of its entries are
`$NNN?` placeholders. Meaning is recovered empirically, so each entry below
carries how we know it.

**Confidence levels:** **device** = confirmed on a physical Kindle · **reference**
= matches Amazon- or Calibre-produced KFX · **inferred** = deduced from
structure, never confirmed.

### Content and structure

| Symbol | Meaning | Confidence |
|---|---|---|
| `$146` | List of strings in a `$145`, or of entries in a `$259` | reference |
| `$4` / `$403` | Content fragment name / index of a string within it | reference |
| `$155` | Position id of a `$259` entry | device |
| `$159` | Entry kind — `$269` text, `$271` image | reference |
| `$175` | Image resource named by an image entry | device |
| `$584` | Alt text on an image entry | reference |
| `$790` | Marks the first entry of a section; `1` on that entry only | device |
| `$184` / `$185` | In `$265`: character offset / the position id at it | device |

### Links and anchors

| Symbol | Meaning | Confidence |
|---|---|---|
| `$142` | List of character spans on an entry | reference |
| `$143` | Character offset within the paragraph | device |
| `$144` | Length of the span in characters | reference |
| `$179` | The `$266` anchor this span links to | device |
| `$180` | An anchor's own name — what `$179` resolves against | device |
| `$183` | An anchor's target: `{$155: position}`, optionally `{$143: offset}` | device |
| `$186` | Alternative anchor target form. Rare (2–11 per book) and **not** the precise-position mechanism | reference |

### Styling

| Symbol | Meaning | Confidence |
|---|---|---|
| `$173` | A style's own name | reference |
| `$16` | Font size, as `{$307: magnitude, $306: unit}` | device |
| `$306` | Unit: `$308` em, `$505` rem, `$314` %, `$318` pt, `$319` px, `$316` mm | reference |
| `$31` | Baseline shift. Superscript is a **small `$16` plus a positive `$31`**, not a flag | device |
| `$31` (negative) | Subscript. No reference file uses one; −20% confirmed anyway | device |
| `$11` / `$12` | Font family / font style (`$382` italic) | device |
| `$23` | Text decoration (`$328` underline) | reference |
| `$34` | Alignment: `$59` left, `$61` right, `$320` center, `$321` justify | reference |

---

## 4. Pitfalls

Each of these cost real debugging. They are listed with the issue that found
them so the full reasoning is recoverable.

### Anchors must name themselves (#51)

`$266` fragments carry `$180`, their own name, and that is what a span's `$179`
resolves against — the fragment id alone is not enough. kfxgen omitted it, so
**every internal link in every book was dead**, rendered as plain text. Nothing
in the output looked wrong; the links simply did nothing.

### An anchor names a paragraph, not a point (#79)

`$183` may carry `$143`, the character offset within the target paragraph.
Without it a note's return link lands on the paragraph's first line while the
marker sits at the end — up to 1.2 pages away on a real book, which at a large
font size is real scrolling.

This is the first "absence of evidence" trap. A research note recorded that no
reference file carried `$143` inside `$183`. Re-measuring three Amazon files
found 321/676, 970/1299 and 488/1015 anchors carrying it, every one within its
target paragraph's length. The negative had been drawn from a small sample and
believed for a week.

### Superscript is not a flag (#52)

A raised run is a `$157` with a reduced `$16` **and** a `$31` baseline shift.
There is no superscript property. Subscript is the same mechanism with a
negative shift — and that is the second trap: no reference file in the corpus
used a negative `$31`, which was taken as reason to doubt it. It works, and is
device-confirmed at −20%.

Publisher EPUBs rarely use `<sup>`. They wrap the marker in a `<span>` whose
class carries `vertical-align`, as often a raw length (`0.25em`) as the keyword.
Both routes have to be recognised.

### `$145` has a byte cap, measured unusually (#37)

8192 bytes, and getting the measurement wrong produces output that passes a
naive check and still fails upstream's:

- the **last** string of a fragment is excluded from the total
- the comparison is `>=`, so a fragment measuring exactly 8192 is already a violation
- the budget is in **encoded bytes**, not characters — CJK runs three bytes per character

The convenient corollary: a fragment holding a single string is always
conformant however long that string is, so there is no unsplittable input.

### Every position must appear in `$265` (#20, #29)

A position id referenced by `$264` or `$550` but absent from `$265` makes
upstream decoders report "position_map has extra eids" and fail the round trip.
Section positions belong at pid 0, length 1, at the section's real start — an
earlier attempt that placed them at the wrong offset produced phantom boundary
markers and made TOC navigation land one page past the target.

`$265` offsets must equal the running length of the actual `$145` text.
Anything else drifts, and the error grows with distance into the book.

### Iterating a set makes output non-reproducible (#96)

Image `$157` styles were allocated by iterating a set of size-class names.
CPython randomizes string hashing per interpreter, so the same book converted
twice produced two different files — the symbols shifted, and `$270` and `$419`
shifted with them. Content was identical; only the symbol numbering moved.

Anything that affects symbol allocation order must iterate a fixed sequence.

### Elided blocks take their anchors with them (#62, #64)

kfxgen drops a heading that duplicates the chapter title, and consumes an opener
split across several blocks. Both remove blocks that may carry ids the TOC links
to. Those ids have to be carried forward onto the chapter's first chunk, or the
links resolve to nothing. Any new elision rule needs the same treatment.

---

## 5. How to verify a claim

**Raw `.kfx` cannot be rendered locally.** Kindle Previewer rejects it and KFX
Input fails on the position map, so structural checks predict and only a
physical Kindle confirms. Until a sideload, a claim about rendering is a
hypothesis.

What each level of evidence is worth:

| Method | Proves |
|---|---|
| Unit tests | kfxgen is internally consistent |
| Golden fixtures | Output has not changed unintentionally |
| Differential decode (tier 2) | Another implementation can read it |
| Corpus A/B diff | A change did not damage a wide range of real books |
| Physical Kindle | It actually works |

Two techniques worth knowing:

- **Corpus A/B** — build the plugin twice, convert the same corpus under two
  isolated Calibre configs, diff the results. This caught a 44% text-loss bug
  that the unit suite and the golden corpus both passed.
- **Reciprocity** — for round-trip links, check that a return link lands where
  the marker pointing at that note actually sits. Position *and* offset. This is
  what caught #79 and is now a standing test.

---

## 6. Where the rest of it lives

- `CHANGELOG.md` — how each rule was found, in the order it was found
- `plugin/kfxgen/native_generator.py` — the reasoning, next to the code that depends on it
- `tests/unit/test_kfx_invariants.py`, `tests/unit/test_position_map.py` — the rules that can be asserted
- `docs/kfx-embedded-fonts-reference.md` — the font subsystem in detail
- `research/kfx-format-baseline/README.md` — drift baselines and the upstream watch
- Closed issues — often the fullest explanation of a single symbol

**When you add to this document**, record how you know, not only what you
concluded. A claim without provenance cannot be re-checked, and the two most
expensive mistakes in this project were both confidently recorded facts that
nobody could tell had come from a sample of four.
