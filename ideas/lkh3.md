# LKH-3

Background, the paused setting pilot and its preliminary result. This is
the route to Claim 4.

## Background

### Where LKH-3 trails specialised solvers

LKH-3 trails on the multi-vehicle capacity and time-window family, not on
every variant. On CVRP the gap is about tenfold and is reported by two
independent comparisons.

| Benchmark and budget | LKH-3 mean gap | Best specialised solver | Source |
|---|---|---|---|
| 100 X instances, 240 s per 100 customers, 10 runs | 1.00% | HGS-CVRP 0.11% | Vidal, *Hybrid genetic search for the CVRP* (2022) |
| 100 X instances, 10 minutes, 50 runs | 1.61% | AILS-II 0.08%, HGS-CVRP 0.15% | Queiroga et al., *The XL instances for the CVRP* (January 2026) |
| XML100 instances, 1 minute, 10 runs | 0.479% | HGS-CVRP 0.003% | Same report |
| 100 XL instances (1,000 to 10,000 customers), 2 hours, 60 runs | 6.66% | AILS-II 0.07%, FILO2 0.21% | Same report |

The XL report adds that LKH-3 found no feasible solution on 10 XL instances,
and that it was run with default parameters and was not designed for that
scale. For time windows, the PyVRP paper states that LKH-3 "is currently not
state-of-the-art for CVRP and VRPTW", without LKH-3 numbers.

Helsgaun's LKH-3 report (2017) counts how often LKH-3's best result was
worse than the best known solution at the time:

| Problem type | Instances | Worse than best known | Share |
|---|---|---|---|
| VRPSPD (simultaneous pickup and delivery) | 266 | 98 | 37% |
| PDTSPF (pickup-delivery TSP, FIFO loading) | 77 | 28 | 36% |
| CVRP | 509 | 138 | 27% |
| CVRPTW | 405 | 101 | 25% |
| CCVRP (cumulative) | 164 | 37 | 23% |
| mTSP | 127 | 4 | 3% |
| SOP (sequential ordering) | 163 | 4 | 2% |
| TSPTW | 900 | 0 | 0% |
| TRP (traveling repairman) | 130 | 0 | 0% |

The counts are from 2017 and reflect LKH-3's best runs, so today's picture
is likely worse for LKH-3. The pattern stands: it struggles where several
vehicles interact with capacity, load or time windows, and holds up on
single-route problems and the unconstrained mTSP.

### Learning inside LKH-3

Four papers modify LKH-3 with learning and report gains on variants. All
four learn which candidate edges to use. None selects between search
operators.

| Paper | What is learned | Variants tested | Result against plain LKH-3 |
|---|---|---|---|
| Zheng et al., Reinforced LKH (VSR-LKH-3) | Q-values replace the fixed ordering of candidate edges, online | TSPTW, coloured TSP | TSPTW average solution: better on 27, tied on 54, worse on 4. 12 new best coloured-TSP solutions. |
| Wang et al., bandit candidate selection (bandLKH-3, 2025) | A bandit per city picks 5 of 7 candidate edges each iteration, online | mTSP, CVRP | CVRP on the 100 X instances, average solution: better on 61, worse on 35. mTSP on 82 instances: better on 47, worse on 19. |
| Wang et al., bandit and backbone (MABB-LKH-3, 2025) | A bandit picks the mix of backbone, α-value and distance used to rank edges, online | Coloured TSP, CVRPTW | CVRPTW (Solomon and Homberger, 48 groups), best solution: better on 25 groups, worse on 11. |
| Xin et al., NeuroLKH (NeurIPS 2021) | A network trained offline outputs edge scores and node penalties | CVRP, pickup-delivery, CVRPTW | Improvements over LKH reported as significant at 99% for most sizes and time limits. |

Outside LKH, Lu et al., *A Learning-based Iterative Method for Solving VRPs*
(ICLR 2020) has an RL controller choose among improvement operators and
reaches 15.57 on CVRP-100 against 15.65 for LKH-3.

What these leave open:

- **Operator level.** No paper was found that selects LKH-3's move type, kick
  type or tour-merging method during a run. This rests on a few searches.
- **Breadth.** Each LKH-3 paper tests two or three variants.
- **Size of the gain.** The methods beat LKH-3 on about a third to 60% of
  instances and lose on up to a third. None compares against HGS or another
  specialised solver on CVRP or CVRPTW. "Improves LKH-3" is supported;
  "makes LKH-3 competitive with specialised solvers" is not.

### Offline tuning, the nearest competitor on LKH

- **Liu, Zhang, Tang and Yao, "How Good Is Neural Combinatorial
  Optimization?" (arXiv 2209.10913)** tune LKH with SMAC on uniform, clustered
  and mixed TSP instances. The tuned LKH is clearly better than default LKH
  on clustered instances, and the authors warn that it overfits its training
  distribution. So the best fixed setting depends on instance structure; it
  is TSP only.
- **Pushak and Hoos (PPSN 2018; GPS, GECCO 2020)** report that the parameter
  landscapes of six SAT, MIP and TSP solvers were statistically
  indistinguishable from uni-modal. If that holds for LKH, a cheap offline
  tuner finds a good fixed setting, which raises the bar for an online
  method. Whether LKH is among the six is not confirmed.

### Why operator selection is mechanically possible in LKH-3

- **One search loop.** LKH-3 turns all its problem types into a symmetric TSP
  and runs the same k-opt search. Constraints enter only through a penalty
  that is compared before cost, so a selector in that loop works for every
  variant.
- **One hand-picked setting for everything.** The LKH-3 report recommends a
  single fixed configuration for every penalty-based variant: the special
  5-opt move, one double-bridge kick, no higher-order moves, population 10.
- **A reason selection could matter.** The report says penalty evaluation
  costs O(N) per move and that high-order moves "will often be too time
  consuming". The best trade-off between cheap and expensive moves should
  then differ by variant and by search phase. This is a hypothesis.

What would have to be designed: a reward on the (penalty, cost) pair, credit
for kicks whose benefit shows only after the next local search, a reward per
unit of time, and changes to the C source, since move and kick types are
read once from the parameter file. Operators can probably be switched
between trials but not inside a Lin-Kernighan move chain; this has not been
checked against the source.

## Setting pilot (paused)

Paused on 2026-10-12 in favour of the GA study. This is the route to Claim 4.

LKH-3 fixes its search settings (move type, kick type, recombination method)
once per run, and cannot switch during a run without a source change. The
pilot asks a narrower question first: does the best fixed setting change from
instance to instance? If one setting wins everywhere there is nothing to
adapt. How to run it is under [LKH-3 setting pilot](../README.md#lkh-3-setting-pilot).

- **Suite (`bench/lkh_pilot_1h.json`):** 8 CVRP, 12 CVRPTW, 10 SOP and 5 TSP
  instances, 3 seeds, 420 runs. Mid-size instances get 20 s, large ones 45 s.
- **Arms:** move type 3 or 5, crossed with kick type 0 or 4 on the TSP and
  kick type 4 or 6 on the constrained types.
- **Left out:** TSPTW and mTSP, where LKH-3 is already at best-known.

### Why the suite looks like this (feasibility checks, one seed)

- **CVRP, X with 317 to 876 customers, 20 s:** kick type 4 reaches a valid
  solution on almost every instance; kick type 0 rarely does, so kick type 6
  replaced it on the constrained types.
- **CVRP, XL with 1,421 to 2,867 customers, 45 s:** no arm reaches a valid
  solution. Removed.
- **CVRPTW:** the 200-customer Homberger instances are valid in all runs; the
  400 to 800 ones mostly are not. Only 200-customer ones are used.
- **SOP:** all runs valid, with gaps of 4 to 51% at these budgets.
- **TSP:** all tours valid. rl5915, pla7397 and rl11849 spend 25 s, 44 s and
  more than 65 s in preprocessing and do not fit the budgets. Removed.
- **Independent check:** every TSP and CVRP run LKH-3 called feasible also
  passed the Python re-check (coverage, capacity, recomputed cost).

### Preliminary result (stopped run, `runs/lkh-pilot-1h`)

Stopped at 71 of 105 blocks, which is two full seeds of every instance.

| Type | Verdict | Basis |
|---|---|---|
| CVRP | Winner changes | 2 clear wins for move 3 / kick 4 and 2 for move 5 / kick 4, of 7 instances |
| CVRPTW | Winner changes | the same 2 against 2, of 9 instances |
| SOP | One setting leads | only move 5 / kick 6 wins clearly (4 of 10) |
| TSP | Within noise | one clear win of 5 |

- The within-type evidence is the split between move types 3 and 5 on CVRP
  and CVRPTW. Kick type 6 never wins clearly there.
- Between types the leading setting differs (kick 4 on the routing types,
  kick 6 on SOP). A fixed setting per type covers that.
- Move type 3 completed several times more trials than move type 5 in the
  same time: the same cheap-and-many against expensive-and-directed trade-off
  as in the GA study.

## To do

- [ ] Agree the direction with the supervisor.
- [ ] Run the prepared pilot once (about 20 minutes on 12 jobs, 29 on 8).
- [ ] Re-read the pilot traces: does the leading setting change during a run?
- [ ] Get the LKH-3 source (`LKH-3.0.14.tgz` on Helsgaun's site; only the
      binary is in `vendor/`) and confirm settings can be switched between
      trials.
- [ ] Read the LKH licence text. Ask Helsgaun whether a modified version or a
      patch may be shared.
- [ ] Find a VRPTW comparison of LKH-3 against HGS or PyVRP with numbers.
- [ ] Read the full text of the ALNS review (Turkeš et al.) to learn when
      adaptiveness did help.
- [ ] Check whether Pushak and Hoos included LKH in their tuning study.
- [ ] Statistical tests for comparing arms over many runs.

## Caveats

- **Figures through a summarising tool.** Exact figures from the HGS,
  VSR-LKH-3, bandLKH-3 and MABB-LKH-3 papers should be checked against the
  PDFs before citing.
- **No time-window head-to-head.** No table comparing LKH-3 with HGS or PyVRP
  on VRPTW was found; that claim rests on the PyVRP statement and the 2017
  counts.
- **PDPTW is left out.** Its row in the LKH-3 report does not add up (3
  better, 180 equal and 139 worse for 222 instances).
- **Coloured-TSP counts for MABB-LKH-3 are left out.** The table looked
  misaligned in the HTML version of the paper.
- **Licence.** The LKH distribution states academic and non-commercial use.
  Ask Helsgaun before releasing anything other than a patch.
- **Competitor code.** Whether VSR-LKH-3, bandLKH-3 or MABB-LKH-3 publish
  source code was not checked.
- **A weighted-sum reward conflicts with LKH-3.** LKH-3 compares penalty
  first and cost second, so a reward that adds them with weights is one
  option, not an established choice.
