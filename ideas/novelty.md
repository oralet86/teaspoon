# What is still new

Status: analysis for discussion. Written 2026-10-16 after reading the 23
sources in [`papers/notes/`](../papers/notes/README.md); E1 results noted
2026-10-19.

## What is already done

| Part of our plan | Already published | Note |
|---|---|---|
| Adaptive operator selection in a GA for the TSP | Boomsma 2003/2004, Jeong and Song 2025, Pettinger and Everson 2002 | Not new. |
| Reward = improvement per unit of time | COBRA (Corne, Ross, Fang 1994; Tuson and Ross 1998), choice function (Cowling et al. 2000), Laborie and Godard 2007, **Thomas and Schaus 2018 (ratio of sums over a time window)**, Gretsista and Burke 2017 | Not new; our planned estimator is Thomas and Schaus's. |
| Claim 1 competitor set (single operators, uniform, best static mixture, oracle by current fitness) | Fialho 2010 (OneMax, BBOB) | The design is not new; the fixed schedule in time is our only extra competitor. |
| Measuring the best operator per solution by mean gain over many samples | Pei et al. 2022 (neighbourhood analysis, complex VRP) | E1's method is not new. |
| One operator distribution per fitness level | Zhang, Weise, Li 2014 | The reference arm's idea is not new. |
| The best operator changes during a run (observed) | Boomsma 2004 (HEU → EAX), Jeong and Song 2025 (OX → IPT), Fialho (OneMax curves), Hansen and Mladenović 2006 (first vs best flips with start) | Shown in selector behaviour or in evaluations, never under time accounting. |
| Chains of operators judged as a whole | Kheiri and Keedwell 2015, Chuang 2020, Kletzander and Musliu 2023 | The chain idea is not new. |
| "Where, then what" | Chen and Tian 2019 (NeuRewriter) | Not new as a policy structure. |
| Learned selectors on routing | Lu et al. 2020, Dantas et al. 2021, LAST-RL | Not new. |

## Candidate contributions

Each one is a claim we could defend, the nearest prior work, and the risk.
Ranked by how defensible they look now.

### N1. Time accounting changes the answer

**Claim.** Comparing operators (and AOS arms) at equal *evaluations* and at
equal *wall-clock time* gives different winners, different switch points and
different conclusions about whether adaptation helps.

- **Nearest prior work:** Turkeš et al. name iteration budgets as a source of
  bias in ALNS comparisons but do not measure it. LAST-RL notes a runtime
  term mattered only when costs differ. Nobody runs the same study under both
  units.
- **What it needs:** E2 and E3 run twice, once with an evaluation budget and
  once with a time budget; rank agreement between the two (e.g. Kendall's τ)
  and changed verdicts.
- **Risk:** low. The result is informative either way. If rankings agree, that
  is also worth reporting, but it weakens the paper.
- **Cost:** about 2× the E2/E3 runs.

### N2. A wall-clock map of the best operator on the TSP

**Claim.** For the TSP, the operator with the best gain per second changes
with tour quality at a measurable switch point, and that switch point moves
with instance size and structure.

- **Nearest prior work:** Fialho's OneMax curves (evaluations, toy problem);
  Hansen and Mladenović (only two start conditions, full descents, no
  switch point located); Pei et al. 2022 (per-move best operator in one VRP,
  no time).
- **What it needs:** E1 as planned, with switch points located and plotted
  against n and instance type.
- **Result (E1, 2026-10-19):** the leader changes with the
  local-optimality level, not with the gap, so the "switch point" is a
  level; the order is the same on Euclidean families and differs on random
  matrices. The order itself (VND then ILS) is what the literature
  predicts; what is new is showing it per second and across all stages.

### N4. Does slicing restore momentum?

**Claim.** Pei et al. 2022 found the best operator per move rarely persists
(12–27% of consecutive solutions). Aggregating moves into time slices should
make the leading operator persist longer, which is the condition under
which stateless AOS can work.

- **Nearest prior work:** Pei et al. 2022 (per move, VRP); Pei et al. 2023
  ("Local optima correlation assisted AOS", GECCO 2023; not yet read).
- **What it needs:** in E1 and E2, the probability that the leading operator
  of one slice still leads in the next, as a function of slice length.
- **Risk:** medium; Pei 2023 must be read first.
- **Cost:** low (logged anyway).

### N5. Is distance from the optimum the right state? (Claim 1.3)

**Claim.** Within one instance, the leading operator depends on local
structure (density, local tour quality) as well as on the global state.

- **Nearest prior work:** NeuRewriter (learned "where", no measurement of
  whether the best operator differs by region); Zhang 2014 (by fitness level
  only, distributions not shown).
- **Risk:** medium-high. Anchored moves exist now and E1 records per-node
  rates (`e1main_nodes.csv`, not interpreted yet).

### N6. The fixed schedule in time as a competitor

**Claim.** A tuned time-based schedule (cheap moves until a switch time,
then scans) is the competitor that separates "the best operator changes" from
"adaptation is needed". If it matches the state-based reference, adaptation
is not needed on a fixed implementation. After E1 this competitor takes
the form of the fixed cascade (2-opt → 3-opt → LK → kick).

- **Nearest prior work:** Davis (1989) derived offline time-varying schedules;
  Fialho uses the best *static* mix. Nobody reports a tuned time schedule as
  the competitor to beat.
- **Risk:** low; it is a design contribution inside Claim 1.

### N7. Chained selection with opportunity-cost acceptance

**Claim.** Accepting a chain when cumulative gain minus ρ·t is positive, and
committing its best prefix (LK's gain criterion with time), beats chains
accepted only on a new best.

- **Nearest prior work:** Kheiri and Keedwell 2015, Chuang 2020, LAST-RL 2023.
- **Risk:** high: a method contribution in a crowded area; needs Claim 2
  infrastructure first.

### N8. Operator selection inside LKH-3 (Claim 4)

**Claim.** Choosing move type and kick type between LKH-3 trials, with a
time-based reward, improves LKH-3 on the variants where it trails.

- **Nearest prior work:** the four learning-in-LKH papers learn candidate
  edges, not operators (`lkh3.md`); Gretsista and Burke select ILS kicks with
  a time-normalised reward on nurse rostering.
- **Risk:** high (source changes, licence, strong competitors); the most
  impactful if it works.

### N9. Is the per-second greedy map a good policy? (Claim 1.4)

**Claim.** The operator with the best immediate gain per second is not
always the best first step: deviating from the greedy choice once, then
continuing greedily, gives a shorter tour at a later time in some states.

- **Nearest prior work:** Hansen and Mladenović 2006 (first against best
  improvement change which local optimum is reached); rollout algorithms
  (Bertsekas, Tsitsiklis and Wu 1997, not read). No AOS paper was found
  that measures the regret of the greedy operator choice per state.
- **What it needs:** E1R, rollouts from E1 states
  ([claim-1-plan.md](claim-1-plan.md)).
- **Risk:** medium; informative either way (a stable greedy map supports
  E3's reference arm).

## Decisions needed

- [x] N1 doubles E2/E3: accepted 2026-10-16 (benchmark protocol §5).
- [ ] Read Pei et al. 2023 before committing to N4.
