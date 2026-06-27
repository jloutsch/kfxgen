# Security Policy

## Threat model

kfxgen is a Calibre output plugin that converts EPUB → KFX. Its threat model
was pinned by the Council of High Intelligence review on 2026-05-02 (issue
issue 42).

### Adversary

- **Adversarial EPUB author.** Users may convert EPUBs from untrusted sources
  (free downloads, mailing lists, scraped archives). Any field of the EPUB —
  manifest hrefs, OPF metadata, image bytes, embedded XML — must be treated as
  attacker-controlled.

### Blast radius

- **Single-user, single-machine.** kfxgen runs locally inside Calibre on the
  user's own machine. There is no multi-tenant server, no network listener, no
  shared filesystem. A successful exploit compromises one user's machine, not a
  fleet.

### Out of scope

- Network attackers (no network surface).
- Other users on the same machine (Unix permissions are the boundary).
- Compromised Calibre installation (we trust the host).
- Compromised Python interpreter or kindlegen / Kindle Previewer binaries.

### Kindle Previewer 3 disposition (\#53)

`Kindle Previewer 3.app` is a third-party Amazon binary used as a manual
smoke-testing tool against generated KFX. It is **not bundled in the
repo** — `.gitignore` (`Kindle Previewer*.app/`, `*.app/`) excludes it
from version control. The maintainer keeps a local copy under the
project root for ergonomic CLI access; that copy never ships to
contributors or CI.

The council's \#53 brief raised three risks based on the assumption that
the binary was committed:

| Risk | Reality |
|---|---|
| Repo bloat (~1 GB on every clone) | False — gitignored, never cloned |
| License/redistribution risk | False — never redistributed by us |
| Supply-chain compromise via silent updates | Limited — affects maintainer's local manual smoke runs only, never CI |

Per \#42's oracle hierarchy, Kindle Previewer is **explicitly excluded
as a CI gate** (its tier-3 oracle role was rejected for empirical
reasons during the v5.3.x cycle: KP "lied" about device behavior 3-4
times). It exists only as a pre-release manual smoke tool against the
fixture corpus.

Disposition: option C from the issue brief — keep the maintainer's
local-only manual tool as-is, document the existing state. No code
change required.

## Layered defense

kfxgen relies on Calibre for the EPUB-parsing surface and defends the
KFX-generation surface itself.

### What Calibre defends — kfxgen does NOT duplicate

| Threat | Calibre defense |
|---|---|
| XXE / external entity attacks | `safe_xml_fromstring` in `calibre/utils/xml_parse.py` (`no_network=True` + custom Resolver returns empty for SYSTEM/PUBLIC entities) |
| Zip-slip on EPUB extraction | `calibre/utils/zipfile.py` strips `..` and `.` segments from zip-member paths during extraction |
| Billion-laughs entity expansion | lxml/libxml2 default expansion limits (Calibre does not enable `huge_tree`) |

### What kfxgen MUST defend

| Threat | kfxgen defense | Tracked in |
|---|---|---|
| Manifest `href` containing `../` (path traversal into output dir) | `_normalize_href` strips traversal segments before resolving | issue 44 |
| Output path symlink at destination, or `..` traversal in path | `_safe_write_bytes` (native_generator.py) refuses `..` segments and existing-symlink destinations, then writes via `os.O_NOFOLLOW` to a `.tmp` slot and renames atomically into place | issue 45 |
| Cover/body image bytes mislabeled by manifest media-type | Body images: `extract_inline_images` magic-byte check (pre-existing). Cover: `extract_cover_image._get_image_data` rejects non-JPEG/PNG at source; `generate_full_book` raises `ValueError` if garbage somehow reaches the cover-emit branch (defense in depth) | issue 46 |
| Crafted Ion binary with oversized length field (DoS via memory exhaustion) | `Deserializer.extract` (kfxlib_minimal/utilities.py) rejects size > `MAX_DECODE_SIZE` (64 MB) and negative sizes BEFORE the slice, with a security-channel WARNING. Defends every length-bounded read in `ion_binary.py` (single choke point) | issue 47 |

### Calibre-territory threats — why kfxgen has no fixture coverage

For each Calibre-defended threat, the integration corpus
(`tests/integration/test_epub_corpus.py`) does NOT include a fixture.
Two reasons combined: post-Calibre input is benign, and kfxgen has no
threat-specific defense to verify if Calibre's filter is bypassed.

| Threat | Post-Calibre shape kfxgen sees | Why no fixture |
|---|---|---|
| XXE | Text with entities resolved to empty | Indistinguishable from any normal-text input — already covered by `single_chapter` and every chapter fixture |
| Billion-laughs | Bounded text (lxml expansion limit hit) | Same as XXE — post-Calibre is benign text |
| Zip-bomb | kfxgen never sees the input (Calibre refuses extraction) | Not exercisable through the `EpubAsOeb` shim |
| Symlink-in-zip | Manifest items with `..` and `.` segments stripped by Calibre's zipfile.py | Already covered by `path_traversal_href` (which exercises the same post-strip code path through `_normalize_href`) |

Defense-in-depth tests (kfxgen's behavior when Calibre's filter is
bypassed) are out of scope for this corpus: kfxgen has no threat-
specific defenses to verify in those scenarios — failures would be OOM
or lxml-unsafe-parse, which are Calibre's responsibility to prevent.

Tracked as **wontfix-by-design** in
issue 83.

## Logging

Defensive rejections (e.g. `_normalize_href` dropping an unsafe href, or
`_safe_write_bytes` refusing a symlink at the output path) are emitted via
the Python `logging` module at WARNING level on per-module security
channels (`kfxgen.converter.security`, `kfxgen.native_generator.security`). Calibre's GUI plugin host surfaces these in the
conversion log panel automatically. CLI users invoking via `calibre-debug` or
similar may need to raise verbosity (`logging.basicConfig(level=logging.WARNING)`)
to see them.

## Advanced configuration

These overrides exist for legitimate edge cases but expand the attack
surface. Set them ONLY when you trust the input being processed.

### `KFXGEN_MAX_DECODE_SIZE`

Overrides the `MAX_DECODE_SIZE` bound used by `Deserializer.extract`
(issue 47). Default is 64 MB,
which is comfortable headroom for legitimate KFX fragments (real
fragments are rarely >10 MB). The value is parsed at import time as a
positive integer byte count; invalid or non-positive values fall back
to the default with a logged WARNING.

**Hard ceiling: 1 GB.** Values above 1 GB are clamped to 1 GB with a
logged WARNING (the requested bound is NOT honored). This bounds the
attack surface even when the operator is misconfigured or hostile —
documentation alone is not a guardrail.

**Trust model.** Raising this bound expands the DoS attack surface — a
crafted Ion binary with a length field at the new ceiling will attempt
to address that much memory before the guard fires. Only raise the
bound when converting EPUBs from sources you trust (your own library,
a vetted publisher feed). For untrusted EPUBs, leave the default.

Example (256 MB ceiling for a known-large internal corpus):

```bash
KFXGEN_MAX_DECODE_SIZE=268435456 calibre-debug -e plugin/kfxgen/...
```

## Reporting a vulnerability

Open a private security advisory on GitHub:
<https://github.com/jloutsch/kfxgen/security/advisories/new>.

Do not file public issues for security bugs.

## Verification

The defenses listed above are tagged in the test suite under the oracle tiers
defined in [CONTRIBUTING.md](CONTRIBUTING.md). Tier-1/2/3 tests run on every
PR; tier-4 (device) gates release tags.
