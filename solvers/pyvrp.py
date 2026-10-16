"""PyVRP wrapper for capacitated vehicle routing instances.

Unlike the wrappers around the command-line binaries, PyVRP runs in-process
through its Python API.  The iterated local search reports every new best
solution through an ``IteratedLocalSearchCallbacks`` object, so the anytime
trace carries wall-clock timestamps measured by the wrapper itself.

PyVRP is a project dependency, so there is no binary to build and no
environment variable to point at one.

Upstream: https://github.com/PyVRP/PyVRP (MIT license).
"""

from __future__ import annotations

import gzip
import inspect
import shutil
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
from pyvrp import (
    ActivityType,
    CostEvaluator,
    IteratedLocalSearchCallbacks,
    IteratedLocalSearchParams,
    ProblemData,
    Result,
    Solution,
    SolveParams,
    read,
    solve,
)
from pyvrp.read import ROUND_FUNCS
from pyvrp.stop import (
    MaxIterations,
    MaxRuntime,
    MultipleCriteria,
    NoImprovement,
    StoppingCriterion,
)

from instances import Instance, load_path

from .base import (
    SolverExecutionError,
    SolverResult,
    TracePoint,
    require_cvrp,
    require_length,
    validate_capacity,
)


def solve_pyvrp(
    instance_path: str | Path,
    *,
    time_limit_seconds: float | None = None,
    max_iterations: int | None = None,
    no_improvement: int | None = None,
    seed: int = 0,
    round_func: str = "round",
) -> SolverResult:
    """Solve a CVRP instance with PyVRP's iterated local search.

    Args:
        instance_path: VRPLIB instance file, plain or gzipped.
        time_limit_seconds: Search budget passed to ``MaxRuntime``.
        max_iterations: Iteration budget passed to ``MaxIterations``.
        no_improvement: Iterations without improvement passed to
            ``NoImprovement``.  At least one of the three stopping criteria
            above must be given.
        seed: Random seed for the solver.
        round_func: Rounding applied to the instance data, one of ``"round"``,
            ``"trunc"``, ``"dimacs"``, ``"exact"``, or ``"none"``.  The
            default matches the CVRPLIB X/XL/AGS convention and the default of
            :func:`solve_hgs`.

    Returns:
        The routes, the length recomputed from the instance, the solver
        summary, and the best-feasible-cost trace.  Trace times count from
        the start of the search, the clock ``time_limit_seconds`` uses, while
        ``runtime_seconds`` covers the whole call including parsing.

    Raises:
        ValueError: If no stopping criterion is given, one of the criteria is
            not positive, or ``round_func`` is unknown.
        SolverError: If the instance is not a CVRP or has no dimension.
        SolverExecutionError: If PyVRP cannot read the instance or returns an
            unusable solution.
    """
    if round_func not in ROUND_FUNCS:
        raise ValueError(
            f"unknown round_func {round_func!r}; expected one of "
            f"{', '.join(sorted(ROUND_FUNCS))}"
        )
    stop = _stopping_criterion(
        time_limit_seconds=time_limit_seconds,
        max_iterations=max_iterations,
        no_improvement=no_improvement,
    )
    started = time.perf_counter()
    path = Path(instance_path)
    instance = load_path(path)
    require_cvrp(instance, "pyvrp")

    with tempfile.TemporaryDirectory(prefix="teaspoon-pyvrp-") as directory:
        readable = _readable_instance(path, Path(directory))
        try:
            data = read(readable, round_func=round_func)
        except (KeyError, TypeError, ValueError) as error:
            raise SolverExecutionError(
                f"pyvrp could not read {path.name}: {error}"
            ) from error
        # The trace clock starts with the search, like MaxRuntime does.
        # Counting the parsing above would stamp late improvements past the
        # budget, where the anytime metrics no longer see them.
        search_started = time.perf_counter()
        tracker = _BestCostTracker(search_started)
        params = SolveParams(ils=IteratedLocalSearchParams(callbacks=tracker))
        try:
            result = solve(data, stop, seed, collect_stats=False, params=params)
        except (RuntimeError, ValueError) as error:
            raise SolverExecutionError(
                f"pyvrp failed on {path.name}: {error}"
            ) from error
        if not result.is_feasible():
            raise SolverExecutionError(
                f"pyvrp returned an infeasible solution for {path.name}"
            )
        routes = _routes_from_solution(instance, data, result)

    length = sum(
        require_length(instance, route, closed=True, solver="pyvrp") for route in routes
    )
    trace = _trace_with_final_point(
        tracker.points, float(result.cost()), search_started
    )
    return SolverResult(
        solver="pyvrp",
        instance_path=path,
        kind=instance.kind,
        routes=routes,
        length=length,
        runtime_seconds=time.perf_counter() - started,
        command=_equivalent_command(
            path,
            time_limit_seconds=time_limit_seconds,
            max_iterations=max_iterations,
            no_improvement=no_improvement,
            seed=seed,
            round_func=round_func,
        ),
        stdout=result.summary() + "\n",
        stderr="",
        trace=trace,
    )


def pyvrp_provenance() -> tuple[str, Path]:
    """Return the installed PyVRP version and its compiled core module.

    Benchmark records store this label and the digest of the compiled core
    file, so a run stays tied to the exact solver build just like the
    command-line solvers.
    """
    core_path = Path(inspect.getfile(ProblemData))
    return f"pyvrp=={version('pyvrp')}", core_path


def _stopping_criterion(
    *,
    time_limit_seconds: float | None,
    max_iterations: int | None,
    no_improvement: int | None,
) -> StoppingCriterion:
    """Combine the requested stopping criteria into one criterion.

    Raises:
        ValueError: If every criterion is omitted or one is not positive.
    """
    criteria: list[StoppingCriterion] = []
    if time_limit_seconds is not None:
        if time_limit_seconds <= 0:
            raise ValueError(
                f"time_limit_seconds must be positive, got {time_limit_seconds}"
            )
        criteria.append(MaxRuntime(time_limit_seconds))
    if max_iterations is not None:
        if max_iterations <= 0:
            raise ValueError(f"max_iterations must be positive, got {max_iterations}")
        criteria.append(MaxIterations(max_iterations))
    if no_improvement is not None:
        if no_improvement <= 0:
            raise ValueError(f"no_improvement must be positive, got {no_improvement}")
        criteria.append(NoImprovement(no_improvement))
    if not criteria:
        raise ValueError(
            "provide at least one of time_limit_seconds, max_iterations or "
            "no_improvement"
        )
    if len(criteria) == 1:
        return criteria[0]
    return MultipleCriteria(criteria)


def _readable_instance(path: Path, directory: Path) -> Path:
    """Return a plain-text copy of ``path``, expanding gzip if necessary.

    ``vrplib``, the parser behind ``pyvrp.read``, opens files in text mode and
    cannot read gzip directly.
    """
    if not path.name.endswith(".gz"):
        return path
    destination = directory / path.name.removesuffix(".gz")
    with gzip.open(path, "rb") as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    return destination


class _BestCostTracker(IteratedLocalSearchCallbacks):
    """Collect feasible best-cost observations during a PyVRP run.

    PyVRP calls :meth:`on_best` whenever the search improves on the best
    solution.  Costs are recomputed with zero penalties, so they match
    ``Result.cost``; infeasible candidates are skipped.

    Attributes:
        points: Best-cost observations in wall-clock order, appended to for
            the duration of the run.
    """

    def __init__(self, started: float) -> None:
        self._started = started
        self.points: list[TracePoint] = []

    def on_best(self, best: Solution) -> None:
        """Record the objective of a new feasible best solution."""
        if not best.is_feasible():
            return
        penalties = [0.0] * len(best.excess_load())
        cost = CostEvaluator(penalties, 0.0, 0.0).cost(best)
        self.points.append(
            TracePoint(
                wall_seconds=time.perf_counter() - self._started,
                best_cost=float(cost),
            )
        )


def _trace_with_final_point(
    points: list[TracePoint],
    final_cost: float,
    started: float,
) -> tuple[TracePoint, ...]:
    """Ensure the trace ends with the final best cost.

    PyVRP does not call ``on_best`` for the initial solution, so a run that
    never improves would otherwise report an empty trace.
    """
    if not points or points[-1].best_cost != final_cost:
        points.append(
            TracePoint(
                wall_seconds=time.perf_counter() - started,
                best_cost=final_cost,
            )
        )
    return tuple(points)


def _routes_from_solution(
    instance: Instance,
    data: ProblemData,
    result: Result,
) -> tuple[np.ndarray, ...]:
    """Convert the best solution into depot-anchored node-index routes.

    The trailing depot visit is dropped so every route matches the project's
    convention of one leading depot followed by its customers.

    Raises:
        SolverExecutionError: If a route uses an unsupported activity, does
            not return to its depot, or the routes do not cover every customer
            exactly once.
    """
    routes: list[np.ndarray] = []
    for position, route in enumerate(result.best.routes(), start=1):
        nodes = []
        for activity in route:
            if activity.type == ActivityType.DEPOT:
                nodes.append(int(data.depot(activity.idx).location))
            elif activity.type == ActivityType.CLIENT:
                nodes.append(int(data.client(activity.idx).location))
            else:
                raise SolverExecutionError(
                    f"pyvrp route {position} contains an unsupported "
                    f"activity of type {activity.type}"
                )
        if len(nodes) < 2 or nodes[0] != nodes[-1]:
            raise SolverExecutionError(
                f"pyvrp route {position} does not return to its depot"
            )
        routes.append(np.array(nodes[:-1], dtype=np.int64))
    if not routes:
        raise SolverExecutionError("pyvrp returned no routes")
    if instance.dimension is None:
        raise SolverExecutionError("cannot validate routes without a dimension")

    depot = int(routes[0][0])
    covered: list[int] = []
    for route in routes:
        if int(route[0]) != depot:
            raise SolverExecutionError(
                "pyvrp routes do not all start at the same depot"
            )
        covered.extend(int(index) for index in route[1:])
    expected = [index for index in range(instance.dimension) if index != depot]
    if sorted(covered) != expected:
        raise SolverExecutionError(
            "pyvrp routes do not cover every customer exactly once"
        )
    validate_capacity(instance, tuple(routes), solver="pyvrp")
    return tuple(routes)


def _equivalent_command(
    path: Path,
    *,
    time_limit_seconds: float | None,
    max_iterations: int | None,
    no_improvement: int | None,
    seed: int,
    round_func: str,
) -> tuple[str, ...]:
    """Return the PyVRP command-line invocation that mirrors this run.

    PyVRP runs in-process, so the wrapper records the equivalent CLI command
    instead of the subprocess command line used by the binary solvers.
    """
    command = ["pyvrp", str(path)]
    if time_limit_seconds is not None:
        command += ["--max_runtime", f"{time_limit_seconds:g}"]
    if max_iterations is not None:
        command += ["--max_iterations", str(max_iterations)]
    if no_improvement is not None:
        command += ["--no_improvement", str(no_improvement)]
    command += ["--round_func", round_func, "--seed", str(seed)]
    return tuple(command)
