# Instances, generators, tours and operators

Reference for the experimental setup as of 2026-10-19: where every instance
comes from, how the tours that the operators are measured on are built, how
seeds work, what the 20 operators do, and how they are measured. This is the
one place these are described; other notes link here. How to run the
generators is in the README.

Citations marked † were checked against the source in this session; the
others are as cited in the code's documentation and should be checked
before they go into the paper.

## 1. Instances of the main E1 run

60 symmetric TSP instances, 20 per size, 4 per cell. Every instance has a
known optimum, and alpha-nearness candidate lists (5 per city) are cached
in `data/cache/candidates/`.

| Family | n ≈ 100 | n ≈ 200 | n ≈ 500 | Generator | Optimum |
|---|---|---|---|---|---|
| Uniform | `portgen-100-{0..3}` | `portgen-200-{0..3}` | `portgen-500-{0..3}` | DIMACS `portgen` | Concorde |
| Clustered | `netgen-100-{0..3}` | `netgen-200-{0..3}` | `netgen-500-{0..3}` | netgen, 5 clusters | Concorde |
| Random matrix | `portmgen-100-{0..3}` | `portmgen-200-{0..3}` | `portmgen-500-{0..3}` | DIMACS `portmgen` | Concorde |
| Morphed | `tspgen-100-{0..3}` | `tspgen-200-{0..3}` | `tspgen-500-{0..3}` | tspgen | Concorde |
| TSPLIB | kroA100, eil101, lin105, gr96 | d198, kroA200, tsp225, gr202 | pcb442, d493, att532, ali535 | Reinelt (1991) | `.opt.tour` or `solution.txt` |

- Generated instances: `data/generated/tsp/` (48 `.tsp` and their Concorde
  `.opt.tour`). TSPLIB: `data/ALL_tsp/`.
- The instance name is `<generator>-<n>-<seed>`; the seed is the
  generator's own seed (section 3).
- Figures of every instance: `journal/figures/claim-1/instances/`
  (`aos/analysis/instances.py`).

## 2. Generators and their configuration

Entry point: `uv run python -m generators.tsp --kind <kind> --n <n>
--seed <first seed> --count <k> --out <dir> [--solve]`. All point instances
lie in [0, 10^6]^2 and use TSPLIB `EUC_2D` (distances rounded to the
nearest integer).

### 2.1 Uniform: DIMACS `portgen`

- **Source:** 8th DIMACS Implementation Challenge, TSP part; C code by
  Lyle McGeoch after Johnson and Bentley, vendored in `vendor/dimacs/`
  from https://dimacs.rutgers.edu/archive/Challenges/TSP/download.html †.
- **Paper:** D. S. Johnson, L. A. McGeoch, "Experimental analysis of
  heuristics for the STSP", in G. Gutin, A. P. Punnen (eds.), *The
  Traveling Salesman Problem and Its Variations*, Kluwer, 2002.
- **What it does:** n integer points, each coordinate uniform on
  0..10^6 − 1, from Knuth's portable subtractive random number generator
  seeded with the integer seed (`sprand(seed)`) †. These are the
  Challenge's `E` instances.
- **Configuration:** none besides n and seed. The output (already TSPLIB)
  is written byte for byte (`generators/dimacs.py`; a test checks it).
- **Build:** `uv run python -m generators.dimacs` (`cc -O -fwrapv`).

### 2.2 Clustered: netgen

- **Source:** R package netgen 1.4.0 (J. Bossek), installed with tspgen;
  function `generateClusteredNetwork`.
- **Paper:** P. Kerschke, L. Kotthoff, J. Bossek, H. H. Hoos,
  H. Trautmann, "Leveraging TSP solver complementarity through machine
  learning", *Evolutionary Computation* 26(4), 2018, section 3.3 † (the
  NETGEN set; procedure from Meisel et al. 2015).
- **What it does** (package defaults = the paper's procedure †): cluster
  centres from a maximin Latin hypercube sample on [0, 100]^2; cities
  split equally over the clusters; each cluster a bivariate normal around
  its centre with the distance to the nearest other centre as variance
  in each dimension; out-of-bounds points mirrored back.
- **Configuration:** **5 clusters** (the paper used 2, 5 and 10). Generated
  on [0, 100]^2 as in the paper, then multiplied by 10^4; the variance is
  not scale-free, so generating directly on [0, 10^6]^2 would give clusters
  about 100 times tighter. Coordinates written with six decimals
  (`generators/netgen.R`, `generators/netgen.py`).
- **Not used:** DIMACS `portcgen` (clustered), because it makes n/100
  clusters, i.e. 1 to 5 at our sizes.

### 2.3 Random distance matrix: DIMACS `portmgen`

- **Source and paper:** as 2.1 (the Challenge's `M` instances) †.
- **What it does:** a symmetric matrix whose entries are a hash of the two
  city indices and the seed, scaled to 0..10^6 − 1; no geometry, no
  triangle inequality (about 16–17% of sampled triples violate it).
  Written as `EXPLICIT` / `UPPER_DIAG_ROW`.
- **Configuration:** none besides n and seed. Built with `-fwrapv` because
  the hash relies on signed integer wrap-around †. A check on
  `portmgen-200-0` found no correlation between neighbouring entries
  (0.01, the same as after relabelling the cities).

### 2.4 Morphed: tspgen

- **Source:** R package tspgen 1.0.0 (J. Bossek,
  https://github.com/jakobbossek/tspgen), called through
  `generators/tspgen.R`.
- **Paper:** J. Bossek, P. Kerschke, A. Neumann, M. Wagner, F. Neumann,
  H. Trautmann, "Evolving diverse TSP instances by means of novel and
  creative mutation operators", *FOGA 2019*, doi:10.1145/3299904.3340307 †.
- **What it does** (Alg. 1 of the paper †): n points uniform on [0, 1]^2,
  then 1,000 iterations, each applying one mutation operator chosen
  uniformly from the collection; points pushed outside the square are
  re-placed uniformly at random; duplicate points are relocated.
- **Configuration:** `iters = 1000` (the paper's TSPGEN set †); collection
  `init("sophisticated")`: explosion, implosion, rotation (pm 0.1),
  cluster (pm 0.1), expansion, compression, axis projection and linear
  projection (pm 0.1, jitter probability 0.5, jitter sd 0.05), grid
  (pm 0.1, jitter 0.5 / 0.05, rotation probability 0.5). The paper says
  TSPGEN uses the sophisticated operators but does not list the exact
  collection; the package preset is our reading of it. Scaled to
  [0, 10^6]^2 with `upper`, written by netgen with six decimals.

### 2.5 TSPLIB

- **Paper:** G. Reinelt, "TSPLIB — a traveling salesman problem library",
  *ORSA Journal on Computing* 3(4), 1991.
- **Selection:** per size three Euclidean instances and one geographic
  (`GEO`) one, all with a known optimum: from a companion `.opt.tour`, or
  else from `data/ALL_tsp/solution.txt` (`aos/problem.py`,
  `listed_optimum`).

### 2.6 CVRP (not used in E1)

`python -m generators` wraps the official CVRPLIB XML100 generator
(Uchoa et al., "New benchmark instances for the capacitated vehicle routing
problem", *EJOR* 257(3), 2017), vendored at `vendor/xml100/generator.py`
(`generators/cvrp.py`).

## 3. Seeds

| Seed | Where | Effect |
|---|---|---|
| Instance seed 0–3 | `generators.tsp --seed` | DIMACS: `sprand(seed)`; netgen and tspgen: R `set.seed(seed)` (Mersenne-Twister). Same seed, same instance; a test checks it. R's random stream may change between R versions, so R, tspgen and netgen versions are written into each instance's `COMMENT`. |
| Concorde seed 0 | `generators.tsp --solve` | Fixed, so the optimal tour written is reproducible. |
| State seed 0–11 | `aos.e1_states --seeds` | Builds one set of tours per (instance, seed); see section 4. |
| Measurement seed 0 | `aos.e1_marginal` settings | Random choices inside the operators during measurement A. |

Our instance seeds (0–3) are not the Challenge's (1000, 10001, …), so
`portgen` and `portmgen` instances are generated the same way as the
Challenge's but are not the Challenge files themselves.

## 4. How the measured tours (E1 states) are built

`uv run python -m aos.e1_states --instances … --seeds 0 … 11 --workers 6
--out <run dir>` (untimed, in parallel; `aos/e1_states.py`). For every
(instance, state seed) it writes `states/<instance>_s<seed>.json` with 12
tours, each with its length and gap to the optimum:

| State | How it is made |
|---|---|
| `random` | Random permutation (`random.Random("e1-states:<name>:<seed>")`) |
| `nearest_neighbour` | Nearest-neighbour tour from city `seed mod n` |
| `d25`, `d50`, `d75`, `d90` | O7 (neighbour-list 2-opt) descent from the nearest-neighbour tour, stopped once it has gained 25/50/75/90% of a full descent's gain |
| `opt2` | Full O7 descent: a 2-opt local optimum |
| `oropt` | VND with O7 and O9 (Or-opt) from `opt2` |
| `opt3` | VND with O7, O9, O10 (sequential 3-opt) from `oropt` |
| `lkopt` | VND adding the LK step on the 5 alpha-nearness candidates (O18) from `opt3`: an LK optimum |
| `ils1`, `ils2` | `lkopt` after n, then 4n further O8 kick trials (keep if shorter), each followed by the same LK VND |

- The descents visit cities in a random order drawn from the same seed, so
  seeds differ beyond their starting tour. Kicks use their own stream
  (`"e1-states-ils:<name>:<seed>"`).
- A state identical to an earlier one is kept but marked (`duplicate_of`)
  and skipped by the measurement; common for `ils1`/`ils2`, where the kicks
  find nothing better.
- Every tour is checked (permutation, positions, recomputed length) before
  it is written.
- Main run: 60 × 12 = 720 files, 8,640 states, 7,676 unique, in
  `runs/e1-main/20261018-144422/states/`; one figure per file in
  `runs/e1-main/20261018-144422/figures/`.
- Neighbour lists: 10 nearest cities per city (`aos/problem.py`). Alpha
  candidates (Helsgaun 2000): 5 per city from a subgradient-optimised
  1-tree, ordered by distance (`aos/candidates.py`).

## 5. Operators (O1–O20)

**Retired 2026-11-05: O2, O3, O4, O6 and O20.** In the 2026-11-02 Rust
measurement A (1,280 states) each led under 1% of the states and was among
a state's five best rates in under 5% (O2, O4 and O20 never led). Their
code is removed from `rust/`; the other operators keep their numbers, so
the Rust set is O1, O5, O7–O19 and O21 (16 operators). The rows below stay
as the description of `aos/operators.py` and of the runs made before the
retirement. Rule and figures: [`e1r-overnight-plan.md`](e1r-overnight-plan.md).

All in `aos/operators.py`, scalar Python, registered in `OPERATORS` in this
order (O-numbers = position). Shared contract (`Operator` protocol):

- **One application** acts on a tour in place, starting from an **anchor**
  city (given, or drawn uniformly from the operator's random stream), and
  returns `(delta, anchor)`.
- **Only improvements are kept.** Moves (O1–O7, O9, O10, O14–O18, O20)
  apply a move only if it shortens the tour; trials (O8, O11–O13, O19)
  build a candidate and keep it only if shorter. A non-improving
  application leaves the tour exactly as it was, so measurement A needs no
  copy for it. Improvements below `EPSILON = 1e-9` are ignored.
- **Deterministic given the anchor:** O5, O6, O7, O9, O10, O14, O16, O17,
  O18, O20 (`DETERMINISTIC`); enumerating every anchor gives their rate
  exactly, up to timing noise.
- **Counted work** (`aos/work.py`): distance lookups and tour positions
  rewritten, logged beside the timer as a cross-check.
- **Tour representation:** an array (`order`) plus a position index
  (`pos`); a reversal rewrites the shorter side, so O(n) per flip in the
  worst case (`aos/tour.py`).
- **Candidate sets:** the 10 nearest cities per city ("neighbour list"),
  or the 5 alpha-nearness candidates (Helsgaun 2000) for O17/O18.
  Neighbour-list searches stop at the first neighbour whose new edge is not
  shorter than the removed one (Johnson and McGeoch 1997).

| ID | Name | Move | Acceptance | Family | Source (as cited in the code) |
|---|---|---|---|---|---|
| O1 | `swap` | exchange the anchor with a city at a random position | if shorter | swap | — |
| O2 (retired) | `insertion` | move the anchor between two random adjacent cities | if shorter | insertion | — |
| O3 (retired) | `inversion` | reverse a segment of random length starting at the anchor (random 2-opt move) | if shorter | inversion | — |
| O4 (retired) | `or_opt` | move the 1–3-city segment starting at the anchor to a random edge, better orientation | if shorter | or_opt | Or-opt |
| O5 | `two_opt_first` | first improving 2-opt move at the anchor edge, full scan of the tour | first improvement | 2-opt | — |
| O6 (retired) | `two_opt_best` | best improving 2-opt move at the anchor edge, full scan | best improvement | 2-opt | — |
| O7 | `two_opt_nl` | first improving 2-opt move at the anchor, over its neighbour list | first improvement | 2-opt | Johnson & McGeoch 1997 |
| O8 | `kick` | segment exchange X Y R → R Y X (a pure sequential 3-opt move) at the anchor's edge and two random edges, then O7 descent from the touched cities | trial, keep if shorter | kick | — |
| O9 | `or_opt_nl` | first improving Or-opt move of the 1–3-city segment at the anchor, reinserted next to one of its neighbours, either orientation | first improvement | or_opt_nl | Johnson & McGeoch 1997 |
| O10 | `three_opt_nl` | first improving sequential 3-opt move removing an edge at the anchor, LK gain criterion over neighbour lists (an LK step of depth 3; includes 2-opt closures) | first improvement | three_opt_nl | Johnson & McGeoch 1997 |
| O11 | `ruin_recreate` | radial ruin of the anchor and its nearest neighbours (10 cities), greedy reinsertion in random order at the cheapest nearby edge | trial, keep if shorter | ruin_recreate | Schrimpf et al. 2000 |
| O12 | `double_bridge` | double bridge (4 edges: S0 S3 S2 S1) at the anchor's edge and 3 random edges, then O7 descent from the 8 cut cities | trial, keep if shorter | kick | Martin, Otto & Felten 1991; Johnson & McGeoch 1997 |
| O13 | `double_bridge_local` | double bridge at the anchor's edge and the edges of 3 random neighbour-list cities, then O7 descent | trial, keep if shorter | kick | Applegate, Cook & Rohe 2003 |
| O14 | `two_opt_descent` | O7 at the anchor, then at every city an applied move touched, until the work queue is empty (the kicks' repair without the kick) | first improvements, many per application | 2-opt | Bentley 1992 (don't-look bits) |
| O15 | `two_opt_targeted` | O7 at the city with the worst local edge among the anchor and 7 random cities (tournament of 8; score = longer tour edge − nearest-neighbour distance) | first improvement | 2-opt | — (designed from E1 node data) |
| O16 | `lin_kernighan` | LK step from the anchor built from 2-opt flips: up to 25 flips, breadth (4, 3, 3, 2, 1) per level, gain criterion, first improving closing committed, both directions | first improvement | lin_kernighan | Lin & Kernighan 1973; Helsgaun 2000 |
| O17 | `two_opt_alpha` | O7 over the 5 alpha-nearness candidates instead of the 10 nearest | first improvement | two_opt_alpha | Helsgaun 2000 |
| O18 | `lin_kernighan_alpha` | O16 over the 5 alpha-nearness candidates (the LKH combination) | first improvement | lin_kernighan_alpha | Helsgaun 2000 |
| O19 | `string_removal` | SISR-style removal of 1–3 strings of 1–10 cities (the anchor's, then neighbours'), reinsertion in random order at the cheapest candidate position with blink probability 0.01 | trial, keep if shorter | string_removal | Christiaens & Vanden Berghe 2020 |
| O20 (retired) | `segment_exchange` | first improving exchange of the 1–3-city segment at the anchor with a 1–3-city segment at one of its neighbours, each in its better orientation | first improvement | segment_exchange | neighbour-list exchange of FILO and HGS-CVRP |

Notes:

- **Families** (`FAMILIES`): O5, O6, O7, O14, O15 are one 2-opt family and
  O8, O12, O13 one kick family; operators of one family make the same move
  or the same kick-plus-descent trial, so their rates are nearly equal by
  construction. The slice-based E1 runs of 2026-10-16 therefore tested the
  leader only against other families; measurement A compares it with the
  runner-up of any family.
- **The kicks contain a descent.** O8, O12 and O13 repair with an O7
  descent from the cut cities; O14 is that descent alone, so a kick leading
  can be told apart from "a work queue beats a random anchor".
- **Trials use an overlay.** O11 and O19 keep changes in successor and
  predecessor links, so a rejected trial costs O(removed · k) and only an
  accepted one rewrites the array.
- **Representation costs.** O8, O12, O13 and O20 rebuild the O(n) array
  when applied; on large instances this, not the move, dominates. Results
  are therefore reported for this implementation ("Implementation" under
  the ground rule in [claims.md](claims.md)).
- **Parameters** (module constants): `RUIN_SIZE = 10`, `TOURNAMENT = 8`,
  `LK_DEPTH = 25`, `LK_BREADTH = (4, 3, 3, 2, 1)`, `STRING_MAX = 10`,
  `STRING_BLINK = 0.01`, `MAX_SEGMENT = 3`; neighbour lists of 10, alpha
  lists of 5.
- **Planned:** O21, a kick repaired by LK instead of O7 (chained LK), the
  operator E1z found missing on random matrices
  ([rust-port.md](rust-port.md)).

### 5.1 How the operators are measured (measurement A)

`aos/marginal.py`, driven by `aos/e1_marginal.py`. Every operator is
applied at every city as anchor (or at a seeded sample of anchors when the
state's timing budget is short), each application starting from the same
state tour; an improving application is undone, untimed. Since 2026-10-18
in two stages (main run: `--budgeted --state-seconds 7.5 --repeats 32`):

1. **Gains** (untimed, every CPU in parallel): deterministic operators
   once per anchor, the others `repeats` times (32 in the main run); each
   application has its own random stream, so it can be replayed alone. Records gain, work and
   a CRC of the improved tour, plus the state's exact local-optimality
   flags.
2. **Times** (one pinned core, nothing else running, garbage collector
   off): rounds replay every (operator, anchor) and time each application;
   round r replays repeat r mod `repeats`. Every replay must reproduce its
   gain and tour, or the run stops. Rounds stop once every deterministic
   operator's mean time is known within 5% (95% interval over round
   means), after 2 to 8 rounds.

Only timing is kept on one core because parallel timed workers slow the
operators unevenly, also at a fixed clock (E0, 2026-10-18: up to 23% at 7
workers, most likely the shared L3 cache). The collector is off while
timing because its collections ran inside whichever application crossed
the allocation threshold (a third of `string_removal`'s time at n = 500,
8–15% of LK's); times therefore exclude all collector work.

The rate is mean gain (stage 1) over mean time (stage 2). Interval: a
cluster bootstrap over anchors when they are sampled, else a stratified
bootstrap (gain repeats and timed rounds resampled within each anchor),
1,000 resamples. A state is "separated" when the leader's 95% interval does
not overlap the runner-up's (the second-best operator of any family).
Anchors: all cities when the 7.5 s timing budget per state allows, else a
seeded sample of at least 32.
