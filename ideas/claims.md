# Claims

The project moves in steps called claims. Each is a statement to be shown
empirically, and each builds on the one before. The goal is a GECCO 2027
paper on operator selection under a time budget.

Last revised 2026-10-27: Claims 1.4 (is the greedy E1 choice also the
best first step of a run?), 1.5 (counting applications instead of seconds
changes the leader) and 1.6 (local optimality depends on the candidate
set) added. 2026-10-19: Claim 1.1 and 1.2 results from the
E1 main run added (`journal/claim-1.md`). Related work per claim comes from
the sources in [`papers/notes/`](../papers/notes/README.md); what remains
new is analysed in [novelty.md](novelty.md).

| Claim | Status |
|---|---|
| 1. No single operator is best throughout | 1.1 shown and 1.2 partly shown on immediate gain (E1); 1.4 test run (E1R) found a policy stall, rerun pending; 1.5 and 1.6 supported per state by E1 data; full runs E2–E5 to do |
| 2. A mechanism can estimate the best operator while running | Not started |
| 4. The same thinking improves state-of-the-art solvers | Paused ([lkh3.md](lkh3.md)) |
| 5. A learned per-level candidate selector gives full-scan gains at neighbour-list cost | Idea; after the Rust port ([learned-candidates.md](learned-candidates.md)) |

## Ground rule: time is always counted

Operators are never compared by improvement alone. A constant-time move and
a move that scans the whole tour are not on an even footing, and with time
ignored the most expensive operator always looks best. Every comparison is
therefore improvement per unit of time, at an equal time budget.

- **Time is wall-clock time**, measured on an implementation where it is
  proportional to the work done. Sorting operators into complexity classes
  was considered and dropped: no two operators have the same cost once
  applying the move is counted, so only measured cost is fair.
- **The current implementation is scalar Python** (`aos/`): plain lists, no
  vectorised calls. Mixing interpreted and vectorised operators would make a
  scan look nearly free, which is a fact about the library and not about the
  operator. The planned replacement is a Rust port (see "Implementation"
  below).
- **Counted work** (distance lookups, elements moved) is logged beside the
  timer as a cross-check.

### Implementation

*Decided 2026-10-17:* that an operator's cost depends on its
implementation is taken as given, not as something to show. It is a design
principle:

- **Every operator gets the best implementation we can give it**:
  suitable data structures first (tour representation, don't-look bits,
  candidate lists, data layout), vectorisation where it applies (full
  scans, candidate evaluation, preprocessing such as 1-trees and alpha
  values). No operator is compared in a weaker implementation than the
  others; this is the fairness condition of the ground rule above.
- **Results are reported for a stated implementation.** Where a leader
  depends on a representation choice, it is flagged. E1 already found two
  such cases: ruin & recreate beats kicks on large instances mainly because
  a kick rebuilds the O(n) array tour (E1x), and fixed-time slices against
  marginal rates moved 61 leads from kicks to the 2-opt descent (E1
  measurement A with O14).
- **Planned implementation: a Rust port** in which the experiments run
  entirely in Rust and Python only draws figures, with two tour
  representations as in LKH; specification in [rust-port.md](rust-port.md).
  Results from scalar Python at n >= 1,000 that depend on cost ratios are
  provisional until then.

Implementation changes operator cost by orders of magnitude:
neighbour-list first-improvement 2-opt is about 100× faster than
best-improvement at n = 1000 (Hansen and Mladenović 2006); neighbour lists,
don't-look bits and the tour data structure change the cost class of 2-opt,
3-opt and LK (Johnson and McGeoch 1997).

**Related work.** Charging operator time is established for the *reward*
(COBRA, the choice function, Laborie and Godard, Thomas and Schaus, and
others; the list is in [prior-work.md](prior-work.md#time-aware-selection)).
What is rare is charging time in the *budget* of the comparison: most AOS
studies count evaluations or iterations, and Turkeš et al. (2021) name
iteration budgets as a source of bias without measuring it. See
[novelty.md](novelty.md), N1.

## Claim 1: no single operator is best throughout

At equal time, choosing the operator for the current state of the search
beats always using one operator and beats choosing at random.

No selection mechanism is needed here. It is enough to show that such a
choice exists: build runs where the best operator at each point is known,
and show that following it beats the alternatives.

- **Claim 1.1: the best operator changes during a run.** The first
  hypothesis was that cheap random moves collect more per second early on,
  and a directed move such as 2-opt wins near a local optimum.
  *Result (E1, 2026-10-19):* the leader changes, but not that way. It
  follows the local-optimality level: a 2-opt variant while 2-opt still
  improves, 3-opt or LK at 2-opt optima, LK or a perturbation at 3-opt
  optima, a perturbation at LK optima. Random moves (O1–O4) lead no state
  with an improvement, not even the random tour. The gap to the optimum is
  not the state; the level is.
- **Claim 1.2: the best operator changes between instances.** Sizes,
  distributions and geometries (Euclidean and non-Euclidean) are the axes.
  *Result (E1):* on random distance matrices the order of leaders differs
  (LK leads every middle level); on the four Euclidean families the order
  is the same and only the choice within a level differs (which
  perturbation, LK against ruin & recreate by size).
- **Claim 1.4: the greedy choice is not always the best first step.**
  Added 2026-10-27. E1 finds the operator with the best gain per second
  for the *next* application. Like any greedy rule, that choice may lead
  to a worse tour later in the run. Formally:

  | Symbol | Definition |
  |---|---|
  | s | A search state: a tour of one instance (the E1 states). |
  | O | The operators O1–O20. |
  | τ | The decision unit: one *slice*, in which operator o is applied repeatedly at random anchors for τ seconds. The application running at the end of the slice is finished and its time charged. |
  | π | The continuation policy. Here: **greedy by level**, i.e. at every slice, the E1 leader for the tour's current local-optimality class on that instance. |
  | t | The horizon: total time from s, including the first slice. |
  | L_t(s, o) | Tour length at time t after one slice of o from s, then π. A random variable over the operators' random streams. |
  | V_t(s, o) | E[L_t(s, o)], the expected length. An expectation, not the best of several draws, which would reward luck. |
  | G(s) | argmax_o r(s, o), with r the E1 marginal rate: the greedy choice (written E1(s) in the first draft). |
  | B_t(s) | argmin_o V_t(s, o): the best first slice for horizon t (written E3(s) in the first draft; renamed because E3 is an experiment). Found offline and not charged, like the E1 map. |
  | R_t(s) | V_t(s, G(s)) − V_t(s, B_t(s)) ≥ 0: the regret of the greedy choice, in percentage points of gap. |

  **Statement.** With π greedy by level, there is a range of horizons t
  over which R_t(s) > 0 in a substantial share of the states of at least
  one local-optimality level. "R_t(s) > 0" means that the paired 95%
  interval of V_t(s, G(s)) − V_t(s, o) excludes zero for some o ≠ G(s),
  with a Bonferroni correction over the alternatives. Reported: the share
  of such states and the median regret, per level, family, size and
  horizon.

  **Failure.** R_t(s) is within noise of zero in nearly all states at
  every horizon tested. Then greedy is stable against single deviations,
  which supports using the E1 map as a policy (E3's reference arm).

  **Why π is the greedy policy.** The claim then asks whether deviating
  from greedy *once* ever pays. If it does, the policy that takes B_t(s)
  at every state (a rollout policy) is at least as good as π (Bertsekas,
  Tsitsiklis and Wu 1997, not read), which gives E3 a stronger reference
  arm than the greedy map.

  **Edge cases.** At very short horizons t ≈ τ the comparison becomes
  gain within τ instead of gain per second, so B and G can differ
  without any long-term effect; at long horizons every first choice
  reaches the optimum on small instances and they tie. The claim is
  about the horizons in between, and every horizon is reported.

  *Test:* E1R, rollouts from E1 states ([claim-1-plan.md](claim-1-plan.md#e1r-does-the-greedy-choice-pay-off-later)).
- **Claim 1.5: counting applications instead of seconds changes the
  leader.** Added 2026-10-27 (novelty N1). For a state s, let G(s) be the
  operator with the best gain per second (E1) and A(s) the operator with
  the best gain per application, the unit of evaluation and iteration
  budgets. The claim: G(s) ≠ A(s) in a substantial share of states, and
  the rankings of all operators under the two units disagree, most of all
  early in a search. At the level of whole runs, the same arms rank
  differently under a time budget and under an application budget (E2's
  twin runs).
  *Evidence (E1 main run, 2026-10-27, from `e1main_cells.csv`):* same
  leader under both units in 7% of 4,919 2-opt-open states, 42% of 796
  2-opt optima, 56% of 615 3-opt optima, 79% of 1,024 LK optima (states
  with an improvement); median Kendall τ between the two rankings of the
  20 operators 0.49, 0.83, 0.95, 1.00. Per application, kicks and double
  bridges lead (they contain a descent); per second, the 2-opt descent
  does. Part of this is built in (an expensive application gains more),
  so the content is the size of the disagreement and where it vanishes.
  The per-application side does not depend on the implementation; the
  per-second side is provisional until the Rust port.
- **Claim 1.6: local optimality, and with it the state, depends on the
  candidate set.** Added 2026-10-27. A tour that no neighbour-list move
  can improve may still be improvable by the same move over a larger
  candidate set, and how often depends on instance structure. The leader
  in such states is not the leader of the neighbour-list class, so a
  state description by local-optimality level must name the candidate set
  each level refers to.
  *Evidence (E1 main run):* 439 of 4,919 2-opt-open states (9%) cannot be
  improved by neighbour-list 2-opt (O7, 10 nearest) but can by the full
  scan (O5); mostly `opt2`, `oropt` and `opt3` states, concentrated on
  clustered (177) and tspgen (113) instances against uniform (36) and
  random matrices (20). Their leaders: alpha-candidate 2-opt O17 (148),
  kick (93), Or-opt (63), 3-opt (62); the 2-opt descent O14 leads none.
  The E1R test run stalled on exactly these tours: its policy applied the
  class leader O14, which cannot improve them
  (`journal/claim-1.md`, 2026-10-27).

Comparisons that Claim 1 has to survive:

| Competitor | What it rules out |
|---|---|
| Best single operator overall | That one operator is simply best |
| Best single operator per instance | That a lookup table by instance is enough (against 1.2) |
| Uniform random choice | That the gain comes from having several operators, not from choosing |
| A fixed schedule, e.g. cheap moves first, then scans | That a hand-made timetable is enough (against 1.1) |

**Related work.**

- **The test design is Fialho's** (thesis 2010, defense slides read): each
  operator alone, uniform selection, the best static mixture tuned offline,
  and an oracle that applies the best operator for the parent's current
  fitness, on OneMax and BBOB under evaluation budgets. Our additions: the
  TSP, wall-clock budgets, and the tuned fixed *schedule* as a competitor.
- **The reference arm's idea** (one operator choice per quality level) is
  Zhang, Weise, Li (2014), who learn a distribution per fitness level but never
  show that the distributions differ.
- **E1's measurement** (best operator = best mean gain over many samples from
  one solution) is Pei et al.'s (2022) neighbourhood analysis. They find, in a
  large VRP, that the best operator persists between consecutive solutions
  only 12–27% of the time. This is a warning that "distance from the optimum"
  may be too coarse a state.
- **Claim 1.1, observed without time:** Boomsma (2004) sees a greedy
  crossover lead early and EAX late; Jeong and Song (2025) see OX early, IPT
  late, k-opt early, inversion late; Hansen and Mladenović (2006) find first-
  improvement 2-opt better from random starts and best-improvement better from
  greedy starts; Fialho's OneMax mutation curves cross as fitness rises.
- **Claim 1.2:** the best fixed operator pair differs between TSPLIB instances
  (Boomsma 2003) and between size groups (Jeong and Song 2025); selected
  operators differ by problem type but not between instances of one type
  (Thomas and Schaus 2018).
- **After E1 the decisive competitor is the fixed cascade** (2-opt descent →
  3-opt → LK → kick, i.e. VND plus ILS), a form of the fixed schedule.
- **The competitors are where claims fail:** uniform selection is close to
  adaptive methods (Boomsma 2004; Chuang 2020; Gunawan et al. 2016); the best
  single operator beats every selector (Gretsista and Burke 2017; Thomas and
  Schaus 2018); adaptiveness adds 0.14% on average in ALNS (Turkeš et al.
  2021).
- **Mixtures (E5):** a mixture beats the operator that is best at the end
  (Boomsma 2004, EAX alone vs adaptive); the most productive operator can want
  a low share (Tuson and Ross 1998).

## Claim 2: a mechanism can estimate the best operator while running

Bandits, a heuristic or a reinforcement-learning model; the kind is open.
It is measured by how much of the distance between the best single operator
and the reference from Claim 1 it closes, with its own running time counted
and its settings tuned on instances other than the test ones.

**Related work.**

- **Stateless selectors:** probability matching and adaptive pursuit
  (Thierens 2005), DMAB (DaCosta et al. 2008; restart on a Page–Hinkley change
  test), sliding and rank-based (AUC) bandits (Fialho 2010). The survey (Pei
  et al. 2025) recommends AP or DMAB with improvement-rate or success credit.
  Tuned p_min for PM/AP was 0–0.03 in DaCosta et al., far below the usual 0.1
  (ours is 0.05).
- **State-based selectors:** contextual bandit with search-state features
  (Gonçalves et al. 2018, LinUCB); SARSA(λ) with 15 trajectory features and
  chains (Kletzander and Musliu 2023, LAST-RL; the rich state helped on TSP);
  DQN (Dantas et al. 2021; lost to DMAB on TSP); LSTM trained offline (Jeong
  and Song 2025); RL over improvement operators with a rule-triggered
  perturbation (Lu et al. 2020).
- **Time in the reward:** see the ground rule above; Thomas and Schaus's
  ratio of sums is the estimator in [claim-2-reward-notes.md](claim-2-reward-notes.md).
  LAST-RL's runtime penalty mattered only where heuristic costs differ widely.
- **Sequences and chains:** Kheiri and Keedwell 2015, Chuang 2020, LAST-RL;
  see [chained-selection.md](chained-selection.md).
- **Tuning on other instances:** SMAC-tuned LAST-RL overfit its training
  domains; offline-trained selectors carry this risk (survey, challenges).
- **Failure modes to expect:** stateless AOS failed in a complex VRP because
  momentum is low (Pei et al. 2022); sparse large rewards defeated every
  selector in DaCosta et al.'s outlier scenario.

## Claim 4: the same thinking improves state-of-the-art solvers

Originally aimed at LKH-3: embed a mechanism that picks operators during the
run, and improve it where it is weakest. That means instances where it cannot
reach the optimum in good time, and problem variants where specialised
solvers hold the best-known results.

Numbers from the scalar TSP study will not transfer. What transfers is the reward
definition, the budget accounting, the test for change during a run, and
which instance features matter.

**Related work.** Learning inside LKH-3 so far targets candidate edges, not
operators (VSR-LKH-3, bandLKH-3, MABB-LKH-3, NeuroLKH; see [lkh3.md](lkh3.md)).
Selecting the *perturbation* of an iterated local search with a
time-normalised reward has been done for nurse rostering (Gretsista and Burke
2017), where the best single perturbation still won. Lu et al. (2020) separate
learned improvement operators from rule-triggered perturbations and report
beating LKH-3 on random CVRP-100, but by step count rather than equal time.
Kheiri and Keedwell (2015) show a sequence learner discovering
perturb-then-local-search on HyFlex TSP, so "rediscovering the LKH pattern"
alone would not be new.

## Claim 5: a learned candidate selector per level

Added 2026-10-27. Follows from Claim 1.6: each level of the greedy cascade
(2-opt, Or-opt, 3-opt, LK, kicks) is only exhausted relative to a candidate
set, and a cheap candidate set misses moves the expensive version of the
same operator finds.

**Statement.** For each level of the cascade, a selector trained to predict
which nodes the full version of that level's operator would choose (the
full-scan or best-improvement move, given the anchor) yields a candidate
list of N nodes per city such that the operator restricted to it reaches,
at equal wall-clock time, a higher gain per second than the same operator
over the strongest cheap candidate sets: nearest neighbours, alpha
candidates, quadrant neighbours, and longer lists. The selector's own
running time is charged; it is trained on instances other than the test
ones.

**Failure.** An oracle that hands the operator the full version's choice
for free does not beat alpha candidates by a useful margin (then no
selector can), or the learned selector does not beat the cheap baselines
at equal time.

**Evidence that there is something to gain (E1 main run).** In the 439
states that neighbour-list 2-opt cannot improve but the full scan can
(Claim 1.6), median over states: the full scan O5 improves at 2.0% of
anchors with a mean gain of 263 per application at 21.7 µs (rate 7.0·10⁶
per second); alpha-candidate 2-opt O17 improves at 0.2% with 0.9 at 1.0 µs
(1.1·10⁶ per second). The gain sits at few anchors and is large, so
choosing *where* may matter as much as choosing the partner.

Design, baselines, first experiment and related work:
[learned-candidates.md](learned-candidates.md).

## Open points

- **What "the best operator at each point" means.** Operators are random, so
  "best" must be best on average over many samples; keeping the best of one
  draw each would reward luck. The best immediate choice is also not
  necessarily the best for the rest of the run, so the reference is a strong
  benchmark, not a proven upper bound. Claim 1.4 tests this directly.
- **Leading on immediate gain is not the whole test.** Even if one operator
  has the best improvement per second in every state, a mixture can still
  beat it, because different moves get stuck in different places. Claim 1
  needs both the per-state comparison and a best-single against mixture run.
- **What counts as failure.** Claim 1's verdict rule is in
  [claim-1-plan.md](claim-1-plan.md#verdict-rule); no margin is fixed in
  advance. Claims 2 and 4 have none yet.
- **Mutations only, or crossover too.** *Decided 2026-10-16: no crossover in
  the main design*, one fixed edge-preserving crossover as a later
  robustness check ([benchmark-protocol.md](benchmark-protocol.md) §1).
  Single-solution AOS methods drop crossover for structural reasons (Kheiri
  and Keedwell 2015, Chuang 2020); immediate rewards undervalue crossover
  (Pei et al. 2025); for the TSP a population of one with strong local search
  beats large GA populations (Johnson and McGeoch 1997). Cost: the algorithm
  becomes a population-based iterated local search, which changes how the
  paper is pitched.
- **Novelty.** Selecting operators adaptively in a GA for the TSP, rewarding
  improvement per unit of time, Fialho's competitor set, the per-solution
  best-operator measurement, and chains judged as a whole are all published.
  What remains is analysed in [novelty.md](novelty.md).

## To do

- [ ] Rust port (best implementation per operator, two tour representations), for larger sizes and final timings; see "Implementation" under the ground rule.
- [ ] Claim 1.4: E1R test run, then decide the full design.
