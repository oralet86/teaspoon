# Journal: Claim 1

Claim 1: at equal time, choosing the operator for the current state of the
search beats always using one operator and beats choosing at random.
Plan: `ideas/claim-1-plan.md`; settings: `ideas/benchmark-protocol.md`;
instances, states and operators: `ideas/instances-and-operators.md` (all
local, not tracked).

What every entry must record and where data and figures go: README,
"Working rules". Raw run directories under `runs/` are not tracked by git
and a time-bounded rerun does not reproduce them exactly, so they are kept.

*2026-10-19:* the exploratory entries of 2026-10-16/17 (E0, E1–E1zr) were
removed from the journal; they used earlier code, instances from a retired
generator and a single-pass measurement. The old journal, data and figures
are archived in `runs/journal-archive-20261019/`.

## Entry template

- **Question:**
- **Command:**
- **Code:** commit, plus digest of `aos/` sources if uncommitted
- **Instances / seeds / budgets:**
- **Raw data:** `runs/...`
- **Journal data:** `journal/data/claim-1/...`
- **Figures:** `journal/figures/claim-1/...`
- **Numbers:**
- **Conclusion:**
- **Surprises / against expectations:**

---

## 2026-10-19 · E1 main run: marginal rates of 20 operators on 60 instances × 12 seeds

- **Question:** In which search state does which operator gain the most
  tour length per second (Claim 1.1), and does that depend on the instance
  type and size (Claim 1.2)?
- **Command:** states with
  `uv run python -m aos.e1_states --instances <60 paths> --seeds 0 … 11 --workers 6 --out runs/e1-main/20261018-144422`
  (paths in `instances.txt`); measurement and analysis with
  `bash runs/e1-main/20261018-144422/run-a.sh`, i.e.
  `uv run python -m aos.e1_marginal --states <run> --budgeted --state-seconds 7.5 --repeats 32 --out <run>/marginal --stage gains`,
  then the same with `--stage times`, then
  `uv run python -m aos.analysis.e1_marginal <run>/marginal --prefix e1main`.
- **Code:** commit `4a9ce51` plus uncommitted `aos/` and `generators/`
  (sha256 of `cat aos/*.py aos/analysis/*.py generators/*.py`:
  `42a9f518d35f2f72`, identical to the code at the time of this entry).
- **Host:** AMD Ryzen 9 7940HS, governor `performance`, boost on, SMT on;
  stage 2 pinned to CPU 0, machine otherwise idle.
- **Instances:** 60 symmetric TSP instances, 4 per (family, size) cell:
  `portgen` (uniform), `netgen` (5 clusters), `portmgen` (random matrix),
  `tspgen` (morphed), each at n = 100, 200, 500 with instance seeds 0–3,
  optima from Concorde; TSPLIB kroA100, eil101, lin105, gr96, d198,
  kroA200, tsp225, gr202, pcb442, d493, att532, ali535.
- **States:** 12 per (instance, seed), state seeds 0–11 (`aos/e1_states.py`):
  random, nearest neighbour, d25/d50/d75/d90 (partial O7 descents), opt2,
  oropt, opt3, lkopt, ils1, ils2. 8,640 tours, 7,676 unique (duplicates,
  mostly ils1/ils2 equal to lkopt, skipped).
- **Measurement A, two stages:**
  - stage 1 (untimed, 16 processes): every operator at each anchor from the
    state tour; deterministic operators once per anchor, the others 32
    times; exact local-optimality flags per state;
  - stage 2 (one pinned core, garbage collector off): rounds replay every
    (operator, anchor) and time it, 2–8 rounds, stop once every
    deterministic operator's mean time is within 5% (95% t-interval);
    every replay must reproduce its stage-1 gain and tour;
  - anchors: all cities when a 7.5 s timing budget allows, else a seeded
    sample of at least 32;
  - rate = mean gain / mean time; 95% bootstrap interval (stratified when
    all anchors are used, cluster over anchors when sampled), 1,000
    resamples.
- **Run:** states 1 min (2026-10-18 14:44); stage 1 00:06 → 01:27; stage 2
  01:27 → 05:04; analysis 05:05 (2026-10-19). About 5 h in all.
  Stage 2 stopped by precision in 7,587 states, by `max_rounds` in 54, by
  budget in 35.
- **Raw data:** `runs/e1-main/20261018-144422/` (`states/`, `marginal/`
  with `gains/`, `samples/`, `nodes/`, `cells.csv`; `figures/` with one
  picture of the 12 tours per (instance, seed)).
- **Journal data:** `journal/data/claim-1/e1main_cells.csv` (153,520 cells),
  `e1main_leaders.csv`, `e1main_classes.csv`, `e1main_stages.csv`,
  `e1main_order.csv`, `e1main_nodes.csv`, `e1main_states_nodes.csv`,
  `e1main_instances.csv`.
- **Figures:** `journal/figures/claim-1/e1main_families.png` (share of
  states each operator leads, per instance and stage, and per class),
  `e1main_leaders.png`, `e1main_rates.png`, `e1main_nodes.png`; instance
  pictures in `journal/figures/claim-1/instances/`.

**Numbers.** Classes are exact: *2-opt open* (some 2-opt move improves),
*2-opt optimum* (none does; Or-opt or 3-opt does), *3-opt optimum* (also
none of those; LK does), *LK optimum* (the LK step improves nowhere).
5,360 of 7,676 states have the leader's interval separated from the
runner-up's.

| Class (states, separated) | Leaders (all states; "none" = no operator improves) |
|---|---|
| 2-opt open (4,919; 4,220) | O14 2,055, O17 659, O7 541, O15 483, O5 427, O8 184, O18 174, O9 154 |
| 2-opt optimum (796; 598) | O10 287, O18 148, O8 126, O9 84, O11 58, O12 32, O13 31 |
| 3-opt optimum (615; 335) | O8 167, O18 164, O11 125, O16 48, O13 46, O12 39, O19 26 |
| LK optimum (1,346; 207) | O8 433, none 322, O11 197, O12 173, O16 81, O19 74, O13 65 |

By family, middle and late classes:

| Family | 2-opt optimum | 3-opt optimum | LK optimum |
|---|---|---|---|
| uniform | O10 127, O8 28, O9 25 (223) | O8 50, O11 31, O18 29 (141) | O8 117, none 55, O12 46 (301) |
| clustered | O10 35, O9 22, O11 21 (116) | O11 32, O8 25, O13 20 (96) | O8 94, O11 79, none 49 (298) |
| tspgen | O8 46, O10 35, O9 20 (139) | O8 50, O11 34, O12 17 (118) | O8 104, none 65, O11 38 (295) |
| TSPLIB | O10 86, O8 43, O9 17 (168) | O8 42, O11 28, O18 23 (116) | O8 108, none 59, O12 48 (302) |
| **random matrix** | **O18 133, O16 13** (150) | **O18 102, O16 42** (144) | **none 94**, O16 22, O13 12 (150) |

- **2-opt open, by stage:** random tours: O5 424, O7 292 of 720;
  nearest-neighbour to d75: O14 leads 54–69%; d90: O14 233, O17 124.
  Random matrices: O15 (targeted 2-opt) 333, O14 231, O18 173 of 964.
- **Median gap by stage** (Euclidean families / random matrices):
  nearest neighbour 25–28% / 163%; opt2 6.6–7.9% / 60%; opt3 2.8–3.1% /
  18%; lkopt 0.6–1.4% / 1.6%; ils2 0.4–0.9% / 1.0%.
- **Size, late classes:** 3-opt optima, O18 leads most at n ≈ 100 (76 of
  209), O11 at n ≈ 500 (77 of 207); LK optima, nothing improves in 194 of
  320 at n ≈ 100, O8 leads at n ≈ 200 and 500.
- **Margins** leader / runner-up (states with an improvement), quartiles:
  2-opt open 1.07 / 1.22 / 1.50; 2-opt optimum 1.10 / 1.23 / 1.44; 3-opt
  optimum 1.09 / 1.21 / 1.44; LK optimum 1.18 / 1.49 / 2.23.
- **Cost per application** (Euclidean, n ≈ 500, median µs): at opt3,
  O7 1.2, O9 3.1, O10 8.6, O11 37, O13 41, O19 45, O12 92, O8 97, O18 114,
  O16 197, O5/O6 68; on random tours the kicks and O14 run whole descents
  (about 5.8 ms).

**Conclusions.**

- **Claim 1.1 holds: the leader changes along a run, with the
  local-optimality level.** A 2-opt variant while 2-opt still improves
  (the full scan O5 on random tours, the descent O14 from nearest neighbour
  to d75); 3-opt (O10) or LK at 2-opt optima; LK or a perturbation at
  3-opt optima; a perturbation (kick O8, ruin & recreate O11, double bridge
  O12) at LK optima. Random moves (O1–O4) lead no state that has an
  improvement.
- **Claim 1.2 holds for random matrices, and within levels elsewhere.**
  On `portmgen` the order of leaders differs: targeted 2-opt (O15) leads
  2-opt-open states, LK (O18/O16) leads every middle level, and at LK
  optima no operator improves in most states. On the four Euclidean
  families the order is the same; which perturbation leads differs
  (ruin & recreate strongest on clustered instances), and so does size
  (LK at small n, ruin & recreate at n ≈ 500 at 3-opt optima).
- **Gap is not the state:** opt2 states span all three early classes,
  and the leader follows the class.
- **The natural competitor for E3** is a fixed cascade (2-opt descent →
  3-opt → LK → kick), i.e. VND plus ILS; the room left for selection is
  the choice within a level, which depends on the family and the size.

**Caveats.**

- LK optima: only 207 of 1,346 separated; improvements are rare and the
  leader is often a point estimate between near-tied perturbations.
- `e1main_leaders.csv` names the first operator (`swap`) as leader where
  nothing improves; use `any_improvement` (322 such states, all LK
  optima). `e1main_classes.csv` counts them as `none`.
- The `group` column of `e1main_instances.csv` and `e1main_order.csv` uses
  the analysis's older instance groups and labels most of these instances
  `other`; the family is the instance-name prefix. *Fixed 2026-10-19:* the
  analysis now writes a `family` column (uniform, clustered, tspgen,
  TSPLIB, random matrix); the CSVs here predate the fix and are not
  regenerated, so they keep `group`.
- O10 contains O7's moves, O18/O16 contain 2-opt and 3-opt moves, and the
  kicks contain an O7 descent, so some leads reflect how operators nest.
- Times exclude garbage collection, including an operator's own garbage.
- Node-level results (`e1main_nodes.csv`) credit O15 and the kicks at the
  anchor although they act elsewhere; not interpreted yet.
- Scalar Python on array tours: costs of array rebuilds (kicks, O20) and
  O(n) flips are part of every rate.

---

## 2026-10-27 · E1R test run: rollouts from E1 states (Claim 1.4) — continuation policy stalls; 2-opt classes invalid

- **Question:** Is the E1 leader G(s) also the best first slice when the
  run continues greedily by level to a horizon t (Claim 1.4,
  `ideas/claims.md`)? Test run of the design in `ideas/claim-1-plan.md`
  (E1R), sized to fit 4 h.
- **Command:** `bash runs/e1-rollouts/20261027-test/chain.sh`, i.e. stages
  `plan` (`--instances-per-cell 2 --states-per-class 2 --candidates 5
  --repeats 32`), `calibrate`, `run`, `validate --validate-rollouts 48` of
  `uv run python -m aos.e1_rollouts --out runs/e1-rollouts/20261027-test`,
  then `uv run python -m aos.analysis.e1_rollouts runs/e1-rollouts/20261027-test --prefix e1r_test`.
- **Code:** commit `f6a0b68` plus uncommitted `aos/rollouts.py`,
  `aos/e1_rollouts.py`, `aos/analysis/e1_rollouts.py` (sha256 of the three,
  concatenated: `7110e7b8052b84d1`, in `code_digest.txt`).
- **Host:** as the E1 main run; calibrate and validate pinned to CPU 0,
  run stage on 16 processes.
- **Instances / states / seeds:** 30 instances (2 per family × size cell,
  drawn with seed 0), 235 E1 main-run states (up to 2 per instance and
  exact class, states with an improvement only), 5 candidates per state
  (G(s), the policy's choice, then by E1 rate), 32 paired repeats, slice
  0.1 ms × n, horizon 64 slices; 34,592 rollouts.
- **Run:** plan 09:37, calibrate 09:37 → 09:46, run 09:46 → 11:45,
  validate and analysis 11:46 (2026-10-27).
- **Raw data:** `runs/e1-rollouts/20261027-test/` (`plan.json`,
  `cost_models.json`, `calibration.csv`, `validate.csv`, `results/`).
- **Journal data:** `journal/data/claim-1/e1r_test_arms.csv`,
  `e1r_test_states.csv`, `e1r_test_summary.csv`.
- **Figures:** `journal/figures/claim-1/e1r_test_beaten.png`,
  `e1r_test_regret.png` (both show the invalid 2-opt classes; kept as the
  record of this run).

**Numbers that hold.**

- **Virtual clock.** E1's per-application timings, fitted on counted work,
  overcharge operators that run back to back in a rollout, unevenly:
  E1 fit / measured, median over instances, O14 1.30, O9 1.47, O15 1.42,
  O10 1.25, O17 1.21, against LK 0.99–1.01 and kicks 1.07–1.14. Hence the
  calibrate stage. After calibration, single-core replays of 48 rollouts
  reproduced their stored results exactly, with real / virtual time per
  operator at medians 0.99–1.06 (`validate.csv`).
- **3-opt and LK optima** (little affected by the stall below; 8% and 3%
  of rollouts stall): greedy is beaten at t = 64 slices in 21% (12 of 58)
  and 5% (3 of 59) of states; median cross-fitted regret 0. At 3-opt
  optima the beaten greedy choice is mostly ruin & recreate, beaten by the
  kick (6 of 12), on n ≈ 500 TSPLIB and clustered instances.

**Bug: the continuation policy stalls.** E1's class "2-opt open" means
that O7 *or the full scan O5* improves somewhere. The class leader the
policy applies is mostly O14 (a neighbour-list descent). On a tour that
is O7-optimal but not O5-optimal, O14 cannot improve, the class never
changes, and the policy repeats O14 to the horizon. Example:
`ali535` s5 `nearest_neighbour`, greedy O14: gap 13.35% at every horizon
from 1 to 64 slices, all 32 repeats still "2-opt open" at the end; the
first-slice kick O13 escapes by chance and ends at 6.28%. Rollouts with
no improvement in the second half while in class 2-opt open or 2-opt
optimum: 38% of those from 2-opt-open states, 11% / 8% / 3% from the
other classes. 29 of the 31 2-opt-open states where greedy was "beaten"
at t = 64 have stalled greedy rollouts.

**Conclusion.** No verdict on Claim 1.4 from this run. The results for
states in class 2-opt open and 2-opt optimum measure the policy's stall,
not the first choice. The pipeline itself (plan, calibrated virtual
clock, parallel rollouts, exact replay, analysis) works and fits the
budget.

**Surprises / against expectations.**

- Greedy by level, as defined for E3's reference arm, can deadlock: the
  E1 classes are coarser than what the leaders need. E3 has to handle
  this too.
- The E1 timings are not neutral between operators once operators run
  back to back; E1's rates are per isolated application and stay valid
  as such, but any rollout or full run needs its own calibration.

**To do before a rerun:** make the policy unable to stall (choice
pending), then rerun the same plan.

---

## 2026-11-02 · E0-rs: the clock of the Rust port — repeatability gate not passed

- **Question:** can the Rust port's timing be trusted (spec
  `ideas/rust-port.md` §7–8; the E0 gate of `ideas/claim-1-plan.md`)?
- **Command:** `rust/target/release/aos e0` (five attempts that day:
  `runs/e0-rs/20261102-125456` … `-164340`, while the timing code was
  tightened; this entry is the last, `runs/e0-rs/20261102-173327`).
- **Code:** commit `4f0eaca`, uncommitted; source digest
  `c27e5243d7aaf3f0…` (`e0/config.json`).
- **Host:** Ryzen 9 7940HS, clock locked with `rust/tools/lock-clock.sh`
  (boost off, 4.0 GHz, `performance`); SMT and the APU's fabric clock were
  not yet locked (added to `lock-clock.sh` afterwards).
- **Raw data / summary:** `runs/e0-rs/20261102-173327/e0/summary.md` and
  its Parquet tables. No journal data or figures exported yet.

**Numbers.** TSC: 3.9925 ticks per ns, resolution 80 ticks (20 ns),
overhead median 80 ticks, drift 1e-7 over the run. Repeatability of the
batched time per application (672 cells: size × layout × state ×
operator): median CV 0.68%, 95th percentile 3.66%, maximum 9.96%;
560 cells passed the 5% limit, 1 failed (n = 200, array, LK optimum,
O8: +9.96% [+5.97%, +28.62%]), 111 were unresolved (their interval
straddles the limit). Gate **not passed**. Earlier attempts that day had
median spreads of 8.5% before the warm-up, spin-up, sampled batches and
retakes of disturbed samples were added (roadmap, "Status at shutdown").

**Conclusion.** Medians are tight; the tail is not. The timing stages
since run with one timed worker and an idle pause after all-core load,
and E1R uses this clock only to calibrate a virtual clock whose replays
are validated. E0 with the full lock (SMT off, fabric clock fixed) is
still to be run.

---

## 2026-11-02/03 · First Rust night: measurement A on 1,280 states, robustness factors, E1R top-5 runs

- **Question:** rerun measurement A in Rust on the Python E1 states;
  first robustness factors (spec §9); first Rust E1R runs (Claim 1.4)
  with stall rule `skip` (decision D1) and `none`, at 0.01 and 0.1 ms
  per city.
- **Command:** `bash rust/tools/overnight.sh` (its header lists the
  steps), run directory `runs/overnight-rs/20261102-232155`
  (`chain.log`).
- **Code:** commit `4f0eaca`, 24 uncommitted paths; source digest
  `3fb9e6b86b299044…`. Operators O1–O21 (before the retirement of
  2026-11-05).
- **Instances / states:** the 60 E1 instances; measurement A on state
  seeds 0 and 1 of `runs/e1-main/20261018-144422/states` (1,280 states),
  1.5 s timing budget per state, one timed worker. E1R: 4 instances per
  (family, size) cell, top-5 candidates, 32 repeats, 64 slices; 430
  states at 0.01 ms per city, 112 at 0.1 ms (one instance per cell, the
  2-per-cell run did not fit).
- **Run:** gains 69 min, timing 83 min; E1R runs 23 min each (0.01 ms)
  and 52 min (0.1 ms); robustness factors 40–54 min each. Two restarts
  early in the night (a failed resume of the gains stage at 00:45 and an
  E1R run that failed on zero-cost models at 02:21; both fixed and the
  chain resumed, `e1r-*-failed-zero-cost/`); `a-nocache` summarise
  failed on a disturbed sample, which was re-timed at 07:20.

**Numbers.**

| Comparison (`compare/*.md`) | Shared states | Leader changed |
|---|---|---|
| Rust against the Python E1 run | 1,280 | 42.3% |
| distances computed from coordinates | 523 | 27.7% |
| candidate cache off | 643 | 4.8% |
| two-level list (n 500–535) | 205 | 21.5% |
| the same states re-timed at the end of the night | 129 | 6.2% |

The class never differed. E1R, greedy beaten (Bonferroni over 4
alternatives) at horizon 1 / 8 / 64 slices: `skip` at 0.01 ms 56% /
27% / 13%; `none` 56% / 35% / 30%; `skip` at 0.1 ms 69% / 20% / 19%.

**Conclusion.** Leaders of measurement A depend on implementation
choices more than expected: computed distances and the two-level list
change a fifth to a quarter of them, so per spec §9 those leaders are
implementation-dependent; the 6.2% re-timing drift is the noise floor
to judge them against. Stall rule `none` keeps greedy "beaten" at long
horizons (30% at 64 slices) where `skip` lets it recover (13%): an
artefact of a stalling continuation, so `skip` is the rule (D1).

**Corrections made later (2026-11-05)**, which these numbers predate:

- "Greedy best" in these summaries is understated at long horizons: a
  tie for the best candidate went to the alphabetically first operator
  (`min_by` over a name-sorted map). At 64 slices of `e1r-skip`, 144 of
  339 state rows were tied; the reported best was the alphabetically
  first in 131, and greedy was in the tie but not reported in 117.
  "Greedy beaten" and the regrets are not affected.
- The tables have no state seed, so 91 (instance, state) pairs of seeds
  0 and 1 cannot be told apart in them.
- These plans name operators retired on 2026-11-05, so they cannot be
  re-analysed by the current build.

---

## 2026-11-05 · Decisions: five operators retired, E1R analysis revised, Python cross-checks dropped

- **Operators retired:** O2 `insertion`, O3 `inversion`, O4 `or_opt`,
  O6 `two_opt_best`, O20 `segment_exchange` are removed from `rust/`.
  Rule, fixed before any new run: in the 2026-11-02 measurement A (1,280
  states) an operator led under 1% of the states **and** was among a
  state's five best rates in under 5%. Leads: 0, 1, 0, 1, 0; in the top
  five: 0%, 1%, 0%, 4%, 0%. Kept although weak: O1 `swap` (leads 111
  states, the random tours), O5 `two_opt_first` (leads 5 but beat greedy
  30 times in E1R), O19 `string_removal` (top five in 7%). The other 16
  operators keep their numbers. Caveat: the evidence is the snapshot rate;
  the retired five were tried 1–3 times each as E1R candidates.
- **E1R analysis:** Holm's step-down test instead of Bonferroni (the same
  "beaten" decision, more challengers named; interval at the Bonferroni
  level); the best candidate on a tie is the one that reached the gap
  sooner in virtual time, then plan order; state seed, candidate count and
  tie count in the tables; `--max-candidates K`; cuts at fixed virtual
  times (`by-time/`, 10·2^k µs per city) beside the slice cuts; nested
  state draws; `calibrate --cost-models-from`.
- **Python cross-checks dropped** (decision of the user): the last run
  of the gains and reference checks passed on the 16 operators (O14 at
  89.3%, the rest 100% of anchors equal); the checks, the `verify`
  subcommand and the Python input of `compare` were then removed.

---

## 2026-11-05 · E1R night: every candidate, three slice lengths (Claim 1.4)

- **Question:** (1) with every operator tried first, which operators are
  the best first slice; (2) does the slice length drive "greedy is
  beaten" (the snapshot rate ignores how fast an operator uses up its
  gains within a slice); (3, offline) does a slice-rate selector beat the
  snapshot rate? Plan: `ideas/e1r-overnight-plan.md`.
- **Command:** `bash rust/tools/e1r-night.sh` with the root job
  `rust/tools/e1r-clock-switch.sh`; run directory
  `runs/e1r-night/20261105-012829` (`chain.log`, 399 min, all steps exit
  0). A second copy of the chain started by accident at 01:28:34 was
  killed at 01:31 during planning (untimed; its directory
  `runs/e1r-night/20261105-012834` is not part of the results).
- **Code:** commit `4f0eaca`, 34 uncommitted paths; source digest
  `3f95ed0f1a5292f6…`. 16 operators.
- **States:** measurement A of 2026-11-02 (`a-main`), all 60 instances,
  every state of each (instance, class) pool with an improvement (1,257
  states, up to 19 per class) for R1 and R2; the first 3 per class of
  the same draw for R3 (556 states, a subset of R1's). Every operator
  with a positive E1 rate as a first candidate (12.2 per state in R1),
  32 paired repeats, stall rule `skip`.
- **Runs:** R1 0.01 ms per city × 64 slices; R2 0.001 ms × 640 slices
  (the same 0.64 ms-per-city window); R3 0.05 ms × 64 slices. One cost
  model, calibrated in R1 on one locked core (24 min); R2 and R3 copied
  it. Rollouts unlocked (boost and SMT on, 16 threads): R1 106 min, R2
  110 min, R3 141 min (sized at 3 per class by the chain from R1's
  speed; the last tasks ran nearly one at a time). Validation and
  analysis on the relocked clock.
- **Raw data:** `runs/e1r-night/20261105-012829/R{1,2,3}`.
- **Journal data:** `journal/data/claim-1/e1rn_R{1,2,3}_states.csv`,
  `_states_top5.csv`, `_states_time.csv`, `_summary.csv`,
  `_summary_time.csv`, `_summary_top5.csv`, `e1rn_validation.csv`,
  `e1rn_equal_time.csv`, `e1rn_best_first.csv`, `e1rn_widening.csv`,
  `e1rn_slice_rate.csv`, `e1rn_catch_up.csv` (the last four written by the
  figure script).
- **Re-analysed 08:30 the same morning** with a build that adds the
  slice-rate selector and catch-up columns and breaks ties in the
  cross-fitted regret by plan order (it was the alphabet). Greedy beaten
  and greedy best did not change; some median regrets fell slightly
  (R1 at 4 slices 0.494% → 0.487%).
- **Figures** (script `journal/scripts/e1rn_figures.py`, 32 repeats,
  556 shared states): `journal/figures/claim-1/e1rn_beaten_by_slices.png`,
  `e1rn_equal_time.png`, `e1rn_validation.png`.

**Numbers.**

Validation (24 rollouts per run replayed with real timing; real /
virtual time, median over rollouts): R1 1.002, R3 0.988 — the shared
cost model holds at 0.01 and 0.05 ms. **R2 1.017 overall, but
`three_opt_nl` 1.25 (max 1.54), `lin_kernighan` 1.11,
`lin_kernighan_alpha` 1.17 (max 1.93):** at 0.001 ms slices the model
undercharges the expensive descents by 10–25%, so R2 gives them more
work per slice than real time would.

Greedy beaten (Holm, every candidate), all states, horizon 1 / 8 / 64
slices: R1 78% / 50% / 25% (1,257 states); R2 68% / 46% / 24% (1,257);
R3 74% / 35% / 19% (556). Median cross-fitted regret at 1 slice: R1
4.34%, R2 2.56%, R3 1.11%; zero from 32 (R1), 128 (R2) and 8 (R3) slices
on (after the re-analysis with plan-order ties in the cross-fitting, see
below). By class on the shared states (`e1rn_beaten_by_slices.png`): in
2-opt open states greedy is beaten almost always at 1 slice for every
slice length; in 2-opt, 3-opt and LK optima, at horizon 1, greedy is
beaten more often the longer the slice (3-opt optimum: 10% at 0.001 ms,
45% at 0.01 ms, 71% at 0.05 ms).

The greedy policy at equal elapsed time (`e1rn_equal_time.png`; share
of the 556 states where the shorter decision interval reaches a lower
mean gap, ties excluded): 0.001 against 0.01 ms: 75% vs 23% at 10 µs
per city, 57% vs 31% at 80 µs, then reversed, 27% vs 38% at 640 µs.
0.01 against 0.05 ms: 72–77% vs 16–21% from 20 to 80 µs, even (31% vs
33%) at 640 µs.

**Question 1, the best first operator** (`e1rn_best_first.csv`; states
with a tie for best left out). At 1 slice the winners are kicks: in R1
`lk_kick` 41% and `kick` 40% of the untied states; in R3 `lk_kick` 79%
(73–84% in every class); in R2 the field is wider (`kick` 30%,
`lin_kernighan_alpha` 20%, `three_opt_nl` 17%, the latter two favoured by
R2's undercharged descents). Widening the candidates beyond the snapshot
top five (`e1rn_widening.csv`, cross-fitted regret with every candidate
minus with the top five) lowers greedy's chances at 1 slice in 36% (R1),
22% (R2) and 48% (R3) of the states; mean gain 0.41, 1.11 and 1.92
percentage points of gap. At 64 slices the gain is under 0.1 point. So the
diversity is real, but it is diversity among kicks, and the snapshot
ranking misses the best kick in a third to a half of the states.

**Question 3, the slice-rate selector** (`e1rn_slice_rate.csv`; pick the
candidate with the most gain over its first slice on half the repeats,
score it at the cut on the other half against greedy). Better than greedy
in 69% / 57% / 77% of the states at 1 slice (R1 / R2 / R3), worse in
11% / 15% / 3%; at 8 slices better in 51–59%, worse in 9–20%; at 64
slices better in 34–36%, worse in 12–28%. Ranking by the rate over a
whole slice beats ranking by the snapshot rate.

**Catch-up** (`e1rn_catch_up.csv`): greedy reaches the best first
operator's gap at 1 slice by 2 slices (median, R2) or 4 slices (R1, R3)
in 85–96% of the states; the head start a better first operator gives
is worth one to two doublings of the horizon.

**Conclusion.** Question 2 is answered yes for the optimum classes:
the longer the slice, the more often a different first operator beats
the snapshot leader at the first slice. Shorter decision intervals also
make the greedy policy itself better early in a run. The advantage
fades and, against 0.001 ms, reverses by 0.3–0.6 ms per city. Two
caveats bound these readings: R2's undercharged descents (they favour
R2), and that these instances (at most 535 cities) are mostly solved by
the end of the window. For Claim 1.4: the snapshot leader is not the best
first slice in most states at short horizons, the gap closes within a few
doublings of the horizon, and a selector that ranks by the gain over a
whole slice does better than the snapshot rate.

**Surprises / against expectations.**

- The late reversal against 0.001 ms. Decisions cost nothing on the
  virtual clock, so the reversal cannot be decision overhead; candidate
  explanations are R2's cost-model bias or stall-rule switching with
  very short slices. To check before reading anything into it.
- The rollouts ran faster than estimated: R1 106 min against ~142
  (unlocked estimate; 185 locked), R3 141 against the chain's ~189.
  Unlocking gave at least the assumed 1.3×.
- The shared calibration did not hold at 0.001 ms. R2 should get its
  own calibration (or a slice-aware model) before its results are used
  for a verdict.

## 2026-11-08 · Where AOS has room: re-analysis of the E1R night

- **Question:** across instance family, size, starting optimality gap
  and state class, where does choosing the operator for the current
  state have room to beat the measurement-A snapshot leader?
- **Data:** `journal/data/claim-1/e1rn_R1_states.csv`,
  `e1main_leaders.csv` (starting gaps), `e1main_instances.csv` (sizes);
  R1 of the 2026-11-05 night, 60 instances, 1,257 states, 32 paired
  repeats. Oracle headroom is `regret_crossfit`; the selector is
  `slice_rate_advantage`; the hit rate is the share of states with a
  positive selector advantage.
- **Script:** `journal/scripts/aos_opportunity_figures.py`.
- **Data behind the figures:** `journal/data/claim-1/aos_opportunity.csv`.
- **Figures:** `journal/figures/claim-1/aos_opportunity_map.png`,
  `aos_horizon_by_family.png`, `aos_gap_bins.png`,
  `aos_class_horizon.png`, `aos_size_scaling.png`,
  `aos_best_first_operators.png`.

**Numbers.**

- At 1 slice 78% of states have some operator that beats the snapshot
  leader (Holm, 0.05); the median oracle headroom is 4.34% of tour
  length. At 8 slices 50% are still beaten but the median headroom is
  0.11%; at 64 slices 25% and 0.00%.
- A first-slice selector is positive against greedy in 82% of the 1,257
  states at 1 slice, 71% at 8 and 47% at 64 (all 1,257 states, not the
  556 shared subset). Its median gain tracks the oracle headroom
  closely, so the binding constraint is the headroom itself, not the
  selector.
- Starting gap is monotonic: <1% 16% beaten and 0 headroom; 2-5% 66%
  and 0.57%; 5-10% 95% and 3.56%; 10-20% 97% and 5.54%; >20% 92% and
  11.34%.
- State class: 2-opt open 98% beaten and 6.14% median headroom at 1
  slice (99% selector hit rate); LK optimum 13% and 0.00% (25%).
- Family: Euclidean families 3.2-4.7% median headroom at 1 slice;
  random matrix 38.8%, but it falls to 0.97% at 2 slices and 0.16% at 4.
- Size: the median headroom at 1 slice rises only modestly from n≈100
  to n≈500 (e.g. clustered 3.47% -> 4.62%), but the headroom left at 8
  slices rises more clearly (clustered 0.00% -> 0.75%; TSPLIB 0.00% ->
  0.51%; uniform 0.13% -> 0.52%).
- Best first operator at 1 slice: `kick` 42%, `lk_kick` 35%, the two
  LK variants 14%, other kicks 7%. The snapshot leader is best in only
  14% of states. Random matrices prefer `lk_kick` 58% and the LK
  variants 42%; clustered prefers `kick` 70%.

**Conclusion.** The room for AOS is concentrated at the start of a
search, on tours that are still far from optimal (roughly >5% gap),
in 2-opt-open states, and on larger Euclidean instances. The advantage
is short-lived: it is mostly a first-slice/early-horizon effect, and a
first-slice selector captures most of the median headroom. A solver
should adapt the operator after construction or perturbation and fall
back to a fixed choice once the tour is in the 3-opt/LK-optimal regime.
The random-matrix headroom is real but even shorter-lived; R3's longer
slices and R2's undercharged descents mean its magnitude should be read
from R1, or from a calibrated run at the intended slice length.

**Robustness.** The class ordering (2-opt open > 2-opt optimum > 3-opt
> LK) and the monotonic gap gradient hold in R1, R2 and R3; the
magnitudes are slice-length-specific (R3, at 0.05 ms, has a smaller
random-matrix median at 1 slice).

**Caveat.** The figures use R1; R2/R3 on the shared 556 states are in
`e1rn_beaten_by_slices.png`. The starting gap and state class are
correlated, so the two splits should be read together.
