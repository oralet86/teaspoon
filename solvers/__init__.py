"""Wrappers around the bundled command-line routing solvers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .base import (
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
from .lkh import solve_lkh

__all__ = [
    "SolverError",
    "SolverExecutionError",
    "SolverResult",
    "SolverTimeoutError",
    "TracePoint",
    "find_executable",
    "solve",
    "solve_concorde",
    "solve_filo2",
    "solve_hgs",
    "solve_lkh",
]

_SOLVER_NAMES = ("concorde", "lkh", "hgs", "filo2")


def solve(
    instance_path: str | Path,
    solver: Literal["concorde", "lkh", "hgs", "filo2"] = "concorde",
    **options: Any,
) -> SolverResult:
    """Dispatch to a solver by name.

    Args:
        instance_path: TSPLIB/VRPLIB instance file.
        solver: ``"concorde"`` (symmetric TSP), ``"lkh"`` (TSP and CVRP),
            ``"hgs"`` (CVRP), or ``"filo2"`` (large-scale CVRP).
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
    raise ValueError(
        f"unknown solver {solver!r}; expected one of {', '.join(_SOLVER_NAMES)}"
    )
