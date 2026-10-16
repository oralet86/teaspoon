# Testing Claim 1

Proposed plan. One week, done by 2026-10-19, in scalar Python. Each
experiment below ends in a journal entry with its data and graphs.

## Setup shared by all experiments

- **Scalar implementation.** Tours as lists, distances as a list of lists,
  the standard `random` module. The current `ga/` is vectorised and counts
  offspring, so it cannot be used as it is.
- **Time slices.** One decision gives one operator a fixed slice of time on
  one tour. A cheap move runs many times in its slice and keeps the changes
  that improve; a scan runs once or a few times. Every decision then costs
  the same, and operators are compared by gain per slice.
- **The GA around it.** Each step picks parents, makes a child by crossover,
  gives the child one slice with the chosen operator, and replaces the worst
  tour if the child is better. Crossover cost is the same in every arm.
- **Budget.** Wall-clock time, equal for all arms, at three lengths per
  instance, because the answer may depend on how long a run is.
- **Operators.** Random swap, random insertion, random inversion, segment
  insertion, 2-opt taking the first improving move at an edge, and 2-opt
  taking the best move from a scan. Double bridge is added if a perturbation
  is needed.
- **Instances this week.** TSPLIB instances from about 100 to a few hundred
  cities, and generated uniform and clustered instances at three sizes, with
  optima from Concorde. A TSP generator has to be written.
- **Seeds.** 30 per cell for the comparisons in E3 to E5, as the experimental
  protocol asks; fewer for the maps in E1.
- **Timing hygiene.** Timed runs do not share cores, and each measurement is
  repeated.

## E0: can the clock be trusted?

- **Measure:** time per operator use against tour size; the same measurement
  repeated on different runs and cores; counted work against time.
- **Graphs:** cost curves per operator over size; spread of repeated timings;
  scatter of time against counted work.
- **Gate:** if repeated timings disagree by more than the differences between
  operators, fix the setup before anything else.

## E1: which operator is best in which state?

No selector and no full runs: operators are sampled on fixed tours.

- **States:** tours at set distances from the optimum (random, then roughly
  50%, 20%, 10%, 5%, 2% and 1% above it), taken from reference runs, plus
  children straight after crossover at the same stages.
- **Measure:** for each operator on each tour, thousands of slices: chance
  of improving, gain per slice, with confidence intervals.
- **Graphs:** gain per slice against distance from the optimum, one line per
  operator with its interval, per instance; a heatmap of the leading operator
  over state and instance; the distance from the optimum at which the lead
  changes, plotted against instance size.
- **Reads on:** Claim 1.1 if the lead changes along a run, Claim 1.2 if the
  heatmap differs between instances. If one operator leads in every cell,
  Claim 1 fails on immediate gain and only E5 can rescue it.

## E2: do fixed-operator runs cross?

- **Run:** the GA with each operator alone, all seeds, all budgets.
- **Graphs:** gap against time per operator, median with the spread over
  seeds, per instance; a strip showing which operator leads at each moment.
- **Reads on:** Claim 1.1 in the plainest sense. Crossing curves mean the
  best operator depends on how much time there is.

## E3: does choosing by state beat the competitors?

- **Reference arm:** at each step, give the slice to the operator that E1
  says is best for the tour's current distance from the optimum. Its
  knowledge comes from E1, measured on other seeds, and is not charged for.
- **Competitors:** best single operator overall, best single operator per
  instance, uniform random, and the best fixed schedule (cheap moves until a
  switch point, then scans, with the switch point tuned).
- **Graphs:** final gap per arm as box plots per instance and budget; gap
  against time for all arms; paired differences between the reference and
  each competitor with intervals; the share of each operator over a run.
- **Reads on:** Claim 1 as stated. Beating the fixed schedule is the hard
  part: if a schedule matches the reference, the best operator changes during
  a run but no adaptation is needed to follow it, which shifts the weight to
  Claim 3.

## E4: does the best operator depend on the instance?

- **Measure:** from E2, the best single operator per instance, and what is
  lost by using the overall best everywhere.
- **Graphs:** the winning operator over size and geometry; the loss from one
  operator for all instances, per instance.
- **Reads on:** Claim 1.2.

## E5: can a mixture beat the best single operator?

- **Run:** fixed mixtures of operators on a coarse grid of proportions,
  against the best single operator, at equal time.
- **Graphs:** final gap over the mixture grid per instance; the best mixture
  against the best single operator with intervals.
- **Reads on:** the case E1 cannot see. Different moves get stuck in
  different places, so a mixture can win even when one operator leads on
  immediate gain everywhere.

## Order and verdict

E0, then E1 on a few instances. E2 to E5 follow if E1 shows a lead that
changes; if it does not, E5 runs alone before Claim 1 is called.

Claim 1 is shown if the reference arm beats all four competitors with
intervals that exclude zero on most instances and budgets, and failed if the
best single operator per instance is never beaten. How large a difference
is worth pursuing is judged once the first measurements exist.

## To do

- [ ] Set up `journal/` and the first entry.
- [ ] Scalar GA with time slices and a time budget.
- [ ] A TSP instance generator, with optima from Concorde.
- [ ] E0: timing checks.
- [ ] E1: the map of the best operator per state, on a few instances.
- [ ] Decide by 2026-10-19 whether E2 to E5 run in full.
- [ ] Settle the open points under [Claims](claims.md#open-points).
- [ ] Tests for `ga/` (none yet).
