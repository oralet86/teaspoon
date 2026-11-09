# Benchmark protocol for Claim 1 (draft)

Status: **draft; decisions recorded** (marked *Decided*). Written
2026-10-16, revised 2026-10-19 after the E1 main run. Turns
[`claim-1-plan.md`](claim-1-plan.md) into concrete settings, using what the
reading showed ([`papers/notes/`](../papers/notes/README.md)). Items marked
**[decide]** need a decision before code is written.

E1 is done and does not use most of this file: its states, operators and
measurement are in [instances-and-operators.md](instances-and-operators.md)
and its results in `journal/claim-1.md`. Sections 1, 2, 5, 6 (per run), 7
and 11 are the settings for the full runs E2 to E5.

## 1. Algorithm frame

- **Main design: a population of tours with mutation and local moves only,
  no crossover.** *Decided 2026-10-16* (`aos/population.py`). Steady-state:
  pick a tour by tournament, copy it, give the copy one time slice with the
  chosen operator, and replace the worst tour if the copy is better and its
  length is not already present. Closest to LKH's trial loop. Cost: the
  algorithm is closer to a population-based iterated
  local search than to a GA, which changes how the paper is pitched. The
  reasoning is recorded once, in [claims.md](claims.md#open-points).
- **Robustness check, later:** the E3 comparison repeated with one fixed
  edge-preserving crossover in every arm.
- **Population size:** 20. Small enough that slices dominate run time,
  large enough to keep diversity. *Decided 2026-10-16.*
- **Initial tours:** random permutations.

## 2. The unit of decision: time slices (E2–E5)

E1 does not use slices: fixed-time slices made its ranking depend on the
slice length (gain within a slice decays) and mixed "many proposals" with
"one trial" as the unit, so E1 measures marginal rates of single
applications instead (*decided 2026-10-16*). Slices remain the unit of the
population runs (`aos/slices.py`); whether they stay the decision unit for
E2/E3 is **[decide]** (a Python selector costs about as much as a cheap
move; see also [softmax-policy.md](softmax-policy.md), point 5).

- One decision gives one operator one slice of wall-clock time on one tour.
  Inside the slice the operator is applied repeatedly; each application is
  kept if it improves. Precedent: Chuang's "amplified" heuristics (10 ms of
  repeated application).
- **Slice length:** a fixed multiple of the cost of an expensive single
  application at that instance size, so that every operator completes at
  least a few applications. The 2026-10-16 rule (3× the median time of one
  O5 pass, calibrated in E0) was made for O1–O8; with LK and the kicks in
  the set it has to be set again. **[decide]** An application still
  running when the slice ends is completed and its full time is charged;
  the overshoot is logged.
- **Slices are timed; single applications are not.** Timing one microsecond
  move would cost more than the move. Counted work is logged per
  application.

## 3. Operators

The 20 operators O1–O20, their moves, acceptance rules, families and
parameters are described once, in
[instances-and-operators.md](instances-and-operators.md) §5. Choice of the
set:

- Small and diverse at first, because the meta-analysis shows that
  adaptiveness pays only when operators are good in different situations
  (Turkeš et al.); grown in steps as E1 showed gaps (the history is in
  the archived journal, `runs/journal-archive-20261019/`).
- Left out on purpose: scramble (designed for scheduling, per Larrañaga),
  reversed displacement (IVM), a full O(n²) best-improvement pass
  (dominated by O5/O6 per Hansen and Mladenović), Balas–Simonetti (much
  more expensive), dynasearch (known to trail 3-opt), ejection chains
  (overlap with LK).
- Planned: O21, a kick repaired by LK ([rust-port.md](rust-port.md) §6.1).

## 4. Instances

*Revised 2026-10-18:* 5 families × 3 sizes (n ≈ 100, 200, 500) × 4
instances, 60 in all, every one with a known optimum. The list, generators
and seeds are in [instances-and-operators.md](instances-and-operators.md)
§1–3.

- Four instances per cell are too few for a test within one cell (the
  smallest two-sided p of a paired test over four is 0.125); Claim 1.2 is
  tested on pooled cells, and per-cell results are descriptive.

## 5. Budgets

- **One long run per (arm, instance, seed), read at three cut points**
  (T/9, T/3, T). Valid for every arm whose behaviour does not depend on the
  budget (single operators, uniform, state-based reference, AOS). The tuned
  fixed schedule must be rerun per budget.
- **T per instance:** the time at which the best single operator's median
  gap stops improving by more than 0.1% per T/10, measured in a pilot.
  **Open: decided after E0 and the pilot**, from the proposals in section 5a.
- **N1 (time vs evaluations), decided 2026-10-16: yes.** E2 and E3 also run with an evaluation budget
  equal to the median number of applications the time budget allowed, so
  both units are compared on the same scale.

## 5a. Proposals for the total budget T (decide after E0 and the pilot)

What T must achieve: runs long enough to pass through the early regime (easy
improvements), the middle, and the late regime near local optima, because
Claim 1.1's switch point lies between them; and short enough that the
experiment fits on one machine.

| Option | Rule | For | Against |
|---|---|---|---|
| A. Plateau | T = time at which the best single operator's median gap improves by less than 0.1 percentage points per T/10 (pilot) | Every arm reaches its late regime | Needs a pilot per instance; the "best single operator" is not known before E2 |
| B. Size scaling | T = c · (time of one full O7 descent from a random tour), c ≈ 50, measured in E0 | Cheap, follows n and the implementation automatically | c is arbitrary; says nothing about reaching a target |
| C. Target | T = 3 × the median time for uniform selection to reach a 2% gap (pilot, 5 seeds) | Ties T to search progress, not to one operator; the cut points T/9, T/3, T then fall roughly before, around and after the 2% region | Needs the pilot; instances where uniform never reaches 2% need a cap |
| D. Fixed seconds | 10 s, 60 s, 300 s for every instance | Simple; comparable with CHeSC-style studies | Small instances finish trivially and large ones never reach the late regime |

**Proposal: C, with B as a cap** (T = min(C, 3 × B)) and a hard cap of
600 s. Uniform selection is the natural yardstick because it is in every
comparison and does not presuppose which operator wins.

**Compute check, to be redone.** E2 alone, with 21 arms (20 operators +
uniform) × 60 instances × 30 seeds × a mean T of 60 s, is about 630
core-hours, and the evaluation-budget twin doubles it. E0 showed that timed
jobs cannot run in parallel, so this does not fit one machine in scalar
Python: the arm set (for example the operators that lead somewhere in E1),
the seeds or T must shrink, or the runs wait for the Rust port.

## 6. Metrics

### Per run (E2–E5)

| Metric | Definition | Use |
|---|---|---|
| Gap at t | (length − optimum) / optimum at cut points | primary outcome |
| Primal integral | area under gap(t) over [0, T], normalised by T (`bench/metrics.py`) | anytime quality in one number |
| Time to target | first t with gap ≤ 5%, 2%, 1% (`bench/metrics.py`) | speed |
| Operator shares | fraction of slices per operator, per tenth of the run | behaviour |

### Per slice (E1, and logged in every run)

| Metric | Definition |
|---|---|
| Gain per second | Σ gain / Σ time over a set of slices (**ratio of sums**, Thomas and Schaus), with a bootstrap 95% interval |
| P(improve) | fraction of slices with any improvement |
| Leading operator | the operator with the highest gain per second in a state, and its margin over the second |
| Switch level | the local-optimality level at which the leading operator changes (E1 showed the gap is not the state) |
| Momentum | P(the leading operator of a slice still leads in the next), as in Pei et al. 2022, per slice length (N4) |
| Counted work | distance lookups and elements moved, against time (E0 cross-check) |

### Per comparison

- **Rank agreement between budget units:** Kendall's τ between arm rankings
  under time and under evaluations (N1).
- **Loss from one operator for all:** gap of the overall best single
  operator minus gap of the per-instance best (E4).

## 7. Statistics

- **Paired by seed:** every arm on a given (instance, seed) starts from the
  same initial population.
- **Per instance:** Wilcoxon signed-rank between the reference arm and each
  competitor, Holm correction across competitors; effect size as the median
  paired difference with a bootstrap interval, plus Vargha–Delaney A₁₂.
- **Across instances:** Friedman test on mean ranks, then pairwise Wilcoxon
  with Holm (as in Gretsista and Burke) or Bergmann's procedure (as in Dantas
  et al.).
- **Anytime curves:** Wilcoxon rank-sum at each cut point (as in Pei et al.
  2022).
- **Seeds:** 30 per cell for E2–E5. E1: 12 state seeds per instance; its
  repeats, timing rounds and intervals are in
  [instances-and-operators.md](instances-and-operators.md) §3 and §5.1.
- **Verdict rule (Claim 1):** unchanged from `claim-1-plan.md`.

## 8. Timing hygiene

- Each run pinned to one core (`taskset`); `OMP_NUM_THREADS=1` and similar
  set to 1; no other timed job on the same physical core.
- Arms for one (instance, seed) run interleaved, not in blocks, so drift in
  machine speed spreads over all arms.
- CPU model, governor and load recorded in each run's metadata
  (`bench/metadata.py` already records host data).
- **E0 gate:** repeated timings must disagree by less than the smallest
  operator difference that matters, or the setup is fixed first.
- *Added 2026-10-18:* timed work never runs in parallel, also at a fixed
  clock (7 workers: up to 23% slower, unevenly by operator). How E1
  handles it: [instances-and-operators.md](instances-and-operators.md)
  §5.1.

## 9. What every run logs

Per slice: run id, instance, seed, arm, slice index, start time, duration,
operator, applications, improving applications, length before, length after,
counted work, previous operator, "last O5/O7 scan found nothing" flag, the
run's recent improvement rate ρ, and, for anchored moves, the anchor node and
its static and dynamic local features (`local-context-selection.md`).

Per run: configuration, git commit and dirty flag, lock-file digest, host
metadata, and the gap-against-time trace (the `bench/` run-directory layout).

## 10. Group A and Group B

| Group | Runs | Needs the full operator set? |
|---|---|---|
| A | E0 timings per operator; E1 measurements per operator (done); E2 single-operator runs; the evaluation-budget twin of E2 | No: run as each operator is added |
| B | E1 leader maps (done); E3 reference arm, uniform, best-fixed, schedule; E4; E5 mixtures; the evaluation-budget twin of E3 | Yes |

Group A results stay valid only if sections 1, 2, 5 and 8 are frozen first.

## 11. E5 grid

Twenty operators make a full proportion grid infeasible (even eight give
210 mixtures at step 0.25). Restrict E5 to mixtures of the **top three operators
from E2** at step 0.25 (15 mixtures). **[decide]**
