"""E0 analysis: summaries, the repeatability gate, and figures.

Reads an E0 run directory (``runs/e0/<timestamp>``) and writes:

- ``journal/data/claim-1/e0_costs.csv``: per (n, operator, state), the median
  cost per application over repeats and the coefficient of variation (CV)
  between repeats;
- ``e0_descents.csv``: full O7 descent times per n;
- ``e0_gate.json``: the repeatability gate;
- figures under ``journal/figures/claim-1/``.

Parallel timed workers are analysed by :mod:`aos.analysis.e0_contention`.

Example::

    uv run python -m aos.analysis.e0 runs/e0/20261016-170000
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
import numpy as np

from aos.operators import OPERATOR_IDS, OPERATORS
from aos.records import read_arrays

from . import journal_paths, source_note

LOGGER = logging.getLogger(__name__)

SCRIPT = "aos/analysis/e0.py"
GATE_CV = 0.05
"""Repeat-to-repeat CV a cell may show before the clock counts as unreliable."""


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into dictionaries."""
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write dictionaries with identical keys as CSV."""
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarise_costs(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Median cost per application and repeat-to-repeat CV per cell."""
    cells: dict[tuple[int, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        cells[(int(row["n"]), row["operator"], row["state"])].append(row)
    summary: list[dict[str, Any]] = []
    for (n, name, state), group in sorted(cells.items()):
        costs = [float(row["ns_per_application"]) for row in group]
        mean = statistics.fmean(costs)
        cv = statistics.stdev(costs) / mean if len(costs) > 1 else float("nan")
        summary.append(
            {
                "n": n,
                "operator": name,
                "id": OPERATOR_IDS[name],
                "state": state,
                "repeats": len(costs),
                "median_ns": statistics.median(costs),
                "cv_between_repeats": cv,
                "lookups_per_application": statistics.fmean(
                    float(row["lookups_per_application"]) for row in group
                ),
                "moved_per_application": statistics.fmean(
                    float(row["moved_per_application"]) for row in group
                ),
                "improving_fraction": statistics.fmean(
                    float(row["improving_fraction"]) for row in group
                ),
            }
        )
    return summary


def gate(costs: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the E0 gate: the repeat-to-repeat spread of timings is small."""
    cvs = sorted(
        float(row["cv_between_repeats"])
        for row in costs
        if row["cv_between_repeats"] == row["cv_between_repeats"]
    )
    median_cv = statistics.median(cvs) if cvs else float("nan")
    high_cv = cvs[int(0.95 * (len(cvs) - 1))] if cvs else float("nan")
    worst = sorted(
        (
            row
            for row in costs
            if row["cv_between_repeats"] == row["cv_between_repeats"]
        ),
        key=lambda row: -float(row["cv_between_repeats"]),
    )[:5]
    return {
        "cv_limit": GATE_CV,
        "median_cv": median_cv,
        "p95_cv": high_cv,
        "passes": bool(high_cv <= GATE_CV),
        "worst_cells": [
            {key: row[key] for key in ("n", "operator", "state", "cv_between_repeats")}
            for row in worst
        ],
    }


def plot_costs(costs: list[dict[str, Any]], path: Path, note: str) -> None:
    """Cost per application against n, one line per operator, one panel per state."""
    states = sorted({str(row["state"]) for row in costs})
    figure, axes = plt.subplots(1, len(states), figsize=(12, 5), sharey=True)
    for axis, state in zip(axes, states, strict=True):
        for name in OPERATORS:
            points = sorted(
                (int(row["n"]), float(row["median_ns"]) / 1000)
                for row in costs
                if row["operator"] == name and row["state"] == state
            )
            if points:
                sizes, values = zip(*points, strict=True)
                axis.plot(
                    sizes, values, marker="o", label=f"{OPERATOR_IDS[name]} {name}"
                )
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_xlabel("cities n")
        axis.set_title(f"state: {state}")
        axis.grid(True, which="both", alpha=0.3)
    axes[0].set_ylabel("median time per application (µs)")
    axes[-1].legend(fontsize=8)
    figure.suptitle("E0: cost per operator application (scalar Python)")
    figure.text(0.01, 0.01, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.03, 1, 1))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_spread(costs: list[dict[str, Any]], path: Path, note: str) -> None:
    """Repeat-to-repeat CV of every cell, per operator."""
    figure, axis = plt.subplots(figsize=(10, 4))
    names = list(OPERATORS)
    for index, name in enumerate(names):
        values = [
            float(row["cv_between_repeats"]) * 100
            for row in costs
            if row["operator"] == name
        ]
        axis.scatter([index] * len(values), values, alpha=0.6)
    axis.axhline(GATE_CV * 100, color="red", linestyle="--", label="gate")
    axis.set_xticks(range(len(names)), [f"{OPERATOR_IDS[n]}\n{n}" for n in names])
    axis.set_ylabel("CV between repeats (%)")
    axis.set_title("E0: repeatability of timings (one point per size x state)")
    axis.legend()
    figure.text(0.01, 0.01, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_work(raw: dict[str, np.ndarray], path: Path, note: str) -> None:
    """Time against counted work (lookups + positions moved), per batch."""
    figure, axis = plt.subplots(figsize=(7, 6))
    for name in OPERATORS:
        xs: list[float] = []
        ys: list[float] = []
        for key, rows in raw.items():
            if key.split("/")[1] != name:
                continue
            work = (rows[:, 2] + rows[:, 3]) / rows[:, 0]
            xs.extend((work + 1).tolist())
            ys.extend((rows[:, 1] / rows[:, 0] / 1000).tolist())
        step = max(1, len(xs) // 400)
        axis.scatter(xs[::step], ys[::step], s=4, alpha=0.4, label=OPERATOR_IDS[name])
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlabel("counted work per application (lookups + moved + 1)")
    axis.set_ylabel("time per application (µs)")
    axis.set_title("E0: time against counted work (subsampled batches)")
    axis.legend(markerscale=4, fontsize=8)
    figure.text(0.01, 0.01, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.03, 1, 1))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def analyse(run_directory: Path) -> dict[str, Any]:
    """Write all E0 journal data and figures; return the gate result."""
    data_dir, figure_dir = journal_paths(1)
    settings = json.loads((run_directory / "config.json").read_text())["settings"]
    repeats = settings["settings"]["repeats"]
    costs = summarise_costs(read_csv(run_directory / "costs.csv"))
    descents = read_csv(run_directory / "descents.csv")
    write_csv(data_dir / "e0_costs.csv", costs)
    write_csv(data_dir / "e0_descents.csv", descents)
    verdict = gate(costs)
    verdict["source_run"] = str(run_directory)
    (data_dir / "e0_gate.json").write_text(json.dumps(verdict, indent=2) + "\n")
    seeds = f"{repeats} interleaved repeats; instance seed 0"
    plot_costs(
        costs,
        figure_dir / "e0_costs.png",
        source_note(["e0_costs.csv"], SCRIPT, seeds),
    )
    plot_spread(
        costs,
        figure_dir / "e0_spread.png",
        source_note(["e0_costs.csv"], SCRIPT, seeds),
    )
    plot_work(
        read_arrays(run_directory / "costs.npz"),
        figure_dir / "e0_work.png",
        source_note([f"{run_directory.name}/costs.npz"], SCRIPT, seeds),
    )
    LOGGER.info("gate: %s", json.dumps(verdict))
    return verdict


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.analysis.e0", description=__doc__
    )
    parser.add_argument("run_directory", type=Path)
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    analyse(arguments.run_directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
