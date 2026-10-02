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

## Exact solvers

`solvers/` wraps the Concorde and LKH-3 binaries in `vendor/` (override the
paths with `CONCORDE_BIN` / `LKH_BIN` or the `executable=` argument):

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
Both wrappers run the binaries in a temporary directory, validate the
returned routes (coverage and capacity) and fail loudly on timeouts or
malformed output.

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
