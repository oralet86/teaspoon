"""E0c analysis: slowdown per operator and state, clock, and drift over time.

Reads an E0c run directory (``runs/e0-contention/<timestamp>``) and writes,
into that directory (not the journal, until the result is accepted):

- ``slowdown.csv``: per phase, state and operator, the median time per
  application over workers and rounds, and its ratio to the first
  single-worker phase;
- ``phases.json``: per phase, the median and worst ratio, the core
  frequencies, and the drift (last minute against first minute);
- ``slowdown.png`` and ``drift.png``.

Example::

    uv run python -m aos.analysis.e0_contention runs/e0-contention/<timestamp>
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import source_note

LOGGER = logging.getLogger(__name__)

SCRIPT = "aos/analysis/e0_contention.py"
DRIFT_WINDOW_S = 60.0
"""Length of the first and last windows compared for drift."""


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read ``rounds.csv``."""
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def median_times(rows: list[dict[str, str]]) -> dict[tuple[int, str, str], float]:
    """Median time per application by (phase, state, operator)."""
    groups: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    for row in rows:
        key = (int(row["phase"]), row["state"], row["operator"])
        groups[key].append(float(row["mean_time_ns"]))
    return {key: statistics.median(values) for key, values in groups.items()}


def summarise(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the per-cell slowdowns and the per-phase summary.

    Raises:
        ValueError: If no phase ran with a single worker.
    """
    workers = {int(row["phase"]): int(row["workers"]) for row in rows}
    baseline = min(
        (phase for phase, count in workers.items() if count == 1), default=None
    )
    if baseline is None:
        raise ValueError("no single-worker phase to compare against")
    times = median_times(rows)
    cells = [
        {
            "phase": phase,
            "workers": workers[phase],
            "state": state,
            "operator": operator,
            "median_time_ns": value,
            "ratio": value / times[(baseline, state, operator)],
        }
        for (phase, state, operator), value in sorted(times.items())
    ]
    phases = []
    for phase in sorted(workers):
        ratios = [cell for cell in cells if cell["phase"] == phase]
        phase_rows = [row for row in rows if int(row["phase"]) == phase]
        khz = [float(row["average_khz"]) for row in phase_rows if row["average_khz"]]
        worst = max(ratios, key=lambda cell: cell["ratio"])
        phases.append(
            {
                "phase": phase,
                "workers": workers[phase],
                "baseline_phase": baseline,
                "median_ratio": statistics.median(cell["ratio"] for cell in ratios),
                "max_ratio": worst["ratio"],
                "worst_cell": f"{worst['state']}/{worst['operator']}",
                "median_khz": statistics.median(khz) if khz else None,
                "min_khz": min(khz) if khz else None,
                "max_khz": max(khz) if khz else None,
                "drift": drift(phase_rows),
                "rounds_per_worker": statistics.median(
                    max(int(row["round"]) for row in phase_rows if row["worker"] == w)
                    + 1
                    for w in {row["worker"] for row in phase_rows}
                ),
            }
        )
    return cells, phases


def drift(rows: list[dict[str, str]]) -> float | None:
    """Median over cells of (last-window time / first-window time) in a phase.

    ``None`` when the phase is shorter than two windows.
    """
    end = max(float(row["elapsed_s"]) for row in rows)
    if end < 2 * DRIFT_WINDOW_S:
        return None
    first: dict[tuple[str, str], list[float]] = defaultdict(list)
    last: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        elapsed = float(row["elapsed_s"])
        key = (row["state"], row["operator"])
        if elapsed <= DRIFT_WINDOW_S:
            first[key].append(float(row["mean_time_ns"]))
        elif elapsed >= end - DRIFT_WINDOW_S:
            last[key].append(float(row["mean_time_ns"]))
    ratios = [
        statistics.median(last[key]) / statistics.median(first[key])
        for key in first
        if key in last
    ]
    return statistics.median(ratios) if ratios else None


def plot_slowdown(cells: list[dict[str, Any]], path: Path, note: str) -> None:
    """Ratio to the single-worker baseline per operator, one panel per state."""
    states = sorted({cell["state"] for cell in cells})
    phases = sorted({(cell["phase"], cell["workers"]) for cell in cells})
    figure, axes = plt.subplots(
        len(states), 1, figsize=(10, 3 * len(states)), squeeze=False
    )
    for axis, state in zip(axes[:, 0], states, strict=True):
        operators = sorted(
            {cell["operator"] for cell in cells if cell["state"] == state}
        )
        for phase, workers in phases:
            values = {
                cell["operator"]: cell["ratio"]
                for cell in cells
                if cell["state"] == state and cell["phase"] == phase
            }
            axis.plot(
                range(len(operators)),
                [values[name] for name in operators],
                marker="o",
                linestyle="none",
                label=f"phase {phase}: {workers} workers",
            )
        axis.axhline(1.02, color="red", linestyle="--", linewidth=0.8)
        axis.axhline(0.98, color="red", linestyle="--", linewidth=0.8)
        axis.set_xticks(range(len(operators)), operators, rotation=60, fontsize=7)
        axis.set_ylabel("time / baseline")
        axis.set_title(state, fontsize=9)
    axes[0, 0].legend(fontsize=7)
    figure.text(0.01, 0.005, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.02, 1, 1))
    figure.savefig(path, dpi=130)
    plt.close(figure)


def plot_drift(rows: list[dict[str, str]], path: Path, note: str) -> None:
    """Round time and core frequency against time, one line per phase."""
    by_round: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    khz: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    for row in rows:
        key = (int(row["phase"]), row["worker"], row["round"])
        by_round[key].append(float(row["mean_time_ns"]))
        if row["average_khz"]:
            khz[key].append(float(row["average_khz"]))
    elapsed = {
        (int(row["phase"]), row["worker"], row["round"]): float(row["elapsed_s"])
        for row in rows
    }
    workers = {int(row["phase"]): row["workers"] for row in rows}
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for phase in sorted(workers):
        keys = sorted(key for key in by_round if key[0] == phase)
        label = f"phase {phase}: {workers[phase]} workers"
        top.scatter(
            [elapsed[key] for key in keys],
            [sum(by_round[key]) / 1e6 for key in keys],
            s=3,
            label=label,
        )
        keys = [key for key in keys if khz[key]]
        bottom.scatter(
            [elapsed[key] for key in keys],
            [statistics.median(khz[key]) / 1e6 for key in keys],
            s=3,
            label=label,
        )
    top.set_ylabel("sum of mean times per round (ms)")
    bottom.set_ylabel("core frequency (GHz)")
    bottom.set_xlabel("seconds into phase")
    top.legend(markerscale=4, fontsize=7)
    figure.text(0.01, 0.005, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.02, 1, 1))
    figure.savefig(path, dpi=130)
    plt.close(figure)


def analyse(directory: Path) -> list[dict[str, Any]]:
    """Write the summaries and figures into ``directory``; return the phases."""
    rows = read_rows(directory / "rounds.csv")
    cells, phases = summarise(rows)
    with (directory / "slowdown.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cells[0]))
        writer.writeheader()
        writer.writerows(cells)
    (directory / "phases.json").write_text(json.dumps(phases, indent=2) + "\n")
    note = source_note([f"{directory.name}/rounds.csv"], SCRIPT, "seed 0")
    plot_slowdown(cells, directory / "slowdown.png", note)
    plot_drift(rows, directory / "drift.png", note)
    for phase in phases:
        LOGGER.info("%s", json.dumps(phase))
    return phases


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.analysis.e0_contention", description=__doc__
    )
    parser.add_argument("run_directory", type=Path)
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    analyse(arguments.run_directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
