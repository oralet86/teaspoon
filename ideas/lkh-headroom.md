# LKH-3 headroom kill test

Written 2026-11-05. Code: `bench/lkh_headroom.py`. Route to Claim 4
([lkh3.md](lkh3.md)).

## Question

Can **any** change of LKH-3's search setting during a run beat the best
*fixed* setting at equal CPU time? An adaptive selector can only capture a
part of what an oracle that sees the results could. If that oracle gains
nothing, nothing adaptive can gain anything, and Claim 4 has to go
elsewhere (constrained variants, or settings this test does not cover).

Why now: on our own operators (E1R, 2026-11-05) the advantage of a better
choice shrinks as the search gets deeper. At LK optima it is near zero:
slice-rate selector against greedy, mean 0.02 points at 1 slice in R1. LKH
spends almost all its time at LK optima, so this is the cheapest way to
learn whether the main route to the goal is open.

## Design

- **Arms:** the four TSP arms of the LKH pilot (`bench/lkh_pilot.py`):
  `MOVE_TYPE` 5 (default) or 3, crossed with `KICK_TYPE` 0 (default, LKH's
  walk-based trial) or 4 (a random 4-swap kick). The default arm is
  `m5_walk`. No arm changes preprocessing, so one candidate set and one pi
  vector per instance (`CANDIDATE_FILE`, `PI_FILE`) serve every run.
- **Clock:** `TIME_LIMIT` counts LKH's process CPU time and excludes
  preprocessing. Checked: two runs pinned to one core each got 3.2 CPU
  seconds over 7.2 s of wall time. So the comparison holds under parallel
  load, and the clock is locked as for every timed stage.
- **Budget:** T = 2 CPU seconds per 1,000 cities, so 20 s at 10,000. A
  10,000-city uniform instance was still improving at 20 s (117 of 138
  trials, 0.6% above the Held-Karp bound).
- **Runs per (instance, seed):**
  - every arm for T without a break (`full`);
  - every arm for T/2 (`first`);
  - from each first half's tour, every arm for T/2 (`second`), restarted
    with `INITIAL_TOUR_FILE` (preprocessing 0.06 s at 10,000).

  A schedule a → b is a first half of a then a second half of b. a → a is
  the fixed arm with the same restart. That gives 16 schedules, 4 of them
  fixed, and the 4 `full` runs measure what the restart costs.
- **Gap:** percentage points above the best tour found by any run of the
  instance. The headroom is a difference of gaps, so the reference cancels
  out.
- **Unit:** V, what the second half of the budget buys the default arm:
  its mean gap at T/2 minus at T. Headroom in units of V reads as "an
  oracle switch is worth this fraction of doubling the time". This makes
  sizes and families comparable, and an instance the default solves by T/2
  shows up as saturated (V ≈ 0) instead of as zero headroom.

## Quantities per instance

| Name | Definition | Reads as |
|---|---|---|
| upper headroom | best a → a minus best schedule, chosen and scored on all seeds | Upper bound: picking the best of 16 on the same data overstates it |
| cross-fitted headroom | chosen on half the seeds, scored on the other half, mean of both directions | Unbiased, but underestimates with only 2 seeds per half |
| against full | best fixed arm *without* a break minus best schedule (cross-fitted) | What a switcher that pays the restart would gain |
| restart cost | default a → a minus default `full` | Cost of the restart itself |
| tuning headroom | default minus best fixed arm, both `full`, cross-fitted | What offline tuning gives; not adaptivity |

The comparison that decides is with a → a, which charges both sides the
same restart. A switch made inside LKH's source between trials would not
pay the restart, so this choice is generous to adaptivity, as a kill test
should be.

## Verdict rule (fixed before the run)

Per instance:
- **saturated:** V ≤ 0.001 points.
- **none:** upper headroom < 0.25 V. Even the optimistic bound is worth
  less than a quarter of doubling the time.
- **room:** cross-fitted headroom ≥ 0.5 V and positive in both directions.
- **unclear:** anything else.

Overall, counting only instances with n ≥ 5,000:
- **GO:** at least two instances have room.
- **KILL:** every instance is none or saturated. Phase-level switching of
  move and kick type has no room on the TSP.
- **INCONCLUSIVE:** otherwise. Rerun the unclear instances with more seeds.

## What a KILL would and would not mean

- **It would mean:** with these four settings, one switch at T/2 never
  pays on the TSP up to 20,000 cities.
- **It would not cover:**
  - switching at every trial, which needs a source change and is a finer
    policy than two phases;
  - other knobs (candidate set, patching, population and recombination);
  - the constrained variants where LKH-3 trails (CVRP, CVRPTW).

  These limits must be stated with the verdict.

## Instances

Sizes 1,000, 2,000, 5,000, 10,000 and 20,000. Generators already in the
repository scale to 20,000 (timed 2026-11-05 at 20,000):
- `portcgen` 0.2 s;
- netgen 14 s;
- tspgen 39 s;
- `portgen` faster than `portcgen`.

There are 27 instances, built by `python -m bench.lkh_headroom build` into
`data/generated/lkh-headroom/` (seed 1). The families come from three
literature searches (2026-11-05, summarised below).

| Family | Sizes | Source | Why |
|---|---|---|---|
| uniform | 1k, 5k, 10k | DIMACS `portgen` | Baseline (E instances) |
| dimacs-clustered | 1k, 5k, 10k, 20k | DIMACS `portcgen`, n/100 Gaussian clusters (a Thomas process) | LKH's best-documented weakness. Helsgaun 2009; Taillard & Helsgaun 2019: C10k.0 3.31% with α candidates vs 0.72% with POPMUSIC |
| netgen-clustered | 1k, 5k, 10k | netgen, 5 clusters, on the 10⁶ square | Few large clusters. LKH+restart's hardest set in Kerschke et al. 2018 |
| tspgen | 1k, 5k, 10k | tspgen, sophisticated preset | Mutation operators of the evolved-instance literature (Bossek et al. 2019) |
| shaken-grid | 1k, 5k, 10k | `generators/points.py`, lattice + N(0, (0.25·spacing)²) | Near-ties, deep neighbour ranks (Fischer, Stützle, Hoos & Merz 2005). Not covered by any other family |
| lgcp | 1k, 5k, 10k | `generators/points.py`, log-Gaussian Cox, variance 4, correlation 0.05 | Density varying at several scales, city-like. No LKH literature found |
| tsplib | 1,002–18,512 | pr1002, d2103, fl3795, rl5915, rl11849, d18512 | Real. fl3795 and d2103 are documented cases where LKH misses the optimum or α misses optimal edges |
| national | 10,639 | fi10639 (Waterloo) | Real settlement density |
| vlsi | 10,150 | xmc10150 (Waterloo, Rohe) | Real, lattice-aligned; hard for EAX and Concorde |

Considered and left out for this test:
- **morphed instances:** optimal matching is about n³ at 20k;
- **evolved EAX-easy/LKH-hard instances:** they cannot be evolved beyond about 1k;
- **fractals with cities removed:** need an L-system generator;
- **Tnm:** hard for Concorde, easy for LKH with POPMUSIC;
- **Italian-address and census-density sets:** large data downloads;
- **LGCP at 20k:** budget.

The first three are the next candidates if the test is inconclusive.

## Budget

Preprocessing, measured on the locked clock, one process:

| Size | Ascent and candidates |
|---|---|
| 5k | 22 s |
| 10k | 84 s |
| 20k | about 470 s |

Preprocessing runs once per instance. A search takes 14 T per seed (4 T
full, 2 T first halves, 8 T second halves), and there are 4 seeds. That
comes to 368 CPU minutes in all, about 46 minutes on 8 parallel jobs with
the clock locked. The run is capped at 58 minutes by `timeout`, and it
resumes from `results.jsonl` if stopped.

## Results (run 2026-11-05, `runs/lkh-headroom/20261105-140622`)

**What ran.** 35 instances × 4 seeds × 24 runs, with the clock locked and
8 parallel jobs. The main run took 14:08–15:19, the netgen rerun
15:20–15:27. Table: `analysis/instances.csv`.

**Incident.** The first netgen instances were built with scale 10⁶. In
`netgen.R` the scale multiplies a [0, 100] box, so coordinates reached
10⁸. LKH multiplies costs by `PRECISION = 100`, which overflowed its
integers, and every netgen run failed with
`Assertion 'Gain % Precision == 0'`. Fix:
- regenerated with the wrapper's own scale (10⁴, i.e. the [0, 10⁶]
  square);
- the bad records moved to `results-with-bad-netgen.jsonl`;
- the netgen family rerun.

Every other family was checked to lie within [0, 1.04 × 10⁶].

**Verdict by the pre-registered rule: GO.** Two instances with n ≥ 5,000
have room: `lgcp-5000` (cross-fitted 1.11 V) and `lgcp-20000` (0.91 V).
The other 18 large instances are *none*, except `tspgen-10000` and
`xmc10150` (*unclear*).

**Why the GO is weak.** The rule had no significance requirement, which
in hindsight is a flaw. It stays as written; these points are added
instead:

- **Neither "room" instance is significant on its own.** Upper headroom
  over its standard error is 1.3 for `lgcp-5000` and 1.8 for
  `lgcp-20000`. Per-seed differences on `lgcp-5000` are −0.017, +0.007,
  +0.047 and +0.105; the mean rests on one seed. Two passes out of 20
  instances under a liberal rule are within what noise could produce.
- **The absolute size is tiny:** 0.026–0.036 points of gap, i.e. under
  0.04% of tour length. LKH is within about 0.1% of the best tour found
  at these budgets everywhere except the clustered instances.
- **Cross-fitted headroom above the upper bound** (`lgcp-20000`,
  `xmc10150`) means the fixed arm chosen on one half of the seeds did
  badly on the other half. Part of the "room" is noise in the fixed
  baseline, not a better schedule.
- **Post hoc check of the recurring best schedule.** On four instances
  the best schedule was move type 3 then default (`m3_walk → m5_walk` or
  `m3_kick4 → m5_walk`). Taken as a single fixed hypothesis against the
  restarted default on all 20 large non-saturated instances:
  - better on 6 instances, median +0.13 V (worse);
  - better on 39 of 80 seeds.

  It wins where move type 3 is the better *fixed* choice (clustered and
  LGCP) and loses slightly on uniform, real, grid and tspgen instances.
  That is per-instance selection, not a benefit of switching.

**The large effect is static, on clustered instances.** On DIMACS
clustered instances the default move type 5 is far from converged and
very noisy across seeds:

| Instance | Default `m5_walk` | Fixed `m3_walk` |
|---|---|---|
| `portcgen-20000` | 0.32–3.44% above the best tour found | 0.00–0.42% |

- **Tuning headroom** (default minus best fixed arm, cross-fitted):
  +1.75 points at 20k, +0.18 at 10k, +0.24 at 5k.
- **No schedule beats fixed `m3_walk` there.**

This is algorithm selection, not adaptivity. It matches Helsgaun's
report that deep moves do not pay on heavily clustered instances: the
long inter-cluster "bait" edges send LK into fruitless searches. netgen
(5 large clusters) has V of 0.09–0.30 points but no room and no tuning
gain.

**Reading.** For the TSP, switching move or kick type once mid-run has no
room that is practically useful or clearly above noise, up to 20,000
cities. The verdict is formally GO, but in substance it is close to a
KILL for this setting space. The only large, mechanistically explained
effect is per-instance: move type 3 on clustered instances.

**Next, if Claim 4 continues on the TSP:**
1. **Confirmatory run.** Fresh seeds (8) on LGCP and clustered instances
   of 5k–20k, with three arms fixed in advance: `m3_walk → m5_walk`,
   `m5_walk → m5_walk`, `m3_walk → m3_walk`. It would settle whether the
   LGCP room is real (about 15–20 minutes).
2. **Online instance-level selection.** Try both move types early, then
   commit. On `portcgen-20000` the second half of a fixed `m3_walk` run
   recovers about 1.75 points over the default. That is a weaker form of
   adaptivity than switching by search phase, but it has a mechanistic
   explanation (bait edges).
3. **Settings this test cannot reach:** candidate set (POPMUSIC,
   quadrant), which is the knob the literature finds structure-dependent;
   and the constrained variants (CVRP, CVRPTW) where LKH-3 trails.
