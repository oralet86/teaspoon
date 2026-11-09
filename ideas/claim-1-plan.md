# Testing Claim 1

The experiments E0 to E5 for Claim 1 ([claims.md](claims.md)), their
status, and the verdict rule. Each experiment ends in a journal entry
(`journal/claim-1.md`) with its data and graphs. Revised 2026-10-19 after
the E1 main run.

Where things live:

- concrete settings (algorithm frame, slices, budgets, metrics, statistics,
  timing hygiene, logging): [benchmark-protocol.md](benchmark-protocol.md);
- instances, generators, the measured tours, seeds and the operators
  O1–O20, and how measurement A works:
  [instances-and-operators.md](instances-and-operators.md);
- results: `journal/claim-1.md`.

**Related work for this plan.** The competitor set follows Fialho (2010);
E1's measurement follows Pei et al.'s (2022) neighbourhood analysis, with
time added; the reference arm resembles Zhang et al.'s (2014) fitness-level
AOS. Details in [claims.md](claims.md); what is still new in
[novelty.md](novelty.md).

## Status

| Experiment | Question | Status |
|---|---|---|
| E0 | Can the clock be trusted? | Done (2026-10-16/18). Repeatability gate passed; parallel timed workers slow operators unevenly (up to 23% at 7 workers), so timing runs on one core. Entries archived in `runs/journal-archive-20261019/`. |
| E1 | Which operator gains most per second in which state? | Done (2026-10-19, main run). Claim 1.1 holds; Claim 1.2 holds for random matrices and within levels. |
| E1R | Does the greedy E1 choice pay off later? (Claim 1.4) | Test run 2026-10-27: pipeline works; the continuation policy stalls in 2-opt classes, no verdict yet |
| E2 | Do fixed-operator runs cross? | Planned |
| E3 | Does choosing by state beat the competitors? | Planned |
| E4 | Does the best operator depend on the instance? | Planned |
| E5 | Can a mixture beat the best single operator? | Planned |

## E0: can the clock be trusted?

- **Measure:** time per operator use against tour size (`aos/e0.py`); the
  same measurement with parallel pinned workers over minutes
  (`aos/e0_contention.py`); counted work against time.
- **Gate:** if repeated timings disagree by more than the differences
  between operators, fix the setup before anything else.

## E1: which operator is best in which state?

No selector and no full runs: every operator's marginal rate (mean gain over
mean time of one more application) is measured on fixed tours.

- **States:** 12 tours per (instance, seed) along one search path, from a
  random tour through 2-opt, Or-opt, 3-opt and LK optima to two ILS states
  (`aos/e1_states.py`). States are defined by local-optimality level, not by
  gap: the first E1 runs showed that the leader follows which
  neighbourhoods are exhausted, and gap-based states mixed levels.
- **Measure:** measurement A (`aos/e1_marginal.py`), details in
  [instances-and-operators.md](instances-and-operators.md) §5.1.
- **Graphs:** share of states each operator leads, per stage and per class;
  leader grids; rates by state; node-level leaders (`aos/analysis/e1_marginal.py`).
- **Still open:** momentum, i.e. how often the leader of one state still
  leads after a few further moves (Pei et al. 2022 found 12–27% per move in
  a VRP; novelty N4).

## E1R: does the greedy choice pay off later?

Claim 1.4 ([claims.md](claims.md)): is the E1 leader G(s) also the best
first slice when the run continues greedily to a horizon t?

- **States:** a sample of E1 main-run states, stratified by (family, size)
  cell and by exact local-optimality class; states where nothing improves
  are left out.
- **Candidates:** G(s), the continuation policy's choice for the state's
  class, and the next operators by E1 rate (5 in all, rate > 0).
- **Rollouts** (`aos/e1_rollouts.py`, `aos/rollouts.py`): one slice of the
  candidate, then greedy by level (the E1 leader of the tour's exact
  class, by mode over the instance's E1 states) up to 64 slices; slice
  = 0.1 ms × n. 32 repeats per (state, candidate), paired: every candidate
  shares the continuation's random stream.
- **Virtual clock**, like E1's split of untimed and timed work: every
  application is charged a + b·lookups + c·moved ns, fitted per instance
  and operator on timed rollouts on one pinned core (calibrate stage); the
  rollouts then run untimed on every CPU and are exactly reproducible. A
  validate stage replays a sample with real timing. The E1 timings
  themselves overcharge cheap neighbour-list operators by up to 1.7×
  relative to the kicks when they run back to back, so they only seed the
  calibration.
- **Analysis** (`aos/analysis/e1_rollouts.py`): per state and horizon
  (1, 2, 4, …, 64 slices), whether some candidate beats G(s) (one-sided
  paired sign-flip test, Bonferroni over the alternatives) and the
  cross-fitted regret; shares per class, family and horizon.
- **Not charged:** classifying the tour for the policy (reference
  knowledge, like the E1 map).

## E2: do fixed-operator runs cross?

- **Run:** the population search (`aos/population.py`) with each operator
  alone, all seeds, all budgets.
- **Graphs:** gap against time per operator, median with the spread over
  seeds, per instance; a strip showing which operator leads at each moment.
- **Reads on:** Claim 1.1 in the plainest sense.
- **Twin run with an evaluation budget** (novelty N1): the same arms with the
  budget counted in operator applications; compare the rankings.

## E3: does choosing by state beat the competitors?

- **Reference arm:** at each step, the operator E1 found best for the
  tour's current local-optimality level (detected cheaply during the run,
  e.g. "the last 2-opt sweep found nothing"), for the instance's family and
  size. Its knowledge comes from E1 and is not charged for.
- **Competitors:** the table under Claim 1 in [claims.md](claims.md). After
  E1 the decisive one is the **fixed cascade** (2-opt descent → 3-opt → LK →
  kick, i.e. VND plus ILS).
- **Policies on the E1 map:** greedy against softmax,
  [softmax-policy.md](softmax-policy.md).
- **Graphs:** final gap per arm as box plots per instance and budget; gap
  against time for all arms; paired differences between the reference and
  each competitor with intervals; the share of each operator over a run.
- **Reads on:** Claim 1 as stated. If the cascade matches the reference, the
  best operator changes during a run but no adaptation is needed to follow
  it; the weight then shifts to the choice within a level (which depends on
  family and size) and to the time-vs-evaluation result (N1).

## E4: does the best operator depend on the instance?

- **Measure:** from E2, the best single operator per instance, and what is
  lost by using the overall best everywhere.
- **Graphs:** the winning operator over size and family; the loss from one
  operator for all instances, per instance.
- **Reads on:** Claim 1.2.

## E5: can a mixture beat the best single operator?

- **Run:** fixed mixtures on a coarse grid of proportions (benchmark
  protocol §11), against the best single operator, at equal time.
- **Reads on:** the case E1 cannot see. Different moves get stuck in
  different places, so a mixture can win even when one operator leads on
  immediate gain everywhere.

## Verdict rule

Claim 1 is shown if the reference arm beats all competitors with intervals
that exclude zero on most instances and budgets, and failed if the best
single operator per instance is never beaten. How large a difference is
worth pursuing is judged once E2 and E3 data exist.

## To do

- [x] Set up `journal/` and the first entry.
- [x] Scalar operators, population search with time slices and a time budget.
- [x] A TSP instance generator, with optima from Concorde.
- [x] E0: timing checks.
- [x] E1: the map of the best operator per state (main run, 60 instances).
- [ ] Decide the decision unit and budget T for E2/E3 (benchmark protocol §2, §5a).
- [ ] E2–E5.
- [ ] Settle the open points under [Claims](claims.md#open-points).
