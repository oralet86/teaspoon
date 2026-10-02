"""LKH-3 wrapper for symmetric TSP and capacitated VRP instances.

LKH consumes a parameter file that points at the problem and at a tour
output.  For TSP that output is a standard TSPLIB tour; for CVRP it is a
single Hamiltonian cycle over an expanded node set that repeats the depot
once per vehicle, so the wrapper splits it back into depot-anchored routes.
"""

from __future__ import annotations

import gzip
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from instances import Instance, load_path

from .base import (
    SolverError,
    SolverExecutionError,
    SolverResult,
    check_permutation,
    find_executable,
    require_length,
    run_process,
)

_DISTANCE_MODES = ("file", "exact")
_EUCLIDEAN_EDGE_WEIGHT_TYPE = "EUC_2D"
_EDGE_WEIGHT_TYPE_PATTERN = re.compile(
    r"^(\s*EDGE_WEIGHT_TYPE\s*:\s*)(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def solve_lkh(
    instance_path: str | Path,
    *,
    executable: str | Path | None = None,
    salesmen: int | None = None,
    runs: int | None = None,
    seed: int | None = None,
    timeout_seconds: float | None = None,
    distance_mode: str = "file",
    extra_parameters: Mapping[str, str | int | float] | None = None,
) -> SolverResult:
    """Solve a TSP or CVRP instance with LKH-3.

    Args:
        instance_path: TSPLIB/VRPLIB instance file, plain or gzipped.
        executable: LKH binary.  Defaults to ``LKH_BIN`` or the bundled
            ``vendor/LKH``.
        salesmen: Number of vehicles for CVRP.  LKH treats the value as a
            floor and raises it to the capacity-feasible minimum when the
            demand requires it; when omitted it picks that minimum itself.
        runs: Number of LKH runs.
        seed: Random seed; LKH uses 1 by default.
        timeout_seconds: Wall-clock limit for the solver process.
        distance_mode: ``"file"`` rounds distances as the file's TSPLIB edge
            weight type dictates.  ``"exact"`` solves a temporary copy with
            ``EXACT_2D`` and computes unrounded Euclidean lengths, which is
            needed for the CMT, tai, Golden and Li CVRPLIB sets that publish
            unrounded optima while declaring ``EUC_2D``.
        extra_parameters: Additional parameter-file entries, e.g.
            ``{"TIME_LIMIT": 60, "MAX_TRIALS": 1000}``.

    Returns:
        The tour or routes, the length recomputed from the instance, and the
        captured process output.

    Raises:
        ValueError: If ``distance_mode`` or a numeric option is invalid.
        SolverError: If the instance family or edge weights do not fit the
            requested mode.
        SolverTimeoutError: If the process exceeds ``timeout_seconds``.
        SolverExecutionError: If LKH fails or writes no usable solution.
        FileNotFoundError: If no LKH executable can be found.
    """
    if distance_mode not in _DISTANCE_MODES:
        raise ValueError(
            f"distance_mode must be one of {_DISTANCE_MODES}, got {distance_mode!r}"
        )
    if salesmen is not None and salesmen <= 0:
        raise ValueError(f"salesmen must be positive, got {salesmen}")
    if runs is not None and runs <= 0:
        raise ValueError(f"runs must be positive, got {runs}")

    path = Path(instance_path)
    instance = load_path(path)
    if instance.kind not in ("TSP", "CVRP"):
        raise SolverError(
            f"lkh supports TSP and CVRP instances, but {path.name} is {instance.kind}"
        )
    if instance.dimension is None:
        raise SolverError(f"{path.name} has no DIMENSION field")
    if salesmen is not None and instance.kind != "CVRP":
        raise SolverError("salesmen only applies to CVRP instances")
    if distance_mode == "exact":
        _require_exact_capable(instance)
    binary = find_executable("lkh", executable)

    with tempfile.TemporaryDirectory(prefix="teaspoon-lkh-") as directory:
        workdir = Path(directory)
        problem_path = path.resolve()
        if distance_mode == "exact":
            problem_path = _exact_distance_copy(path, workdir)
        tour_path = workdir / "solution.tour"
        parameter_path = workdir / "lkh.parameters"
        parameters: list[tuple[str, str | int | float | Path]] = [
            ("PROBLEM_FILE", problem_path),
            ("TOUR_FILE", tour_path),
        ]
        if salesmen is not None:
            parameters.append(("SALESMEN", salesmen))
        if runs is not None:
            parameters.append(("RUNS", runs))
        if seed is not None:
            parameters.append(("SEED", seed))
        if extra_parameters is not None:
            parameters.extend(extra_parameters.items())
        _write_parameter_file(parameter_path, parameters)

        outcome = run_process(
            [binary, parameter_path],
            solver="lkh",
            cwd=workdir,
            timeout_seconds=timeout_seconds,
        )
        if not tour_path.is_file():
            raise SolverExecutionError(
                "lkh exited without writing a tour file\n"
                f"stdout:\n{outcome.stdout[-2000:]}"
            )
        routes = _read_routes(tour_path, instance, salesmen)
        length = _compute_length(instance, routes, distance_mode)

    return SolverResult(
        solver="lkh",
        instance_path=path,
        kind=instance.kind,
        routes=routes,
        length=length,
        runtime_seconds=outcome.runtime_seconds,
        command=outcome.command,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
    )


def _require_exact_capable(instance: Instance) -> None:
    """Check that the instance has the coordinates exact mode needs.

    Raises:
        SolverError: If the instance does not use ``EUC_2D`` coordinates.
    """
    edge_weight_type = instance.metadata.get("Edge weight type", "")
    nodes = instance.nodes
    if (
        edge_weight_type != _EUCLIDEAN_EDGE_WEIGHT_TYPE
        or nodes is None
        or nodes.coordinates is None
    ):
        raise SolverError(
            "distance_mode='exact' needs EUC_2D coordinates, but "
            f"{instance.source_path.name} uses "
            f"{edge_weight_type or 'EXPLICIT'} weights"
        )


def _exact_distance_copy(path: Path, workdir: Path) -> Path:
    """Copy an ``EUC_2D`` instance with ``EXACT_2D`` edge weights for LKH.

    The CMT, tai, Golden and Li CVRPLIB sets publish unrounded distances but
    still declare ``EUC_2D``; rewriting the header is what makes LKH
    reproduce their optima.

    Raises:
        SolverError: If the file lacks an ``EUC_2D`` header.
    """
    text = _read_text(path)
    match = _EDGE_WEIGHT_TYPE_PATTERN.search(text)
    if match is None:
        raise SolverError(f"{path.name} has no EDGE_WEIGHT_TYPE field")
    edge_weight_type = match.group(2).upper()
    if edge_weight_type != _EUCLIDEAN_EDGE_WEIGHT_TYPE:
        raise SolverError(
            "distance_mode='exact' needs EUC_2D coordinates, but "
            f"{path.name} uses {edge_weight_type}"
        )
    modified = _EDGE_WEIGHT_TYPE_PATTERN.sub(r"\g<1>EXACT_2D", text, count=1)
    copy_path = workdir / path.name.removesuffix(".gz")
    copy_path.write_text(modified, encoding="utf-8")
    return copy_path


def _read_routes(
    tour_path: Path,
    instance: Instance,
    requested_salesmen: int | None,
) -> tuple[np.ndarray, ...]:
    """Turn LKH's tour file into zero-based, depot-anchored routes.

    Raises:
        SolverExecutionError: If the file does not hold a permutation or the
            routes are inconsistent with the instance.
    """
    values = _read_tour_section(tour_path)
    dimension = instance.dimension
    if dimension is None:
        raise SolverError(f"{instance.source_path.name} has no DIMENSION field")
    if instance.kind == "TSP":
        check_permutation(values, dimension, solver="lkh")
        return (values - 1,)
    check_permutation(values, values.size, solver="lkh")
    return _reconstruct_cvrp_routes(values, instance, requested_salesmen)


def _read_tour_section(path: Path) -> np.ndarray:
    """Read the 1-based node ids from a TSPLIB ``TOUR_SECTION``.

    Raises:
        SolverExecutionError: If the section is missing, empty or holds
            non-integer values.
    """
    tokens = path.read_text(encoding="utf-8").split()
    try:
        start = tokens.index("TOUR_SECTION") + 1
    except ValueError as error:
        raise SolverExecutionError(f"{path.name} has no TOUR_SECTION") from error
    raw: list[int] = []
    for token in tokens[start:]:
        if token in {"-1", "EOF"}:
            break
        try:
            raw.append(int(token))
        except ValueError as error:
            raise SolverExecutionError(
                f"{path.name}: non-integer tour value {token!r}"
            ) from error
    if not raw:
        raise SolverExecutionError(f"{path.name} has an empty TOUR_SECTION")
    return np.array(raw, dtype=np.int64)


def _reconstruct_cvrp_routes(
    values: np.ndarray,
    instance: Instance,
    requested_salesmen: int | None,
) -> tuple[np.ndarray, ...]:
    """Split LKH's expanded CVRP tour into per-vehicle routes.

    LKH solves CVRP as a TSP over ``dimension + salesmen - 1`` nodes by
    appending depot copies numbered ``dimension + 1`` onwards.  Cutting the
    single cycle at every depot occurrence recovers the routes, each of which
    is returned as a zero-based array starting with the depot.

    Raises:
        SolverExecutionError: If the vehicle count, customer coverage or
            route loads are inconsistent with the instance.
    """
    dimension = instance.dimension
    if dimension is None:
        raise SolverError(f"{instance.source_path.name} has no DIMENSION field")
    expanded_dimension = int(values.size)
    salesmen_used = expanded_dimension - dimension + 1
    if salesmen_used < 1:
        raise SolverExecutionError(
            "lkh wrote a CVRP tour shorter than the instance dimension"
        )
    if requested_salesmen is not None and salesmen_used < requested_salesmen:
        raise SolverExecutionError(
            f"lkh used {salesmen_used} vehicles, fewer than the requested "
            f"{requested_salesmen}"
        )
    depots = _depot_node_indices(instance)
    depot_ids = {int(index) + 1 for index in depots}
    depot_ids.update(range(dimension + 1, expanded_dimension + 1))
    tour = values.tolist()
    start = next((index for index, node in enumerate(tour) if node in depot_ids), None)
    if start is None:
        raise SolverExecutionError("lkh wrote no depot node in its CVRP tour")
    rotated = tour[start:] + tour[:start]
    segments: list[list[int]] = []
    current: list[int] = []
    for node in rotated:
        if node in depot_ids:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(node)
    if current:
        segments.append(current)
    depot_index = depots[0]
    routes = tuple(
        np.array([depot_index, *(node - 1 for node in segment)], dtype=np.int64)
        for segment in segments
    )
    if routes:
        customers = np.concatenate([route[1:] for route in routes])
    else:
        customers = np.empty(0, dtype=np.int64)
    expected_customers = dimension - len(depots)
    if (
        customers.size != expected_customers
        or np.unique(customers).size != customers.size
    ):
        raise SolverExecutionError(
            f"lkh covered {customers.size} customers, expected {expected_customers}"
        )
    _validate_capacity(instance, routes)
    return routes


def _depot_node_indices(instance: Instance) -> tuple[int, ...]:
    """Return the zero-based depot indices of a CVRP instance.

    Raises:
        SolverExecutionError: If no depot category or node can be found.
    """
    nodes = instance.nodes
    if nodes is None or nodes.categories is None:
        raise SolverExecutionError(
            f"cannot locate the depot in {instance.source_path.name}"
        )
    try:
        depot_category = nodes.category_names.index("depot")
    except ValueError as error:
        raise SolverExecutionError(
            f"{instance.source_path.name} has no depot category"
        ) from error
    depots = tuple(
        int(index) for index in np.flatnonzero(nodes.categories == depot_category)
    )
    if not depots:
        raise SolverExecutionError(f"{instance.source_path.name} has no depot node")
    return depots


def _validate_capacity(
    instance: Instance,
    routes: tuple[np.ndarray, ...],
) -> None:
    """Check every route load against the instance capacity, when known.

    Raises:
        SolverExecutionError: If a route carries more than the capacity.
    """
    nodes = instance.nodes
    capacity_text = instance.metadata.get("Capacity")
    if nodes is None or capacity_text is None or "demand" not in nodes.attributes:
        return
    capacity = float(capacity_text)
    demands = nodes.attributes["demand"]
    for position, route in enumerate(routes, start=1):
        load = float(demands[route[1:]].sum())
        if load > capacity + 1e-6:
            raise SolverExecutionError(
                f"lkh route {position} carries {load:g}, above the capacity "
                f"{capacity:g}"
            )


def _compute_length(
    instance: Instance,
    routes: tuple[np.ndarray, ...],
    distance_mode: str,
) -> float:
    """Compute the solution length with the convention of ``distance_mode``."""
    if distance_mode == "exact":
        return _exact_euclidean_length(instance, routes)
    return float(
        sum(
            require_length(instance, route, closed=True, solver="lkh")
            for route in routes
        )
    )


def _exact_euclidean_length(
    instance: Instance,
    routes: tuple[np.ndarray, ...],
) -> float:
    """Sum unrounded Euclidean tour lengths over all routes.

    Raises:
        SolverError: If the instance carries no coordinates.
    """
    nodes = instance.nodes
    coordinates = nodes.coordinates if nodes is not None else None
    if coordinates is None:
        raise SolverError(
            f"exact distances need coordinates for {instance.source_path.name}"
        )
    total = 0.0
    for route in routes:
        starts = route
        ends = np.roll(route, -1)
        deltas = coordinates[starts] - coordinates[ends]
        total += float(np.sqrt((deltas**2).sum(axis=1)).sum())
    return total


def _write_parameter_file(
    path: Path,
    parameters: list[tuple[str, str | int | float | Path]],
) -> None:
    """Write ``KEY = value`` lines for LKH."""
    lines = [f"{key} = {value}" for key, value in parameters]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_text(path: Path) -> str:
    """Read a plain or gzipped instance file as text."""
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8")
