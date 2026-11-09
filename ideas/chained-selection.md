# Chained operator selection with a time-based advantage

Status: idea, for Claim 2. Written 2026-10-16.

## The idea

Use the time traces of runs (gain and cost of every operator application) to
learn an advantage per operator. Then, as in Lin-Kernighan, chain operator
applications while the cumulative advantage stays positive, allowing single
steps that make the tour worse, and commit the best prefix of the chain once
it cannot be extended.

LK's key trick is the gain criterion, not k-opt itself: a step may lose, the
chain goes on while the cumulative gain is positive, and only the best prefix
is kept. Applied across operator types:

```
kick (-40)  ->  2-opt (+25)  ->  2-opt (+30)  ->  2-opt (0, stuck)
cumulative:  -40         -15           +15   <- commit this prefix
```

The kick's loss and the repair by local search are judged as one unit. This
solves the delayed-credit problem of kicks without n-step returns or a
sequence model: the chain structure does the credit assignment.

## Advantage as opportunity cost

$$A(\text{chain}) = \Delta\text{length} - \rho \cdot t_{\text{chain}}$$

- `Δlength`: improvement achieved by the chain.
- `ρ`: the search's current average improvement rate (length per second,
  over a recent window).
- `t_chain`: cost of the chain.

A chain is worth committing if it beat what the search would have gained on
average in the same time. Early in a run ρ is large, so chains must be fast;
late in a run ρ is small and slower chains become acceptable.

## Two roles, kept separate

1. **Learned advantage (prediction).** Expected gain per second of an operator
   in a given state, learned from the traces. Decides which operator to try
   next in the chain, like LK's candidate ordering.
2. **Realised advantage (measurement).** Actual gain minus ρ·t of the moves
   applied so far. Decides when to stop and which prefix to commit.

Moves are executed tentatively and the measured gain decides acceptance. If
the learned estimate also decided acceptance, the model would grade its own
predictions.

## Problems to solve

- **Random chains rarely pay off.** LK stays tractable because each move
  connects to the previous one through candidate lists. Unrelated random moves
  will almost never reach a positive total. Needs a depth limit and a breadth
  limit (LKH's `MAX_SWAPS`, `MAX_BREADTH`), probably neighbour lists.
- **Rollback.** The uncommitted tail of a chain must be undone. Every
  operator needs an undo (cheap for inversion and swap).
- **Timing single moves.** A random swap takes about a microsecond in Python;
  in Rust, timing the call costs more than the move. Use an estimated cost
  per operator, calibrated by E0, rather than a timer around every call. This
  also makes the method portable between implementations.
- **Novelty: narrowed after reading (2026-10-16).** See "Related work" below.
  Still unread: ejection chains, variable neighbourhood descent.

## Related work (read 2026-10-16)

- **Kheiri and Keedwell (GECCO 2015), sequence-based selection
  hyper-heuristic.** A hidden Markov model over heuristics; each step carries
  a flag that either evaluates acceptance or continues the sequence
  *without* accepting or rejecting; a sequence ending in a new best gets credit
  for every transition in it. On HyFlex TSP it learned, unaided, that
  perturbations improve the best solution only when followed by the hill
  climber: perturb-then-local-search. Closest prior work.
- **Chuang (CMU thesis, 2020).** Solution chains: up to ℓ heuristics in
  succession, stop at the first improvement over the incumbent, else keep the
  incumbent; ℓ from Luby's sequence, which has an O(m log m) guarantee when the
  success-vs-length curve is unknown. Uniform heuristic choice with Luby
  lengths placed 4th of 21 in CHeSC; a bigram model of improving chains placed
  1st. Also "amplified" heuristics (repeat for 10 ms), our time slices.
- **Kletzander and Musliu (AAAI 2023), LAST-RL.** RL over chains with Luby
  lengths scaled by the mean successful chain length, extended while still
  perturbing or improving; reward at chain end with a runtime penalty.
- **Lu et al. (ICLR 2020).** All operators of one improvement iteration share
  its reward equally; perturbation separated from learned improvement.
- **Gunawan et al. (PATAT 2016).** Learns the *order* of a fixed set of
  local-search operators; random order is about as good.
- **Chen and Tian (NeurIPS 2019).** Learns when *uphill* rewrites pay off
  through discounted cumulative reward; the main source of their gain.
- **Johnson and McGeoch (1997).** LK's gain criterion and chained local
  optimisation (Martin, Otto, Felten): double-bridge kick + local search,
  accepted only if better (ILK).

**What remains ours:** accepting a chain by cumulative gain minus ρ·t (an
opportunity cost in time), committing the **best prefix**, and keeping the
learned prediction (which operator next) separate from the measured
acceptance. Baselines any chain method must beat: Luby-length chains with
uniform choice (Chuang) and a fixed perturb-then-local-search macro (O8,
O12, O13 in [instances-and-operators.md](instances-and-operators.md) §5).

## Consequence now: what runs must log

So that run traces can be used as training data without rerunning, every
slice logs what [benchmark-protocol.md](benchmark-protocol.md) §9 lists
(operator, lengths, cost, previous operator, local-optimum flag, ρ, anchor
and its features).

## Where it fits

- **Not Claim 1.** With chains, "best operator at each point" becomes much
  harder to define. Claim 1 keeps single-step decisions.
- **Claim 2.** Simpler alternatives to compare against, cheapest first:
  - a fixed macro-operator, "double bridge, then 2-opt until stuck", scored
    as one unit;
  - a selector with a few history features (last operator, slices since the
    last improvement, local-optimum flag).
- **Claim 4.** A selector that finds the local-search-then-kick pattern
  without being told would be a stronger result than building it in.
  *Update:* Kheiri and Keedwell (2015) already showed this on HyFlex TSP, so
  it would need to happen inside LKH or under time accounting to count.
