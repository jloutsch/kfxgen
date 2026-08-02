#!/bin/bash
# Format-drift watch (#46, variant B — notify only).
#
# Reports when the local KFX toolchain has moved past the committed baseline,
# which is the only condition under which running the drift diff can learn
# anything. It NEVER installs, updates, or converts: it reads two version
# numbers and compares them. Acting on a notification is the operator's job.
#
# Why version-compare rather than "ask Amazon for a new release": re-converting
# the same source with the same installed Previewer and kfxlib reports "no
# drift" forever (#46, constraint 2). The informative moment is when the
# installed tooling differs from what produced the baseline — that is exactly
# what this detects, with no network access and nothing to break when Amazon
# changes a download page.
#
#   exit 0  nothing to do — silent, so a monthly job is not noise
#   exit 1  a version moved; the drift diff is now worth running
#   exit 2  cannot check (Previewer or KFX Input missing)
#
# Usage:
#   ./check_drift.sh            # silent unless action is needed
#   ./check_drift.sh --verbose  # always print what it compared

set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

PREVIEWER_APP="${KFXGEN_PREVIEWER_APP:-/Applications/Kindle Previewer 3.app}"
PLUGIN_ZIP="${KFXGEN_KFX_INPUT_ZIP:-$HOME/Library/Preferences/calibre/plugins/KFX Input.zip}"
LOG="${KFXGEN_DRIFT_LOG:-$HOME/Library/Logs/kfxgen-drift-check.log}"

say() { printf '%s\n' "$*"; }
log() { mkdir -p "$(dirname "$LOG")"; printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG"; }

notify() {
  # Best-effort desktop notification; a headless launchd context may have no
  # GUI session, so never let this fail the run.
  osascript -e "display notification \"$1\" with title \"kfxgen format drift\"" 2>/dev/null || true
}

# --- every committed baseline ------------------------------------------------
#
# Each baseline is checked, not just one. Picking a single "newest" file was
# wrong as soon as a second corpus sample landed: `sort | tail -1` orders by
# name, so with baseline-fonts-* and baseline-gatsby-* alongside each other the
# choice is alphabetical rather than chronological, and it fails toward a false
# all-clear — the watch reports "in sync" off whichever name sorts last while
# another baseline sits behind the installed toolchain (#85).
#
# Checking all of them also produces the more useful answer: which baselines
# need regenerating, not merely that something moved.
BASELINES=()
while IFS= read -r f; do BASELINES+=("$f"); done < <(ls -1 "$DIR"/baseline-*.json 2>/dev/null)
if [ ${#BASELINES[@]} -eq 0 ]; then
  say "no baseline-*.json in $DIR — nothing to compare against"
  log "ERROR no baseline found"
  exit 2
fi

read_json() {  # read_json <file> <key>
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],""))' "$1" "$2"
}

# --- what is installed now ---------------------------------------------------
if [ ! -d "$PREVIEWER_APP" ]; then
  say "Kindle Previewer not found at $PREVIEWER_APP"
  log "ERROR previewer missing"
  exit 2
fi
NOW_PREVIEWER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
                 "$PREVIEWER_APP/Contents/Info.plist" 2>/dev/null)"

if [ ! -f "$PLUGIN_ZIP" ]; then
  say "KFX Input plugin not found at $PLUGIN_ZIP"
  log "ERROR kfx input missing"
  exit 2
fi
NOW_KFXLIB="$(unzip -p "$PLUGIN_ZIP" kfxlib/version.py 2>/dev/null \
              | sed -nE 's/^__version__ *= *"([^"]+)".*/\1/p')"

if [ -z "$NOW_PREVIEWER" ] || [ -z "$NOW_KFXLIB" ]; then
  say "could not read installed versions (previewer='$NOW_PREVIEWER' kfxlib='$NOW_KFXLIB')"
  log "ERROR unreadable versions"
  exit 2
fi

# Previewer reports "3.98" in Info.plist while the baseline records "3.98.0".
# A string compare would call that drift on the very first run and train the
# operator to ignore this job, so compare zero-padded numeric components.
same_version() {
  python3 -c '
import sys, itertools
a, b = sys.argv[1], sys.argv[2]
def parts(v):
    return [int(x) if x.isdigit() else x for x in v.replace("-", ".").split(".")]
pa, pb = parts(a), parts(b)
for x, y in itertools.zip_longest(pa, pb, fillvalue=0):
    if x != y:
        sys.exit(1)
sys.exit(0)' "$1" "$2"
}

STALE=()
CHECKED=()
for bl in "${BASELINES[@]}"; do
  name="$(basename "$bl")"
  bp="$(read_json "$bl" previewer_version)"
  bk="$(read_json "$bl" kfxlib_version)"
  CHECKED+=("$name (Previewer $bp, kfxlib $bk)")
  why=""
  same_version "$bp" "$NOW_PREVIEWER" || why="$why Previewer $bp -> $NOW_PREVIEWER;"
  same_version "$bk" "$NOW_KFXLIB"   || why="$why kfxlib $bk -> $NOW_KFXLIB;"
  [ -n "$why" ] && STALE+=("$name:$why")
done

if [ ${#STALE[@]} -eq 0 ]; then
  if [ "$VERBOSE" = 1 ]; then
    say "no change — Previewer $NOW_PREVIEWER, kfxlib $NOW_KFXLIB"
    for c in "${CHECKED[@]}"; do say "  matches $c"; done
  fi
  log "ok previewer=$NOW_PREVIEWER kfxlib=$NOW_KFXLIB (${#BASELINES[@]} baseline(s) current)"
  exit 0
fi

say "${#STALE[@]} of ${#BASELINES[@]} baseline(s) are behind the installed toolchain:"
say ""
for s in "${STALE[@]}"; do say "  ${s%%:*} —${s#*:}"; done
say ""
say "The drift diff can now learn something for those. Re-convert each sample"
say "with Previewer and diff it against its baseline (see README), e.g.:"
say ""
say "  cd $DIR"
say "  /Applications/calibre.app/Contents/MacOS/calibre-debug kfx_inventory.py -- \\"
say "      \"$PLUGIN_ZIP\" out/KPF/<sample>.kpf \\"
say "      --previewer $NOW_PREVIEWER --diff <baseline-above>.json"
say ""
say "Nothing has been installed or changed. This job only reads versions."
log "ACTION ${#STALE[@]}/${#BASELINES[@]} stale: ${STALE[*]}"
notify "${#STALE[@]} drift baseline(s) behind the installed toolchain"
exit 1
