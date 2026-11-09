# Greedy vs softmax operator choice

Status: idea, for E3 (Claim 1) and Claim 2. Written 2026-10-17 from a
discussion; no code yet.

## The idea

E1 measures, for every search state, each operator's improvement per
second. The obvious policy built on that map is **greedy**: always use the
operator with the highest rate in the current state. Greedy is myopic, so it
may be the wrong way to use the map.

Alternative: choose operators **at random, weighted by their rates**
(softmax). Better operators are chosen more often but not always, so a kick
is sometimes used while 2-opt still works. The hypothesis: this reaches
good tours faster than greedy.

Test: solve the same problems with the greedy policy and with the softmax
policy, at equal time. If the hypothesis is wrong, greedy should be at
least as good everywhere.

## What E1 already says

- E1 **measures, it does not choose**: measurement A estimates every
  operator's rate from a fixed tour, and "leader" is only a label for the
  highest. The map is valid for any policy. The question is how to *use*
  it.

## Suggested design

1. **One dial from greedy to random.** Choose operator i with probability
   proportional to exp(s_i / tau):
   - tau -> 0 is greedy;
   - tau -> infinity is uniform random choice (already a planned
     competitor);
   - several values in between give a curve. Best quality at an
     intermediate tau means greedy is beaten; best at tau -> 0 means greedy
     is right. Either result is informative.
2. **Score on a log scale.** Rates span orders of magnitude (about 1e8 vs
   1e3 per second); a softmax on raw rates is greedy for any sensible tau.
   Use s_i = log(rate_i), rates relative to the best, or probability
   proportional to rate_i^beta (beta = 1 is plain probability matching).
3. **Related work.** Probability matching (choose in proportion to
   quality) vs adaptive pursuit (mostly winner-take-all) is a classic AOS
   comparison (Thierens 2005); this design is that comparison with E1's
   per-state rates as the quality, and tau / beta as the dial.
4. **Fair knowledge for both policies.** Measuring rates during a run costs
   seconds per state. Both policies use E1's map as a lookup table: detect
   the current local-optimality level cheaply during the run (e.g. "the
   last 2-opt sweep found nothing", "the last Or-opt / 3-opt sweep found
   nothing"), then look up that level's rates for the instance type. Neither
   policy is charged for the table (it is the reference-arm knowledge of
   `claim-1-plan.md`).
5. **Decision unit.** Choosing per single application costs about 1 us
   per decision for 1 us moves in Python. Use a fixed batch of applications
   (or one application for expensive operators) per decision, and charge the
   decision time. This is the open decision-unit question of the protocol.
6. **Kicks before local optima are not pure waste**: a kick contains its own
   descent, so it explores and repairs at once. Whether that pays is what
   the tau curve shows.

## How to judge it

- **Not "solve twice".** One run per policy can be luck. Use about 30 paired
  seeds per instance (same start tours for every policy), several instances
  from different groups (Euclidean, random matrix, explicit, large), and
  test the paired differences (Wilcoxon signed-rank, Holm across
  comparisons), as in `benchmark-protocol.md`.
- **Measure "faster to good tours" concretely**: gap at fixed times, time to
  reach 2% / 1% gap, and the primal integral (area under gap over time, one
  number for "good sooner"). Most runs will not reach the optimum.

## Proposed pilot (on the E1 main-run map)

Equal time budget, 30 paired seeds, a few instances per group. Arms:

| Arm | What it does |
|---|---|
| Greedy by level | best-rated operator for the current level (tau -> 0) |
| Softmax, 3–4 temperatures | weighted random choice by log-rate |
| Uniform random | tau -> infinity |
| Fixed cascade | 2-opt until stuck, then Or-opt / 3-opt, then LK, then kick |
| Best single operator | per instance |

Outputs: anytime gap curves per arm, time-to-target, primal integral,
paired differences with intervals. This tests the hypothesis (greedy vs
softmax) and Claim 1 itself (choosing by state against random choice and
the fixed cascade) in one experiment.

## Robustness check: does the map depend on how snapshots were made?

E1's snapshots (states) come from greedy-ish searches: descents that always
take improving moves (2-opt, then Or-opt, 3-opt, LK), and kick-based ILS.
A softmax or random policy would pass through somewhat different tours.
Check: build a second state set from runs of the softmax and uniform
policies, captured at the same local-optimality levels, and compare the
leader map with the greedy-built one (share of states with the same
leader per level and instance group). Weak evidence so far, from the
archived runs of 2026-10-16/17 (earlier code and instances): states from
uniform-choice population runs and from descents gave the same leading
family in 281 of 318 paired states (88%).
