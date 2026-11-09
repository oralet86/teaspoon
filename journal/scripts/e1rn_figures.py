"""Figures of the E1R night of 2026-11-05 (journal/claim-1.md).

Reads only the journal data in ``journal/data/claim-1/e1rn_*.csv`` (exported
from ``runs/e1r-night/20261105-012829`` with ``aos show``) and writes PNGs to
``journal/figures/claim-1/``. Every comparison between runs is restricted to
R3's 556 states, the states all three runs share.

Usage: ``uv run python journal/scripts/e1rn_figures.py``
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "journal" / "data" / "claim-1"
FIGURES = ROOT / "journal" / "figures" / "claim-1"

# Sequential blue (lighter = shorter slice); markers and direct labels carry
# identity too, since the lightest step is below 3:1 on the surface.
RUNS = {
    "R2": {"label": "0.001 ms slices (R2)", "color": "#6da7ec", "marker": "o"},
    "R1": {"label": "0.01 ms slices (R1)", "color": "#2a78d6", "marker": "s"},
    "R3": {"label": "0.05 ms slices (R3)", "color": "#104281", "marker": "^"},
}
CLASSES = ["2-opt open", "2-opt optimum", "3-opt optimum", "LK optimum"]
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
StateKey = tuple[str, str, str]


def read_rows(name: str) -> list[dict[str, str]]:
    """Read one exported table.

    Args:
        name: File name inside ``journal/data/claim-1``.

    Returns:
        The rows as dictionaries.

    Raises:
        FileNotFoundError: If the export is missing.
    """
    with (DATA / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def key_of(row: dict[str, str]) -> StateKey:
    """Identify a state by instance, state seed and state name."""
    return (row["instance"], row["seed"], row["state"])


def style_axes(axes: Axes) -> None:
    """Recessive grid and spines, text in ink colours."""
    axes.set_facecolor(SURFACE)
    axes.grid(True, color=GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(MUTED)
    axes.tick_params(colors=MUTED, labelsize=8)


def shared_states() -> set[StateKey]:
    """The states of R3, which R1 and R2 contain (nested draws)."""
    return {key_of(row) for row in read_rows("e1rn_R3_states.csv")}


def beaten_by_slices(shared: set[StateKey]) -> None:
    """Share of states where some first operator beats greedy, by horizon in
    slices, per class and run, on the shared states."""
    figure, panels = plt.subplots(
        1, 4, figsize=(13, 3.4), sharey=True, facecolor=SURFACE
    )
    for run, spec in RUNS.items():
        counts: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
        for row in read_rows(f"e1rn_{run}_states.csv"):
            if key_of(row) in shared and int(row["slices"]) <= 64:
                cell = counts[(row["class"], int(row["slices"]))]
                cell[0] += row["beaten"] == "true"
                cell[1] += 1
        for axes, label in zip(panels, CLASSES, strict=True):
            horizons = sorted(h for (c, h) in counts if c == label)
            shares = [
                100 * counts[(label, h)][0] / counts[(label, h)][1] for h in horizons
            ]
            axes.plot(
                horizons,
                shares,
                color=spec["color"],
                marker=spec["marker"],
                markersize=6,
                linewidth=2,
                label=spec["label"],
            )
    for axes, label in zip(panels, CLASSES, strict=True):
        style_axes(axes)
        axes.set_xscale("log", base=2)
        axes.set_title(label, color=INK, fontsize=10)
        axes.set_xlabel("horizon (slices)", color=MUTED, fontsize=9)
    panels[0].set_ylabel("greedy beaten (% of states)", color=MUTED, fontsize=9)
    panels[0].legend(frameon=False, fontsize=8, labelcolor=INK)
    figure.suptitle(
        "E1R 2026-11-05: greedy first operator beaten (Holm, alpha = 0.05), "
        "556 shared states, 32 repeats",
        color=INK,
        fontsize=10,
    )
    figure.tight_layout()
    path = FIGURES / "e1rn_beaten_by_slices.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def equal_time(shared: set[StateKey]) -> None:
    """The greedy policy at equal elapsed time with different decision
    intervals: per state, is the shorter interval's mean gap lower or
    higher? Writes the numbers to e1rn_equal_time.csv as well."""
    gaps: dict[str, dict[tuple[StateKey, int], float]] = {}
    for run in RUNS:
        gaps[run] = {
            (key_of(row), int(row["us_per_city"])): float(row["greedy_mean_gap"])
            for row in read_rows(f"e1rn_{run}_states_time.csv")
            if key_of(row) in shared
        }
    times = sorted({t for (_, t) in gaps["R1"]})
    pairs = [("R2", "R1"), ("R1", "R3")]
    table = []
    for shorter, longer in pairs:
        for time in times:
            values = [
                (gaps[shorter][(s, time)], gaps[longer][(s, time)]) for s in shared
            ]
            better = 100 * sum(a < b for a, b in values) / len(values)
            worse = 100 * sum(a > b for a, b in values) / len(values)
            table.append(
                {
                    "shorter": shorter,
                    "longer": longer,
                    "us_per_city": time,
                    "shorter_better_pct": round(better, 1),
                    "shorter_worse_pct": round(worse, 1),
                    "states": len(values),
                }
            )
    with (DATA / "e1rn_equal_time.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    figure, panels = plt.subplots(
        1, 2, figsize=(10, 3.6), sharey=True, facecolor=SURFACE
    )
    for axes, (shorter, longer) in zip(panels, pairs, strict=True):
        rows = [row for row in table if row["shorter"] == shorter]
        x = [row["us_per_city"] for row in rows]
        axes.plot(
            x,
            [row["shorter_better_pct"] for row in rows],
            color=RUNS[shorter]["color"],
            marker=RUNS[shorter]["marker"],
            linewidth=2,
            markersize=6,
            label=f"{shorter} lower gap",
        )
        axes.plot(
            x,
            [row["shorter_worse_pct"] for row in rows],
            color=RUNS[longer]["color"],
            marker=RUNS[longer]["marker"],
            linewidth=2,
            markersize=6,
            label=f"{longer} lower gap",
        )
        # Direct labels at the right end, nudged apart when the lines meet.
        upper = rows[-1]["shorter_better_pct"] >= rows[-1]["shorter_worse_pct"]
        for series, name, above in (
            ("shorter_better_pct", shorter, upper),
            ("shorter_worse_pct", longer, not upper),
        ):
            axes.annotate(
                name,
                (x[-1], rows[-1][series]),
                xytext=(6, 5 if above else -5),
                textcoords="offset points",
                color=INK,
                fontsize=8,
                va="center",
            )
        style_axes(axes)
        axes.set_xscale("log", base=2)
        axes.set_xlabel("virtual time (µs per city)", color=MUTED, fontsize=9)
        axes.set_title(
            f"greedy with {RUNS[shorter]['label'].split(' (')[0]} "
            f"vs with {RUNS[longer]['label'].split(' (')[0]}",
            color=INK,
            fontsize=9,
        )
        axes.legend(frameon=False, fontsize=8, labelcolor=INK)
    panels[0].set_ylabel("% of states", color=MUTED, fontsize=9)
    figure.suptitle(
        "E1R 2026-11-05: greedy policy at equal time, 556 shared states "
        "(ties make up the rest)",
        color=INK,
        fontsize=10,
    )
    figure.tight_layout()
    path = FIGURES / "e1rn_equal_time.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def validation() -> None:
    """Real over virtual time of the replayed rollouts, per operator and run
    (median, with min-max whiskers)."""
    rows = [row for row in read_rows("e1rn_validation.csv") if row["operator"] != "all"]
    operators = sorted({row["operator"] for row in rows})
    figure, axes = plt.subplots(figsize=(11, 3.8), facecolor=SURFACE)
    offsets = {"R2": -0.25, "R1": 0.0, "R3": 0.25}
    for run, spec in RUNS.items():
        mine = {row["operator"]: row for row in rows if row["run"] == run}
        xs = [i + offsets[run] for i, op in enumerate(operators) if op in mine]
        med = [float(mine[op]["median"]) for op in operators if op in mine]
        low = [
            float(mine[op]["median"]) - float(mine[op]["min"])
            for op in operators
            if op in mine
        ]
        high = [
            float(mine[op]["max"]) - float(mine[op]["median"])
            for op in operators
            if op in mine
        ]
        axes.errorbar(
            xs,
            med,
            yerr=[low, high],
            fmt=spec["marker"],
            color=spec["color"],
            markersize=6,
            elinewidth=1,
            capsize=0,
            label=spec["label"],
        )
    axes.axhline(1.0, color=MUTED, linewidth=1)
    style_axes(axes)
    axes.set_xticks(range(len(operators)), operators, rotation=45, ha="right")
    axes.set_ylabel("real / virtual time", color=MUTED, fontsize=9)
    axes.legend(frameon=False, fontsize=8, labelcolor=INK)
    axes.set_title(
        "E1R 2026-11-05 validation: 24 replayed rollouts per run, one shared "
        "cost model (calibrated in R1)",
        color=INK,
        fontsize=10,
    )
    figure.tight_layout()
    path = FIGURES / "e1rn_validation.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def write_table(name: str, rows: list[dict[str, object]]) -> None:
    """Write rows to ``journal/data/claim-1/<name>``."""
    with (DATA / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    LOGGER.info("wrote %s", DATA / name)


def median(values: list[float]) -> float:
    """Median of a non-empty list."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def extra_tables(shared: set[StateKey]) -> None:
    """Question 1 (best first operator, widening beyond the top five),
    question 3 (slice-rate selector) and the catch-up lag, per run, class
    and horizon (1, 8 and 64 slices), on the shared states."""
    best_rows, widening_rows, selector_rows, catch_rows = [], [], [], []
    for run in RUNS:
        full = [r for r in read_rows(f"e1rn_{run}_states.csv") if key_of(r) in shared]
        top5 = {
            (key_of(r), r["slices"]): float(r["regret_crossfit"])
            for r in read_rows(f"e1rn_{run}_states_top5.csv")
            if key_of(r) in shared
        }
        for label in [*CLASSES, "all"]:
            for horizon in ("1", "8", "64"):
                rows = [
                    r
                    for r in full
                    if r["slices"] == horizon and label in (r["class"], "all")
                ]
                untied = [r for r in rows if r["tied_best"] == "1"]
                counts: dict[str, int] = defaultdict(int)
                for row in untied:
                    counts[row["best"]] += 1
                for operator, count in sorted(
                    counts.items(), key=lambda item: -item[1]
                ):
                    best_rows.append(
                        {
                            "run": run,
                            "class": label,
                            "slices": horizon,
                            "operator": operator,
                            "states": count,
                            "share_of_untied": round(count / len(untied), 3),
                            "tied_states": len(rows) - len(untied),
                        }
                    )
                gains = [
                    float(r["regret_crossfit"]) - top5[(key_of(r), horizon)]
                    for r in rows
                ]
                widening_rows.append(
                    {
                        "run": run,
                        "class": label,
                        "slices": horizon,
                        "states": len(rows),
                        "mean_gain_pct": round(100 * sum(gains) / len(gains), 4),
                        "share_positive": round(
                            sum(g > 0 for g in gains) / len(gains), 3
                        ),
                    }
                )
                advantage = [float(r["slice_rate_advantage"]) for r in rows]
                selector_rows.append(
                    {
                        "run": run,
                        "class": label,
                        "slices": horizon,
                        "states": len(rows),
                        "mean_advantage_pct": round(
                            100 * sum(advantage) / len(advantage), 4
                        ),
                        "share_better": round(
                            sum(a > 0 for a in advantage) / len(advantage), 3
                        ),
                        "share_worse": round(
                            sum(a < 0 for a in advantage) / len(advantage), 3
                        ),
                    }
                )
                lags = [
                    float(r["greedy_catch_up"]) / float(horizon)
                    for r in rows
                    if r["greedy_catch_up"]
                ]
                catch_rows.append(
                    {
                        "run": run,
                        "class": label,
                        "slices": horizon,
                        "states": len(rows),
                        "share_caught_up": round(len(lags) / len(rows), 3),
                        "median_factor": round(median(lags), 2) if lags else "",
                    }
                )
    write_table("e1rn_best_first.csv", best_rows)
    write_table("e1rn_widening.csv", widening_rows)
    write_table("e1rn_slice_rate.csv", selector_rows)
    write_table("e1rn_catch_up.csv", catch_rows)


def main() -> None:
    """Draw every figure of the entry."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    FIGURES.mkdir(parents=True, exist_ok=True)
    shared = shared_states()
    LOGGER.info("%d shared states", len(shared))
    beaten_by_slices(shared)
    equal_time(shared)
    validation()
    extra_tables(shared)


if __name__ == "__main__":
    main()
