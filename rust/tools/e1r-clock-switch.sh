#!/usr/bin/env bash
# Root companion of e1r-night.sh: unlocks the clock once the calibrations
# are done and locks it again once the rollouts are done, so the night
# needs no password. It watches the flags the chain writes in
# runs/e1r-night/flags and exits when the chain ends; if the chain ends
# while the clock is unlocked, the clock is locked again first.
#
# Start it as a transient root service, before or after the chain:
#   sudo systemd-run --unit=teaspoon-e1r-clock --collect \
#       /mnt/mert/Code/teaspoon/rust/tools/e1r-clock-switch.sh
# Check:  systemctl status teaspoon-e1r-clock
# Stop:   sudo systemctl stop teaspoon-e1r-clock
# It runs scripts from the repository as root: a personal machine only.
set -u

TOOLS="$(cd "$(dirname "$0")" && pwd)"
FLAGS="$(cd "$TOOLS/../.." && pwd)/runs/e1r-night/flags"

if [[ $EUID -ne 0 ]]; then
    echo "error: run as root (see the header)" >&2
    exit 1
fi

say() { echo "$(date '+%F %T') e1r-clock-switch: $*"; }

# wait_flag NAME: waits for FLAGS/NAME; returns 1 if the chain ended first
# or after 14 hours.
wait_flag() {
    local waited=0
    until [[ -e $FLAGS/$1 ]]; do
        if [[ -e $FLAGS/chain-ended ]] || ((waited >= 14 * 3600)); then
            return 1
        fi
        sleep 15
        waited=$((waited + 15))
    done
}

say "watching $FLAGS"
# A chain started after this job clears the flags, so a stale
# `chain-ended` from an earlier night must not end this job at once.
rm -f "$FLAGS/chain-ended"
if ! wait_flag calibrations-done; then
    say "the chain ended before its calibrations were done; nothing changed"
    exit 0
fi
say "calibrations done: unlocking"
"$TOOLS/unlock-clock.sh"
if wait_flag rollouts-done; then
    say "rollouts done: locking"
else
    say "the chain ended during the rollouts: locking again"
fi
"$TOOLS/lock-clock.sh"
say "done"
