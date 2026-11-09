"""Summary figures of every Claim 1 and Claim 4 experiment, for review.

Each figure answers one question and names its data source in the footer.
The figures are 1600 x 900 px (16:9 at 100 dpi).

Sources:

- ``journal/data/claim-1/e1main_classes.csv``: E1 main run (2026-10-19).
- ``journal/data/claim-1/e1rn_R1_summary.csv`` and ``aos_opportunity.csv``:
  E1R night, R1 (2026-11-05), 1,257 states, 32 repeats.
- ``runs/aos-large/e1r-final/states.csv``: uniform 1k and 10k (2026-11-08),
  8 repeats. The 50k results are excluded: their virtual clock is
  miscalibrated.
- ``journal/data/claim-4/lkh_headroom_instances.csv``: LKH-3 kill test
  (2026-11-05), 4 seeds.

Usage: ``uv run python journal/scripts/meeting_20261109_figures.py``.
"""

from __future__ import annotations

import csv
import logging
import statistics
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
CLAIM1 = ROOT / "journal" / "data" / "claim-1"
CLAIM4 = ROOT / "journal" / "data" / "claim-4"
LARGE = ROOT / "runs" / "aos-large"
OUT = ROOT / "journal" / "figures" / "meeting-20261109"

SURFACE = "#FBFBF8"
INK = "#1B2433"
INK_SECONDARY = "#52514E"
GRID = "#E3E2DC"
BLUE = "#2A78D6"
ORANGE = "#EB6834"
AQUA = "#1BAF7A"
YELLOW = "#EDA100"
VIOLET = "#6250D6"
GRAY = "#A3A29C"

CLASSES = ["2-opt open", "2-opt optimum", "3-opt optimum", "LK optimum"]

OPERATOR_FAMILIES: dict[str, list[str]] = {
    "2-opt variants": [
        "two_opt_first",
        "two_opt_best",
        "two_opt_nl",
        "two_opt_descent",
        "two_opt_targeted",
        "two_opt_alpha",
    ],
    "Or-opt / 3-opt": ["or_opt", "or_opt_nl", "three_opt_nl"],
    "Lin-Kernighan": ["lin_kernighan", "lin_kernighan_alpha"],
    "Perturbation": [
        "kick",
        "ruin_recreate",
        "double_bridge",
        "double_bridge_local",
        "string_removal",
        "segment_exchange",
    ],
    "Nothing improves": ["none"],
}
FAMILY_COLOURS = [BLUE, ORANGE, AQUA, VIOLET, GRAY]


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into a list of row dictionaries.

    Raises:
        FileNotFoundError: If the file does not exist, so a missing export
            fails loudly instead of producing an empty figure.
    """
    if not path.exists():
        raise FileNotFoundError(f"missing data file: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def style_axes(axes: Axes) -> None:
    """Apply the shared recessive grid and spine style to one axes."""
    axes.set_facecolor(SURFACE)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(GRID)
    axes.tick_params(colors=INK_SECONDARY, labelsize=15, length=0)
    axes.grid(axis="y", color=GRID, linewidth=1)
    axes.set_axisbelow(True)


def new_figure(columns: int = 1) -> tuple[Figure, list[Axes]]:
    """Create a slide-sized figure with ``columns`` side-by-side axes."""
    figure, axes = plt.subplots(1, columns, figsize=(16, 9), dpi=100, squeeze=False)
    figure.patch.set_facecolor(SURFACE)
    flat = list(axes[0])
    for single in flat:
        style_axes(single)
    return figure, flat


def finish(figure: Figure, name: str, source: str) -> None:
    """Add the source footer and save the figure as a PNG under ``OUT``."""
    figure.text(0.01, 0.01, f"Source: {source}", fontsize=13, color=INK_SECONDARY)
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)
    logger.info("wrote %s", path)


def leaders_by_class() -> None:
    """E1: share of states each operator family leads, per class."""
    counts: dict[str, dict[str, int]] = {name: defaultdict(int) for name in CLASSES}
    for row in read_rows(CLAIM1 / "e1main_classes.csv"):
        for family, operators in OPERATOR_FAMILIES.items():
            counts[row["class"]][family] += sum(int(row[op]) for op in operators)
    figure, (axes,) = new_figure()
    axes.grid(False)
    positions = list(range(len(CLASSES)))[::-1]
    left = [0.0] * len(CLASSES)
    for colour, family in zip(FAMILY_COLOURS, OPERATOR_FAMILIES, strict=True):
        shares = []
        for name in CLASSES:
            total = sum(counts[name].values())
            shares.append(100 * counts[name][family] / total)
        axes.barh(
            positions,
            shares,
            left=left,
            color=colour,
            height=0.62,
            edgecolor=SURFACE,
            linewidth=2,
            label=family,
        )
        for position, start, share in zip(positions, left, shares, strict=True):
            if share >= 8:
                axes.text(
                    start + share / 2,
                    position,
                    f"{share:.0f}%",
                    ha="center",
                    va="center",
                    fontsize=15,
                    color="#FFFFFF" if colour != GRAY else INK,
                )
        left = [a + b for a, b in zip(left, shares, strict=True)]
    # The export counts every state twice (each total is exactly double the
    # journal's 7,676 unique states); the shares are unaffected.
    totals = [sum(counts[name].values()) // 2 for name in CLASSES]
    axes.set_yticks(
        positions,
        [f"{c}\n({t:,} states)" for c, t in zip(CLASSES, totals, strict=True)],
    )
    axes.set_xlim(0, 100)
    axes.set_xlabel("Share of states led (%)", fontsize=15, color=INK_SECONDARY)
    axes.legend(
        ncols=5,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.08),
        frameon=False,
        fontsize=15,
    )
    finish(
        figure,
        "f1_leaders_by_class",
        "e1main_classes.csv, E1 main run, 60 instances x 12 seeds",
    )


def headroom_by_class() -> None:
    """E1R night R1: median oracle headroom against horizon, per class."""
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in read_rows(CLAIM1 / "e1rn_R1_summary.csv"):
        if row["grouping"] == "class":
            series[row["group"]].append(
                (int(row["slices"]), 100 * float(row["median_regret_crossfit"]))
            )
    figure, (axes,) = new_figure()
    for colour, name in zip([BLUE, ORANGE, AQUA, VIOLET], CLASSES, strict=True):
        points = sorted(series[name])
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        axes.plot(
            xs, ys, color=colour, linewidth=3, marker="o", markersize=9, label=name
        )
        if ys[0] < 1:
            continue
        axes.annotate(
            f"{name}  {ys[0]:.2f}%",
            (xs[0], ys[0]),
            xytext=(12, 0),
            textcoords="offset points",
            fontsize=15,
            color=INK,
            va="center",
        )
    axes.set_xscale("log", base=2)
    axes.set_xticks([1, 2, 4, 8, 16, 32, 64], ["1", "2", "4", "8", "16", "32", "64"])
    axes.set_xlabel(
        "Horizon (slices of 0.01 ms per city)", fontsize=15, color=INK_SECONDARY
    )
    axes.set_ylabel(
        "Median oracle headroom (% of tour length)", fontsize=15, color=INK_SECONDARY
    )
    axes.legend(frameon=False, fontsize=15, loc="upper right")
    finish(
        figure,
        "f2_headroom_by_class",
        "e1rn_R1_summary.csv, E1R night R1, 1,257 states, 32 repeats, cross-fitted",
    )


def headroom_by_gap() -> None:
    """E1R night R1: median oracle headroom at 1 and 8 slices by starting gap."""
    order = ["<1%", "1-2%", "2-5%", "5-10%", "10-20%", ">20%"]
    values: dict[tuple[str, int], tuple[float, int]] = {}
    for row in read_rows(CLAIM1 / "aos_opportunity.csv"):
        if row["grouping"] == "gap_bin" and row["slices"] in ("1", "8"):
            values[(row["group"], int(row["slices"]))] = (
                float(row["median_oracle_pct"]),
                int(row["states"]),
            )
    figure, (axes,) = new_figure()
    width = 0.38
    for offset, slices, colour in ((-width / 2, 1, BLUE), (width / 2, 8, ORANGE)):
        heights = [values[(group, slices)][0] for group in order]
        positions = [index + offset for index in range(len(order))]
        axes.bar(
            positions,
            heights,
            width=width - 0.03,
            color=colour,
            label=f"{slices} slice{'s' if slices > 1 else ''}",
        )
        for position, height in zip(positions, heights, strict=True):
            axes.text(
                position,
                height + 0.15,
                f"{height:.2f}",
                ha="center",
                fontsize=14,
                color=INK,
            )
    labels = [f"{group}\n({values[(group, 1)][1]} states)" for group in order]
    axes.set_xticks(range(len(order)), labels)
    axes.set_xlabel("Starting gap to the optimum", fontsize=15, color=INK_SECONDARY)
    axes.set_ylabel(
        "Median oracle headroom (% of tour length)", fontsize=15, color=INK_SECONDARY
    )
    axes.legend(frameon=False, fontsize=15)
    finish(
        figure, "f3_headroom_by_gap", "aos_opportunity.csv, E1R night R1, 1,257 states"
    )


def scaling_curves() -> None:
    """Uniform 1k and 10k: median headroom of 2-opt-open states against horizon.

    The 50k results of the same nights are left out on purpose: their cost
    model is miscalibrated (validation ratios up to 1.60, degenerate
    coefficients) and the clustered 50k continuation policy stalled.
    """
    names = {"portgen-1000-1": "uniform 1k", "portgen-10000-1": "uniform 10k"}
    groups: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in read_rows(LARGE / "e1r-final" / "states.csv"):
        label = names.get(row["instance"])
        if label is None or row["class"] != "2-opt open":
            continue
        groups[label][int(row["slices"])].append(100 * float(row["regret_crossfit"]))
    figure, (axes,) = new_figure()
    # Real / virtual time of the replayed rollouts (validate.parquet): 1k
    # 1.04-1.08 over four replays; 10k 1.35 over its single replay.
    clock_notes = {
        "uniform 1k": "clock checked",
        "uniform 10k": "clock 1.35x off, 1 replay",
    }
    for label, colour in (("uniform 1k", BLUE), ("uniform 10k", AQUA)):
        points = sorted(groups[label].items())
        if not points:
            raise ValueError(f"no 2-opt-open states for {label}")
        xs = [p[0] for p in points]
        ys = [statistics.median(p[1]) for p in points]
        axes.plot(
            xs,
            ys,
            color=colour,
            linewidth=3,
            marker="o",
            markersize=9,
            label=f"{label} ({len(points[0][1])} states; {clock_notes[label]})",
        )
    axes.set_xscale("log", base=2)
    axes.set_xticks([1, 2, 4, 8], ["1", "2", "4", "8"])
    axes.set_ylim(0, 5)
    axes.set_xlabel(
        "Horizon (slices of 0.01 ms per city)", fontsize=15, color=INK_SECONDARY
    )
    axes.set_ylabel(
        "Median oracle headroom (% of tour length)", fontsize=15, color=INK_SECONDARY
    )
    axes.legend(frameon=False, fontsize=15, loc="upper right")
    finish(
        figure,
        "f4_scaling_1k_10k",
        "runs/aos-large/e1r-final/states.csv; 2-opt-open states; 8 repeats; "
        "1 instance per size; 50k excluded (miscalibrated clock)",
    )


def lkh_headroom() -> None:
    """LKH-3 kill test: cross-fitted headroom in units of V per large instance."""
    rows = [
        row
        for row in read_rows(CLAIM4 / "lkh_headroom_instances.csv")
        if int(row["n"]) >= 5000 and row["verdict"] != "saturated"
    ]
    rows.sort(key=lambda r: float(r["crossfit_norm"]))
    figure, (axes,) = new_figure()
    axes.grid(axis="y", visible=False)
    axes.grid(axis="x", color=GRID, linewidth=1)
    positions = range(len(rows))
    colours = [
        ORANGE
        if r["verdict"] == "room"
        else (YELLOW if r["verdict"] == "unclear" else BLUE)
        for r in rows
    ]
    axes.scatter(
        [float(r["crossfit_norm"]) for r in rows],
        list(positions),
        s=140,
        color=colours,
        edgecolor=SURFACE,
        linewidth=2,
        zorder=3,
    )
    axes.set_yticks(
        list(positions),
        [f"{r['instance']} ({r['verdict']})" for r in rows],
        fontsize=13,
    )
    axes.axvline(0.5, color=INK_SECONDARY, linestyle="--", linewidth=1.5)
    axes.axvline(0, color=GRID, linewidth=2)
    axes.text(
        0.52, len(rows) - 0.6, "room threshold 0.5 V", fontsize=14, color=INK_SECONDARY
    )
    axes.set_xlabel(
        "Cross-fitted headroom of the best switch, "
        "in units of V (value of doubling the time)",
        fontsize=15,
        color=INK_SECONDARY,
    )
    finish(
        figure,
        "f5_lkh_headroom",
        "lkh_headroom_instances.csv, LKH-3 kill test, n >= 5,000, 4 seeds; "
        "0.03 points of gap at most",
    )


def main(figures: Iterable[str] | None = None) -> None:
    """Draw the requested figures (all by default) into ``OUT``."""
    drawers = {
        "f1": leaders_by_class,
        "f2": headroom_by_class,
        "f3": headroom_by_gap,
        "f4": scaling_curves,
        "f5": lkh_headroom,
    }
    for key in figures or drawers:
        drawers[key]()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
