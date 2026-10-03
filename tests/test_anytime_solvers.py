"""Tests for the HGS-CVRP and FILO2 wrappers and their anytime traces.

Fake solver scripts drive the real subprocess plumbing, following the
pattern used for Concorde and LKH in ``test_solvers.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from solvers import (
    SolverError,
    SolverExecutionError,
    SolverTimeoutError,
    solve_filo2,
    solve_hgs,
)

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
VENDOR_DIRECTORY = Path(__file__).resolve().parents[1] / "vendor"

_FAKE_HGS_SOURCE = r"""
import sys
from pathlib import Path

def read_sections(text):
    header = {}
    sections = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith("_SECTION"):
            current = line
            sections[current] = []
            continue
        if line == "EOF":
            break
        if current is not None:
            sections[current].append(line)
        elif ":" in line:
            key, _, value = line.partition(":")
            header[key.strip().upper()] = value.strip()
    return header, sections

instance_path = Path(sys.argv[1])
solution_path = Path(sys.argv[2])
header, sections = read_sections(instance_path.read_text())
dimension = int(header["DIMENSION"])
for step in (1, 2, 3):
    print(f"It  {step * 10} {step * 10} | T(s) {step}.00 | "
          f"Feas 10 {100 - step}.00 101.00 | Inf 0 1e30 1e30 | "
          f"Div 0.5 0.5 | Feas 1.0 1.0 | Pen 0 0")
routes = [f"Route #{index}: {index}" for index in range(1, dimension)]
solution_path.write_text("\n".join(routes + ["Cost 0"]) + "\n")
print("----- GENETIC ALGORITHM FINISHED AFTER 30 ITERATIONS. TIME SPENT: 3.0")
"""

_FAKE_HGS_OVERLOAD_SOURCE = r"""
import sys
from pathlib import Path

def read_sections(text):
    header = {}
    sections = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith("_SECTION"):
            current = line
            sections[current] = []
            continue
        if line == "EOF":
            break
        if current is not None:
            sections[current].append(line)
        elif ":" in line:
            key, _, value = line.partition(":")
            header[key.strip().upper()] = value.strip()
    return header, sections

instance_path = Path(sys.argv[1])
solution_path = Path(sys.argv[2])
header, sections = read_sections(instance_path.read_text())
dimension = int(header["DIMENSION"])
customers = " ".join(str(index) for index in range(1, dimension))
solution_path.write_text(f"Route #1: {customers}\nCost 0\n")
"""

_FAKE_FILO2_SOURCE = r"""
import sys
from pathlib import Path

def read_sections(text):
    header = {}
    sections = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith("_SECTION"):
            current = line
            sections[current] = []
            continue
        if line == "EOF":
            break
        if current is not None:
            sections[current].append(line)
        elif ":" in line:
            key, _, value = line.partition(":")
            header[key.strip().upper()] = value.strip()
    return header, sections

instance_path = Path(sys.argv[1])
arguments = sys.argv[2:]
seed = arguments[arguments.index("--seed") + 1]
outpath = Path(arguments[arguments.index("--outpath") + 1])
header, sections = read_sections(instance_path.read_text())
dimension = int(header["DIMENSION"])
outpath.mkdir(parents=True, exist_ok=True)
print("\x1b[1m     %   Iterations    Objective   Routes       Iter/s\x1b[0m")
print(" 40.00        11103        99       26      5523.00")
print(" 80.00        18280        98       26      4558.00")
routes = [f"Route #{index}: {index}" for index in range(1, dimension)]
(outpath / f"{instance_path.name}_seed-{seed}.vrp.sol").write_text(
    "\n".join(routes + ["Cost 98.000000"]) + "\n"
)
print(f"obj = 98, n. routes = {dimension - 1}")
"""

_SLEEPING_SOURCE = r"""
import time
time.sleep(30)
"""


def _write_script(path: Path, source: str) -> Path:
    """Write an executable Python script with the current interpreter."""
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)
    return path


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


_LINE_COORDINATES = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]


def test_hgs_solution_and_trace(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 2
    )
    script = _write_script(tmp_path / "fake_hgs", _FAKE_HGS_SOURCE)
    result = solve_hgs(instance, executable=script, time_limit_seconds=3, seed=1)
    assert result.solver == "hgs"
    assert result.kind == "CVRP"
    assert [route.tolist() for route in result.routes] == [
        [0, 1],
        [0, 2],
        [0, 3],
        [0, 4],
    ]
    assert result.length == pytest.approx(20.0)
    assert [point.best_cost for point in result.trace] == [99.0, 98.0, 97.0]
    assert [point.wall_seconds for point in result.trace] == sorted(
        point.wall_seconds for point in result.trace
    )
    assert "-t" in result.command


def test_hgs_rejects_non_cvrp(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "tiny.tsp")
    script = _write_script(tmp_path / "fake_hgs", _FAKE_HGS_SOURCE)
    with pytest.raises(SolverError, match="CVRP instances only"):
        solve_hgs(instance, executable=script)


def test_hgs_capacity_violation_is_reported(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 1
    )
    script = _write_script(tmp_path / "fake_hgs", _FAKE_HGS_OVERLOAD_SOURCE)
    with pytest.raises(SolverExecutionError, match="above the capacity"):
        solve_hgs(instance, executable=script)


def test_hgs_timeout_is_reported(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 2
    )
    script = _write_script(tmp_path / "sleeping_hgs", _SLEEPING_SOURCE)
    with pytest.raises(SolverTimeoutError):
        solve_hgs(instance, executable=script, timeout_seconds=0.2)


def test_filo2_solution_and_trace(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 2
    )
    script = _write_script(tmp_path / "fake_filo2", _FAKE_FILO2_SOURCE)
    result = solve_filo2(instance, executable=script, optimization_seconds=2, seed=4)
    assert result.solver == "filo2"
    assert [route.tolist() for route in result.routes] == [
        [0, 1],
        [0, 2],
        [0, 3],
        [0, 4],
    ]
    assert [point.best_cost for point in result.trace] == [99.0, 98.0]
    assert "--optimization-seconds" in result.command


def test_filo2_rejects_non_positive_budget(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 2
    )
    script = _write_script(tmp_path / "fake_filo2", _FAKE_FILO2_SOURCE)
    with pytest.raises(ValueError, match="must be positive"):
        solve_filo2(instance, executable=script, optimization_seconds=0)


def test_filo2_timeout_is_reported(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 2
    )
    script = _write_script(tmp_path / "sleeping_filo2", _SLEEPING_SOURCE)
    with pytest.raises(SolverTimeoutError):
        solve_filo2(
            instance, executable=script, optimization_seconds=2, timeout_seconds=0.2
        )


# ---------------------------------------------------------------------------
# Integration tests against the vendored binaries
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not (VENDOR_DIRECTORY / "hgs").exists(),
    reason="the vendored HGS binary is missing",
)
def test_hgs_solves_small_x_instance() -> None:
    instance = DATA_ROOT / "X" / "X-n101-k25.vrp"
    if not instance.is_file():
        pytest.skip("the CVRPLIB X set is not downloaded")
    result = solve_hgs(instance, time_limit_seconds=5, seed=1, timeout_seconds=120)
    assert result.kind == "CVRP"
    assert sum(route.size - 1 for route in result.routes) == 100
    assert result.trace


@pytest.mark.integration
@pytest.mark.skipif(
    not (VENDOR_DIRECTORY / "filo2").exists(),
    reason="the vendored FILO2 binary is missing",
)
def test_filo2_solves_small_x_instance() -> None:
    instance = DATA_ROOT / "X" / "X-n101-k25.vrp"
    if not instance.is_file():
        pytest.skip("the CVRPLIB X set is not downloaded")
    result = solve_filo2(instance, optimization_seconds=5, seed=1, timeout_seconds=120)
    assert result.kind == "CVRP"
    assert sum(route.size - 1 for route in result.routes) == 100
    assert result.trace
