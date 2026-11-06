"""Analysis of E1R (Claim 1.4): is the greedy E1 choice the best first slice?

Reads a run of :mod:`aos.e1_rollouts` and, for every state and horizon t
(1, 2, 4, ... slices), compares the tour length after the greedy first slice
G(s) with every other candidate's, paired by repeat (the repeats of all
candidates share the continuation's random stream).  Writes, with a file-name
prefix (default ``e1r``), into the journal:

- ``<prefix>_arms.csv``: per state, horizon and candidate, the mean gap and
  its spread over repeats;
- ``<prefix>_states.csv``: per state and horizon, the best candidate B_t(s),
  the regret of the greedy choice (naive, and cross-fitted: B chosen on
  half the repeats, its advantage measured on the other half), and whether
  some candidate beats G(s): a one-sided paired sign-flip permutation test
  at level 0.05 / (number of alternatives), with a bootstrap interval of
  the advantage for display;
- ``<prefix>_summary.csv``: per class (and per family) and horizon, the
  share of states where greedy is beaten and the median regrets;
- ``<prefix>_beaten.png`` and ``<prefix>_regret.png``.

Example::

    uv run python -m aos.analysis.e1_rollouts runs/e1-rollouts/test --prefix e1r_test
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from aos.analysis import REPOSITORY_ROOT, journal_paths, source_note
from aos.problem import Problem
from aos.records import read_arrays
from aos.rollouts import CLASSES, length_at

LOGGER = logging.getLogger(__name__)

BOOTSTRAP = 2000
"""Bootstrap resamples per paired interval."""

FLIPS = 10_000
"""Random sign flips per permutation test."""

MIN_REPEATS = 8
"""Fewest paired repeats a test is made on."""

ALPHA = 0.05
"""Family-wise error per state and horizon (Bonferroni over alternatives)."""


def horizons(horizon_slices: int) -> list[int]:
    """Horizons in slices: powers of two up to the run's horizon."""
    result = [1]
    while result[-1] * 2 <= horizon_slices:
        result.append(result[-1] * 2)
    return result


def gaps(
    arrays: dict[str, np.ndarray], optimum: float, cut_ns: list[float]
) -> np.ndarray:
    """Gap of every repeat at every cut, shape (repeats, cuts), repeats in
    increasing order."""
    initial = float(arrays["initial_length"])
    rows = []
    for repeat in np.sort(arrays["repeat"]):
        mask = arrays["event_repeat"] == repeat
        times = arrays["event_ns"][mask]
        lengths = arrays["event_length"][mask]
        if np.any(np.diff(times) < 0):
            raise ValueError("events out of order")
        rows.append(
            [length_at(initial, times, lengths, cut) / optimum - 1.0 for cut in cut_ns]
        )
    return np.asarray(rows)


def paired_interval(
    differences: np.ndarray, level: float, rng: np.random.Generator
) -> tuple[float, float]:
    """Percentile bootstrap interval of the mean of paired differences."""
    count = len(differences)
    samples = differences[rng.integers(0, count, size=(BOOTSTRAP, count))].mean(axis=1)
    tail = (1.0 - level) / 2.0
    return float(np.quantile(samples, tail)), float(np.quantile(samples, 1.0 - tail))


def sign_flip_p(differences: np.ndarray, rng: np.random.Generator) -> float:
    """One-sided p-value of "mean difference > 0" by random sign flips of
    the paired differences (exact under a symmetric null, up to sampling of
    the flips)."""
    observed = differences.mean()
    if observed <= 0:
        return 1.0
    signs = rng.choice((-1.0, 1.0), size=(FLIPS, len(differences)))
    flipped = (signs * differences).mean(axis=1)
    return float((1 + np.count_nonzero(flipped >= observed)) / (FLIPS + 1))


def crossfit_regret(by_candidate: dict[str, np.ndarray], greedy: str) -> float:
    """Regret of the greedy choice with B chosen on one half of the repeats
    and its advantage over G measured on the other, averaged over both
    splits.  Unbiased by the choice of B, so it can be negative."""
    count = len(by_candidate[greedy])
    halves = (np.arange(count) % 2 == 0, np.arange(count) % 2 == 1)
    values = []
    for choose, measure in (halves, halves[::-1]):
        best = min(by_candidate, key=lambda name: by_candidate[name][choose].mean())
        values.append(
            float(
                by_candidate[greedy][measure].mean()
                - by_candidate[best][measure].mean()
            )
        )
    return float(np.mean(values))


def analyse_state(
    state: dict[str, Any], directory: Path, settings: dict[str, Any], seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Arm rows and state rows of one state, for every horizon.

    Raises:
        ValueError: If there are fewer than :data:`MIN_REPEATS` repeats.
    """
    optimum = state["optimum"]
    results = directory / "results" / state["instance"] / f"s{state['seed']}"
    loaded = {
        name: read_arrays(results / state["state"] / f"{name}.npz")
        for name in state["candidates"]
    }
    lengths = {float(arrays["slice_ns"]) for arrays in loaded.values()}
    slice_ns = settings["slice_ns_per_city"] * state["n"]
    if lengths != {slice_ns}:
        raise ValueError(f"{state['instance']}: unexpected slice lengths {lengths}")
    cuts = horizons(settings["horizon_slices"])
    cut_ns = [slices * slice_ns for slices in cuts]
    matrix = {name: gaps(arrays, optimum, cut_ns) for name, arrays in loaded.items()}
    repeats = len(next(iter(matrix.values())))
    if repeats < MIN_REPEATS:
        raise ValueError(f"{repeats} repeats; at least {MIN_REPEATS} are needed")
    greedy = state["greedy"]
    alternatives = [name for name in state["candidates"] if name != greedy]
    threshold = ALPHA / max(1, len(alternatives))
    level = 1.0 - 2 * threshold
    rng = np.random.default_rng(seed)
    base = {
        key: state[key]
        for key in ("instance", "family", "n", "seed", "state", "class", "greedy")
    }
    arm_rows = []
    state_rows = []
    for column, slices in enumerate(cuts):
        at = {name: values[:, column] for name, values in matrix.items()}
        for name, values in at.items():
            arm_rows.append(
                {
                    **base,
                    "slices": slices,
                    "candidate": name,
                    "e1_rate": state["rates"][name],
                    "mean_gap": float(values.mean()),
                    "sd_gap": float(values.std(ddof=1)),
                    "repeats": len(values),
                }
            )
        best = min(at, key=lambda name: at[name].mean())
        challenger = ""
        advantage = low = high = float("nan")
        smallest_p = 1.0
        beaten = False
        for name in alternatives:
            differences = at[greedy] - at[name]
            p_value = sign_flip_p(differences, rng)
            smallest_p = min(smallest_p, p_value)
            if p_value <= threshold and (not beaten or differences.mean() > advantage):
                beaten = True
                challenger = name
                advantage = float(differences.mean())
                low, high = paired_interval(differences, level, rng)
        state_rows.append(
            {
                **base,
                "slices": slices,
                "candidates": len(at),
                "best": best,
                "greedy_is_best": best == greedy,
                "greedy_mean_gap": float(at[greedy].mean()),
                "best_mean_gap": float(at[best].mean()),
                "regret": float(at[greedy].mean() - at[best].mean()),
                "regret_crossfit": crossfit_regret(at, greedy),
                "beaten": beaten,
                "smallest_p": smallest_p,
                "challenger": challenger,
                "advantage": advantage,
                "advantage_low": low,
                "advantage_high": high,
            }
        )
    return arm_rows, state_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write rows (all with the same keys) to a CSV file."""
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def summarise(state_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per (grouping, group, horizon): states, share beaten, regrets."""
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in state_rows:
        groups[("class", row["class"], row["slices"])].append(row)
        groups[("family", row["family"], row["slices"])].append(row)
        groups[("all", "all", row["slices"])].append(row)
    rows = []
    for (grouping, group, slices), members in sorted(groups.items()):
        rows.append(
            {
                "grouping": grouping,
                "group": group,
                "slices": slices,
                "states": len(members),
                "beaten_share": sum(row["beaten"] for row in members) / len(members),
                "greedy_best_share": sum(row["greedy_is_best"] for row in members)
                / len(members),
                "median_regret": median(row["regret"] for row in members),
                "median_regret_crossfit": median(
                    row["regret_crossfit"] for row in members
                ),
                "max_regret_crossfit": max(row["regret_crossfit"] for row in members),
            }
        )
    return rows


def plot_beaten(summary: list[dict[str, Any]], path: Path, note: str) -> None:
    """Share of states where some first slice beats the greedy one, against
    the horizon; one line per class (left) and per family (right)."""
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for axis, grouping in zip(axes, ("class", "family"), strict=True):
        groups = sorted(
            {row["group"] for row in summary if row["grouping"] == grouping},
            key=lambda name: CLASSES.index(name) if name in CLASSES else name,
        )
        for group in groups:
            rows = sorted(
                (
                    row
                    for row in summary
                    if row["grouping"] == grouping and row["group"] == group
                ),
                key=lambda row: row["slices"],
            )
            axis.plot(
                [row["slices"] for row in rows],
                [row["beaten_share"] for row in rows],
                marker="o",
                label=f"{group} ({rows[0]['states']})",
            )
        axis.set_xscale("log", base=2)
        axis.set_xlabel("horizon t (slices, first slice included)")
        axis.set_title(f"by {grouping}")
        axis.set_ylim(0, 1)
        axis.grid(alpha=0.3)
        axis.legend(fontsize=8)
    axes[0].set_ylabel("share of states where greedy is beaten")
    figure.suptitle("Claim 1.4: some first slice beats the E1 greedy one")
    figure.text(0.01, 0.01, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_regret(state_rows: list[dict[str, Any]], path: Path, note: str) -> None:
    """Cross-fitted regret of the greedy first slice (percentage points of
    gap) per class, one box per horizon."""
    cuts = sorted({row["slices"] for row in state_rows})
    figure, axes = plt.subplots(
        1, len(CLASSES), figsize=(4 * len(CLASSES), 4.5), sharey=True
    )
    for axis, label in zip(axes, CLASSES, strict=True):
        data = [
            [
                100 * row["regret_crossfit"]
                for row in state_rows
                if row["class"] == label and row["slices"] == slices
            ]
            for slices in cuts
        ]
        if any(data):
            axis.boxplot(data, tick_labels=[str(slices) for slices in cuts])
        axis.axhline(0, color="black", linewidth=0.8)
        axis.set_title(label)
        axis.set_xlabel("horizon (slices)")
        axis.grid(alpha=0.3)
    axes[0].set_ylabel("regret of greedy, cross-fitted (gap points, %)")
    figure.suptitle("Claim 1.4: regret of the greedy first slice")
    figure.text(0.01, 0.01, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def analyse(directory: Path, prefix: str, out: Path | None = None) -> list[Path]:
    """Analyse a finished E1R run into the journal, or into ``out`` (data
    and figures together) when given.

    Raises:
        FileNotFoundError: If a result file is missing.
        ValueError: If a state's instance has no known optimum.
    """
    plan = json.loads((directory / "plan.json").read_text())
    settings = json.loads((directory / "config.json").read_text())["settings"][
        "settings"
    ]
    optima = _optima(plan)
    arm_rows: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    for index, state in enumerate(plan["states"]):
        arms, states = analyse_state(
            {**state, "optimum": optima[state["instance"]]},
            directory,
            settings,
            seed=index,
        )
        arm_rows.extend(arms)
        state_rows.extend(states)
    summary = summarise(state_rows)
    if out is None:
        data, figures = journal_paths(1)
    else:
        out.mkdir(parents=True, exist_ok=True)
        data = figures = out
    paths = [
        write_csv(data / f"{prefix}_arms.csv", arm_rows),
        write_csv(data / f"{prefix}_states.csv", state_rows),
        write_csv(data / f"{prefix}_summary.csv", summary),
    ]
    note = source_note(
        [f"journal/data/claim-1/{prefix}_summary.csv", f"{prefix}_states.csv"],
        "aos/analysis/e1_rollouts.py",
        f"{settings['repeats']} paired repeats per (state, candidate); run {directory}",
    )
    beaten = figures / f"{prefix}_beaten.png"
    plot_beaten(summary, beaten, note)
    regret = figures / f"{prefix}_regret.png"
    plot_regret(state_rows, regret, note)
    paths += [beaten, regret]
    for row in summary:
        if row["grouping"] != "family":
            LOGGER.info(
                "%-14s t=%3d slices: %3d states, beaten %.2f, greedy best %.2f, "
                "median regret (cross-fitted) %.4f%%",
                row["group"],
                row["slices"],
                row["states"],
                row["beaten_share"],
                row["greedy_best_share"],
                100 * row["median_regret_crossfit"],
            )
    return paths


def _optima(plan: dict[str, Any]) -> dict[str, float]:
    """Optimum of every instance in the plan.

    Raises:
        ValueError: If an instance has no known optimum.
    """
    optima = {}
    for state in plan["states"]:
        if state["instance"] in optima:
            continue
        problem = Problem.from_tsplib(REPOSITORY_ROOT / state["path"])
        if problem.optimum is None:
            raise ValueError(f"{state['instance']}: optimum unknown")
        optima[state["instance"]] = problem.optimum
    return optima


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.analysis.e1_rollouts", description=__doc__
    )
    parser.add_argument("run", type=Path, help="E1R run directory")
    parser.add_argument("--prefix", default="e1r")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write here instead of the journal (for test runs)",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for path in analyse(arguments.run, arguments.prefix, arguments.out):
        LOGGER.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
