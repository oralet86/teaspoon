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

## Data and synthetic instances

`data/` holds the CVRPLIB X, XL and AGS instance sets with their best-known
solutions (https://galgos.inf.puc-rio.br/cvrplib/).  The XL archive ships no
solutions and the AGS archive solutions can be stale, so the authoritative
ones come from the CVRPLIB `download/bks/<id>` endpoint (XL ids 280-379,
AGS ids 258-267).  The official Uchoa/XML generator is vendored under
`vendor/xml100/` from the XML100 page
(https://galgos.inf.puc-rio.br/cvrplib/index.php/en/xml100).

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
    --seeds 0 1 --out experiments/runs/smoke
uv run python -m bench report experiments/runs/smoke --csv
```

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
