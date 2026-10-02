"""Tests for the Concorde and LKH solver wrappers.

Unit tests drive fake solver scripts through the real subprocess plumbing;
integration tests (marked ``integration``) run the vendored binaries.
"""

import sys
from pathlib import Path

import pytest

from solvers import (
    SolverError,
    SolverExecutionError,
    SolverTimeoutError,
    solve,
    solve_concorde,
    solve_lkh,
)

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
VENDOR_DIRECTORY = Path(__file__).resolve().parents[1] / "vendor"

_FAKE_CONCORDE_SOURCE = r"""
import sys
from pathlib import Path

arguments = sys.argv[1:]
output_path = Path(arguments[arguments.index("-o") + 1])
output_path.write_text("4\n0 1 2 3\n")
"""

_FAKE_LKH_SOURCE = r"""
import sys
from pathlib import Path

parameter_path = Path(sys.argv[1])
parameters = {}
for line in parameter_path.read_text().splitlines():
    if "=" in line:
        key, _, value = line.partition("=")
        parameters[key.strip()] = value.strip()
problem_path = Path(parameters["PROBLEM_FILE"])
tour_path = Path(parameters["TOUR_FILE"])
header = {}
for line in problem_path.read_text().splitlines():
    if ":" in line:
        key, _, value = line.partition(":")
        header[key.strip().upper()] = value.strip()
dimension = int(header["DIMENSION"])
problem_type = header.get("TYPE", "TSP")
if problem_type == "CVRP":
    salesmen = int(parameters.get("SALESMEN", 2))
    customers = list(range(2, dimension + 1))
    per_vehicle = max(1, -(-len(customers) // salesmen))
    tour_values = [1]
    next_depot = dimension + 1
    for start in range(0, len(customers), per_vehicle):
        if start > 0:
            tour_values.append(next_depot)
            next_depot += 1
        tour_values.extend(customers[start:start + per_vehicle])
else:
    tour_values = list(range(1, dimension + 1))
lines = [
    "NAME : fake",
    "TYPE : TOUR",
    f"DIMENSION : {len(tour_values)}",
    "TOUR_SECTION",
    *(str(value) for value in tour_values),
    "-1",
    "EOF",
]
tour_path.write_text("\n".join(lines) + "\n")
sidecar = Path(__file__).parent
(sidecar / "lkh.parameters.saved").write_text(parameter_path.read_text())
(sidecar / "lkh.problem.types").write_text(header.get("EDGE_WEIGHT_TYPE", ""))
"""

_SLEEPING_SOURCE = r"""
import time
time.sleep(30)
"""

_FAILING_SOURCE = r"""
import sys
print("something went wrong", file=sys.stderr)
sys.exit(3)
"""

_SQUARE_COORDINATES = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
_TRIANGLE_COORDINATES = [(0.0, 0.0), (1.0, 1.0), (3.0, 0.0)]
_LINE_COORDINATES = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]


def _write_script(path: Path, source: str) -> Path:
    """Write an executable Python script with the current interpreter."""
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)
    return path


def _write_tsp_instance(path: Path, coordinates: list[tuple[float, float]]) -> Path:
    """Write a small EUC_2D TSPLIB instance."""
    lines = [
        "NAME : tiny",
        "TYPE : TSP",
        f"DIMENSION : {len(coordinates)}",
        "EDGE_WEIGHT_TYPE : EUC_2D",
        "NODE_COORD_SECTION",
    ]
    lines += [f"{index} {x} {y}" for index, (x, y) in enumerate(coordinates, start=1)]
    lines.append("EOF")
    path.write_text("\n".join(lines) + "\n")
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


# ---------------------------------------------------------------------------
# Unit tests: Concorde
# ---------------------------------------------------------------------------


def test_concorde_parses_tour_file(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "fake_concorde", _FAKE_CONCORDE_SOURCE)
    result = solve_concorde(instance, executable=script)
    assert result.solver == "concorde"
    assert result.kind == "TSP"
    assert result.node_indices.tolist() == [0, 1, 2, 3]
    assert result.length == pytest.approx(4.0)


def test_concorde_rejects_non_tsp_instances(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "tiny.vrp", _SQUARE_COORDINATES, [0, 1, 1, 1], 2
    )
    script = _write_script(tmp_path / "fake_concorde", _FAKE_CONCORDE_SOURCE)
    with pytest.raises(SolverError, match="symmetric TSP"):
        solve_concorde(instance, executable=script)


def test_concorde_missing_executable_is_reported(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    with pytest.raises(FileNotFoundError, match="missing or not executable"):
        solve_concorde(instance, executable=tmp_path / "does_not_exist")


def test_concorde_timeout_is_reported(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "sleeping_concorde", _SLEEPING_SOURCE)
    with pytest.raises(SolverTimeoutError):
        solve_concorde(instance, executable=script, timeout_seconds=0.2)


def test_concorde_nonzero_exit_is_reported(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "failing_concorde", _FAILING_SOURCE)
    with pytest.raises(SolverExecutionError, match="exited with code 3"):
        solve_concorde(instance, executable=script)


# ---------------------------------------------------------------------------
# Unit tests: LKH
# ---------------------------------------------------------------------------


def test_lkh_tsp_solution_and_parameters(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    result = solve_lkh(instance, executable=script, runs=2, seed=7)
    assert result.solver == "lkh"
    assert result.node_indices.tolist() == [0, 1, 2, 3]
    assert result.length == pytest.approx(4.0)
    parameters = (tmp_path / "lkh.parameters.saved").read_text()
    assert "RUNS = 2" in parameters
    assert "SEED = 7" in parameters
    assert "SALESMEN" not in parameters


def test_lkh_cvrp_routes_and_export(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 2
    )
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    result = solve_lkh(instance, executable=script, salesmen=2, seed=1)
    assert result.kind == "CVRP"
    assert [route.tolist() for route in result.routes] == [[0, 1, 2], [0, 3, 4]]
    assert result.length == pytest.approx(12.0)
    sequences = result.to_sequences()
    assert [sequence.num_stops for sequence in sequences] == [3, 3]
    assert all(sequence.closed for sequence in sequences)
    solution_path = result.save_solution(tmp_path / "line.sol")
    assert solution_path.read_text().splitlines() == [
        "Route #1: 2 3",
        "Route #2: 4 5",
        "Cost 12",
    ]


def test_lkh_capacity_violation_is_reported(tmp_path: Path) -> None:
    instance = _write_cvrp_instance(
        tmp_path / "line.vrp", _LINE_COORDINATES, [0, 1, 1, 1, 1], 1
    )
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    with pytest.raises(SolverExecutionError, match="above the capacity"):
        solve_lkh(instance, executable=script, salesmen=2)


def test_lkh_exact_mode_uses_unrounded_distances(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "triangle.tsp", _TRIANGLE_COORDINATES)
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    rounded = solve_lkh(instance, executable=script)
    exact = solve_lkh(instance, executable=script, distance_mode="exact")
    assert rounded.length == pytest.approx(6.0)
    assert exact.length == pytest.approx(2**0.5 + 5**0.5 + 3.0)
    assert (tmp_path / "lkh.problem.types").read_text() == "EXACT_2D"


def test_lkh_rejects_unknown_distance_mode(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    with pytest.raises(ValueError, match="distance_mode"):
        solve_lkh(instance, executable=script, distance_mode="magic")


def test_lkh_requires_salesmen_only_for_cvrp(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    with pytest.raises(SolverError, match="salesmen only applies"):
        solve_lkh(instance, executable=script, salesmen=3)


# ---------------------------------------------------------------------------
# Unit tests: result helpers and dispatcher
# ---------------------------------------------------------------------------


def test_save_tsplib_tour(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "fake_concorde", _FAKE_CONCORDE_SOURCE)
    result = solve_concorde(instance, executable=script)
    tour_path = result.save_solution(tmp_path / "square.tour")
    lines = tour_path.read_text().splitlines()
    assert lines[0] == "NAME : square"
    assert "TYPE : TOUR" in lines
    assert "DIMENSION : 4" in lines
    assert lines[lines.index("TOUR_SECTION") + 1 : lines.index("-1")] == [
        "1",
        "2",
        "3",
        "4",
    ]


def test_solve_dispatcher(tmp_path: Path) -> None:
    instance = _write_tsp_instance(tmp_path / "square.tsp", _SQUARE_COORDINATES)
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    result = solve(instance, solver="lkh", executable=script)
    assert result.solver == "lkh"
    with pytest.raises(ValueError, match="unknown solver"):
        solve(instance, solver="magic")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration tests against the vendored binaries
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not (VENDOR_DIRECTORY / "concorde").exists(),
    reason="the vendored concorde binary is missing",
)
def test_concorde_solves_burma14() -> None:
    result = solve_concorde(DATA_ROOT / "ALL_tsp" / "burma14.tsp", timeout_seconds=60)
    assert result.length == pytest.approx(3323.0)
    assert sorted(result.node_indices.tolist()) == list(range(14))


@pytest.mark.integration
@pytest.mark.skipif(
    not (VENDOR_DIRECTORY / "LKH").exists(),
    reason="the vendored LKH binary is missing",
)
def test_lkh_solves_burma14() -> None:
    result = solve_lkh(
        DATA_ROOT / "ALL_tsp" / "burma14.tsp", seed=1, timeout_seconds=60
    )
    assert result.length == pytest.approx(3323.0)
    assert sorted(result.node_indices.tolist()) == list(range(14))


@pytest.mark.integration
@pytest.mark.skipif(
    not (VENDOR_DIRECTORY / "LKH").exists(),
    reason="the vendored LKH binary is missing",
)
def test_lkh_solves_cvrp_a_n32_k5() -> None:
    result = solve_lkh(
        DATA_ROOT / "A" / "A-n32-k5.vrp",
        salesmen=5,
        seed=1,
        timeout_seconds=120,
    )
    assert result.length == pytest.approx(784.0)
    assert len(result.routes) == 5
    assert all(route[0] == 0 for route in result.routes)
    assert sum(route.size - 1 for route in result.routes) == 31
