# teaspoon

Experimental repository for research regarding metaheuristic optimization

## Instance viewer

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

## Solvers

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

## Solver binaries

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

## Data and synthetic instances

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

## Benchmark runner

`bench/` runs solvers over named suites (`smoke`, `x`, `xl`, `ags`) and
stores anytime traces with best-known-solution-relative metrics: gap,
time-to-target and primal integral.

```bash
uv run python -m bench run --suite smoke --solvers hgs filo2 --budget 60 \
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

## Instance formats

`instances/` keeps the data model (`model.py`), the format registry
(`registry.py`) and concrete parsers (`tsplib.py`) separate. The viewer only
imports the registry, so adding a format means implementing the
`DatasetFormat` protocol (`scan_directory`, `matches`, `load`, `load_tours`)
and registering it, for example for the Amazon Last Mile JSON files:

```python
from instances import register_format

register_format(AmazonLastMileFormat())
```
