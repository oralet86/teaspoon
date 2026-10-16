"""Tests for the in-process PyVRP solver wrapper.

PyVRP is a project dependency, so these tests need no vendored binary and run
by default.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from solvers import SolverError, pyvrp_provenance, solve, solve_pyvrp

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"

_LINE_COORDINATES = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
_LINE_DEMANDS = [0, 1, 1, 1, 1]
_LINE_BKS = 12.0


def _write_cvrp_instance(
    path: Path,
    coordinates: list[tuple[float, float]],
    demands: list[int],
    capacity: int,
) -> Path:
    """Write a small CVRP instance with a single depot at node 1."""
    lines = [
        "NAME : tiny_cvrp",
        "TYPE : CVRP",
        f"DIMENSION : {len(coordinates)}",
        "EDGE_WEIGHT_TYPE : EUC_2D",
        f"CAPACITY : {capacity}",
        "NODE_COORD_SECTION",
    ]
    lines += [f"{index} {x} {y}" for index, (x, y) in enumerate(coordinates, start=1)]
    lines.append("DEMAND_SECTION")
    lines += [f"{index} {demand}" for index, demand in enumerate(demands, start=1)]
    lines += ["DEPOT_SECTION", "1", "-1", "EOF"]
    path.write_text("\n".join(lines) + "\n")
    return path


def _write_tsp_instance(path: Path) -> Path:
    """Write a small EUC_2D TSP instance."""
    lines = [
        "NAME : tiny",
        "TYPE : TSP",
        "DIMENSION : 3",
        "EDGE_WEIGHT_TYPE : EUC_2D",
        "NODE_COORD_SECTION",
        "1 0 0",
        "2 1 0",
        "3 0 1",
        "EOF",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def _write_line_instance(path: Path) -> Path:
    """Write the five-node line CVRP with capacity 2."""
    return _write_cvrp_instance(path, _LINE_COORDINATES, _LINE_DEMANDS, 2)


def test_pyvrp_solution_and_trace(tmp_path: Path) -> None:
    instance = _write_line_instance(tmp_path / "line.vrp")
    result = solve_pyvrp(instance, time_limit_seconds=1.0, seed=0)
    assert result.solver == "pyvrp"
    assert result.kind == "CVRP"
    assert all(int(route[0]) == 0 for route in result.routes)
    covered = sorted(int(index) for route in result.routes for index in route[1:])
    assert covered == [1, 2, 3, 4]
    assert result.length == pytest.approx(_LINE_BKS)
    assert result.trace
    assert [point.wall_seconds for point in result.trace] == sorted(
        point.wall_seconds for point in result.trace
    )
    assert result.trace[-1].best_cost == pytest.approx(result.length)
    assert "objective" in result.stdout
    assert result.command[0] == "pyvrp"
    assert result.command[result.command.index("--seed") + 1] == "0"
    assert "--max_runtime" in result.command


def test_pyvrp_reads_gzipped_instance(tmp_path: Path) -> None:
    source = _write_line_instance(tmp_path / "line.vrp")
    gzipped = tmp_path / "line.vrp.gz"
    with source.open("rb") as source_file, gzip.open(gzipped, "wb") as target:
        target.write(source_file.read())
    result = solve_pyvrp(gzipped, max_iterations=500, seed=0)
    assert result.length == pytest.approx(_LINE_BKS)
    assert result.trace


def test_pyvrp_rejects_non_cvrp(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "tiny.tsp")
    with pytest.raises(SolverError, match="CVRP instances only"):
        solve_pyvrp(instance, time_limit_seconds=1.0)


def test_pyvrp_requires_stopping_criterion(tmp_path: Path) -> None:
    instance = _write_line_instance(tmp_path / "line.vrp")
    with pytest.raises(ValueError, match="at least one"):
        solve_pyvrp(instance)


def test_pyvrp_rejects_non_positive_budget(tmp_path: Path) -> None:
    instance = _write_line_instance(tmp_path / "line.vrp")
    with pytest.raises(ValueError, match="must be positive"):
        solve_pyvrp(instance, time_limit_seconds=0)


def test_pyvrp_rejects_unknown_rounding(tmp_path: Path) -> None:
    instance = _write_line_instance(tmp_path / "line.vrp")
    with pytest.raises(ValueError, match="unknown round_func"):
        solve_pyvrp(instance, time_limit_seconds=1.0, round_func="bogus")


def test_solve_dispatches_to_pyvrp(tmp_path: Path) -> None:
    instance = _write_line_instance(tmp_path / "line.vrp")
    result = solve(instance, solver="pyvrp", time_limit_seconds=1.0, seed=1)
    assert result.solver == "pyvrp"
    assert result.command[result.command.index("--seed") + 1] == "1"


def test_pyvrp_provenance_points_at_core_module() -> None:
    label, path = pyvrp_provenance()
    assert label.startswith("pyvrp==")
    assert path.is_file()


@pytest.mark.integration
def test_pyvrp_solves_small_x_instance() -> None:
    instance = DATA_ROOT / "X" / "X-n101-k25.vrp"
    if not instance.is_file():
        pytest.skip("the CVRPLIB X set is not downloaded")
    result = solve_pyvrp(instance, time_limit_seconds=5, seed=1)
    assert result.kind == "CVRP"
    assert sum(route.size - 1 for route in result.routes) == 100
    assert result.trace
