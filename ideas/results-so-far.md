# Results so far

All from one or two seeds. Illustrations, not evidence.

## GA: one run on kroA100

`python -m ga data/ALL_tsp/kroA100.tsp --seed 1`, 20,000 offspring, with the
current vectorised code and an offspring budget.

| Selector | Gap to best-known |
|---|---|
| 2-opt only | 1.2% |
| Adaptive pursuit | 6.1% |
| Probability matching | 8.7% |
| Uniform random | 20.5% |
| Inversion, insertion or swap only | 98% to 164% |

With improvement-only rewards 2-opt wins from the first step to the last.
Per unit of work the picture reverses (adaptive-pursuit run):

| Operator | Improves the child | Mean gain when it does | Expected gain per use | Candidate moves examined per use |
|---|---|---|---|---|
| 2-opt | 51% | 566 | about 289 | about 97 (a scan of the tour) |
| inversion | 3.2% | 650 | about 21 | 1 |
| swap | 2.5% | 346 | about 9 | 1 |
| insertion | 1.8% | 415 | about 7 | 1 |

- Per candidate move examined, 2-opt yields about 3 and a random inversion
  about 21.
- Per offspring 2-opt still wins, because every offspring pays for a
  crossover and a full length measurement whatever the mutation costs.
- The cheap operators were used on only 5% of offspring each; their success
  rates under heavy use are unknown.

## LKH-3 pilot

The stopped two-seed run is summarised in [lkh3.md](lkh3.md#preliminary-result-stopped-run-runslkh-pilot-1h).

## Caveats

- **All GA and pilot numbers so far** come from one or two seeds.
