"""Tests for anytime metrics and the benchmark runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bench import RunSpec, load_bks, run_specs
from bench.metrics import best_cost_at, gap_at, primal_integral, time_to_target
from bench.report import load_records, summarize, write_summary_csv
from solvers import TracePoint

_TRACE = (
    TracePoint(wall_seconds=1.0, best_cost=110.0),
    TracePoint(wall_seconds=5.0, best_cost=105.0),
    TracePoint(wall_seconds=9.0, best_cost=100.0),
)

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
"""


def _write_script(path: Path, source: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)
    return path


def _write_instance_with_bks(tmp_path: Path) -> Path:
    """Write a five-node CVRP with one-customer-per-route companion BKS."""
    coordinates = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
    lines = [
        "NAME : tiny_cvrp",
        "TYPE : CVRP",
        f"DIMENSION : {len(coordinates)}",
        "EDGE_WEIGHT_TYPE : EUC_2D",
        "CAPACITY : 2",
        "NODE_COORD_SECTION",
    ]
    lines += [f"{index} {x} {y}" for index, (x, y) in enumerate(coordinates, 1)]
    lines += ["DEMAND_SECTION", "1 0", "2 1", "3 1", "4 1", "5 1"]
    lines += ["DEPOT_SECTION", "1", "-1", "EOF"]
    instance_path = tmp_path / "line.vrp"
    instance_path.write_text("\n".join(lines) + "\n")
    solution_path = tmp_path / "line.sol"
    solution_path.write_text(
        "Route #1: 1\nRoute #2: 2\nRoute #3: 3\nRoute #4: 4\nCost 20\n"
    )
    return instance_path


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_best_cost_at() -> None:
    assert best_cost_at(_TRACE, 0.5) is None
    assert best_cost_at(_TRACE, 1.0) == 110.0
    assert best_cost_at(_TRACE, 6.0) == 105.0
    assert best_cost_at(_TRACE, 100.0) == 100.0


def test_gap_at() -> None:
    assert gap_at(_TRACE, 100.0, 5.0) == pytest.approx(0.05)
    assert gap_at(_TRACE, 100.0, 0.5) is None


def test_time_to_target_and_censoring() -> None:
    assert time_to_target(_TRACE, 100.0, 0.05, budget_seconds=10.0) == 5.0
    assert time_to_target(_TRACE, 100.0, 0.0, budget_seconds=10.0) == 9.0
    assert time_to_target(_TRACE, 100.0, 0.0, budget_seconds=5.0) is None


def test_primal_integral_hand_computed() -> None:
    # 5 s at 10 % + 4 s at 5 % + 1 s at 0 %, over a 10 s budget.
    assert primal_integral(_TRACE, 100.0, budget_seconds=10.0) == pytest.approx(0.07)
    assert primal_integral(
        _TRACE, 100.0, budget_seconds=10.0, normalize=False
    ) == pytest.approx(0.7)


def test_empty_trace_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        best_cost_at((), 1.0)


# ---------------------------------------------------------------------------
# Runner and report
# ---------------------------------------------------------------------------


def test_load_bks_recomputes_from_companion(tmp_path: Path) -> None:
    instance_path = _write_instance_with_bks(tmp_path)
    assert load_bks(instance_path) == pytest.approx(20.0)


def test_run_specs_records_trace_and_metrics(tmp_path: Path) -> None:
    instance_path = _write_instance_with_bks(tmp_path)
    script = _write_script(tmp_path / "fake_hgs", _FAKE_HGS_SOURCE)
    spec = RunSpec(
        instance_path=instance_path,
        solver="hgs",
        seed=0,
        budget_seconds=2,
    )
    run_dir = tmp_path / "run"
    records = run_specs([spec], run_dir, executables={"hgs": script})
    assert len(records) == 1
    record = records[0]
    assert record.status == "ok"
    assert record.best_cost == pytest.approx(20.0)
    assert record.gap == pytest.approx(0.0)
    assert record.trace_points == 3
    assert record.time_to_target_1pct is None
    assert record.primal_integral is not None
    assert (run_dir / "runs.jsonl").is_file()
    assert record.trace_file is not None
    assert (run_dir / record.trace_file).is_file()
    assert record.log_file is not None
    assert (run_dir / record.log_file).is_file()
    assert record.command[0] == str(script)


def test_report_round_trip(tmp_path: Path) -> None:
    instance_path = _write_instance_with_bks(tmp_path)
    script = _write_script(tmp_path / "fake_hgs", _FAKE_HGS_SOURCE)
    spec = RunSpec(
        instance_path=instance_path,
        solver="hgs",
        seed=1,
        budget_seconds=2,
    )
    run_dir = tmp_path / "run"
    run_specs([spec], run_dir, executables={"hgs": script})
    records = load_records(run_dir)
    assert "hgs" in summarize(records)
    summary_path = write_summary_csv(run_dir, records)
    assert summary_path.is_file()
    assert "gap" in summary_path.read_text().splitlines()[0]
