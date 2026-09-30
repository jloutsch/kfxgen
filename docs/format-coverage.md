# Format coverage: what has been tested, and the gaps

A review of which kinds of books kfxgen has been exercised against, what broke
on each, and which formatting constructs no corpus or test covers yet. Written
2026-09-30 against `main` at v5.8.7 (plus open PR #221 where noted).

Sources: every issue in the tracker, `CHANGELOG.md`, the test tiers
(`CONTRIBUTING.md`), the Gutenberg baselines under `research/`, and a direct
probe of `extract_blocks_from_html` with one small snippet per construct
(section 3). Private-library books are described by genre only.

## 1. What has been tested, by genre

| Genre / book type | Where it came from | What it surfaced | State |
|---|---|---|---|
| Novels (plain prose) | Gutenberg 90; device checks | Dead links, TOC and Go To pane (#20, #23, #51, #62, #143); doubled chapter openers (#64); emphasis lost after nested tags (#59) | Fixed; the most-covered genre |
| Illustrated novels, photo books | Gutenberg (Pride & Prejudice 164 images, Monte Cristo 450, Mars Rovers 427, Forbidden Land 1,052) | Images drawn as slivers (#145); captioned images dropped (#113); oversized files (#11, #55) | Fixed. Tap-to-zoom not planned (#118) |
| Children's picture books, page scans | Contributor's 226-EPUB library | Unlisted art pages dropped (#152); stated sizes ignored (#153); SVG-wrapped art (#154); CSS-background pages (#168); GIF pages (#177); title-page pictures (#178, #182) | Fixed, mostly against Kindle Previewer; GIF output not seen on a device |
| Comics, fixed-layout | Calibre Comic Input, IDPF sample | SVG-rooted spine pages (#165); "Page N" TOC links (#156); cover layout (#155); no home-screen cover on Oasis (#188, not planned) | #155 open |
| Drama | Gutenberg (Shakespeare with 1,169 TOC entries, Ibsen, Wilde) | Section-position overflow at ~1,140 chapters (#30) | Fixed. Only `<p>`-based speaker layouts are covered |
| Verse, poetry | Gutenberg (Beowulf, Odyssey, Chaucer); Voyage device check on sonnets | `<br>` lost, 4,101 fused words (#202, #212); inherited centering (#33) | Fixed for `<br>` and `<pre>` verse. See G6 for span-per-line verse |
| Technical, code | 50-book library sample (30 with code) | `<pre>` collapsed (#202) | Fixed for `<pre>`. Gutenberg has no `<pre>` |
| Academic / trade non-fiction with endnotes | Trade EPUB (975 note refs); history book (#223) | Superscripts dropped (#52); inert note links (#53, #79); notes laid out as a table run together (#223) | #223 fixed by PR #221 |
| Reference | Gutenberg (CIA World Factbook ×2, Dutch dictionary) | Adjacent table cells fused (#128) | Fixed. Tables still have no layout (#219) |
| Commercial EPUB 3 | Maintainer's 1,333-EPUB library (surveyed, not all converted) | List markers carried only in markup (#201); hidden page-list and landmarks rendered as text (#60) | Fixed |
| CJK, Cyrillic | A Korean book, a CJK reference book, a CJK novel | Font family names collapsed (#36); content fragments over 8 KB (#37) | Fixed. Only horizontal text has been tried |
| German, Dutch, Swedish | Gutenberg | Nothing specific | OK |
| Markdown input | #192 | Surfaced #201, #202, #203 | Fixed |
| PDF input | #94 | Verse and dialogue line breaks can't be recovered | Not planned |

Coverage tiers, for reference: unit tests, 13 golden fixtures, about 15
edge-case EPUBs, 22 list cases checked against the real Stylizer (local only),
a tier-2 decode (read back by the upstream KFX Input plugin), an opt-in corpus
sweep, and a six-item device checklist (tier 4) on Oasis, Paperwhite and
Voyage. Golden and corpus runs use the `EpubAsOeb` shim, which builds no
Stylizer, so **CSS-dependent behaviour is only exercised for list markers**
(#33, #205, #222).

## 2. Gaps not yet accounted for

Ranked by harm. "Loses content" means text or meaning is gone or misleading,
not just styled differently.

### Loses or garbles content

| # | Gap | Genres hit | Evidence |
|---|---|---|---|
| G1 | **`<dl>` runs together with no spaces.** `<dt>Term</dt><dd>Def</dd>` comes out as one block, `TermDef.Term2Def two.` | Glossaries, dictionaries, cast lists, notes laid out as definition lists, cookbook ingredient/quantity pairs | Probe; the same on the #221 branch |
| G2 | **Tables have no row or column layout.** Cells are joined into one paragraph. PR #221 gives one paragraph per row; there are still no columns, and `colspan` is ignored | Reference, textbooks, cookbooks (nutrition), sports and finance, notes laid out as a table | #219 (open), #221, #222 |
| G3 | **Scene breaks vanish.** `<hr/>` is dropped with nothing in its place, and the spacing between paragraphs (`margin-top`/`-bottom`) isn't read, so a break made only by a gap disappears too. A typed `* * *` survives | Fiction, most commercial novels | Probe; resolver fields at `converter.py:88-135` |
| G4 | **Text drawn inside inline SVG is dropped.** `<svg><image/><text>Once upon</text></svg>` keeps the picture and loses the words | Picture books with text over the art, comics with SVG lettering | Probe |
| G5 | **`display:none` content is emitted.** `display` is read only to suppress list markers (`converter.py:931`); hidden elements are otherwise walked like visible ones | Textbooks (hidden answers), EPUB 3 content with a hidden fallback, publisher boilerplate | Code reading; unconfirmed, because the shim has no Stylizer |
| G6 | **Lines made with `display:block` spans run together.** `<span class="line">A</span><span class="line">B</span>` becomes `AB` | Verse from some producers, song lyrics, addresses | Probe (no CSS applied; the extractor ignores `display` on spans either way) |
| G7 | **`<epub:switch>` renders every branch.** The MathML case and the fallback are both emitted (`MDefault`) | Maths and science EPUB 3 | Probe |
| G8 | **MathML is flattened to linear text.** `r<sup>2</sup>` in MathML reads `r2`, and fractions, roots and matrices are lost | Textbooks, academic, science | Probe |
| G9 | **Ruby annotations are run into the base text.** `漢<rt>kan</rt>字<rt>ji</rt>` reads `漢kan字ji` | Japanese (furigana), Chinese (pinyin), language learners | Probe |
| G10 | **Strike-through and inserted text look unmarked.** `<s>`, `<del>`, `<u>`, `<ins>` get no style (only generated TOC links carry underline), so deleted text reads as current | Legal, errata, annotated editions, some fiction | Probe; `native_generator.py:1394` is the only underline |

### Loses styling, text intact

| # | Gap | Genres hit |
|---|---|---|
| G11 | Boxed sidebars and callouts (`border`, `background-color`) come out as ordinary paragraphs | Textbooks, cookbooks, how-to, children's non-fiction |
| G12 | Small caps, `text-transform`, `letter-spacing`, colour are not carried | Literary fiction openers, headings, children's books (coloured text) |
| G13 | Drop caps (`::first-letter`, float spans) come out as plain first letters | Literary fiction, classics |
| G14 | `font-size` is carried only for headings and sub/sup, so large-type or small-print blocks lose their size | Picture books (large type), legal small print, epigraphs |
| G15 | Inline `<code>` loses its monospace font (`<pre>` is fine) | Technical |
| G16 | A floated image doesn't wrap text, and a figure's caption shares the image's block (the effect on a device is unverified) | Textbooks, magazines, illustrated non-fiction |
| G17 | Leading `&nbsp;` indentation is stripped | Verse set with non-breaking spaces, older conversions |
| G18 | WOFF and WOFF2 fonts are skipped with a warning (`font_table.py:169`) | Modern EPUB 3 exports |

### Genres or features never tried

| # | What | Why it matters |
|---|---|---|
| G19 | **Right-to-left books.** Arabic and Hebrew text passes through, but `dir` and `page-progression-direction="rtl"` are not carried, so manga and Arabic books page in the wrong direction | Manga, Arabic, Hebrew, Persian |
| G20 | **Vertical writing** (`writing-mode: vertical-rl`) | Japanese novels and manga |
| G21 | **Read-aloud (media overlays, audio).** Audio and video show only their fallback text, which is acceptable, but read-aloud children's books lose their narration without any warning | Children's EPUB 3 |
| G22 | **Cookbooks, textbooks, magazines** as whole genres: no book of these types has been converted and read | They combine G1, G2, G11, G14, G16 |
| G23 | **CSS against the real Stylizer.** Apart from list markers (#205), no test runs CSS through Calibre's real Stylizer. #222 covers tables | Every CSS-dependent gap above |

Known and accepted: `max-width` and `max-height` are ignored, matching
Previewer (`converter.py:233`); there is no tap-to-zoom (#118) and no on-device
search (#151); malformed source TOC labels pass through (#119).

## 3. Probe results (`main`, v5.8.7)

Each snippet was passed to `extract_blocks_from_html` with no style resolver.
✓ = one block per logical unit with the text intact.

| Construct | Result |
|---|---|
| `<br>` verse, `<div>` per line, stanzas | ✓ |
| `<ul>` nested, `<ol start type>`, `<ol reversed>` | ✓ (`e. five`, `2. x`) |
| `<aside>` sidebar, `<details>`, `<header>`/`<footer>`, `<hgroup>` | ✓ text (no box styling) |
| `<pre><code>` | ✓ whitespace kept |
| `<sub>`/`<sup>` | ✓ |
| Notes as `ol/li`, `aside`, `p` with back-links | ✓ |
| Notes as a table | one block per table on `main`; ✓ on #221 |
| `<dl>` | ✗ `TermDefinition one.Term2Def two.` (G1) |
| `<table colspan>` | ✗ one block (G2) |
| `<hr/>` between paragraphs | ✗ dropped (G3) |
| Inline `<svg><text>` | ✗ text dropped (G4) |
| `display:block` spans | ✗ `AB` (G6) |
| `<epub:switch>` | ✗ `MDefault` (G7) |
| MathML `msup` | ✗ `r2` (G8) |
| `<ruby>` | ✗ `漢kan字ji` (G9) |
| `<s> <del> <u> <ins>` | ✗ no spans (G10) |
| Leading `&nbsp;` | ✗ stripped (G17) |
| `<audio>`/`<video>`/`<object>` fallback | fallback text shown (acceptable) |

## 4. Suggested order

1. **G1 `<dl>`**, **G3 scene breaks** and **G6 block spans**: small extractor
   changes that affect common books.
2. Land **#221**, then **#222** and **G23**: build the real-Stylizer harness
   once and reuse it for G5, G10–G14.
3. **G4 SVG text** and **G21 read-aloud warning**: the picture-book library
   already exists to verify against.
4. **G7 `epub:switch`**: take only the default branch. It's cheap, and it fixes
   doubled text in maths books before G8.
5. **G19/G20 RTL and vertical text**: needs a reference KFX from Previewer
   first, like #155 did for covers.
6. Convert one cookbook, one textbook and one manga (G22) and put each through
   the device checklist.
