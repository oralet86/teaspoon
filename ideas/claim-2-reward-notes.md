# Notes for the reward in Claim 2

- Keep running estimates of improvement and of time per operator and divide
  them. Averaging single ratios is noisier, because most uses improve nothing.
- If a baseline is wanted, use the current average rate. A reward that is
  positive only when an operator beats 2-opt builds the answer in.
- Pursuit-style selectors only use the ranking, so a baseline changes nothing
  for them; probability matching breaks on negative rewards.

## Prior work (read 2026-10-16)

The first two notes above are already published:

- **Ratio of sums over a window = Thomas and Schaus (2018).** Their L(o) is
  summed Δc over summed Δt for operator o inside the time window
  [t* − w, now], where t* is the time of the last new best; they blend it with
  the run-long ratio normalised by the all-operator ratio L/T. They introduced
  it precisely because an exponentially smoothed per-use Δc/Δt (Laborie and
  Godard 2007) is biased when run times differ: fast operators are updated
  more often. Cite them for the estimator.
- **Average rate as a baseline** is close to their L/T normalisation.
- **Other time-aware credits:** listed in
  [prior-work.md](prior-work.md#time-aware-selection).
- **Sparse large gains** (cheap random moves near a local optimum) defeat
  average-based credit (DaCosta et al. 2008, outlier scenario); extreme-value
  credit (Fialho 2010) or rank-based credit (AUC) are the published answers.
- **Rewards shrink as the run progresses** ("moment-dependent", Fialho);
  normalising by the current rate (our baseline) or ranking addresses this.
- **Restart on change:** DMAB's Page–Hinkley restart reacts faster than
  smoothing when the best operator changes abruptly.

What may still be ours: anchoring the window on slices rather than single
uses, and charging the selector's own time on the same clock.
