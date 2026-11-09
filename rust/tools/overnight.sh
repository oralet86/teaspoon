#!/usr/bin/env bash
# The Rust reruns of the Python experiments plus the new robustness
# factors, in priority order, on a locked clock.  Written 2026-11-01 for a
# 10-hour idle window; revised 2026-11-02 for a 9-hour window without the
# calibration experiment.  Estimates are at 4.0 GHz with one timed worker,
# from pilots on 67 E1 states and 17 E1R states (taken with SMT on: the
# untimed stages now have 8 logical CPUs instead of 16 and may run longer).
#
#   1. measurement A on the Python E1 main-run states, and the
#      comparison with the Python cells.csv (spec §11.3); a timing
#      budget of 1.5 s per state, state seeds 0 and 1 (gains of
#      every seed: 69 min on 8 CPUs; timing about 6 s per state) ~3.3 h
#   2. population runs: count-mode replay check (phase P5)       ~2 min
#   3. E1R rollouts at 0.01 ms per city, stall rule skip
#      (decision D1), every instance of each (family, size)
#      cell (4; default 2)                                       ~40 min
#   4. the same with stall rule none (the Python test run's rule) ~40 min
#   5. robustness factors (spec §9) on state seed 0, on the
#      anchors and the budget of step 1, each compared with it:
#      distances computed from coordinates (48 instances; the
#      gains are copied from step 1, they are identical)         ~55 min
#      candidate cache off (all instances)                       ~70 min
#      two-level list (the 18 instances of 500-535 cities, within
#      a factor 2 of n_split = 1000)                             ~25 min
#   6. E1R at 0.1 ms per city (the Python design's time per
#      slice), stall rule skip, the default sample (2 instances
#      per cell, or 1 if only that fits): does the verdict
#      depend on the slice?                                      ~2.2 h
#   7. re-timing check, always last: state seed 0 of 12
#      instances timed again on step 1's anchors (gains copied)
#      and compared with step 1: how far the measurement moved
#      over the night                                            ~15 min
#                                             total ~9.5 h (step 6 dropped first)
#
# The calibration experiment (E0) is not rerun: its 2026-11-02 17:33 run
# (runs/e0-rs/20261102-173327) is the reference.  It did not confirm that
# several timed workers leave times unchanged (cells unresolved), so the
# timing stages use one timed worker unless TIMED_WORKERS=<n> is set.
# E1R calibrates and validates on one core.  E1R slices: 0.01 ms per city
# for the main runs (the same work per slice as Python's 0.1 ms; spec
# §8.5), 0.1 ms for the sensitivity run.
#
# Steps run in this order.  A step whose estimate no longer fits before
# the deadline (DEADLINE_AT, or start + WINDOW_HOURS) is skipped and logged,
# so the last steps are the ones dropped when earlier ones run long; the
# re-timing check's time is reserved from the start.
#
# Lock the clock first (rust/tools/lock-clock.sh); the script refuses to
# start otherwise and keeps the machine awake while it runs.  A failed step
# is logged and the steps that do not depend on it still run.  Progress:
#   tail -f runs/overnight-rs/<timestamp>/chain.log
# Run from anywhere:  bash rust/tools/overnight.sh
set -u

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1

if [[ -z ${OVERNIGHT_INHIBITED:-} ]]; then
    export OVERNIGHT_INHIBITED=1
    exec systemd-inhibit --what=sleep:idle:handle-lid-switch \
        --who=teaspoon --why="timed experiment runs" -- bash "$0" "$@"
fi

if [[ $(cat /sys/devices/system/cpu/cpufreq/boost) != 0 || ! -e /run/teaspoon-clock.state ]]; then
    echo "error: the clock is not locked; run rust/tools/lock-clock.sh first" >&2
    exit 1
fi

AOS="$REPO/rust/target/release/aos"
E1=runs/e1-main/20261018-144422
STATES=$E1/states
PYTHON_CELLS=$E1/marginal/cells.csv
# RUN_DIR resumes an earlier chain (every stage skips the states it has done).
R="${RUN_DIR:-runs/overnight-rs/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$R/compare"

log() { echo "$(date '+%F %T') $*" | tee -a "$R/chain.log"; }

# The deadline: DEADLINE_AT=HH:MM (the next such time), else WINDOW_HOURS
# (whole hours, default 9) from the start.
if [[ -n ${DEADLINE_AT:-} ]]; then
    now=$(date +%s)
    target=$(date -d "$DEADLINE_AT" +%s) || exit 1
    ((target <= now)) && target=$(date -d "tomorrow $DEADLINE_AT" +%s)
    DEADLINE=$((SECONDS + target - now))
else
    DEADLINE=$((SECONDS + ${WINDOW_HOURS:-9} * 3600))
fi
log "deadline $(date -d "@$(($(date +%s) + DEADLINE - SECONDS))" '+%F %T')"
# Minutes kept for the re-timing check (step 7) by every step before it.
RESERVE=20
# fits MINUTES NAME [RESERVE]: true if a step of about MINUTES still ends
# RESERVE minutes (default $RESERVE) before the deadline.
fits() {
    local reserve=${3:-$RESERVE}
    if ((SECONDS + ($1 + reserve) * 60 > DEADLINE)); then
        log "$2: skipped, about $1 min needed and $(((DEADLINE - SECONDS) / 60)) min left"
        return 1
    fi
}

# step NAME COMMAND...: runs one step, its output to $R/NAME.log; returns its status.
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

# measure NAME OPTIONS...: one measurement-A run in three steps: the gains
# (every CPU), an idle pause, the timing (pinned workers) and the summary.
# The pause lets the chip leave the all-core load behind before timing
# (E0 2026-11-02: memory-bound timings stayed up to 28% slower for minutes
# after many-worker load).
measure() {
    local name=$1
    shift
    step "$name-gains" "$AOS" measure --out "$R/$name" --stage gains "$@" &&
        { log "$name: idle 120 s before timing"; sleep 120; } &&
        step "$name-times" "$AOS" measure --out "$R/$name" --stage times "$@" &&
        step "$name-summarise" "$AOS" measure --out "$R/$name" --stage summarise "$@"
}

log "commit $(git rev-parse --short HEAD), $(git status --porcelain | wc -l) uncommitted paths"
log "boost $(cat /sys/devices/system/cpu/cpufreq/boost), cpu0 $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq)-$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq) kHz"
step build cargo build --release --manifest-path rust/Cargo.toml || exit 1
step clock "$AOS" clock || exit 1

log "calibration reference: runs/e0-rs/20261102-173327 (not rerun)"
WORKERS=${TIMED_WORKERS:-1}
log "timed workers: $WORKERS"
STATE_SECONDS=1.5
log "timing budget per state: $STATE_SECONDS s"

# 1. Measurement A on the Python E1 states, and the comparison with Python.
# State seeds of step 1 (MAIN_SEEDS, default 0 1).  Timing takes about 6 s
# per state, not the 1.5 s budget: anchors are sized so that two rounds
# fit the budget by the untimed gains estimate, but every timed region is
# sampled 3-9 times and preceded by a warm-up pass, so each state runs its
# two rounds at about four times that.  All 7676 states (12 seeds) would
# take about 13 h; two seeds about 2.1 h.
read -r -a MAIN_SEED_LIST <<<"${MAIN_SEEDS:-0 1}"
log "step 1 state seeds: ${MAIN_SEED_LIST[*]}"
if fits 150 a-main && measure a-main --states "$STATES" --seeds "${MAIN_SEED_LIST[@]}" \
    --timed-workers "$WORKERS" --state-seconds "$STATE_SECONDS"; then
    main_ok=1
    step compare-python "$AOS" compare "$R/a-main" "$PYTHON_CELLS" --out "$R/compare/python"
else
    main_ok=0
fi

# 2. Population runs: a time-bounded run must replay exactly in count mode.
fits 10 population-check && step population-check "$AOS" population \
    --instances data/ALL_tsp/kroA100.tsp data/generated/tsp/netgen-500-0.tsp \
    --arms uniform fixed:two_opt_nl fixed:lin_kernighan --seeds 0 1 \
    --slice-seconds 0.001 --budget-seconds 10 --timed-workers 1 --check-replay \
    --out "$R/population-check"

# e1r NAME RULE SLICE INSTANCES_PER_CELL: the five E1R stages on step 1's leaders.
e1r() {
    local name=$1 rule=$2 slice=$3 per_cell=$4 E="$R/$1"
    step "$name-plan" "$AOS" e1r --out "$E" --stage plan --measurement "$R/a-main" \
        --stall-rule "$rule" --slice-ns-per-city "$slice" --instances-per-cell "$per_cell" &&
        { log "$name: idle 120 s before calibrating"; sleep 120; } &&
        step "$name-calibrate" "$AOS" e1r --out "$E" --stage calibrate &&
        step "$name-run" "$AOS" e1r --out "$E" --stage run &&
        { log "$name: idle 120 s before validating"; sleep 120; } &&
        step "$name-validate" "$AOS" e1r --out "$E" --stage validate &&
        step "$name-analyse" "$AOS" e1r --out "$E" --stage analyse
}

# 3-4. E1R with both stall rules (decision D1), 0.01 ms per city, every
#      instance of each cell.
((main_ok)) && fits 60 e1r-skip && e1r e1r-skip skip 10000 4
((main_ok)) && fits 60 e1r-none && e1r e1r-none none 10000 4

# 5. Robustness factors, one choice changed at a time, on state seed 0
#    and on the main run's anchors (so a changed leader is not a changed
#    sample).
coordinate=$(for f in $(cat $E1/instances.txt); do
    grep -q "EDGE_WEIGHT_TYPE *: *EXPLICIT" "$f" || basename "$f" .tsp
done)
around_500=$(for f in $(cat $E1/instances.txt); do
    n=$(grep -m1 DIMENSION "$f" | tr -dc 0-9)
    ((n >= 500)) && basename "$f" .tsp
done)
FACTOR=(--states "$STATES" --timed-workers "$WORKERS" --state-seconds "$STATE_SECONDS"
    --seeds 0 --anchors-from "$R/a-main")
# factor NAME OPTIONS...: one factor run and its comparison with step 1.
factor() {
    local name=$1
    shift
    measure "a-$name" "${FACTOR[@]}" "$@" &&
        step "compare-$name" "$AOS" compare "$R/a-main" "$R/a-$name" --out "$R/compare/$name"
}
if ((main_ok)); then
    # Computed distances give gains rows identical to the matrix's (checked
    # on kroA100, wall time aside), so they are copied, not recomputed.
    if fits 60 a-computed; then
        mkdir -p "$R/a-computed/gains.parts" "$R/a-computed/gains.meta"
        for instance in $coordinate; do
            for seed in 0; do
                cp "$R/a-main/gains.parts/${instance}_s${seed}_"*.parquet "$R/a-computed/gains.parts/" 2>/dev/null
                cp "$R/a-main/gains.meta/${instance}_s${seed}_"*.json "$R/a-computed/gains.meta/" 2>/dev/null
            done
        done
        log "a-computed: copied $(find "$R/a-computed/gains.parts" -name '*.parquet' | wc -l) gains parts from a-main"
        # shellcheck disable=SC2086  # the instance list is meant to split into words
        factor computed --storage computed --instances $coordinate
    fi
    fits 75 a-nocache && factor nocache --no-cache
    # shellcheck disable=SC2086
    fits 30 a-twolevel && factor twolevel --layout two-level --instances $around_500
fi

# 6. E1R at the Python design's 0.1 ms per city: does the verdict depend
#    on the slice?  Two instances per cell if they fit, else one (~75 min).
if ((main_ok)); then
    if fits 150 e1r-skip-0.1ms; then
        e1r e1r-skip-0.1ms skip 100000 2
    elif fits 80 e1r-skip-0.1ms-one-per-cell; then
        e1r e1r-skip-0.1ms skip 100000 1
    fi
fi

# 7. Re-timing check: state seed 0 of 12 instances (every generator and
#    size, and three TSPLIB instances) timed again on step 1's anchors, its
#    gains copied from step 1 (the same states, seeds and anchors give the
#    same gains), and compared with step 1.
RECHECK=(netgen-100-0 portgen-100-0 portmgen-100-0 tspgen-100-0 netgen-200-0
    portgen-200-0 netgen-500-0 portgen-500-0 tspgen-500-0 kroA100 d198 ali535)
if ((main_ok)) && fits 20 a-recheck 0; then
    mkdir -p "$R/a-recheck/gains.parts" "$R/a-recheck/gains.meta"
    cp "$R/a-main/gains.parts/"*_s0_*.parquet "$R/a-recheck/gains.parts/" 2>/dev/null
    cp "$R/a-main/gains.meta/"*_s0_*.json "$R/a-recheck/gains.meta/" 2>/dev/null
    log "a-recheck: copied $(find "$R/a-recheck/gains.parts" -name '*.parquet' | wc -l) gains parts from a-main"
    measure a-recheck --states "$STATES" --timed-workers "$WORKERS" \
        --state-seconds "$STATE_SECONDS" --seeds 0 --instances "${RECHECK[@]}" \
        --anchors-from "$R/a-main" &&
        step compare-recheck "$AOS" compare "$R/a-main" "$R/a-recheck" --out "$R/compare/recheck"
fi

log "done"
