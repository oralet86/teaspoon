# Operator selection by local context

Status: idea, for Claims 1 (as 1.3) and 2. Written 2026-10-16.

## The idea

Different regions of one instance may favour different operators: a dense
cluster and a sparse area are different landscapes. Instead of one operator
distribution per run, keep a distribution that depends on where the move is
applied.

Framed over **local features, not coordinates**. A distribution over (x, y)
belongs to one instance and has to be relearned from scratch on every new
one. A function of local features (a contextual bandit, in bandit terms)
carries over to new instances and to LKH.

## Related work (checked 2026-10-16 unless marked)

- **"Where" and "what" learned jointly: NeuRewriter** (Chen and Tian,
  NeurIPS 2019; read). A region-picking policy scores every node of the
  current solution, then a rule-picking policy chooses a rewrite for it;
  trained offline with actor-critic on one instance distribution. For CVRP the
  region is a node and the rule moves it in the visiting order. This is the
  "where, then which" structure of this idea, already published.
- **Contextual bandit for operator selection** (Gonçalves et al., CEC 2018;
  read). LinUCB with one linear model per operator over search-state features
  (stagnation, local diversity, budget used); the context is per subproblem,
  not per node. Features expressed relative to their running average worked
  best.
- **Solution-structure features recommended** (Pei et al., PRICAI 2022;
  read). After showing that stateless AOS fails in a large VRP because the
  best operator rarely persists, they recommend features of the solution's
  structure rather than fitness-based ones.
- **Cheap structural features exist:** the mean neighbour rank of tour edges
  correlates strongly with tour length (Hansen and Mladenović 2006; read).
  "Are the anchor's tour edges among its k nearest neighbours?" is the local
  version.
- **Per-node adaptivity for edges, not operators:** bandLKH-3 (a bandit per
  city picks candidate edges) and VSR-LKH-3 (Q-values per node order
  candidate edges); see [lkh3.md](lkh3.md). Not read in full.
- **Learned node-pair choice for 2-opt:** Wu et al., IEEE TNNLS 2021 (from
  memory, not read).
- **Spatial operators and decomposition:** Shaw removal in ALNS; LKH's
  `SUBPROBLEM_SIZE` and POPMUSIC; Johnson and McGeoch on geometric and
  tour-based partitioning (read).
- **LK adapts "where" crudely already:** don't-look bits (Bentley 1992,
  described in Johnson and McGeoch; read).

**What remains ours:** (a) explicit, cheap, transferable local features
instead of a neural embedding trained per distribution; (b) the decision
charged on a wall-clock budget; (c) online use without offline training;
(d) the measurement question of Claim 1.3: does the leading operator differ
between regions **within one instance**? None of the papers above asks it.

## Design points

1. **Moves need an anchor.** A random swap or inversion picks two positions
   that may lie in different regions, so it has no single location. Moves are
   anchored at a node, as LK and "2-opt at an edge" already are: pick a node,
   then a move around it. The decision becomes "where, then which", and
   "where" may matter more than "which".
2. **Anchoring alone does not make a move local.** If the second endpoint is
   drawn uniformly from the tour, the move still spans the instance. For a
   local move the second endpoint must come from the anchor's neighbour list.
   *Done:* every operator O1–O20 is now anchored, and the targeted ones
   take the second endpoint from the anchor's candidate list.
3. **Static and dynamic features.**
   - Static, from the instance: nearest-neighbour distance, local density,
     cluster membership.
   - Dynamic, from the tour: whether the anchor's two tour edges are among
     its k nearest neighbours, how long since its area last improved.
   Static features alone cannot follow the search state, which is what
   Claim 1.1 is about.
4. **Splitting costs samples.** Every split divides the evidence; a continuous
   field needs smoothing (a kernel, or a small parametric model over the
   features). Under a strict time budget a sharper policy must pay for its
   own learning, and computing features for every decision is charged too,
   by the ground rule.

## Testing it cheaply (a possible Claim 1.3)

Before building any selector: E1 records every operator's gain and time per
anchor. Group the results into feature bins (e.g. density tercile × how
many of the anchor's tour edges go to its nearest neighbours), not per node.
Then check whether the leading operator differs between bins **within one
instance**. The E1 main run writes this table (`e1main_nodes.csv`); it is
not interpreted yet, and O15 and the kicks are credited to the anchor
although they act elsewhere.

That would be Claim 1.3, alongside 1.1 (over time) and 1.2 (between
instances). Instances with dense and sparse regions in one tour are the
natural test bed: National TSPs (real geography) and tspgen instances
(cluster and implosion mutations on a uniform cloud).

## Where it fits

- **Claim 1:** measurement as 1.3, nearly free if logging includes the anchor
  and its features.
- **Claim 2:** a contextual selector.
- **Claim 4 / LKH:** combines with [chained selection](chained-selection.md).
  Each chain starts at a node, so the first operator choice can depend on
  that node's context.
