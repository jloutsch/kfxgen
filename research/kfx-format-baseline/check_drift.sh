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

# The plugin's own version, distinct from the kfxlib date above. The index
# check below compares this one, because that is what calibre publishes.
NOW_KFX_INPUT="$(unzip -p "$PLUGIN_ZIP" __init__.py 2>/dev/null \
                 | sed -nE 's/^[[:space:]]*version *= *\(([0-9]+), *([0-9]+), *([0-9]+)\).*/\1.\2.\3/p' \
                 | head -1)"

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

# --- is a newer Previewer available upstream? --------------------------------
#
# The baseline comparison above fires when YOU update, not when Amazon ships,
# so on its own the watch can sit quiet for months while drift accumulates
# upstream (#88). Previewer announces its own availability at the end of a run,
# which is an official signal rather than a scraped download page.
#
# `-update` is NOT used: its own help says "Download and install the latest
# software update", and this job never installs anything.
#
# `-log` validates without producing a KPF, so a 2 KB book costs ~8s. The
# absence of the notice is deliberately NOT treated as "up to date" — offline,
# a reworded notice, or a broken invocation would all look identical to good
# news. A run is only trusted when a known-stable marker proves Previewer
# actually ran and reached the end; otherwise this reports "could not check".
#
#   0 = ran, no update offered   1 = update available   2 = could not check

#: Hard ceiling on the Previewer probe. An 8-second job ran for minutes when it
#: was launched while an installer was replacing the app bundle underneath it,
#: and a monthly job that hangs is indistinguishable from one that is slow —
#: the same failure bounded in the corpus workflow (#78). Any unbounded
#: external command in a scheduled job is a hang waiting to happen.
PROBE_TIMEOUT="${KFXGEN_DRIFT_PROBE_TIMEOUT:-90}"

# Run a command with a wall-clock ceiling. Returns 124 on timeout, matching
# coreutils. Falls back to a bash watchdog because a stock macOS ships neither
# `timeout` nor `gtimeout` — this job is meant to run on an unprepared machine.
run_bounded() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
  else
    "$@" &
    local pid=$! waited=0
    while kill -0 "$pid" 2>/dev/null; do
      if [ "$waited" -ge "$secs" ]; then
        kill -TERM "$pid" 2>/dev/null
        sleep 2
        kill -KILL "$pid" 2>/dev/null
        return 124
      fi
      sleep 1
      waited=$((waited + 1))
    done
    wait "$pid" 2>/dev/null
  fi
}

check_upstream() {
  local src="$DIR/../../test_books/minimal_test_book"
  [ -d "$src" ] || return 2
  command -v zip >/dev/null 2>&1 || return 2

  local tmp epub rc
  tmp="$(mktemp -d)" || return 2
  epub="$tmp/probe.epub"
  mkdir -p "$tmp/out"
  ( cd "$src" && zip -X -q0 "$epub" mimetype && zip -X -q -r "$epub" META-INF OEBPS -x '.*' ) \
    >/dev/null 2>&1 || { rm -rf "$tmp"; return 2; }

  # Output to a file rather than a capture, so the bounded run works the same
  # whether it goes through `timeout` or the fallback watchdog.
  run_bounded "$PROBE_TIMEOUT" \
    "$PREVIEWER_APP/Contents/MacOS/Kindle Previewer 3" "$epub" -log -output "$tmp/out" \
    >"$tmp/probe.log" 2>&1
  rc=$?

  local out=""
  [ -f "$tmp/probe.log" ] && out="$(cat "$tmp/probe.log")"
  rm -rf "$tmp"

  # A timeout is "could not check", never "no update available".
  [ "$rc" = 124 ] && return 2

  # Proof the run happened and got far enough to have printed the notice.
  grep -q "Post-processing in progress" <<<"$out" || return 2

  grep -qiE "new version .*available|update to the latest version" <<<"$out" && return 1
  return 0
}

# --- is a newer KFX Input plugin available? ----------------------------------
#
# The vendored kfxlib pin and the drift baselines are maintained in different
# places, so each can look fine on its own while the plugin that produced them
# has moved (#91). Amazon grew the shared YJ_symbols table past what the pinned
# kfxlib can name; a newer plugin is what would let us name the new symbols.
#
# Unlike the Previewer probe above, this needs no GUI: it reads the same JSON
# index calibre's own plugin updater uses. That distinction is why this one
# runs on the schedule and that one does not (#92) — a background agent cannot
# get Kindle Previewer to finish, but it can fetch a file. Still bounded, and
# still reports `unknown` rather than pretending an unreachable index means
# up to date.
#
#   0 = index reachable, installed is current   1 = newer available   2 = could not check
PLUGIN_INDEX_URL="${KFXGEN_PLUGIN_INDEX_URL:-https://code.calibre-ebook.com/plugins/plugins.json.bz2}"

# Passed with `python3 -c`, never on stdin. `run_bounded`'s fallback watchdog
# backgrounds the command, and a background job in a non-interactive shell has
# its stdin redirected from /dev/null — so a heredoc-fed script arrives empty,
# python exits 0 having done nothing, and the check reports "current" when it
# in fact never ran. That reads as an all-clear and is the failure shape that
# took the Previewer probe out on the schedule (#92). It only appears where
# `timeout` is absent, which is stock macOS: the launchd case exactly.
read -r -d '' KFX_INPUT_INDEX_PY <<'PY'
import bz2, json, os, sys

url = os.environ.get("KFXGEN_INDEX_URL", "")
installed = os.environ.get("KFXGEN_INDEX_INSTALLED", "")
try:
    if url.startswith("file://"):
        with open(url[7:], "rb") as f:
            raw = f.read()
    else:
        from calibre.utils.https import get_https_resource_securely
        raw = get_https_resource_securely(url)
    index = json.loads(bz2.decompress(raw).decode("utf-8"))
    latest = index["KFX Input"]["version"]
except Exception:
    sys.exit(2)  # unreachable, malformed, or renamed -- never "up to date"

def parts(v):
    if isinstance(v, (list, tuple)):
        return [int(x) for x in v]
    return [int(x) if str(x).isdigit() else 0 for x in str(v).split(".")]

a, b = parts(latest), parts(installed)
a += [0] * (len(b) - len(a))
b += [0] * (len(a) - len(b))
print(".".join(str(x) for x in a))
sys.exit(1 if a > b else 0)
PY

# The fetch runs inside calibre, and that is not incidental: `code.calibre-
# ebook.com` serves an incomplete certificate chain, so anything relying on the
# OS trust store fails it. Measured on this machine — GitHub and example.com
# return 200 while that host gives curl `(60) unable to get local issuer
# certificate`, and Xcode's /usr/bin/python3 gives the urllib equivalent. It is
# the host, not the network and not launchd.
#
# calibre ships its own CA bundle, which is why its plugin updater can reach an
# index that curl cannot. Using `calibre.utils.https.get_https_resource_securely`
# means this check reads the index exactly the way calibre itself does, and
# calibre is guaranteed present on any machine where the rest of this script has
# something to check.
#
# A file:// URL is read directly, bypassing the fetch, which is how all four
# states are exercised without waiting for a release. (#91)
CALIBRE_DEBUG="${KFXGEN_CALIBRE_DEBUG:-/Applications/calibre.app/Contents/MacOS/calibre-debug}"

check_kfx_input_upstream() {
  [ -n "$NOW_KFX_INPUT" ] || return 2
  [ -x "$CALIBRE_DEBUG" ] || return 2
  KFXGEN_INDEX_URL="$PLUGIN_INDEX_URL" KFXGEN_INDEX_INSTALLED="$NOW_KFX_INPUT" \
    run_bounded "$PROBE_TIMEOUT" "$CALIBRE_DEBUG" -c "$KFX_INPUT_INDEX_PY"
}

KFX_INPUT_MSG=""
if [ "${KFXGEN_DRIFT_SKIP_PLUGIN_INDEX:-0}" = "1" ]; then
  KFX_INPUT_STATE="skipped"
else
  KFX_INPUT_LATEST="$(check_kfx_input_upstream)"
  case $? in
    0) KFX_INPUT_STATE="current" ;;
    1) KFX_INPUT_STATE="available"
       KFX_INPUT_MSG="KFX Input $KFX_INPUT_LATEST is available (installed: $NOW_KFX_INPUT, kfxlib $NOW_KFXLIB)." ;;
    *) KFX_INPUT_STATE="unknown"
       KFX_INPUT_MSG="Could not reach the calibre plugin index — treat as unknown, not as up to date." ;;
  esac
fi

UPSTREAM_MSG=""
if [ "${KFXGEN_DRIFT_SKIP_UPSTREAM:-0}" = "1" ]; then
  UPSTREAM_STATE="skipped"
else
  check_upstream
  case $? in
    0) UPSTREAM_STATE="current" ;;
    1) UPSTREAM_STATE="available"
       UPSTREAM_MSG="A newer Kindle Previewer is available upstream (installed: $NOW_PREVIEWER)." ;;
    *) UPSTREAM_STATE="unknown"
       UPSTREAM_MSG="Could not check for a newer Previewer — treat as unknown, not as up to date." ;;
  esac
fi

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

if [ ${#STALE[@]} -eq 0 ] && [ "$UPSTREAM_STATE" != "available" ] \
   && [ "$KFX_INPUT_STATE" != "available" ]; then
  if [ "$VERBOSE" = 1 ]; then
    say "no change — Previewer $NOW_PREVIEWER, kfxlib $NOW_KFXLIB, KFX Input $NOW_KFX_INPUT"
    for c in "${CHECKED[@]}"; do say "  matches $c"; done
    say "upstream Previewer: $UPSTREAM_STATE"
    [ -n "$UPSTREAM_MSG" ] && say "  $UPSTREAM_MSG"
    say "upstream KFX Input: $KFX_INPUT_STATE"
    [ -n "$KFX_INPUT_MSG" ] && say "  $KFX_INPUT_MSG"
  fi
  log "ok previewer=$NOW_PREVIEWER kfxlib=$NOW_KFXLIB kfxinput=$NOW_KFX_INPUT (${#BASELINES[@]} baseline(s) current, upstream=$UPSTREAM_STATE, index=$KFX_INPUT_STATE)"
  exit 0
fi

# A newer plugin while everything local is in sync: the vendored pin and the
# audit table can now be brought up to the current upstream surface (#91).
if [ ${#STALE[@]} -eq 0 ] && [ "$UPSTREAM_STATE" != "available" ]; then
  say "$KFX_INPUT_MSG"
  say ""
  say "Refresh the vendored copy so the differential decode tests run against"
  say "what upstream ships now (see CONTRIBUTING.md → Vendored kfxlib):"
  say ""
  say "  tests/fixtures/vendor/kfx_input_plugin.zip        # not committed"
  say "  tests/fixtures/vendor/kfx_input_plugin.version.txt"
  say ""
  say "Nothing has been installed or changed. This job only reads versions."
  log "ACTION kfx input $KFX_INPUT_LATEST available (installed=$NOW_KFX_INPUT)"
  notify "A newer KFX Input plugin is available"
  exit 1
fi

# A newer Previewer upstream while every baseline is current: nothing has
# drifted yet, but the update is what would make a diff informative.
if [ ${#STALE[@]} -eq 0 ]; then
  say "$UPSTREAM_MSG"
  say ""
  say "Every baseline matches the installed toolchain, so there is nothing to"
  say "diff yet. Updating Previewer is what would make one worth running:"
  say ""
  say "  kindlepreviewer -update     # installs; deliberately not done by this job"
  say ""
  say "Then re-convert each sample and diff it against its baseline (see README)."
  say "Set KFXGEN_DRIFT_SKIP_UPSTREAM=1 to silence this check."
  log "ACTION upstream previewer update available (installed=$NOW_PREVIEWER, baselines current)"
  notify "A newer Kindle Previewer is available"
  exit 1
fi

say "${#STALE[@]} of ${#BASELINES[@]} baseline(s) are behind the installed toolchain:"
say ""
for s in "${STALE[@]}"; do say "  ${s%%:*} —${s#*:}"; done
[ -n "$UPSTREAM_MSG" ] && { say ""; say "$UPSTREAM_MSG"; }
[ -n "$KFX_INPUT_MSG" ] && { say ""; say "$KFX_INPUT_MSG"; }
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
