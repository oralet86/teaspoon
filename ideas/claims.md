# Claims

The project moves in steps called claims. Each is a statement to be shown
empirically, and each builds on the one before. The goal is a GECCO 2027
paper on adaptive operator selection in genetic algorithms.

Last revised 2026-10-12.

## Ground rule: time is always counted

Operators are never compared by improvement alone. A constant-time move and
a move that scans the whole tour are not on an even footing, and with time
ignored the most expensive operator always looks best. Every comparison is
therefore improvement per unit of time, at an equal time budget.

- **Time is wall-clock time**, measured on an implementation where it is
  proportional to the work done. Sorting operators into complexity classes
  was considered and dropped: no two operators have the same cost once
  applying the move is counted, so only measured cost is fair.
- **The first implementation is scalar Python**: plain lists, no vectorised
  calls. Mixing interpreted and vectorised operators would make a scan look
  nearly free, which is a fact about the library and not about the operator.
- **Counted work** (distance lookups, elements moved) is logged beside the
  timer as a cross-check.

## Claim 1: no single operator is best throughout

At equal time, choosing the operator for the current state of the search
beats always using one operator and beats choosing at random.

No selection mechanism is needed here. It is enough to show that such a
choice exists: build runs where the best operator at each point is known,
and show that following it beats the alternatives.

- **Claim 1.1: the best operator changes during a run.** Early on a tour is
  full of easy improvements, so many cheap random moves should collect more
  per second than one scan. Near a local optimum almost no random move
  improves, and a directed, scan-based move such as 2-opt should win.
- **Claim 1.2: the best operator changes between instances.** Sizes,
  distributions and geometries (Euclidean and non-Euclidean) are the axes to
  test.

Comparisons that Claim 1 has to survive:

| Competitor | What it rules out |
|---|---|
| Best single operator overall | That one operator is simply best |
| Best single operator per instance | That a lookup table by instance is enough (against 1.2) |
| Uniform random choice | That the gain comes from having several operators, not from choosing |
| A fixed schedule, e.g. cheap moves first, then scans | That a hand-made timetable is enough (against 1.1) |

## Claim 2: a mechanism can estimate the best operator while running

Bandits, a heuristic or a reinforcement-learning model; the kind is open.
It is measured by how much of the distance between the best single operator
and the reference from Claim 1 it closes, with its own running time counted
and its settings tuned on instances other than the test ones.

## Claim 3: the best operator depends on the implementation

Run the same algorithm in scalar Python, in vectorised Python and in Rust.
If the best operator, or the point in a run where it changes, differs between
them, then the right choice is a property of the implementation and hardware
as well as of the problem.

That would be a reason for selecting by measured time: a fixed schedule
tuned on one implementation is wrong on another, while a mechanism that
measures improvement per second adapts without being told. Vectorisation is
one such factor; neighbour lists and tour data structures are others.

## Claim 4: the same thinking improves state-of-the-art solvers

Originally aimed at LKH-3: embed a mechanism that picks operators during the
run, and improve it where it is weakest. That means instances where it cannot
reach the optimum in good time, and problem variants where specialised
solvers hold the best-known results.

Numbers from the GA will not transfer. What transfers is the reward
definition, the budget accounting, the test for change during a run, and
which instance features matter.

## Open points

- **What "the best operator at each point" means.** Operators are random, so
  "best" must be best on average over many samples; keeping the best of one
  draw each would reward luck. The best immediate choice is also not
  necessarily the best for the rest of the run, so the reference is a strong
  benchmark, not a proven upper bound.
- **Leading on immediate gain is not the whole test.** Even if one operator
  has the best improvement per second in every state, a mixture can still
  beat it, because different moves get stuck in different places. Claim 1
  needs both the per-state comparison and a best-single against mixture run.
- **What counts as failure.** No claim has a metric, a number of seeds or a
  result that would make us drop it yet. For Claim 1 the judgement is made
  after the first measurements, with no margin fixed in advance.
- **The operator set is thin.** Inversion is a random 2-opt move, and it
  probably leads swap and insertion early in a run. Segment insertion and a
  perturbation move are the first additions.
- **Mutations only, or crossover too.** Not decided.
- **Novelty.** Selecting operators adaptively in a GA, and rewarding
  improvement per unit of time, are both established (see
  [prior work](prior-work.md)). The contribution
  has to be the characterisation in Claims 1 and 3, or Claim 4.

## To do

- [ ] Vectorised Python version and Rust port, for Claim 3 and larger sizes.
