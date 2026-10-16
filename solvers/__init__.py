"""Wrappers around the bundled command-line routing solvers and PyVRP."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .base import (
    SolverCancelledError,
    SolverError,
    SolverExecutionError,
    SolverResult,
    SolverTimeoutError,
    TracePoint,
    find_executable,
)
from .concorde import solve_concorde
from .filo2 import solve_filo2
from .hgs import solve_hgs
from .lkh import (
    LkhReport,
    LkhTracePoint,
    read_parameter_file,
    run_lkh_reported,
    solve_lkh,
)
from .pyvrp import pyvrp_provenance, solve_pyvrp

__all__ = [
    "LkhReport",
    "LkhTracePoint",
    "SolverCancelledError",
    "SolverError",
    "SolverExecutionError",
    "SolverResult",
    "SolverTimeoutError",
    "TracePoint",
    "find_executable",
    "pyvrp_provenance",
    "read_parameter_file",
    "run_lkh_reported",
    "solve",
    "solve_concorde",
    "solve_filo2",
    "solve_hgs",
    "solve_lkh",
    "solve_pyvrp",
]

_SOLVER_NAMES = ("concorde", "lkh", "hgs", "filo2", "pyvrp")


def solve(
    instance_path: str | Path,
    solver: Literal["concorde", "lkh", "hgs", "filo2", "pyvrp"] = "concorde",
    **options: Any,
) -> SolverResult:
    """Dispatch to a solver by name.

    Args:
        instance_path: TSPLIB/VRPLIB instance file.
        solver: ``"concorde"`` (symmetric TSP), ``"lkh"`` (TSP and CVRP),
            ``"hgs"`` (CVRP), ``"filo2"`` (large-scale CVRP), or ``"pyvrp"``
            (CVRP, in-process).
        **options: Keyword arguments forwarded to the solver function.

    Raises:
        ValueError: If the solver name is unknown.
    """
    if solver == "concorde":
        return solve_concorde(instance_path, **options)
    if solver == "lkh":
        return solve_lkh(instance_path, **options)
    if solver == "hgs":
        return solve_hgs(instance_path, **options)
    if solver == "filo2":
        return solve_filo2(instance_path, **options)
    if solver == "pyvrp":
        return solve_pyvrp(instance_path, **options)
    raise ValueError(
        f"unknown solver {solver!r}; expected one of {', '.join(_SOLVER_NAMES)}"
    )
