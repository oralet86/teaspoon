"""Wrappers around the bundled Concorde and LKH-3 command-line solvers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .base import (
    SolverError,
    SolverExecutionError,
    SolverResult,
    SolverTimeoutError,
    find_executable,
)
from .concorde import solve_concorde
from .lkh import solve_lkh

__all__ = [
    "SolverError",
    "SolverExecutionError",
    "SolverResult",
    "SolverTimeoutError",
    "find_executable",
    "solve",
    "solve_concorde",
    "solve_lkh",
]


def solve(
    instance_path: str | Path,
    solver: Literal["concorde", "lkh"] = "concorde",
    **options: Any,
) -> SolverResult:
    """Dispatch to a solver by name.

    Args:
        instance_path: TSPLIB/VRPLIB instance file.
        solver: ``"concorde"`` (symmetric TSP) or ``"lkh"`` (TSP and CVRP).
        **options: Keyword arguments forwarded to the solver function.

    Raises:
        ValueError: If the solver name is unknown.
    """
    if solver == "concorde":
        return solve_concorde(instance_path, **options)
    if solver == "lkh":
        return solve_lkh(instance_path, **options)
    raise ValueError(f"unknown solver {solver!r}; expected 'concorde' or 'lkh'")
