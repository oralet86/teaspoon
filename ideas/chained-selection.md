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
  also makes the method portable between implementations (Claim 3).
- **Novelty, unchecked.** Related work to read first:
  - sequence-based selection hyper-heuristics (from memory: Kheiri and
    Keedwell, hidden Markov model over heuristic sequences, around GECCO
    2015);
  - ejection chains;
  - variable neighbourhood descent;
  - Jeong and Song (2025), LSTM predicting operator performance in a GA for
    the TSP.

## Consequence now: what Group A runs must log

So that the Group A traces can be used as training data without rerunning,
log for every operator application:

- operator;
- tour length before and after;
- cost: counted work, plus time at slice level;
- previous operator;
- whether the tour was at a local optimum (a 2-opt scan found nothing);
- the run's current improvement rate ρ.

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
