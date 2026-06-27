# tools/

Diagnostic utilities for kfxgen output. Used during the structural-match
work that landed in \#13, \#14, \#16, \#20, \#21 (roadmap \#7) to compare
kfxgen's KFX output against a Calibre `KFX Output` (jhowell) gold-standard
build of the same source EPUB.

## kfxdiff.py

```
python3 tools/kfxdiff.py <input.kfx> [output.txt]
```

Dumps a KFX file's full fragment structure to a text report. Groups
fragments by Ion type (`$145`, `$259`, `$260`, ...) and prints the
serialized form of each. Output is large (a few MB for a typical book)
but lets you grep/diff specific fragments.

If `output.txt` is omitted, writes `<input>.dump.txt` next to the input.

## kfxanalyze.py

```
python3 tools/kfxanalyze.py <reference.kfx> <ours.kfx>
```

Side-by-side structural comparison: fragment-type histograms,
`$164/$417` resource lists, `$157` style-shape distribution, and the
first few `$145` content fragments. The fastest way to confirm whether
kfxgen output is in the right shape vs a known-good reference.

## glossary_compare.py

```
python3 tools/glossary_compare.py <reference.kfx> <ours.kfx>
```

Targeted comparison for glossary-bearing books. Locates the glossary
content in `$145` fragments (using a keyword heuristic — the default
tokens are placeholders keyed to the maintainer's local test book and
will not match anything else), then dumps the matching `$259` entries
from both files so you can compare flat-vs-nested structures and
image references.

Edit `GLOSSARY_HEURISTIC` at the top of `glossary_compare.py` to set
tokens characteristic of your own glossary chapter before running.

## Generating a reference KFX

These tools assume you have two KFX files for the same source EPUB:
one from kfxgen and one from jhowell's `KFX Output`. To generate the
reference:

1. Install jhowell's plugin (`KFX Output` from MobileRead) in Calibre.
2. Disable kfxgen (`calibre-customize --disable-plugin "kfxgen"`)
   so the file extension resolves to jhowell's plugin.
3. Run `ebook-convert <input.epub> <ref.kfx>`.

Then re-enable kfxgen and run the same command to a different output
path for the comparison side.
