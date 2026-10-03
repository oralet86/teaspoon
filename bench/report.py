"""Summaries of a completed benchmark run directory."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_SUMMARY_FIELDS = (
    "instance",
    "solver",
    "seed",
    "budget_seconds",
    "status",
    "bks",
    "best_cost",
    "gap",
    "time_to_target_1pct",
    "time_to_target_0_5pct",
    "primal_integral",
    "runtime_seconds",
    "trace_points",
)


def load_records(run_dir: str | Path) -> list[dict[str, Any]]:
    """Read the ``runs.jsonl`` records of a run directory.

    Raises:
        FileNotFoundError: If the directory holds no ``runs.jsonl``.
    """
    path = Path(run_dir) / "runs.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def summarize(records: Sequence[Mapping[str, Any]]) -> str:
    """Return a per-solver summary table as text."""
    solvers = sorted({str(record["solver"]) for record in records})
    header = (
        f"{'solver':<8} {'runs':>5} {'ok':>4} {'mean gap %':>11} "
        f"{'mean ttt 1% s':>14} {'reached 1%':>11} {'mean PI':>9}"
    )
    lines = [header, "-" * len(header)]
    for solver in solvers:
        group = [record for record in records if record["solver"] == solver]
        successes = [record for record in group if record["status"] == "ok"]
        gaps = [float(r["gap"]) for r in successes if r["gap"] is not None]
        times = [
            float(r["time_to_target_1pct"])
            for r in successes
            if r["time_to_target_1pct"] is not None
        ]
        integrals = [
            float(r["primal_integral"])
            for r in successes
            if r["primal_integral"] is not None
        ]
        reached = f"{len(times)}/{len(successes)}" if successes else "-"
        lines.append(
            f"{solver:<8} {len(group):>5} {len(successes):>4} "
            f"{_mean(gaps) * 100:>11.3f} {_mean(times):>14.2f} "
            f"{reached:>11} {_mean(integrals):>9.4f}"
        )
    return "\n".join(lines)


def write_summary_csv(
    run_dir: str | Path, records: Sequence[Mapping[str, Any]]
) -> Path:
    """Write the per-run table to ``summary.csv`` and return its path."""
    path = Path(run_dir) / "summary.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_SUMMARY_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in _SUMMARY_FIELDS})
    return path


def _mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean, or ``nan`` for an empty sequence."""
    if not values:
        return float("nan")
    return sum(values) / len(values)
