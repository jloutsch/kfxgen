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
- `baseline-gatsby-20260520.json` — prose sample (v1 baseline).
- `baseline-fonts-20260520.json` — embedded-font sample (#85).
- `check_drift.sh` — monthly watch that says when running the diff is worth it.
- `com.kfxgen.driftcheck.plist` — `launchd` job for the above.

## Samples and what each one reaches

The two baselines are complementary, and neither alone is the format surface.
Diff **both** when the watch fires.

| Sample | Source | Reaches |
|---|---|---|
| `gatsby` | Gutenberg #64317 | prose, `$145` content splitting, anchors (`$266`), and the basic image surface (`$164`/`$417`) via its cover |
| `fonts` | `test_books/font-matching-test/` | `$262` face descriptors ×4 and `$418` font locations ×4 — one per face — plus `$11`/`$15` |

The font sample was added because the prose baseline had **no** font surface at
all: `$262`, `$418` and `$11` were absent, so drift in the family+weight+style
matching behind #50 was invisible to this check. Note the inverse is not true —
prose already covers basic image emission, so an "image sample" would add much
less than it appears to (#85).

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

It checks two independent things:

1. **Installed vs baseline** (offline, reliable) — has your toolchain moved past
   any committed baseline? Every baseline is checked and the stale ones named.
2. **Upstream availability** (network, advisory) — has Amazon shipped a newer
   Previewer? This exists because the first check only fires when *you* update,
   so on its own the watch can sit quiet for months while drift accumulates
   upstream (#88).

| Exit | Meaning |
|------|---------|
| 0 | in sync — silent, so a monthly job is not noise |
| 1 | a baseline is stale, or a newer Previewer is available |
| 2 | cannot check (Previewer or KFX Input missing, or no baseline) |

### How the upstream check works, and what it will not claim

Previewer announces its own availability at the end of a run, which is an
official signal rather than a scraped download page. The job runs `-log` (which
validates without producing a KPF) over `test_books/minimal_test_book`, about
8 seconds on a 2 KB book, and reads the notice from its output.

`-update` is deliberately **not** used. Its own help says *"Download and install
the latest software update"* — this job never installs anything.

Absence of the notice is **not** treated as "up to date". Offline, a reworded
notice, or a broken invocation would all look identical to good news, so the
run is only trusted when a stable marker proves Previewer actually got to the
end of its work. Otherwise the state is `unknown`, which is logged rather than
reported as current. `unknown` does not raise an alert — a transient network
failure should not nag monthly — but every run records its upstream state in
the log, so a persistently broken check is visible there.

Set `KFXGEN_DRIFT_SKIP_UPSTREAM=1` to disable the upstream check entirely and
keep only the offline comparison.

### The KFX Input index check (#91)

A second upstream check, independent of the Previewer one: it compares the
installed KFX Input plugin against the version calibre publishes in its plugin
index — the same JSON its own plugin updater reads. This exists because the
vendored kfxlib pin and the drift baselines are maintained in different places,
so each can look current on its own while the plugin that produced them has
moved on. Amazon grew the shared `YJ_symbols` table past what the pinned kfxlib
can name; a newer plugin is what would let us name the new symbols.

**This one does run on the schedule**, and that was verified under the real
agent rather than assumed — see below. It is bounded by the same
`KFXGEN_DRIFT_PROBE_TIMEOUT`, and an unreachable, malformed, or renamed index
reports `unknown`, never "up to date".

Set `KFXGEN_DRIFT_SKIP_PLUGIN_INDEX=1` to disable it. `KFXGEN_PLUGIN_INDEX_URL`
overrides the index location, which is also how the check's own behaviour is
exercised — point it at a local `file://` bz2 and you can drive every state
without waiting for Amazon to ship anything. `KFXGEN_CALIBRE_DEBUG` overrides
the path to `calibre-debug`.

#### Why the fetch goes through calibre

`code.calibre-ebook.com` serves an **incomplete certificate chain** — it does not
send its intermediate — so anything trusting only the OS store fails on it.
Measured on this machine: `api.github.com` and `example.com` return 200 while
that host gives `curl: (60) unable to get local issuer certificate`, and Xcode's
`/usr/bin/python3` gives the urllib equivalent. It is the host, not the network
and not `launchd`.

calibre ships its own CA bundle, which is why its plugin updater reaches an index
`curl` cannot. The check calls `calibre.utils.https.get_https_resource_securely`
through `calibre-debug`, so it reads the index exactly as calibre does, and
calibre is guaranteed present wherever this script has anything to check.

#### Two traps this check walked into, both silent

Recorded because each produced a **false all-clear** — the "a newer plugin
exists" case reporting `current` — and each only in the conditions the schedule
actually runs in:

1. **stdin.** The Python is passed with `-c`, never on stdin. `run_bounded`'s
   fallback backgrounds its command, and a background job in a non-interactive
   shell has stdin redirected from `/dev/null`, so a heredoc-fed script arrives
   empty, python exits 0 having done nothing, and that reads as success. The
   fallback is only reached where `timeout` is absent, which is stock macOS.
2. **Certificates.** The first two implementations used `urllib`, then `curl`.
   Both worked by hand and returned `unknown` on every scheduled run.

The lesson for anyone changing this: run it under the installed agent
(`launchctl kickstart -k gui/$(id -u)/com.kfxgen.driftcheck`) and read the log
line. A hand-run proves nothing about the schedule — that is what #92 already
cost, and this check re-learned it twice.

#### The upstream check is interactive-only

It does not work under `launchd`, so the shipped plist disables it. Measured on
the same machine, same script:

| Context | Elapsed | Result |
|---|---|---|
| run by hand | ~9-16s | `current` / `available` — correct |
| run by `launchd` | 100s | hits the 90s ceiling → `unknown`, every time |

Kindle Previewer simply does not finish when a background agent launches it —
most likely a GUI-session constraint on driving an Aqua app that way. Nothing
appears on stderr.

The timeout means this costs 90 wasted seconds rather than hanging the job, but
a check that can only ever return `unknown` is worse than no check: it looks
installed and healthy while reporting nothing. So the scheduled job runs the
**offline half only** — which is the half that has actually caught drift — and
the upstream check is something you run by hand.

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

## Current baselines

Both taken with Kindle Previewer **3.106.0** (`-convert` → KPF) and `kfxlib`
**20260520** (KFX Input 2.33.0).

| Baseline | Source | Surface |
|---|---|---|
| `gatsby` | *The Great Gatsby* (Project Gutenberg #64317, US public domain) | 21 fragment types, 157 fragments, 161 symbols, max `$800` |
| `fonts` | `test_books/font-matching-test/` (Charis SIL, OFL) | 20 fragment types, 34 fragments, 95 symbols, max `$799` |

The filenames key on the **kfxlib** version, which is what determines the
inventory's vocabulary. Previewer 3.98.0 → 3.106.0 moved the contents without
changing that, so these files were updated in place; git history holds the
3.98.0 snapshots.

### First real drift, Previewer 3.98.0 → 3.106.0

Recorded because it is the only worked example of this machinery firing:

- prose gained one fragment and one symbol, **`$23`**, appearing on exactly one
  of 53 `$157` style fragments as `{$173: s1WF, $23: $328}`

  Identified rather than left as a number: `$23` is `text-decoration` and
  `$328` is `underline` (upstream `yj_to_epub_properties.py` carries the enum —
  `$329` double, `$330` dashed, `$331` dotted, `$349` none), and `$173` is the
  style's own name. So the fragment is a style named `s1WF` whose only property
  is underline — Previewer 3.106.0 began emitting an underline style for this
  book where 3.98.0 did not, most likely for its internal links.

  Note this is a symbol newly *used*, not newly invented: `$23` has always been
  in the table, and **kfxgen already emits `$23: $328` itself** for underlined
  TOC links (`native_generator.py`, and the `linked_toc` golden carries one).
  Amazon started using a construct we were already producing.
- the font sample was **unchanged**, which is what narrowed the finding — the
  drift is not in the font surface
- `kfxlib` did not move, so there were no upstream decoder changes to fold into
  `kfxlib_minimal`; the change is purely in what Amazon's converter emits
- kfxgen writes its own `$157` styles and never emits `$23`, so its output is
  unaffected

Decoding also warned that Previewer 3.106.0 emits `YJ_symbols` with `max_id 844`
while the installed `kfxlib` knows 843 — Amazon extended the shared symbol
table. Tracked separately.

Together they cover 164 symbols. The font sample contributes `$262`, `$418`,
`$11` and `$15`; the prose sample contributes `$164`, `$266` and `$417`, which
the font sample lacks. Keep both.

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

### The font sample

Its source is the tracked `test_books/font-matching-test/` directory (Charis
SIL, SIL OFL, license included), so there is nothing to download. Zip it to an
EPUB first — `mimetype` must be stored first and uncompressed:

```bash
cd test_books/font-matching-test
zip -X -q0 /tmp/font-matching.epub mimetype
zip -X -q -r /tmp/font-matching.epub META-INF OEBPS -x '.*'
```

Then convert and inventory it exactly as above. Two things that will waste time
otherwise: Previewer's `-output` folder must already exist or it exits with
"Output should point to a valid folder", and the font sample must **not** be
routed through the Calibre GUI — a local-only "Strip Embedded Fonts" plugin
removes embedded faces on add, which would produce a font baseline containing
no fonts.

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
