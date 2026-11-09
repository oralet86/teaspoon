# Instance sets for testing Claim 1.2

Status: **history**. Written 2026-10-16 after an early E1 run (15
operators). Superseded on 2026-10-18 by the 60-instance design
([instances-and-operators.md](instances-and-operators.md) §1), which took
the axes below (random matrix, morphed, clustered, TSPLIB with GEO, three
sizes) but not the shortlist. Kept for the literature on what matters, the
Claim 1.2 criterion, and the shortlist as candidates for larger runs.

## Why

E1 on five Euclidean instances (n = 100–442; uniform, clustered, TSPLIB)
found the same leader sequence everywhere: full-scan 2-opt on random tours,
2-opt descent, single 2-opt moves, 3-opt / Or-opt at 2-opt optima,
perturbations at 3-opt optima. Only *which perturbation* leads at 3-opt
optima differed by instance. Claim 1.2 (the best operator depends on the
instance) is therefore weak so far, but the instance set was narrow. This
note picks instances along the axes the literature says matter.

## Criterion, fixed before looking

Claim 1.2 holds on E1 data if, for some instance group, **the order of
leaders across local-optimality classes differs** from the Euclidean order
above (for example random moves or 3-opt leading in 2-opt-open states, or a
perturbation leading before 3-opt optima), in at least half of that group's
seeds. A difference only in which perturbation leads at 3-opt optima counts
as *weak* support. Differences only in the 2-opt variant (O5 / O7 / O14)
count as Claim 1.1 within a class, not 1.2.

## What the literature says matters

- **Distance structure.** On random distance matrices (no triangle
  inequality, no geometry) local search behaves very differently: 3-opt
  ends about 80% above the Held–Karp bound at large N, against about 3% on
  random Euclidean instances, and Lin–Kernighan cuts the excess by about a
  factor of 10 relative to 3-opt (2/3 on Euclidean). Few improving moves
  exist from good starts. Deeper moves should matter far more there
  (Johnson and McGeoch 1997, sections 3 and 4; local copy
  `papers/TSP-JohMcg97.pdf`).
- **Nearest-neighbour distance variation.** For 2-opt, the key feature
  separating hard from easy instances is the coefficient of variation of
  nearest-neighbour distances (CVND); a second feature concerns the edge-cost
  distribution. Evolved hard instances for chained LK point the other way
  on the same feature (Mersmann et al. 2012, LION 6, "Local Search and the
  Traveling Salesman Problem: A Feature-Based Characterization of Problem
  Hardness"; Smith-Miles, van Hemert and Lim 2010, LION 4).
- **Spatial topology beyond uniform and clustered.** Random uniform
  Euclidean (RUE, DIMACS portgen) and netgen clustered instances are too
  similar for algorithm selection between EAX and LKH. Mutation operators
  (explosion, implosion, cluster, expansion, compression, linear
  projection, grid, rotation) produce instances that look like VLSI data
  and spread much further in feature space, and separate the solvers
  (Bossek, Kerschke, Neumann, Wagner, Neumann and Trautmann, FOGA 2019,
  doi:10.1145/3299904.3340307; generator `tspgen`, an R package).
- **Real-world structure.** Per-instance algorithm selection between TSP
  solvers pays off on mixed benchmarks of random, clustered, morphed and
  real instances (Kerschke, Kotthoff, Bossek, Hoos and Trautmann, Evol.
  Comput. 26(4), 2018, doi:10.1162/evco_a_00215; instance sets not
  re-checked here). Real-world sets: TSPLIB, the VLSI collection (102
  instances, 131 to 744,710 cities) and the National TSPs (about 25
  countries, 29 to 71,009 cities), both on the Waterloo TSP site in TSPLIB
  format.

## Shortlist

All symmetric; the loader (`instances/tsplib.py`) already reads EUC_2D,
CEIL_2D, ATT, GEO and EXPLICIT. "Opt" = optimal tour present locally.

| Axis | Instance | n | Why |
|---|---|---|---|
| Distance: geographic | gr202 (GEO, opt) | 202 | great-circle distances; near-Euclidean control |
| Distance: geographic | gr666 (GEO, opt) | 666 | larger GEO |
| Distance: explicit matrix | brg180 (EXPLICIT, opt) | 180 | non-geometric matrix |
| Distance: explicit matrix | pa561 (EXPLICIT, opt) | 561 | non-geometric matrix |
| Distance: random matrix | portmgen-200, portmgen-500 (DIMACS `portmgen`) | 200, 500 | no triangle inequality; the strongest test |
| Spatial: grid-like | pcb442 (in the reference group), VLSI below | 442 | low CVND, many tied distances |
| Spatial: morphed | tspgen-200 (tspgen, paper settings) | 200 | topologies outside uniform / clustered, incl. grid and line structure |
| Spatial: clustered | netgen-200 (netgen, 5 clusters) | 200 | the NETGEN set's procedure (Kerschke et al. 2018) |
| Real world: VLSI | one of 131–1,000 cities (download) | ~700 | lines and grids |
| Real world: national | uy734 or zi929 (download) | 734/929 | real geography |
| Size | pr1002 (opt), pr2392 (opt) | 1,002, 2,392 | larger, optima known |
| Size, extreme | rl11849 or usa13509 | ~12k | one case only; see caveats |

Existing five instances stay as the reference group.

## Changes to the method

All three were implemented (2026-10-18):

1. **States by local-optimality class instead of gap** (`aos/e1_states.py`).
2. **Anchor sampling** when all anchors do not fit a state's timing budget
   (`aos/e1_marginal.py`, `--budgeted`).
3. **Features per instance**: CVND, tied nearest neighbours, triangle
   violations (`e1main_instances.csv`).

## Caveats

- **Neighbour-list pruning** (O7, O9, O10, O14, O15) relies on "a new edge
  shorter than the removed one" being a useful filter; on random matrices
  it is still valid for 2-opt's gain criterion but neighbour lists carry
  less information. That is part of what the test measures, not a bug.
- **Array tours above ~10k cities**: every 2-opt move and kick rewrites
  O(n) positions; Johnson and McGeoch switch to two-level lists there. At
  12k the measurement mixes Claim 1.2 with the tour representation, so
  it is one extreme case, reported separately.
- The in-house grid and mixed generators were dropped on 2026-10-18 and
  their instances deleted: E1 runs before that date used `uniform200-s0`
  and `clustered200-s0`, which are no longer reproducible.

## Proposed run

Not run. If larger or real-world instances are wanted after E1: measurement
A on the shortlist (about 15 instances), 3–4 seeds each, sized to about one
hour on one pinned core.

## Sources

- Johnson, McGeoch (1997), The TSP: a case study in local optimization —
  `papers/TSP-JohMcg97.pdf`.
- Mersmann et al. (2012) — https://cs.adelaide.edu.au/~markus/pub/2012lion.pdf
- Bossek et al. (2019), FOGA — https://cs.adelaide.edu.au/users/markus/pub/2019foga-tspInstances.pdf ; code https://github.com/jakobbossek/tspgen
- Smith-Miles, van Hemert, Lim (2010), Understanding TSP difficulty by
  learning from evolved instances — https://findanexpert.unimelb.edu.au/scholarlywork/1231777-understanding-tsp-difficulty-by-learning-from-evolved-instances
- Kerschke et al. (2018), Leveraging TSP solver complementarity through
  machine learning — https://ris.uni-paderborn.de/record/46352
- van Hemert (2005), evolving hard instances for Lin–Kernighan — https://arxiv.org/abs/cs/0502096
- DIMACS TSP Challenge generators and instance types — https://dimacs.rutgers.edu/archive/Challenges/TSP/download.html
- Waterloo TSP data (VLSI, National) — https://math.uwaterloo.ca/tsp/vlsi/index.html , https://www.math.uwaterloo.ca/tsp/world/countries.html
