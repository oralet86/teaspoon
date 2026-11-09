#!/usr/bin/env bash
# E1R night (Claim 1.4), written 2026-11-05 for an 8-hour idle window;
# plan and estimates in ideas/e1r-overnight-plan.md.
#
#   R1  0.01 ms per city, horizon 64 slices   (window 0.64 ms per city)
#   R2  0.001 ms per city, horizon 640 slices (the same window as R1)
#   R3  0.05 ms per city, horizon 64 slices   (window 3.2 ms per city)
#
# R1 and R2 on every improving state of last night's measurement A (all 60
# instances, every state of each (instance, class) pool; up to 19 per
# class, 1,257 states) with every current operator as a first candidate.
# R3 then takes the time left: the largest states-per-class whose
# estimate, from R1's measured rollout speed, fits before the deadline.
# The plan's nested draws make R3's states the first k of each class of
# R1's, so R1 and R2 at that k are exact prefixes of these runs, and a
# later night can extend R3 by copying its finished rollouts.
#
# Estimates (locked 8 threads; boost and SMT on should be ~1.3x faster):
# calibration R1 ~20-25 min on one core, R1 rollouts ~185 min, R2 ~190 min.
# The deadline (WINDOW_HOURS, default 8) is a hard limit: every rollout
# stage runs under `timeout`, and RESERVE minutes are kept for relocking,
# the validations and the analyses.
#
# Order: locked build, clock check, plans, calibration of R1 (R2 takes
# R1's cost models); the flag `calibrations-done` lets the root job
# e1r-clock-switch.sh unlock the clock for the rollouts (virtual clock, so
# their results do not depend on machine speed); the flag `rollouts-done`
# lets it lock the clock again for the validations; then the analyses.
# The rollout stages stop at the deadline minus the time kept for the
# validations; a stopped run resumes with RUN_DIR=<its directory>.
#
# Before starting (see ideas/e1r-overnight-plan.md, "Commands"):
#   rust/tools/lock-clock.sh
#   sudo systemd-run --unit=teaspoon-e1r-clock --collect \
#       "$PWD/rust/tools/e1r-clock-switch.sh"
# Then:  bash rust/tools/e1r-night.sh
# Progress:  tail -f runs/e1r-night/<timestamp>/chain.log
set -u

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1

if [[ -z ${E1R_NIGHT_INHIBITED:-} ]]; then
    export E1R_NIGHT_INHIBITED=1
    exec systemd-inhibit --what=sleep:idle:handle-lid-switch \
        --who=teaspoon --why="E1R night" -- bash "$0" "$@"
fi

# One chain at a time: a second start (as on 2026-11-05, five seconds after
# the first) would calibrate on the same core and clear the flags under
# the running chain. The lock is released when this process exits.
mkdir -p "$REPO/runs/e1r-night"
exec 9>"$REPO/runs/e1r-night/.chain.lock"
if ! flock -n 9; then
    echo "error: another e1r-night.sh is running (lock runs/e1r-night/.chain.lock)" >&2
    exit 1
fi

CPUS=/sys/devices/system/cpu
locked() { [[ $(cat $CPUS/cpufreq/boost) == 0 && -e /run/teaspoon-clock.state ]]; }
unlocked() { [[ $(cat $CPUS/cpufreq/boost) == 1 && ! -e /run/teaspoon-clock.state ]]; }

if ! locked; then
    echo "error: the clock is not locked; run rust/tools/lock-clock.sh first" >&2
    exit 1
fi

AOS="$REPO/rust/target/release/aos"
MEASUREMENT=runs/overnight-rs/20261102-232155/a-main
R="${RUN_DIR:-runs/e1r-night/$(date +%Y%m%d-%H%M%S)}"
# A fixed place the root job watches (it cannot know the timestamp).
FLAGS="$REPO/runs/e1r-night/flags"
mkdir -p "$R" "$FLAGS"
rm -f "$FLAGS"/*
trap 'touch "$FLAGS/chain-ended"' EXIT

log() { echo "$(date '+%F %T') $*" | tee -a "$R/chain.log"; }

DEADLINE=$((SECONDS + ${WINDOW_HOURS:-8} * 3600))
log "deadline $(date -d "@$(($(date +%s) + DEADLINE - SECONDS))" '+%F %T')"
# Minutes kept after the rollouts for relocking, validations and analyses.
RESERVE=30

# step NAME COMMAND...: one step, its output to $R/NAME.log; returns its status.
step() {
    local name=$1
    shift
    local started=$SECONDS
    log "$name: start"
    "$@" >>"$R/$name.log" 2>&1
    local status=$?
    log "$name: exit $status after $(((SECONDS - started) / 60)) min"
    return "$status"
}

# bounded NAME COMMAND...: a step that is killed at the deadline.
bounded() {
    local name=$1 left=$((DEADLINE - SECONDS))
    shift
    if ((left <= 0)); then
        log "$name: skipped, the deadline has passed"
        return 1
    fi
    step "$name" timeout "$left" "$@"
}

# wait_for TEST LABEL: waits up to 10 minutes for the clock state TEST.
wait_for() {
    local waited=0
    until $1; do
        if ((waited >= 600)); then
            log "clock not $2 after 10 min"
            return 1
        fi
        sleep 10
        waited=$((waited + 10))
    done
    log "clock $2 (boost $(cat $CPUS/cpufreq/boost), SMT $(cat $CPUS/smt/control))"
}

log "commit $(git rev-parse --short HEAD), $(git status --porcelain | wc -l) uncommitted paths"
step build cargo build --release --manifest-path rust/Cargo.toml || exit 1
step clock "$AOS" clock || exit 1

PLAN=(--stage plan --measurement "$MEASUREMENT" --stall-rule skip --instances-per-cell 4
    --states-per-class "${STATES_PER_CLASS:-1000}" --candidates 21 --repeats 32)
if [[ ! -e $R/R1/plan.json ]]; then
    step R1-plan "$AOS" e1r --out "$R/R1" "${PLAN[@]}" --slice-ns-per-city 10000 \
        --horizon-slices 64 || exit 1
fi
if [[ ! -e $R/R2/plan.json ]]; then
    step R2-plan "$AOS" e1r --out "$R/R2" "${PLAN[@]}" --slice-ns-per-city 1000 \
        --horizon-slices 640 || exit 1
fi

if [[ ! -e $R/R1/cost_models.json ]]; then
    log "idle 120 s before calibrating"
    sleep 120
    step R1-calibrate "$AOS" e1r --out "$R/R1" --stage calibrate || exit 1
fi
if [[ ! -e $R/R2/cost_models.json ]]; then
    step R2-calibrate "$AOS" e1r --out "$R/R2" --stage calibrate \
        --cost-models-from "$R/R1" || exit 1
fi

touch "$FLAGS/calibrations-done"
log "calibrations done; waiting for the root job to unlock the clock"
wait_for unlocked unlocked || log "rollouts run on the locked clock (slower, same results)"

# run NAME: the rollout stage, stopped at the deadline minus RESERVE.
run() {
    local seconds=$((DEADLINE - SECONDS - RESERVE * 60))
    if ((seconds <= 60)); then
        log "$1-run: skipped, no time left"
        return 1
    fi
    log "$1-run: at most $((seconds / 60)) min"
    step "$1-run" timeout "$seconds" "$AOS" e1r --out "$R/$1" --stage run
}
r1_started=$SECONDS
run R1
r1=$?
r1_seconds=$((SECONDS - r1_started))
run R2
r2=$?

# R3 in the time left, sized from R1's measured speed: the cost of a
# state is proportional to n x candidates x slice length, and R3's slices
# are 5 times R1's (long slices cost slightly less per ms because more
# rollouts reach the optimum and stop; the 5% margin is on top of that).
r3=1
if ((r1 == 0)); then
    budget=$((DEADLINE - SECONDS - RESERVE * 60 - 120))
    read -r k minutes < <(python3 -I - "$R/R1/plan.json" "$r1_seconds" "$budget" <<'PY'
import json
import sys
from collections import defaultdict

plan, r1_seconds, budget = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
groups = defaultdict(list)
for state in json.load(open(plan))["states"]:
    # Plan order is the nested draw order within each (instance, class).
    groups[(state["instance"], state["class"])].append(state["n"] * len(state["candidates"]))
per_unit = r1_seconds / sum(sum(units) for units in groups.values())
best, best_seconds = 0, 0.0
for k in range(1, max(len(units) for units in groups.values()) + 1):
    seconds = sum(sum(units[:k]) for units in groups.values()) * per_unit * 5.0 * 1.05
    if seconds <= budget:
        best, best_seconds = k, seconds
print(best, round(best_seconds / 60))
PY
    )
    log "R3: R1 rollouts took $((r1_seconds / 60)) min; $((budget / 60)) min left for R3; chosen ${k:-0} states per class (~${minutes:-0} min)"
    if ((${k:-0} > 0)); then
        step R3-plan "$AOS" e1r --out "$R/R3" --stage plan --measurement "$MEASUREMENT" \
            --stall-rule skip --instances-per-cell 4 --states-per-class "$k" --candidates 21 \
            --repeats 32 --slice-ns-per-city 50000 --horizon-slices 64 &&
            step R3-calibrate "$AOS" e1r --out "$R/R3" --stage calibrate \
                --cost-models-from "$R/R1" &&
            run R3
        r3=$?
    else
        log "R3: skipped, not even one state per class fits"
    fi
else
    log "R3: skipped, R1 did not finish"
fi

touch "$FLAGS/rollouts-done"
log "rollouts done; waiting for the root job to lock the clock"
if wait_for locked locked; then
    log "idle 120 s before validating"
    sleep 120
    ((r1 == 0)) && bounded R1-validate "$AOS" e1r --out "$R/R1" --stage validate
    ((r2 == 0)) && bounded R2-validate "$AOS" e1r --out "$R/R2" --stage validate
    ((r3 == 0)) && bounded R3-validate "$AOS" e1r --out "$R/R3" --stage validate
else
    log "validations skipped: the clock is not locked"
fi
for name in R1 R2 R3; do
    [[ -e $R/$name/plan.json ]] || continue
    bounded "$name-analyse" "$AOS" e1r --out "$R/$name" --stage analyse &&
        bounded "$name-analyse-top5" "$AOS" e1r --out "$R/$name" --stage analyse --max-candidates 5
done
log "done ($(((SECONDS) / 60)) min)"
