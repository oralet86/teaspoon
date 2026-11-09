# Learned candidate selection per level (Claim 5)

Status: idea, written 2026-10-27; to be built after the Rust port. The claim
itself is in [claims.md](claims.md#claim-5-a-learned-candidate-selector-per-level).

## The idea

The greedy E1 choice gives a cascade: a 2-opt variant while 2-opt
improves, then 3-opt or LK, then LK or a kick, then kicks (Claim 1.1). Each
level is only "exhausted" relative to the candidate set its operator
searches (Claim 1.6): a tour can be optimal for neighbour-list 2-opt (O7)
and still be improvable by the full 2-opt scan (O5).

For each level, train a selector that predicts which nodes the *full*
version of that level's operator would choose, and give the cheap operator
the top N of them as its candidate list. The aim: the cost of
neighbour-list search with the gains of the full scan.

## What E1 already says

In the 439 E1 states that O7 cannot improve but O5 can (median over
states, from `e1main_cells.csv`):

| Operator | P(improve) per anchor | Gain per application | µs per application | Gain per second |
|---|---|---|---|---|
| O7, 10 nearest | 0.000 | 0 | 1.1 | 0 |
| O17, 5 alpha candidates | 0.002 | 0.9 | 1.0 | 1.1·10⁶ |
| O5, full scan | 0.020 | 263 | 21.7 | 7.0·10⁶ |

The missed moves are rare (2% of anchors) and large (long edges between
clusters; 290 of the 439 states are clustered or tspgen instances). So a
selector has two jobs, and the first may matter more: choose *where*
(which anchors), and choose *with whom* (which partner).

## Design points

1. **Target.** For 2-opt and Or-opt, label every (tour, anchor) with the
   partner the best-improvement scan (O6) picks; O(n²) per tour, cheap for
   n ≤ 1,000. For 3-opt the full scan is O(n³) and for LK there is no full
   version, so higher levels need another target (for example edges of
   better local optima, or a deeper but bounded search).
2. **Static before dynamic.** A list per city and level, computed once per
   instance like the alpha lists, costs nothing during the search. A list
   that depends on the current tour must be recomputed and charged (ground
   rule); start static.
3. **Features.** Cheap and transferable: distance rank, alpha value,
   quadrant, distance relative to the city's nearest-neighbour distance,
   cluster membership; for anchors, how far the current tour edge exceeds
   the nearest-neighbour distance (O15's score).
4. **Evaluation unit.** Gain per second of the operator over the learned
   list, at equal wall-clock time, then whole-run quality; recall of the
   full scan's choice is a diagnostic, not the result.
5. **Instances.** Train on generated instances, test on others and on
   TSPLIB, across families; the clustered and tspgen families are where
   the gap is.

## Baselines the selector must beat

All cheap and not learned:

- 10 nearest neighbours (O7), and longer lists (k = 20, 40);
- 5 alpha-nearness candidates (O17; Helsgaun 2000);
- quadrant neighbours: Johnson and McGeoch (1997, §3.3) choose 40
  neighbours as the 10 closest in each quadrant around the city and report
  "real value on more structured instances" (local copy
  `papers/TSP-JohMcg97.pdf`);
- worst-edge targeting (O15);
- the k-nearest "prior" heatmap of Pan et al. (below).

## First experiment: the oracle bound (no learning)

Give O7, at each anchor, the partner O6 would choose, for free, and
measure the rate on E1's states. If even this perfect selector does not
beat alpha and quadrant candidates by a useful margin, no learned
selector can, and the claim fails cheaply. Needs the chosen partner
recorded per anchor; easy in the Rust port.

## Related work (searched 2026-10-27)

Abstracts read unless marked; nothing read in full yet.

| Work | What it learns | How it differs from this idea |
|---|---|---|
| Xin, Song, Cao, Zhang, NeuroLKH (NeurIPS 2021, [arXiv 2110.07983](https://arxiv.org/abs/2110.07983)) | A sparse graph network gives edge scores (supervised) and node penalties (unsupervised); the scores form LKH's candidate set | Closest. One candidate set for the whole search, target = edges of good tours, not per level and not the full operator's choice |
| Fitzpatrick, Ajwani, Carroll, "Learning to sparsify TSP instances" (2021, [arXiv 2104.09345](https://arxiv.org/abs/2104.09345)) | Which edges to prune, from LP, cutting-plane and MST features | Pruning for exact solving; target = optimal-tour edges |
| Lin, Mei, Zhang, two-stage sparsification ([arXiv 2604.20236](https://arxiv.org/abs/2604.20236), 2026) | α-nearest + POPMUSIC candidates, then a learned (logistic regression) pruning | Shrinks the candidate graph; target = optimal-tour edges |
| Fu et al. 2021; Min, Bai, Gomes, UTSP ([NeurIPS 2023](https://www.cs.cornell.edu/gomes/pdf/2023_min_neurips_tsp.pdf)) | Heatmaps of edges likely in the optimal tour, decoded by MCTS-guided k-opt | Global edge probabilities, not per level |
| Pan et al., "Beyond the heatmap" ([arXiv 2411.09238](https://arxiv.org/abs/2411.09238), ICLR 2026) | Shows a parameter-free k-nearest-neighbour heatmap matches or beats learned heatmaps once MCTS is tuned | **Warning**: learned edge scores may add little over distance |
| Zhou, Ju, Moura, learning-guided local search for the ATSP ([ICML 2025](https://icml.cc/virtual/2025/52424)) | Heatmaps for an MCTS pipeline on the ATSP | Same finding: "MCTS alone drives most of the performance, with heatmaps offering limited standalone value" |
| Santana, Lodi, Vidal, "A possible overkill?" ([arXiv 2210.12075](https://arxiv.org/abs/2210.12075), 2022) | GNN heatmaps as relatedness inside HGS's local search and crossover (CVRP) | **Warning**: "heatmaps did not present significant advantages over simpler distance measures" |
| Herdianto et al., edge-selector model ([arXiv 2508.14071](https://arxiv.org/abs/2508.14071), 2025) | Classifies solution edges to forbid moves in the local search of CVRP/CVRPTW metaheuristics | Learned move filter for VRP; label and equal-time comparison not stated in the abstract |
| da Costa et al., learning 2-opt heuristics ([ACML 2020](https://proceedings.mlr.press/v129/costa20a.html)) | A policy that picks the 2-opt move itself | Learns the move, not a candidate list; compared by steps |
| Gasse et al., learning to branch ([NeurIPS 2019](https://proceedings.neurips.cc/paper/2019/hash/d14c2267d848abeb81fd590f371d39bd-Abstract.html)) | A GCN imitates strong branching (an expensive exact choice) in MIP | Methodological precedent: cheap model imitating an expensive oracle |
| Hudson et al., GNN guided local search (ICLR 2022; not opened) | Edge regret to guide guided local search | Global edge scores for penalties |

**What may be new.** The target (the choice of the expensive version of
the *same* operator), one selector per level of the cascade, and the test
at equal wall-clock time against alpha and quadrant candidates. No source
found does all three. The warnings above say learned edge scores often
add nothing over distance; this idea differs in that its target is
state-dependent (per level) rather than "edges of the optimal tour", but
the same failure is the main risk, so the oracle bound comes first.

**Still to search:** granular neighbourhoods (Toth and Vigo 2003) and
learned granular neighbourhoods; learned don't-look bits; POPMUSIC
candidates (Taillard and Helsgaun 2019); DIMES, DIFUSCO and NeuOpt.
