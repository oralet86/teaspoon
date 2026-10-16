# teaspoon

Experimental repository for research on metaheuristic optimization: adaptive
operator selection (AOS) in a genetic algorithm for the TSP, and later in
state-of-the-art routing solvers.

Last updated: 2026-10-16

## Status

- **Target:** GECCO 2027. The deadline was estimated as about 2027-01-26;
  confirm it against the official call.
- **Active:** the GA study under `ga/`. Time box of one week: the GA
  experiments are to be done by 2026-10-19.
- **Paused:** the LKH-3 work. The setting pilot is built and was run on two
  of three seeds; a full rerun is prepared.
- **Later:** a vectorised version and a Rust port of the GA (Claim 3). The
  Rust toolchain is installed.

## Contents

1. [Working rules](#working-rules)
2. [Research notes](#research-notes)
3. [Using the repository](#using-the-repository)

## Working rules

- **Journal in detail.** Every experiment gets a journal entry: the question,
  the exact command and code version, the instances, seeds and budgets, the
  raw numbers, the graphs, and what was concluded, including results that
  went against expectations.
- **No claim is closed without data and many graphs.** A claim is only marked
  as shown, or as failed, when its journal holds the measurements and the
  figures that support the verdict. Work on the next claim starts after that.
- **Where it lives.** `journal/claim-N.md` for the entries, with figures under
  `journal/figures/claim-N/` and the summarised data behind every figure
  under `journal/data/claim-N/`. Run directories under `runs/` are not
  tracked by git, so a figure must be reproducible from the files in
  `journal/data/`.
- **Every figure names its source**: the data file, the script that drew it,
  and the number of seeds.

## Research notes

The research plan, results and literature live in [`ideas/`](ideas/), one
topic per file:

| File | Contents |
|---|---|
| [`claims.md`](ideas/claims.md) | The ground rule on time, Claims 1 to 4, open points |
| [`claim-1-plan.md`](ideas/claim-1-plan.md) | Setup and experiments E0 to E5 for Claim 1, verdict, to do |
| [`claim-2-reward-notes.md`](ideas/claim-2-reward-notes.md) | Notes on the reward for a Claim 2 selector |
| [`chained-selection.md`](ideas/chained-selection.md) | Idea: LK-style chaining of operators with a time-based advantage |
| [`results-so-far.md`](ideas/results-so-far.md) | The single GA run on kroA100 |
| [`prior-work.md`](ideas/prior-work.md) | Adaptive operator selection and time-aware selection in the literature |
| [`lkh3.md`](ideas/lkh3.md) | LKH-3 background, the paused setting pilot and its result, to do |
| [`original-proposal.md`](ideas/original-proposal.md) | The hand-written research questions and protocol |

## Using the repository

Development commands are in `AGENTS.md`: `uv run ruff format .`,
`uv run ruff check .`, `uv run pyright .`, `uv run pytest`.

### Genetic algorithm

`ga/` is a small steady-state GA for the symmetric TSP with adaptive operator
selection. It needs no extra dependencies.

```bash
uv run python -m ga data/ALL_tsp/kroA100.tsp
uv run python -m ga data/ALL_tsp/kroA100.tsp --selector uniform --seed 3
uv run python -m ga data/ALL_tsp/kroA100.tsp --selector fixed:two_opt
```

- `ga/operators.py`: swap, inversion, insertion, 2-opt and order crossover.
  Each mutation changes the tour in place and returns the change in length.
  Add an operator by writing one function and adding it to `MUTATIONS`.
- `ga/selectors.py`: fixed, uniform, probability matching and adaptive
  pursuit behind two methods, `select` and `update`. Add a strategy with one
  class and one entry in `SELECTORS`.
- `ga/algorithm.py`: the loop, which logs the operator and its length change
  at every step.
- The command prints the result, the gap to best-known when the instance has
  a companion tour, and per-operator usage by phase of the run.

### Instance viewer

`python -m viewer` opens a Qt window listing everything under `data/`:

```bash
uv run python -m viewer                        # browse ./data
uv run python -m viewer data data/ALL_tsp/a280.tsp
```

- Double-click a file to load it; a matching `.opt.tour` (TSP) or `.sol`
  (CVRP) is attached automatically.
- The **Plan** tab draws nodes and sequences, the **Matrices** tab draws
  explicit edge-weight matrices (EXPLICIT TSP/CVRP).  Instances without
  coordinates, such as `brazil58`, hide the Plan tab and open on Matrices.
- `Open solution…` attaches an extra TSPLIB tour or CVRPLIB `.sol` and
  computes its length from the instance's edge weights.
- `Export PNG…` saves the current figure for papers and reports.
- Progress, warnings and errors are logged to the terminal.

### Solvers

`solvers/` wraps the solver binaries in `vendor/`:

```python
from solvers import solve_concorde, solve_lkh

tsp = solve_concorde("data/ALL_tsp/a280.tsp")  # symmetric TSP only
tsp.length, tsp.routes[0]  # recomputed length, zero-based tour

cvrp = solve_lkh("data/A/A-n32-k5.vrp", salesmen=5)  # TSP or CVRP
cvrp.length, [route.tolist() for route in cvrp.routes]
cvrp.to_sequences()  # instances.Sequence objects for the viewer
cvrp.save_solution("A-n32-k5.sol")  # CVRPLIB .sol; TSP writes a TSPLIB tour

# CMT/tai/Golden/Li declare EUC_2D but publish unrounded optima:
exact = solve_lkh("data/CMT/CMT1.vrp", distance_mode="exact")
```

`solve(instance_path, solver="concorde" | "lkh", ...)` dispatches by name.
Both wrappers run the binaries in a temporary directory and validate the
returned routes (coverage and capacity).

`solvers/` also wraps HGS-CVRP and FILO2.  Both are built from source into
`vendor/` and return wall-clock anytime traces:

```python
from solvers import solve_filo2, solve_hgs

hgs = solve_hgs("data/X/X-n101-k25.vrp", time_limit_seconds=60, seed=1)
[(point.wall_seconds, point.best_cost) for point in hgs.trace[-3:]]

filo2 = solve_filo2("data/XL/XL-n1048-k237.vrp", optimization_seconds=60, seed=1)
```

`solvers/` also wraps PyVRP's iterated local search, which runs in-process
through the `pyvrp` Python API (`pyvrp>=0.14` is a project dependency, so
there is nothing to build):

```python
from solvers import solve_pyvrp

pyvrp = solve_pyvrp("data/X/X-n101-k25.vrp", time_limit_seconds=60, seed=1)
[(point.wall_seconds, point.best_cost) for point in pyvrp.trace[-3:]]
```

### Solver binaries

`vendor/` is not tracked because the binaries are large build artifacts and
some upstream projects (Concorde, LKH-3) do not allow redistribution.
Build or download them into `vendor/` before running solvers or benchmarks:

| solver | upstream | revision | build | binary | override |
| --- | --- | --- | --- | --- | --- |
| HGS-CVRP | https://github.com/vidalt/HGS-CVRP | `1a927955cd2861a29d978f0d359d6e647db9319c` | CMake release build | `vendor/hgs` | `HGS_BIN` |
| FILO2 | https://github.com/acco93/filo2 | `17b8f844b4779832d673b59d6ba28aedee1cba52` | CMake with `ENABLE_TIMELIMIT` and `ENABLE_VERBOSE` | `vendor/filo2` | `FILO2_BIN` |
| Concorde | https://www.math.uwaterloo.ca/tsp/concorde.html | not redistributable; use the TSP source distribution | `./configure && make` | `vendor/concorde` | `CONCORDE_BIN` |
| LKH-3 | http://akira.ruc.dk/~keld/research/LKH-3/ | not redistributable; use a release archive | `make` | `vendor/LKH` | `LKH_BIN` |

Every wrapper resolves its binary in this order: the `executable=...`
argument, the environment variable, the `vendor/` default.  The benchmark
runner records each resolved binary's path and SHA-256 digest in
`runs.jsonl`, so a reported result stays tied to the exact build even when
an upstream revision cannot be pinned.  The official Uchoa/XML generator is
vendored under `vendor/xml100/generator.py`; set `XML_GENERATOR_SCRIPT` to
override it.

PyVRP is a Python dependency rather than a vendored binary: `solve_pyvrp`
runs in-process, and benchmark records store its version and the SHA-256
digest of its compiled core module instead of an executable path.

### Data and synthetic instances

The viewer, solver wrappers and benchmark suites expect these files under
`data/` (all are gitignored for size and licensing reasons):

| path | contents | source |
| --- | --- | --- |
| `data/X`, `data/XL`, `data/AGS` | CVRP instances with companion `.sol` solutions | https://galgos.inf.puc-rio.br/cvrplib/ |
| `data/A`, `data/ALL_vrp` | classic CVRP sets used by the examples and parser tests | CVRPLIB archives |
| `data/ALL_tsp` | symmetric TSP instances with `.opt.tour` files | TSPLIB95, http://comopt.ifi.uni-heidelberg.de/software/TSPLIB95/ |
| `data/almrcc2021` | Amazon Last Mile JSON data (optional, not used by the benchmark) | Amazon Last Mile Routing Challenge 2021 |

Instance files and companions may be gzip-compressed; an instance is
matched with a sibling `.opt.tour`/`.tour`/`.sol` automatically.

The X, XL and AGS best-known solutions are pinned in
`bench/bks_manifest.json`: one entry per `.sol` file with its source URL,
SHA-256 digest and recomputed cost.  Adopt, verify or refresh them with:

```bash
uv run python -m bench fetch-bks              # adopt and verify local files
uv run python -m bench fetch-bks --refresh    # re-download from CVRPLIB
```

The CVRPLIB `download/bks/<id>` endpoint is authoritative because the XL
archive ships no solutions and the AGS archive solutions can be stale.  Run
records store the source and digest of the BKS actually used, and
`load_bks` recomputes the cost with the instance's own length function
instead of trusting the file's `Cost` line.

```bash
uv run python -m generators --n 500 --count 20 --seed 0 --out data/generated/train
```

The generator samples the depot positioning, customer positioning, demand
distribution and route-size families used by the X and XL sets.

### Benchmark runner

`bench/` runs solvers over named suites (`smoke`, `x`, `xl`, `ags`) and
stores anytime traces with best-known-solution-relative metrics: gap,
time-to-target and primal integral.

```bash
uv run python -m bench run --suite smoke --solvers hgs filo2 pyvrp --budget 60 \
    --seeds 0 1 --out runs/smoke
uv run python -m bench report runs/smoke --csv
```

A run directory contains:

- `config.json` — host metadata (git commit and dirty state, lock file
  digests, package versions, CPU model, thread environment) and the planned
  specs.
- `runs.jsonl` — one record per run with the solver binary path and
  SHA-256, the BKS source and digest, the metrics, and pointers to the
  trace, log and solution files.
- `traces/` — best-cost-against-wall-clock CSV per run.
- `logs/` — raw solver stdout and stderr per run.
- `solutions/` — final routes as TSPLIB tours or CVRPLIB `.sol` files.

### LKH-3 setting pilot

`bench lkh-pilot` asks whether the best fixed LKH-3 setting changes from
instance to instance. Each instance is solved under four arms and several
seeds: `MOVE_TYPE` 3 or 5 crossed with `KICK_TYPE` 0 or 4 for the TSP, and
with `KICK_TYPE` 4 or 6 for constrained problem types, where kick type 0
rarely reaches a feasible solution within the budgets. The arms of one
(instance, seed) pair run side by side as a block, every run is killed at a
fixed deadline, and the command refuses to start when the worst-case wall
time exceeds `--max-minutes`.

The pilot can be stopped and continued. Ctrl+C asks whether to stop now
(the block in progress is discarded), stop after the block in progress, or
keep running. Running the same command again on a directory that holds
results asks whether to continue or start over; starting over moves the
earlier results into a `superseded-<timestamp>` folder. `--resume` and
`--restart` answer that question up front for non-interactive use.

```bash
uv run python -m bench fetch-lkh                       # SOP, TSPTW, CVRPTW archives
uv run python -m bench lkh-pilot --suite pilot-1h --dry-run
uv run python -m bench lkh-pilot --suite pilot-1h --jobs 8 --out runs/pilot
uv run python -m bench lkh-report runs/pilot
```

Runs go through `run_lkh_reported`, which works for every problem type
LKH-3 supports and takes the `(penalty, cost)` LKH prints at face value. For
TSP and CVRP the tour is also re-checked in Python (coverage, capacity,
recomputed cost). The report only counts an instance towards its verdict
when every run is feasible and the instance is not solved to best-known by
all of them. Suites are JSON files such as `bench/lkh_pilot_1h.json`.

### Instance formats

`instances/` keeps the data model (`model.py`), the format registry
(`registry.py`) and concrete parsers (`tsplib.py`) separate. The viewer only
imports the registry, so adding a format means implementing the
`DatasetFormat` protocol (`scan_directory`, `matches`, `load`, `load_tours`)
and registering it, for example for the Amazon Last Mile JSON files:

```python
from instances import register_format

register_format(AmazonLastMileFormat())
```
