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

## 3. The table of contents

The TOC does more work here than its name suggests. It is not only what the
reader taps — **it decides where chapters begin.** Get it wrong and the book's
structure is wrong, not merely its navigation.

### Where it comes from

Calibre parses the EPUB's nav document or NCX and exposes `oeb_book.toc`.
`_extract_toc_with_hrefs` walks that tree recursively and keeps three things per
entry: `title`, `href`, and `level`. Nothing else survives, and nothing is
invented — a book with no TOC yields an empty list and the caller falls back to
one chapter per spine file.

### How it becomes chapter boundaries

`_assemble_chapters_by_coordinate` resolves every TOC entry to a
`(spine file, block index)` coordinate, then slices the flattened block stream
between consecutive coordinates. Each slice is a chapter.

Consequences worth internalising:

- **Two TOC entries pointing into the same file split that file.** This is how a
  single `chapter.xhtml` containing three sections becomes three chapters (#23).
- **An entry whose href is not in the spine is dropped**, with a warning. It
  cannot become a chapter because there is no content to slice.
- **An entry whose fragment does not exist snaps forward** to the block after
  the previous entry's, rather than being discarded. The log records the snap.
- **Content before the first TOC coordinate** is kept only if it carries an
  anchor the TOC references, or is not image-only. A cover image sitting ahead
  of the first entry is dropped rather than becoming a phantom chapter.

### The Contents page kfxgen builds

kfxgen synthesizes its own Contents chapter rather than passing the source's
through. Each chapter title becomes an entry linking to a `toc_anchor_N` `$266`,
which names that chapter's start position.

**Those anchors must target a leaf `$259` child** — one with a `$145` content
reference and, for the first, `$790: 1`. Kindle treats the outer wrapper
position as non-navigable, so a TOC entry pointing at a wrapper is not a broken
link; it is a link that does nothing at all when tapped, which is considerably
harder to notice.

Chapter titles also drive the synthesized heading, which is where two elision
rules come from: a body heading duplicating the chapter title is stripped
(#62), and an opener split across several blocks — numeral in one, title in the
next — is consumed as a unit (#64). Both remove blocks, and both must carry any
anchors on those blocks forward or the links die.

### `$389`, the navigation fragment

The shape matches real Kindle books: the value is a **list** containing one
struct, carrying `$178: $351` (reading-order reference) and `$392` (navigation
containers). Two containers are emitted — the TOC (`$235: $212`) and landmarks
(`$235: $236`).

### What TOC extraction must ignore

Navigation documents contain markup that is structure, not reading content, and
treating it as text is a visible defect. Two signals are honoured:

- the HTML5 `hidden` attribute — the producer said not to display this
- an `epub:type` naming a non-rendered navigation kind: `page-list`,
  `landmarks`, `lot`, `loi`, `lov`

`page-list` is the one that bites. An EPUB 3 print-pagination nav holds one
entry per printed page, which arrived as several hundred bare page numbers in
the body text (#60). Nested lists and tail flags were the other two extraction
bugs (#58, #59).

---

## 4. Symbols, and how much to trust each one

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

## 5. Notes and internal links

A footnote is two halves that must agree: a **marker** in the chapter that links
to the note, and an **anchor** on the note that the marker resolves to. Most of
the ways this breaks are one half being right while the other is missing, which
looks like nothing at all on screen — the run renders as plain text.

### Resolving a target

`_resolve_link_target` normalises every in-book `<a href>` to one key shape,
`"<file>#<fragment>"`, so both halves agree on a single string:

- `#frag` alone resolves against **the file the markup came from**, so a marker
  in a chapter and the note it points at agree.
- `notes.xhtml#n1` resolves against **the containing document's directory**.
  Keys were once bare basenames, so `text/notes.xhtml` and `back/notes.xhtml`
  produced the same key and the first document silently won every link aimed at
  either. That failed quietly — the link resolved, nothing reported as dangling,
  and the reader simply landed in the wrong chapter (#69).
- A leading `../` is an ordinary cross-folder link, not something to discard.
- Any URL scheme, absolute path, or traversal out of the book root is rejected.
  These leave the book and cannot be anchors.

A TOC entry may name a whole file with no fragment, so the first block of every
document also gets a bare-filename key. Without it, a file that declares no ids
anywhere is unreachable and the link is dropped (#62).

### Emitting the anchors

Only ids that something **actually links to** get a `$266`. A real book's markup
carries thousands of ids — `kobo.4.2`, `ji_364`, one per sentence in some
producers — and emitting a fragment each would bloat the container for no
reading benefit.

Targets that nothing declares are dropped rather than emitted dangling. An
anchor that resolves to nothing is worse than an unlinked run, because the
reader has no way to tell you it failed.

### Where the anchor points

The anchor carries the marker's character offset within its paragraph (`$143`),
so the return link lands on the marker rather than the paragraph's first line.
When a paragraph is longer than `CHUNK_SIZE` and splits across several chunks,
the offset rebases into the chunk that actually contains the marker.

Two markers in one paragraph therefore get **distinct** anchors — before `$143`
they collapsed to the same position and were indistinguishable (#79).

### Rendering the marker

A raised run is a `$157` with a reduced `$16` **and** a `$31` baseline shift.
Defaults, all device-confirmed and overridable per conversion (#68):

| Value | Default | Environment variable |
|---|---|---|
| Font size | 0.75 rem | `KFXGEN_SUPERSCRIPT_FONT_SIZE` |
| Superscript shift | +35% | `KFXGEN_SUPERSCRIPT_SHIFT_PCT` |
| Subscript shift | −20% | `KFXGEN_SUBSCRIPT_SHIFT_PCT` |

They are overridable because they were recovered from a handful of reference
files and confirmed on one device model; a Kindle that disagrees can be
corrected without a rebuild.

Publisher EPUBs rarely use `<sup>`. They wrap the marker in a `<span>` whose
class carries `vertical-align`, and the value is as often a raw length
(`0.25em`) as the `super` keyword — both routes have to be recognised. Note
that the CSS route depends on Calibre's Stylizer, which only exists inside
Calibre: the local end-to-end shim exercises the tag route only, so the CSS
route is unit-tested against an injected resolver rather than end to end.

A run that is **only** a link gets no `$157`. Reference KFX leaves link spans
unstyled and lets the reader render them.

### The invariant that catches regressions

Wherever a return link lands must be exactly where the marker pointing at that
note sits — same position **and** same offset. That is the property that broke
in #79 and it is now a standing test.

Measured on a trade book with 2,367 anchors and 1,950 reciprocal pairs: before
the fix, 975 of 1,950 return links landed on their marker — only the ones that
happened to sit at a paragraph start. After, 1,950 of 1,950.

---

## 6. Images, fonts, and styling

### Images

Body images become their own `$259` entries — `$159: $271`, a `$175` naming the
resource, and `$584` carrying alt text — paired with a `$164` describing the
resource and a `$417` holding the bytes. They hold no text, so they carry no
`$145` reference.

They still occupy a position. Image entries get a synthetic one-character
offset in `$265` so they receive their own entry without colliding with
adjacent text. Excluding them broke navigation in image-heavy chapters: the
surrounding storyline had unmapped positions and the reader bailed out to the
start of the book.

Each image is classified by pixel size, and the class picks the `$157`:

| Class | Test | Style |
|---|---|---|
| `page` | ≥ 600 × 600 | `s_img_page` |
| `small` | ≤ 300 × 300 and aspect ratio ≤ 1.4 | `s_img_sm` |
| `inline` | anything else, including unknown dimensions | `s_img` |

Optimization gates on **both** dimensions and bytes (`DEFAULT_MAX_DIM` 2048,
`DEFAULT_MAX_BYTES` 1 MB). It once triggered on dimensions alone, so an image
well inside the pixel budget but enormous in bytes passed through untouched
(#55).

Anything under 100 bytes, or without a JPEG/PNG magic number, is not treated as
an image at all.

### Fonts

`$418` holds the font bytes and `$262` the `@font-face` declaration — the exact
analogues of `$417` and `$164` for images. `$11` is the family, `$13` the weight
(`$361` bold), `$12` the style (`$382` italic).

The matching model is the thing to understand: **Kindle matches a run to a face
by family, weight, and style together.** The `$157` applied to a run must carry
that run's own weight and style, or a bold run resolves against the regular face
descriptor and falls back to a synthesized face (#50).

`docs/kfx-embedded-fonts-reference.md` covers this subsystem in detail.

### Block styling

Block styles come from Calibre's Stylizer, which computes CSS per element. That
matters for testing: **Stylizer only exists inside Calibre**, so any CSS-driven
behaviour is unit-tested against an injected resolver and cannot be exercised by
the local end-to-end shim.

What is carried: alignment (`$34`), text-indent, and left/right margins. Inline
emphasis becomes character spans rather than block properties.

One asymmetry worth knowing: `vertical-align` is consulted **only** for inline
descendants, never for the block element itself. A paragraph carrying it would
otherwise turn its entire text into one raised run.

---

## 7. The container, and the Ion layer underneath

Everything above describes fragments. This section is about the envelope they
travel in, and the encoding that turns them into bytes.

### What a container carries

| Fragment | Holds |
|---|---|
| `$270` | Container info — **required**. Container id, chunk size (4096), compression (`0`, none), DRM scheme (`0`, none), container format, and the entity map |
| `$419` | Entity index — container id and the names of every entity in it |
| `$490` | Book metadata, in three groups: audit, ebook, and title |
| `$258` | Reading order metadata |
| `$538` | Document data / reading order |
| `$585` | Content features |

The **entity map** in `$270` is a list of `[fragment type, entity id]` pairs
naming everything the container holds. It is not decoration — this is what a
reader consults to find fragments, and it is also why symbol allocation order
matters: change the order symbols are created and the ids in this map shift,
which is how the same book produced two different files (#96).

### kfxgen identifies itself as Kindle Previewer

`$490`'s audit group declares `file_creator: "KPR"` and
`creator_version: "3.98.0"`. kfxgen is not Kindle Previewer, and 3.98.0 is not
the Previewer version installed today.

**The history:** these values were copied from a reference file during early
debugging, as one of a series of attempts to get output a reader would accept.
The debugging log records that attempt as *"Still fails"* — it was not the fix.
The values stayed. `3.98.0` was simply the Previewer version in use at the time.

**What testing shows (2026-08-07):** they appear not to be load bearing.

Two builds of the same book, differing in exactly one fragment — the `$490`
audit group present in one and absent in the other, 2,617 fragments each,
verified identical elsewhere:

| Check | With fields | Without |
|---|---|---|
| Upstream `kfxlib` decode | identical messages, features, warnings | identical |
| Opens on device | yes | yes |
| Note links, both directions | work | work |
| TOC navigation | works | works |
| Page Flip | works | works |
| Reading progress | normal | normal |

**What that does and does not establish.** It covers the visible reading surface
on one device and one firmware. It does not cover Amazon-side features a
sideloaded personal document never reaches — store integration, X-Ray, Word
Wise, cross-device sync — nor any behaviour gated on `creator_version` in a
firmware not tested. Upstream `kfxlib` never reads either field, so the decode
comparison is weak evidence by itself; the device run is what carries weight
here.

So the values are inherited and, on everything that can be observed from a
sideload, unnecessary. They are still emitted, because "no observed difference"
and "safe to remove" are not the same claim, and the cost of keeping them is 54
bytes.

The title group carries ASIN, `asset_id` (the container id), author, title, and
`cde_content_type: "PDOC"` — personal document, which is what a sideloaded book
is.

### Ion, in one paragraph

KFX is [Amazon Ion](https://amzn.github.io/ion-docs/) underneath: a typed,
self-describing serialization format with both binary and text encodings. A
fragment is an Ion struct; `$NNN` is not kfxgen notation but an Ion **symbol
id** — an integer standing in for a name, resolved through a symbol table.

That indirection is the reason this whole document is necessary. The names exist
in Amazon's table; we get the integers.

### Symbol tables

Two tables are in play:

- **`YJ_symbols`**, Amazon's shared table (version 10). A file *imports* it,
  declaring a `max_id` — how many of its symbols the file uses. kfxgen declares
  842, which is legal: an import may name a prefix. The highest symbol kfxgen
  actually emits is `$790`.
- **The local symbol table**, built per file, holding names kfxgen invents —
  `content_1`, `s0_h`, `body_anchor_427`, `toc_anchor_0`. Every name a fragment
  references must be created here first, or the file references a symbol that
  does not exist.

Amazon has grown `YJ_symbols` past what the pinned decoder knows — Previewer
3.106.0 emits `max_id` 844, upstream `kfxlib` 20260520 knows 843, and our own
trimmed copy knows 842. Harmless for generation, since kfxgen emits nothing up
there, and tracked in #91.

### The encoding layer

`kfxlib_minimal` is mostly a trimmed copy of jhowell's `kfxlib`, carrying the
Ion binary and text codecs, the symbol table machinery, and the KFX/YJ container
readers and writers. kfxgen does not implement Ion itself. (One file there,
`standard_symbols.py`, is original kfxgen work rather than vendored — see that
directory's README.)

That is a deliberate boundary. Bugs in serialization are upstream's territory
and get fixed by re-syncing the copy; bugs in *what we serialize* are ours.
The tier-2 differential decode exists precisely to tell those apart — it parses
kfxgen's output with both our trimmed copy and the full upstream library, on the
theory that where two decoders of shared ancestry disagree, one of them has
found a real bug.

---

## 8. Pitfalls

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

## 9. How to verify a claim

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

## 10. Where the rest of it lives

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
