# E1R overnight plan (one 9-hour window)

Status: **run on the night of 2026-11-05** (`runs/e1r-night/20261105-012829`;
results and caveats in `journal/claim-1.md`, entry "2026-11-05 · E1R
night"). Written 2026-11-03 after the first full Rust night
(`runs/overnight-rs/20261102-232155`); revised 2026-11-05. Belongs to
Claim 1.4 ([`claim-1-plan.md`](claim-1-plan.md)); the E1R design is in
[`rust-port.md`](rust-port.md) §8.5.

## What was run (2026-11-05), against the plan below

The plan below was changed once more before the night (8-hour window,
`rust/tools/e1r-night.sh`):

| Run | Slice per city | Horizon | States | Rollouts (unlocked) |
|---|---|---|---|---|
| R1 | 0.01 ms | 64 slices | 1,257 (every improving state, up to 19 per class) | 106 min |
| R2 | 0.001 ms | **640 slices** (R1's 0.64 ms window, for equal-time cuts) | 1,257 (same) | 110 min |
| R3 | 0.05 ms | 64 slices | 556 (first 3 per class; sized by the chain from R1's speed) | 141 min |

One calibration (R1, 24 min, one locked core), copied by R2 and R3.
Validation: the shared model holds for R1 (real/virtual 1.002) and R3
(0.988) but undercharges `three_opt_nl` (1.25) and LK (1.11–1.17) at
R2's 0.001 ms slices, so R2 needs its own calibration. An extension of R3
to 4 per class (~51 min locked plus a slow serial tail) did not fit the
61 min left and was not run. Next: R3 at more states per class (its
rollouts so far are reusable), R2 recalibrated.

## Questions the night answers

The first Rust E1R runs (top-5 candidates, 430 states, 0.01 ms and 0.1 ms
per city) left three questions:

1. **Is the diversity real?** Every state tried only its five highest-rate
   operators, so "best first operator" meant best of five, and LK could win
   only where it ranked in the top five. Trying every candidate first
   answers whether the winners are mostly LK and LK kicks or spread over
   kicks, double bridges and 3-opt, per class.
2. **Does the slice length drive the "greedy is beaten" result?** The
   explanation from the first runs is that the snapshot rate ignores how
   fast an operator uses up its easy gains within a slice. Then greedy
   should be beaten less with shorter slices and more with longer ones.
   A sweep of 0.001, 0.01 and 0.05 ms per city tests that.
3. **Does a slice-rate selector beat the snapshot rate?** No extra run is
   needed: with every candidate tried first, each one's gain over its
   first slice is its rate over a whole slice, which can be scored offline
   as a selector against the greedy choice.

## Why sweep the slice length

The slice is the decision interval: greedy picks the operator with the
best snapshot rate and then commits to it for one slice. The sweep gives
three things:

- **A test of the mechanism (question 2).** As the slice shrinks, the
  snapshot rate and the rate over the slice coincide, so greedy must
  become (nearly) optimal over one slice. If "greedy beaten" does not fall
  towards 0.001 ms, the depletion explanation is wrong and something else
  (e.g. the first operator setting up states the continuation exploits)
  is at work.
- **The decision unit for E2/E3** (open item in
  [`claim-1-plan.md`](claim-1-plan.md)). Shorter slices cost more
  decisions; the sweep measures what deciding less often loses.
- **Robustness of the Claim 1.4 verdict.** A verdict that holds at one
  arbitrary slice length is fragile; one that holds, or changes in a
  predictable way, over 50× is not.

Last night's two top-5 runs already point that way. At about the same
elapsed virtual time, greedy was beaten in 27% of states with 0.01 ms
slices (horizon 8, 0.08 ms per city, 430 states) and in 69% with 0.1 ms
slices (horizon 1, 0.1 ms per city, 112 states). The state samples differ,
so this is a hint, not a result.

**Why the ceiling is 0.05 ms and not 0.1 ms.** 0.1 ms has no special
meaning in Rust: it was the Python design's slice, and in Rust 0.01 ms
already holds about as much work (spec §8.5). Long slices saturate on
instances of at most 535 cities, and a state where every candidate reaches
the optimum carries no information about the choice:

| Slice | Horizon 1 | Horizon 4 | Horizon 16 | Horizon 64 |
|---|---|---|---|---|
| 0.01 ms: every candidate at the optimum | 1% | 4% | 18% | 30% |
| 0.1 ms: every candidate at the optimum | 1% | 22% | 37% | 43% |

(Share of states where all 32 repeats of every candidate end at the
optimum; `arms.parquet` of `e1r-skip` and `e1r-skip-0.1ms`.) 0.05 ms keeps
1.7 decades of slice length, halves R3's cost against 0.1 ms, and so lets
R3 try every candidate on 3 states per class.

## Operators retired 2026-11-05

O2 insertion, O3 inversion, O4 or_opt, O6 two_opt_best and O20
segment_exchange are removed from the repository's Rust code (not only
from E1R). Rule, fixed before any new run: an operator is retired if, in
the 2026-11-02 measurement A (`a-main`, 1,280 states), it led under 1% of
the states **and** was among a state's five best rates in under 5%.

| Operator | Leads (of 1,280) | In the top 5 | Beat greedy in E1R (both runs) |
|---|---|---|---|
| O2 insertion | 0 | 0% | 0 |
| O3 inversion | 1 | 1% | 0 |
| O4 or_opt | 0 | 0% | 0 |
| O6 two_opt_best | 1 | 4% | 1 |
| O20 segment_exchange | 0 | 0% | 0 |

Kept although weak: O1 swap leads 111 states (the random starting tours),
O5 two_opt_first leads 5 but beat greedy 30 times in rollouts, O19
string_removal leads 3 but is in the top 5 in 7%. Caveat: last night's
E1R runs were top-5 only, so the five were tried 1–3 times each; the
evidence is the snapshot rate of measurement A, which never ranks them
near the top. They make up about 16% of the rollouts an all-candidate run
would have spent.

The remaining 16 operators keep their protocol numbers (O1, O5, O7–O19,
O21). Last night's measurement is reused as it is: E1R's plan drops the
retired operators' rates, and the two states they led (netgen-500-3 s1
`oropt`, led by inversion, and `opt3`, led by two_opt_best) get the best
current operator as greedy (three_opt_nl and two_opt_first). Plans of
earlier E1R runs name retired candidates and can no longer be run or
re-analysed by this build; their tables are still read.

## What is reused, and which checks are dropped

- **No new operator timing (measurement A).** E1R draws its states from
  last night's 1,280 timed states (seeds 0 and 1) in
  `runs/overnight-rs/20261102-232155/a-main`. The single-call timing pass
  the operator timing runs alongside its batched pass is therefore not run
  at all. (If new states are timed later, dropping that pass is possible:
  for expensive operators per-call and batched times agree within a few
  per cent, but the rate intervals are bootstrapped from the per-call
  times, so a gains-only bootstrap would have to replace it first.)
- **Dropped from last night's chain:** the Python comparison (the port
  is no longer cross-checked against Python, decided 2026-11-05), the
  population replay check, the robustness factors (computed distances,
  candidate cache, two-level list), the re-timing check, and stall rule
  `none` (shown to be an artefact of a stalling continuation).
- **Dropped 2026-11-05:** R4 (0.03 ms, top 5). Three slice lengths a
  decade or less apart show the direction of the effect; R4 would only
  have shown its curvature.
- **One calibration, shared** (decided 2026-11-05). R1 calibrates on one
  locked core for every candidate; R2 and R3 take its cost models
  (`e1r --stage calibrate --cost-models-from <R1>`, which copies
  `cost_models.json` byte for byte and records its SHA-256). All three
  runs then use the same virtual clock, so slice length is not mixed with
  the fit noise of three calibrations. Each run keeps its own validation
  (24 rollouts replayed with real timing), which checks that the shared
  model holds at that slice length.
- **Faster untimed stage:** the rollouts themselves run on a virtual
  clock, so their results do not depend on machine speed. Only
  calibration and validation need the locked clock; the rollout stage can
  run with boost and SMT on (16 threads instead of 8). The speed-up is
  estimated at about 1.3 times and is not yet measured.

## Runs

All runs: stall rule `skip`, 32 paired repeats, horizon 64 slices,
4 instances per (family, size) cell (all 60 instances), every candidate
(`--candidates 21`, a cap above the 16 operators), the cost fit with the
intercept kept (2026-11-03).

"Every candidate" means every current operator with a positive E1 rate
in the state: 10.6 per state on average, of 16. The states are nested: the
plan draws a whole random order of each pool and keeps the first k, so
R3's 3 states per class are the first 3 of R1's 6 (checked on the actual
plans).

| Run | Slice per city | States per instance and class | States | Rollouts, locked 8 threads | Unlocked at 1.3× |
|---|---|---|---|---|---|
| R1 | 0.01 ms | 6 | 808 | ~101 min | ~78 min |
| R2 | 0.001 ms | 6 (same as R1) | 808 | ~10 min | ~8 min |
| R3 | 0.05 ms | 3 (first 3 of R1's) | 556 | ~278–314 min | ~214–241 min |
| R3, if time allows | 0.05 ms | 6 (all of R1's) | 808 | ~448–506 min | ~345–389 min |
| R3, fallback | 0.05 ms | 2 | 430 | ~205–231 min | ~158–178 min |

R3 is planned at 6, 3 and 2 states per class (nested). Once R1 and R2
are done, their measured rollout time gives the real unlocked speed-up,
and the chain runs the largest R3 that still ends before the deadline.
At 6 per class the slice sweep covers every R1 state.

**How the estimates were made.** Rollout CPU time is proportional to
Σ over states of n × candidates × repeats × horizon × slice. Last night's
runs give the cost per unit of n × candidates (locked, 8 threads):
44.1 min per million at 0.01 ms (`e1r-skip`, 23.8 min for 430 states)
and 392.6 at 0.1 ms (`e1r-skip-0.1ms`, 52 min for 112 states). Per
millisecond of slice that is 4,414 against 3,926: long slices cost less
than proportionally because more rollouts reach the optimum and stop.
0.05 ms is interpolated between the two (196–221 per million, hence the
range), and 0.001 ms is assumed to cost a tenth of 0.01 ms. Neither has
been measured; the pilot measures both.

## Budget (9 hours)

| Block | Time |
|---|---|
| Build, clock check | 2 min |
| Plans R1–R3 (and R3's 2-per-class fallback) | 1 min |
| Idle before calibrating | 2 min |
| Calibration R1 (locked, one core, every candidate) | ~17 min |
| Calibrations R2, R3 (copied from R1) | 0 min |
| Unlock, wait until unlocked | ~2 min |
| Rollout stages R1, R2, R3 at 3 per class (unlocked, at 1.3×) | ~300–327 min |
| Lock, wait until locked | ~2 min |
| Validations (idle 120 s and ~1 min each) | ~9 min |
| Analyses (full and top 5) | ~3 min |
| Margin | ~175–202 min |
| **Total** | **9 h** |

The calibration figure is the earlier estimate for 21 operators; with 16
it should be shorter. At no speed-up at all R1–R3 (3 per class) take
~389–425 min and the night ends with ~77–113 min to spare. R3 at 6 per
class fits if the speed-up is at least about 1.2× (~27–71 min margin at
1.3×).

## Order of the chain

1. Locked: build, clock check, then `plan` for R1, R2, R3 (6, 3 and 2
   per class); one 120 s idle; `calibrate` R1; `calibrate
   --cost-models-from` R1 for R2 and every R3 plan.
2. Write the flag `calibrations-done`; the root job (below) unlocks the
   clock (boost on, SMT on); the chain waits until the machine reports
   unlocked.
3. Rollout stages R1 and R2 on every logical CPU; then the largest R3
   (6, 3 or 2 per class) whose estimate, scaled by R1's measured speed,
   still fits before the deadline.
4. Write the flag `rollouts-done`; the root job locks the clock again; the
   chain waits until locked.
5. Validate R1–R3 (locked), then analyse R1–R3, each also with
   `--max-candidates 5` (into `analysis-top5/`).

## Commands for the user

Evening, before starting the chain:

```
/mnt/mert/Code/teaspoon/rust/tools/lock-clock.sh
sudo systemd-run --unit=teaspoon-e1r-clock --collect /mnt/mert/Code/teaspoon/rust/tools/e1r-clock-switch.sh
```

`systemd-run` starts `e1r-clock-switch.sh` as a root background job (a
transient systemd service, removed when it ends; `--collect` also cleans
it up if it fails). The script, still to be written, waits for the flag
`calibrations-done`, runs `unlock-clock.sh`, waits for `rollouts-done`,
runs `lock-clock.sh`, and exits. It needs no password during the night.
Check it with `systemctl status teaspoon-e1r-clock`; cancel it with
`sudo systemctl stop teaspoon-e1r-clock`. It runs a script from the
repository as root, so it suits a personal machine only.

Morning:

```
/mnt/mert/Code/teaspoon/rust/tools/unlock-clock.sh
```

## Preparation before the night

Done 2026-11-05 (in `rust/src/experiments/rollouts.rs`; fmt, clippy and
tests pass):

- [x] The state seed in E1R's `states.parquet` and `arms.parquet` (91
  (instance, state) pairs of seeds 0 and 1 could not be told apart), and
  the number of candidates analysed per state.
- [x] Nested state and instance draws in `plan`, so runs with different
  sample sizes share their states by construction.
- [x] `analyse --max-candidates K`: only the first K candidates of each
  state (greedy first), into `analysis-top<K>/`. Rollout streams depend on
  (state, repeat) only, so this is exactly the analysis of a run planned
  with K candidates.
- [x] Holm's step-down test for "greedy beaten" (family-wise error 0.05
  per state and horizon), with the challenger's interval at the Bonferroni
  level; Holm-adjusted p-values per arm (`holm_p`). Holm decides "beaten"
  exactly as Bonferroni did; on last night's `e1r-skip` 25 of 28 summary
  rows are unchanged and 3 move by one point (Monte Carlo noise of the
  sign-flip test).
- [x] `calibrate --cost-models-from <run>`.
- [x] Ties for the best candidate: greedy wins a tie it is part of, other
  ties go to the earliest candidate in plan order, and `tied_best` counts
  the tied candidates. Before, a tie went to the alphabetically first name
  (`min_by` over a name-sorted map): at horizon 64 of last night's
  `e1r-skip`, 144 of 339 state rows were tied, the reported best was the
  alphabetically first in 131, and greedy was in the tie but not reported
  in 117. "Greedy best" in last night's summaries is therefore understated
  at long horizons, and `double_bridge`'s "wins" there are mostly ties.
  "Greedy beaten" and the regrets were not affected.
- [x] O2, O3, O4, O6 and O20 removed (see above); the Python cross-checks
  were run a last time on the 16 operators and passed.

Still to do (needs approval):

1. Write the chain script (`rust/tools/e1r-night.sh`) and
   `rust/tools/e1r-clock-switch.sh`.
2. Pilot, about 15 minutes, untimed: plan and run R1, R2 and R3 on two
   instances, once locked on 8 threads and once unlocked on 16. It
   measures the unlocked speed-up and the cost of 0.001 ms and 0.05 ms
   slices, the three numbers the budget assumes (slices of 0.001 ms may
   hold only a few applications of the expensive operators on 100-city
   instances).

## Analyses afterwards (no extra compute)

- Best first operator by family, class and horizon, from every candidate
  (R1, R2, R3), with tied states (`tied_best` > 1) split or left out.
- **Slice sweep (question 2):** greedy beaten and regret against slice
  length on R3's 556 states, which all three runs share; same candidates
  in every run, so the same Holm thresholds.
- **Widening (question 1):** cross-fitted regret with every candidate minus
  that with the top 5 (`--max-candidates 5`), per class and slice. Not
  "greedy beaten", whose test size changes with the number of candidates:
  on last night's `e1r-skip`, analysing only 3 of the 5 candidates lowers
  "greedy beaten" at horizon 1 from 56% to 46% on the same rollouts.
- The head-start lag (slices greedy needs to reach the best rollout's gap)
  against slice length.
- A slice-rate selector: rank operators by gain over their first slice,
  score its regret against greedy (R1, R2, R3). Rank and score on
  different halves of the repeats, as the cross-fitted regret does;
  otherwise it wins at horizon 1 by construction.
- Across nights: R1's top-5 analysis against last night's `e1r-skip` on
  their 363 shared states, 360 of them with identical top-5 lists (the
  other 3 had a retired operator in last night's top 5); the results
  should agree up to the refitted cost model. 100 of the 112 states of
  last night's 0.1 ms run are in R1, 81 in R3.

## Open problems

- **Equal-time comparisons across slices.** The analysis cuts at powers
  of two of the slice count, so R1, R2 and R3 cut at different virtual
  times (R3/R1 = 5, R1/R2 = 10). Comparing the greedy arm at equal elapsed
  time (the decision-interval question) needs either cuts at fixed virtual
  times in `analyse` (the events allow any cut) or slice lengths in
  power-of-two ratios (e.g. 0.00125, 0.01 and 0.04 ms). Not decided.
- The instances stop at 535 cities, and 57–60% of rollouts reach the
  optimum within 64 slices. Whether the greedy gap also closes on larger
  instances needs new states, timing and optima for TSPLIB instances of
  1,000–2,000 cities; that is a separate night.
- The unlocked speed-up (boost and SMT) and the costs of 0.001 ms and
  0.05 ms slices are estimates until the pilot.
- R3's interval at the Bonferroni level and Holm's decision can disagree
  for a challenger rejected at a later Holm step (its interval may cross
  zero); accepted 2026-11-05 as the conservative choice.
