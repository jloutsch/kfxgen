# KFX format-drift baseline (#18, item C)

KFX is an undocumented Amazon format that changes as Kindle firmware evolves. This
directory is the drift detector: a fixed source EPUB converted by **Amazon's own
engine** (Kindle Previewer), decoded to a stable **inventory** of fragment types +
`$`-symbols. Diffing that inventory across Kindle Previewer / `kfxlib` versions
surfaces new symbols or fragment types the vendored `kfxlib_minimal` fork may need
to catch up to.

It is **forward-looking**: Amazon does not archive old Previewer installers, so
history can't be rebuilt. Snapshot the current version as the baseline, then add a
snapshot on each future Previewer release and diff.

## What's here

- `kfx_inventory.py` — decode a KPF/KFX (via the full `kfxlib` in the installed
  *KFX Input* plugin) → JSON inventory; `--diff` a new inventory vs a baseline.
- `baseline-gatsby-20260520.json` — the v1 baseline.
- `check_drift.sh` — monthly watch that says when running the diff is worth it.
- `com.kfxgen.driftcheck.plist` — `launchd` job for the above.

## Automated watch (#46)

Running the diff on a schedule learns nothing on its own: re-converting the same
source with the same installed Previewer and `kfxlib` reports "no drift" forever.
The informative moment is when the *installed* toolchain moves past whatever
produced the baseline. `check_drift.sh` watches for exactly that.

It is **notify-only**. It reads two version numbers — Kindle Previewer's
`CFBundleShortVersionString` and `kfxlib/version.py` inside the installed KFX
Input zip — and compares them to `previewer_version` / `kfxlib_version` in the
newest committed `baseline-*.json`. It installs nothing, updates nothing, and
converts nothing, so it is safe to run unattended. Acting on a notification is a
person's job.

```bash
./check_drift.sh            # silent unless something moved
./check_drift.sh --verbose  # always print what it compared
```

| Exit | Meaning |
|------|---------|
| 0 | in sync — silent, so a monthly job is not noise |
| 1 | a version moved; the diff below is now worth running |
| 2 | cannot check (Previewer or KFX Input missing, or no baseline) |

On exit 1 it prints the exact commands to run, logs to
`~/Library/Logs/kfxgen-drift-check.log`, and posts a desktop notification.
Overridable via `KFXGEN_PREVIEWER_APP`, `KFXGEN_KFX_INPUT_ZIP`,
`KFXGEN_DRIFT_LOG`.

Note that Previewer reports `3.98` in its Info.plist while the baseline records
`3.98.0`. Version comparison is component-wise and numeric for that reason — a
string compare would report drift on the very first run and train you to ignore
this job.

### Installing the monthly job

`launchd` does not expand `~` or `$HOME` in `ProgramArguments`, so the path must
be edited before loading:

```bash
sed "s|REPLACE_WITH_ABSOLUTE_PATH|$(cd ../.. && pwd)|" \
    com.kfxgen.driftcheck.plist > ~/Library/LaunchAgents/com.kfxgen.driftcheck.plist
launchctl load ~/Library/LaunchAgents/com.kfxgen.driftcheck.plist

launchctl list | grep kfxgen          # registered?
launchctl start com.kfxgen.driftcheck # force one run now
cat ~/Library/Logs/kfxgen-drift-check.log
```

Remove with `launchctl unload ~/Library/LaunchAgents/com.kfxgen.driftcheck.plist`.

### Owner

Drift notifications go to the repository maintainer, who decides whether the new
symbols matter to `kfxlib_minimal`. This is a heads-up, not a gate — on-device
testing remains the real one, and a drift notice never blocks a release.

## Current baseline

| Field | Value |
|-------|-------|
| Source | *The Great Gatsby* (Project Gutenberg #64317, US public domain) |
| Converter | Kindle Previewer **3.98.0** (`-convert` → KPF) |
| Decoder | `kfxlib` **20260520** (KFX Input 2.33.0) |
| Surface | 21 fragment types, 156 fragments, 160 symbols, max `$800` |

Note: Gatsby's content already splits into **36 `$145` fragments** (Amazon's
≤8 KB content splitting — the reference behavior for #37), and uses `$593`
(capability metadata) and `$597` (section aux).

## Regenerating / checking drift on a new Previewer version

```bash
ZIP="$HOME/Library/Preferences/calibre/plugins/KFX Input.zip"
KP="/Applications/Kindle Previewer 3.app/Contents/MacOS/Kindle Previewer 3"
CD=/Applications/calibre.app/Contents/MacOS/calibre-debug

# 1. Fetch the fixed source (public domain):
curl -sL -o gatsby.epub "https://www.gutenberg.org/ebooks/64317.epub3.images"

# 2. Convert with Amazon's engine -> KPF:
"$KP" gatsby.epub -convert -output out/

# 3. Diff the new conversion against the committed baseline:
"$CD" kfx_inventory.py -- "$ZIP" out/KPF/gatsby.kpf \
    --previewer <new-version> --diff baseline-gatsby-20260520.json
```

- Exit 0 / "no drift" → format surface unchanged; nothing to do.
- Exit 1 / "DRIFT DETECTED" → new fragment types or `$`-symbols. Investigate:
  fold the relevant upstream `kfxlib` changes into `kfxlib_minimal`, update its
  audit table + upstream baseline, then snapshot a new
  `baseline-gatsby-<kfxlib-version>.json` and commit it.

To snapshot a fresh baseline (drop `--diff`):

```bash
"$CD" kfx_inventory.py -- "$ZIP" out/KPF/gatsby.kpf --previewer <ver> \
    > baseline-gatsby-<kfxlib-version>.json
```

## Do NOT commit the raw KPF/KFX

The source content is public domain, but the KPF/KFX **container** is Amazon's
converter output — a gray area in a public repo. Keep `*.kpf` / `*.kfx` local
(gitignored here); commit only the decoded JSON inventory (plain symbol/fragment
data, no Amazon code).

## Corpus TODO

Add one font-embedding sample and one image-heavy sample so drift in fonts/images
(not just prose) is caught too. Gatsby (plain prose) is the v1 seed.
