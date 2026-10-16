"""LKH-3 wrappers.

LKH consumes a parameter file that points at the problem and at a tour
output.  :func:`solve_lkh` handles symmetric TSP and CVRP and validates the
result in Python: a TSP output is a standard TSPLIB tour; a CVRP output is a
single Hamiltonian cycle over an expanded node set that repeats the depot
once per vehicle, so the wrapper splits it back into depot-anchored routes.

:func:`run_lkh_reported` runs any problem type LKH-3 supports and takes the
``(penalty, cost)`` LKH prints at face value.  It exists to compare LKH
settings against each other, where every run is scored by the same LKH
evaluator; it is not a substitute for an independent feasibility check.
"""

from __future__ import annotations

import gzip
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from instances import Instance, load_path

from .base import (
    SolverError,
    SolverExecutionError,
    SolverResult,
    StreamedLine,
    check_permutation,
    find_executable,
    require_length,
    run_process,
    run_process_streaming,
    validate_capacity,
)

_DISTANCE_MODES = ("file", "exact")
_EUCLIDEAN_EDGE_WEIGHT_TYPE = "EUC_2D"
_EDGE_WEIGHT_TYPE_PATTERN = re.compile(
    r"^(\s*EDGE_WEIGHT_TYPE\s*:\s*)(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# "* 2: Cost = 0_784, Time = 0.03 sec." for penalised problem types and
# "* 14: Cost = 50778, Gap = 0.0000%, Time = 0.08 sec. =" otherwise.
_TRIAL_PATTERN = re.compile(
    r"^\*\s+(?P<trial>\d+):\s+Cost\s*=\s*(?:(?P<penalty>-?\d+)_)?(?P<cost>-?\d+),"
    r".*?Time\s*=\s*(?P<seconds>[\d.]+)\s*sec"
)
_TRIAL_COUNT_PATTERN = re.compile(r"^Trials\.min\s*=\s*(\d+)")
_PARAMETER_SPLIT = re.compile(r"[\s:=]+")
# Keys the reported runner sets itself, so a base parameter file cannot
# change the protocol of a comparison.
_MANAGED_PARAMETERS = frozenset(
    {"RUNS", "SEED", "TIME_LIMIT", "TOTAL_TIME_LIMIT", "MAX_TRIALS", "TRACE_LEVEL"}
)
# Large enough that TIME_LIMIT is always the binding stopping rule.
_UNBOUNDED_TRIALS = 1_000_000_000


@dataclass(eq=False, frozen=True, slots=True)
class LkhTracePoint:
    """One improving trial reported by LKH.

    Attributes:
        wall_seconds: Seconds between process start and the line's arrival,
            which includes LKH's preprocessing.
        solver_seconds: Seconds LKH itself reports, measured from the start
            of the search.
        trial: Trial number of the improvement.
        penalty: Constraint violation; ``0`` for a feasible solution and for
            problem types without a penalty.
        cost: Objective value.
    """

    wall_seconds: float
    solver_seconds: float
    trial: int
    penalty: int
    cost: int


@dataclass(eq=False, frozen=True, slots=True)
class LkhReport:
    """The outcome of one LKH run as LKH reported it.

    Attributes:
        problem_path: Problem file that was solved.
        penalty: Penalty of the best solution; ``0`` means feasible.
        cost: Cost of the best solution.
        trace: Every improving trial in arrival order.
        trials: Trials LKH completed, or ``None`` when the run was killed
            before LKH printed its summary.
        optimum: The ``OPTIMUM`` parameter the run was given, if any.
        validated_cost: Cost recomputed in Python from the tour LKH wrote,
            after checking coverage and vehicle capacity.  ``None`` when
            validation was not requested, not possible, or failed.
        validation_error: Why the independent check failed or could not
            run; ``None`` when it passed or was not requested.
        runtime_seconds: Wall-clock time of the process.
        killed: ``True`` when the deadline stopped the process.
        command: Full command line.
        parameters: The parameter file content the run used.
        stdout: Captured standard output.
        stderr: Captured standard error.
    """

    problem_path: Path
    penalty: int
    cost: int
    trace: tuple[LkhTracePoint, ...]
    trials: int | None
    optimum: int | None
    validated_cost: float | None
    validation_error: str | None
    runtime_seconds: float
    killed: bool
    command: tuple[str, ...]
    parameters: str
    stdout: str
    stderr: str

    @property
    def reached_optimum(self) -> bool:
        """Whether the run found a feasible solution no worse than ``optimum``."""
        return (
            self.optimum is not None and self.penalty == 0 and self.cost <= self.optimum
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


def read_parameter_file(path: str | Path) -> dict[str, str]:
    """Read an LKH parameter file into an ordered key-to-value mapping.

    Keys are upper-cased.  A keyword without a value, such as ``SPECIAL``,
    maps to an empty string.  A key that occurs several times keeps its last
    value.

    Raises:
        OSError: If the file cannot be read.
    """
    parameters: dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper() == "EOF":
            break
        key, *rest = _PARAMETER_SPLIT.split(line, maxsplit=1)
        parameters[key.upper()] = rest[0].strip() if rest else ""
    return parameters


def run_lkh_reported(
    problem_path: str | Path,
    *,
    time_limit_seconds: float,
    base_parameters: Mapping[str, str] | None = None,
    overrides: Mapping[str, str | int | float] | None = None,
    seed: int = 1,
    deadline_seconds: float | None = None,
    validate: bool = False,
    cancel_event: threading.Event | None = None,
    executable: str | Path | None = None,
) -> LkhReport:
    """Run LKH-3 on any supported problem type and report its own result.

    The run is a single LKH run bounded by ``TIME_LIMIT``.  Its parameter
    file is ``base_parameters`` without file paths and without the protocol
    keys this function manages (runs, seed, limits, trace level), followed
    by ``overrides``, so an override always wins over the base.

    Args:
        problem_path: Uncompressed problem file in a format LKH-3 reads.
        time_limit_seconds: Search budget passed as ``TIME_LIMIT``.  LKH
            starts this clock after its preprocessing.
        base_parameters: Problem-specific settings, typically read with
            :func:`read_parameter_file` from the parameter file published
            with the instance.
        overrides: Settings under test, e.g. ``{"MOVE_TYPE": "3 SPECIAL"}``.
        seed: Random seed.
        deadline_seconds: Wall-clock limit for the whole process.  A run
            killed at the deadline still reports the best solution it had
            printed.
        validate: Also check the tour LKH wrote with the Python-side
            validation of :func:`solve_lkh`.  Only TSP and CVRP can be
            checked; the outcome lands in ``validated_cost`` and
            ``validation_error`` and never raises.
        cancel_event: When given, LKH runs in its own session, shielded from
            a Ctrl+C in the terminal, and is killed once the event is set.
        executable: LKH binary.  Defaults to ``LKH_BIN`` or the bundled
            ``vendor/LKH``.

    Returns:
        The best ``(penalty, cost)`` LKH printed, with the improvement trace.

    Raises:
        ValueError: If a limit is not positive.
        SolverError: If the problem file is gzipped, which LKH cannot read.
        SolverCancelledError: If ``cancel_event`` was set during the run.
        SolverExecutionError: If LKH fails or prints no solution at all.
        FileNotFoundError: If the problem file or the executable is missing.
    """
    if time_limit_seconds <= 0:
        raise ValueError(
            f"time_limit_seconds must be positive, got {time_limit_seconds}"
        )
    if deadline_seconds is not None and deadline_seconds <= 0:
        raise ValueError(f"deadline_seconds must be positive, got {deadline_seconds}")
    path = Path(problem_path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    if path.name.endswith(".gz"):
        raise SolverError(f"LKH cannot read the gzipped file {path.name}")
    binary = find_executable("lkh", executable)

    with tempfile.TemporaryDirectory(prefix="teaspoon-lkh-") as directory:
        workdir = Path(directory)
        lines = _reported_parameter_lines(
            problem_path=path.resolve(),
            tour_path=workdir / "solution.tour",
            base_parameters=base_parameters or {},
            overrides=overrides or {},
            seed=seed,
            time_limit_seconds=time_limit_seconds,
        )
        parameter_text = "\n".join(lines) + "\n"
        parameter_path = workdir / "lkh.parameters"
        parameter_path.write_text(parameter_text, encoding="utf-8")
        outcome, streamed = run_process_streaming(
            [binary, parameter_path],
            solver="lkh",
            cwd=workdir,
            timeout_seconds=None,
            deadline_seconds=deadline_seconds,
            cancel_event=cancel_event,
            new_session=cancel_event is not None,
        )
        validated_cost: float | None = None
        validation_error: str | None = None
        if validate:
            validated_cost, validation_error = _validate_reported_tour(
                path, workdir / "solution.tour", killed=outcome.killed
            )

    trace = parse_lkh_trace(streamed)
    if not trace:
        raise SolverExecutionError(
            "lkh printed no solution"
            + (" before the deadline" if outcome.killed else "")
            + f"\nstdout:\n{outcome.stdout[-2000:]}\nstderr:\n{outcome.stderr[-2000:]}"
        )
    best = min(trace, key=lambda point: (point.penalty, point.cost))
    optimum_text = {
        **{key.upper(): value for key, value in (base_parameters or {}).items()},
        **{key.upper(): str(value) for key, value in (overrides or {}).items()},
    }.get("OPTIMUM")
    return LkhReport(
        problem_path=path,
        penalty=best.penalty,
        cost=best.cost,
        trace=trace,
        trials=_parse_trial_count(outcome.stdout),
        optimum=int(optimum_text) if optimum_text else None,
        validated_cost=validated_cost,
        validation_error=validation_error,
        runtime_seconds=outcome.runtime_seconds,
        killed=outcome.killed,
        command=outcome.command,
        parameters=parameter_text,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
    )


def _validate_reported_tour(
    problem_path: Path, tour_path: Path, *, killed: bool
) -> tuple[float | None, str | None]:
    """Check LKH's tour file independently of LKH's own penalty.

    Returns:
        The recomputed cost and ``None``, or ``None`` and the reason the
        tour is invalid or could not be checked.
    """
    if killed:
        return None, "the run was killed before LKH wrote its final tour"
    if not tour_path.is_file():
        return None, "lkh wrote no tour file"
    try:
        instance = load_path(problem_path)
        if instance.kind not in ("TSP", "CVRP"):
            return None, f"no Python-side check for {instance.kind} instances"
        routes = _read_routes(tour_path, instance, None)
        return _compute_length(instance, routes, "file"), None
    except (SolverError, ValueError) as error:
        return None, str(error)


def parse_lkh_trace(lines: Iterable[StreamedLine]) -> tuple[LkhTracePoint, ...]:
    """Extract the improving trials from LKH's ``TRACE_LEVEL = 1`` output."""
    points: list[LkhTracePoint] = []
    for line in lines:
        if line.stream != "stdout":
            continue
        match = _TRIAL_PATTERN.match(line.text)
        if match is None:
            continue
        points.append(
            LkhTracePoint(
                wall_seconds=line.wall_seconds,
                solver_seconds=float(match.group("seconds")),
                trial=int(match.group("trial")),
                penalty=int(match.group("penalty") or 0),
                cost=int(match.group("cost")),
            )
        )
    return tuple(points)


def _reported_parameter_lines(
    *,
    problem_path: Path,
    tour_path: Path,
    base_parameters: Mapping[str, str],
    overrides: Mapping[str, str | int | float],
    seed: int,
    time_limit_seconds: float,
) -> list[str]:
    """Build the parameter file lines of a reported run.

    Base entries that name a file are dropped because their relative paths
    do not survive the temporary working directory, and because an initial
    or merge tour would bias a comparison of settings.
    """
    override_keys = {key.upper() for key in overrides}
    entries: list[tuple[str, str]] = [("PROBLEM_FILE", str(problem_path))]
    entries += [
        (key.upper(), value)
        for key, value in base_parameters.items()
        if not key.upper().endswith("_FILE")
        and key.upper() not in _MANAGED_PARAMETERS
        and key.upper() not in override_keys
    ]
    entries += [
        ("TOUR_FILE", str(tour_path)),
        ("RUNS", "1"),
        ("SEED", str(seed)),
        ("TIME_LIMIT", f"{time_limit_seconds:g}"),
        ("MAX_TRIALS", str(_UNBOUNDED_TRIALS)),
        ("TRACE_LEVEL", "1"),
    ]
    entries += [(key.upper(), str(value)) for key, value in overrides.items()]
    return [f"{key} = {value}" if value else key for key, value in entries]


def _parse_trial_count(stdout: str) -> int | None:
    """Return the trial count from LKH's final summary, if it was printed."""
    for line in stdout.splitlines():
        match = _TRIAL_COUNT_PATTERN.match(line)
        if match is not None:
            return int(match.group(1))
    return None


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
    validate_capacity(instance, routes, solver="lkh")
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
