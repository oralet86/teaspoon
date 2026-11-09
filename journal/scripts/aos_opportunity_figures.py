"""Figures for where adaptive operator selection (AOS) has room.

The figures read the E1R night export of 2026-11-05 and the measurement-A
leader table, both in ``journal/data/claim-1``. They split the same 1,257
states by instance family, nominal size, starting optimality gap,
local-optimality class, and horizon:

- ``aos_opportunity_map.png``: family and size maps of (a) how often greedy
  is beaten, (b) the oracle headroom at one slice, (c) the headroom that
  survives eight slices, and (d) how often a first-slice selector beats
  greedy at eight slices.
- ``aos_horizon_by_family.png``: oracle headroom and selector hit rate
  against the horizon, per family.
- ``aos_gap_bins.png``: the same two metrics by starting gap and horizon.
- ``aos_class_horizon.png``: the same two metrics by state class and
  horizon.
- ``aos_size_scaling.png``: oracle headroom against nominal instance size
  for the Euclidean families at one and eight slices.
- ``aos_best_first_operators.png``: which operator is the best first
  operator, by family and by class.

The run is R1 (0.01 ms per city per slice, 64 slices). The E1R states are
those with an improvement in measurement A; every state has 32 paired
repeats. ``regret_crossfit`` is the oracle headroom: the gap between greedy
(the measurement-A snapshot leader) and the best candidate at that horizon,
cross-fitted over the repeats. ``slice_rate_advantage`` is the gain of a
selector that picks the candidate with the lowest gap after its first slice
on one half of the repeats and is scored on the other half. At one slice
the two definitions coincide, so the figures use the oracle headroom there.

Usage: ``uv run python journal/scripts/aos_opportunity_figures.py``
"""

from __future__ import annotations

import csv
import logging
import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "journal" / "data" / "claim-1"
FIGURES = ROOT / "journal" / "figures" / "claim-1"

RUN = "R1"
FAMILIES = ("uniform", "clustered", "tspgen", "TSPLIB", "random matrix")
FAMILY_LABELS = {
    "uniform": "uniform",
    "clustered": "clustered",
    "tspgen": "tspgen",
    "TSPLIB": "TSPLIB",
    "random matrix": "random matrix",
}
FAMILY_COLORS = {
    "uniform": "#0072B2",
    "clustered": "#E69F00",
    "tspgen": "#009E73",
    "TSPLIB": "#CC79A7",
    "random matrix": "#D55E00",
}
FAMILY_MARKERS = {
    "uniform": "o",
    "clustered": "s",
    "tspgen": "^",
    "TSPLIB": "D",
    "random matrix": "v",
}
SIZES = (100, 200, 500)
SIZE_LABELS = ("n≈100", "n≈200", "n≈500")
CUTS = (1, 2, 4, 8, 16, 32, 64)
CLASSES = ("2-opt open", "2-opt optimum", "3-opt optimum", "LK optimum")
GAP_BINS = (
    ("<1%", 0.0, 1.0),
    ("1-2%", 1.0, 2.0),
    ("2-5%", 2.0, 5.0),
    ("5-10%", 5.0, 10.0),
    ("10-20%", 10.0, 20.0),
    (">20%", 20.0, math.inf),
)
OPERATORS = (
    "kick",
    "lk_kick",
    "lin_kernighan_alpha",
    "lin_kernighan",
    "double_bridge",
    "double_bridge_local",
    "ruin_recreate",
    "string_removal",
    "three_opt_nl",
)
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
StateKey = tuple[str, str, str]


@dataclass(frozen=True)
class StateRow:
    """One state at one horizon cut, with the context needed by the plots."""

    instance: str
    family: str
    class_name: str
    size: int
    start_gap_pct: float
    slices: int
    tied_best: int
    best_operator: str
    beaten: bool
    greedy_best: bool
    oracle_pct: float
    selector_pct: float


@dataclass(frozen=True)
class Cell:
    """Aggregated metrics for one group of states at one horizon."""

    states: int
    median_start_gap_pct: float
    beaten_share: float
    greedy_best_share: float
    median_oracle_pct: float
    oracle_q25_pct: float
    oracle_q75_pct: float
    median_selector_pct: float
    selector_positive_share: float


def read_rows(name: str) -> list[dict[str, str]]:
    """Read one CSV from the claim-1 journal data directory.

    Args:
        name: File name inside ``journal/data/claim-1``.

    Returns:
        The rows as dictionaries.

    Raises:
        FileNotFoundError: If the export is missing.
    """
    with (DATA / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def nominal_size(n: int) -> int:
    """Map a real instance size to the nearest nominal size used in the plots.

    The synthetic families have n = 100, 200 and 500 exactly. TSPLIB has
    sizes such as 96, 225, 442 and 535; those are grouped into the nearest
    of the same three columns so that family and size stay comparable.
    """
    if n <= 150:
        return 100
    if n <= 350:
        return 200
    return 500


def gap_bin(gap_pct: float) -> str:
    """Return the starting-gap label for a state's optimality gap."""
    for label, low, high in GAP_BINS:
        if low <= gap_pct < high:
            return label
    return GAP_BINS[-1][0]


def load_states() -> list[StateRow]:
    """Load the E1R R1 states and attach sizes and starting gaps.

    Returns:
        One row per (state, horizon cut).

    Raises:
        ValueError: If a state is missing from the leader or instance tables.
    """
    leader_gaps: dict[StateKey, float] = {}
    for row in read_rows("e1main_leaders.csv"):
        key = (row["instance"], row["seed"], row["state"])
        leader_gaps[key] = float(row["gap"]) * 100.0

    instance_sizes: dict[str, int] = {
        row["instance"]: int(float(row["n"]))
        for row in read_rows("e1main_instances.csv")
    }

    states: list[StateRow] = []
    for row in read_rows(f"e1rn_{RUN}_states.csv"):
        key = (row["instance"], row["seed"], row["state"])
        if key not in leader_gaps:
            raise ValueError(f"state {key!r} has no measurement-A leader row")
        if row["instance"] not in instance_sizes:
            raise ValueError(f"instance {row['instance']!r} has no size")
        states.append(
            StateRow(
                instance=row["instance"],
                family=row["family"],
                class_name=row["class"],
                size=nominal_size(instance_sizes[row["instance"]]),
                start_gap_pct=leader_gaps[key],
                slices=int(row["slices"]),
                tied_best=int(row["tied_best"]),
                best_operator=row["best"],
                beaten=row["beaten"] == "true",
                greedy_best=row["greedy_is_best"] == "true",
                oracle_pct=float(row["regret_crossfit"]) * 100.0,
                selector_pct=float(row["slice_rate_advantage"]) * 100.0,
            )
        )
    return states


def summarize(rows: Iterable[StateRow]) -> Cell:
    """Aggregate one group of state rows."""
    values = list(rows)
    if not values:
        raise ValueError("cannot summarize an empty group")
    oracle = [row.oracle_pct for row in values]
    selector = [row.selector_pct for row in values]
    return Cell(
        states=len(values),
        median_start_gap_pct=statistics.median(row.start_gap_pct for row in values),
        beaten_share=sum(row.beaten for row in values) / len(values),
        greedy_best_share=sum(row.greedy_best for row in values) / len(values),
        median_oracle_pct=statistics.median(oracle),
        oracle_q25_pct=float(np.quantile(oracle, 0.25)),
        oracle_q75_pct=float(np.quantile(oracle, 0.75)),
        median_selector_pct=statistics.median(selector),
        selector_positive_share=sum(value > 0.0 for value in selector) / len(values),
    )


def group_cells(
    states: Iterable[StateRow],
    key_of: Callable[[StateRow], str],
) -> dict[tuple[str, int], Cell]:
    """Aggregate states by a group label and a horizon cut."""
    groups: dict[tuple[str, int], list[StateRow]] = defaultdict(list)
    for row in states:
        groups[(key_of(row), row.slices)].append(row)
    return {key: summarize(rows) for key, rows in groups.items()}


def cell_matrix(
    cells: dict[tuple[str, int], Cell],
    rows: Iterable[str],
    cuts: Iterable[int],
    metric: Callable[[Cell], float],
) -> np.ndarray:
    """Turn grouped cells into a row x cut matrix for a heatmap."""
    row_labels = list(rows)
    cut_values = list(cuts)
    matrix = np.full((len(row_labels), len(cut_values)), np.nan)
    for row_index, row_label in enumerate(row_labels):
        for column_index, cut in enumerate(cut_values):
            cell = cells.get((row_label, cut))
            if cell is not None:
                matrix[row_index, column_index] = metric(cell)
    return matrix


def style_axes(axes: Axes) -> None:
    """Recessive grid and spines, text in the journal colours."""
    axes.set_facecolor(SURFACE)
    axes.grid(True, color=GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(MUTED)
    axes.tick_params(colors=MUTED, labelsize=8)


def format_heatmap_value(value: float, decimals: int) -> str:
    """Format a heatmap annotation, keeping zero readable."""
    if np.isnan(value):
        return "-"
    if abs(value) < 0.005 and decimals > 0:
        return "0"
    return f"{value:.{decimals}f}"


def heatmap(
    figure: Figure,
    axes: Axes,
    matrix: np.ndarray,
    row_labels: list[str],
    column_labels: list[str],
    *,
    title: str,
    cmap: str,
    vmin: float,
    vmax: float,
    decimals: int,
    colorbar_label: str,
) -> None:
    """Draw one annotated heatmap with a colour bar."""
    image = axes.imshow(
        matrix,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        aspect="auto",
    )
    axes.set_xticks(
        range(len(column_labels)),
        column_labels,
        fontsize=8,
    )
    axes.set_yticks(
        range(len(row_labels)),
        row_labels,
        fontsize=8,
    )
    axes.set_xticks(np.arange(-0.5, len(column_labels), 1), minor=True)
    axes.set_yticks(np.arange(-0.5, len(row_labels), 1), minor=True)
    axes.grid(which="minor", color=SURFACE, linewidth=1.8)
    axes.tick_params(which="minor", bottom=False, left=False)
    axes.grid(which="major", visible=False)
    for spine in axes.spines.values():
        spine.set_visible(False)
    axes.tick_params(colors=MUTED)
    axes.set_title(title, color=INK, fontsize=9.5, loc="left", pad=8)

    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = matrix[row_index, column_index]
            if np.isnan(value):
                color = MUTED
            else:
                normalized = (value - vmin) / (vmax - vmin)
                color = "white" if normalized > 0.62 else INK
            axes.text(
                column_index,
                row_index,
                format_heatmap_value(value, decimals),
                ha="center",
                va="center",
                color=color,
                fontsize=7.6,
            )

    colorbar = figure.colorbar(
        image,
        ax=axes,
        fraction=0.045,
        pad=0.02,
    )
    colorbar.set_label(colorbar_label, color=MUTED, fontsize=7.5)
    colorbar.ax.tick_params(colors=MUTED, labelsize=7)


def source_footer(
    figure: Figure,
    *,
    extra: str = "",
) -> None:
    """Put the data, script and repeat count under every figure."""
    source = (
        "Source: journal/data/claim-1/e1rn_R1_states.csv, "
        "e1main_leaders.csv, e1main_instances.csv"
        "\nscript: journal/scripts/aos_opportunity_figures.py | "
        "60 instances, 1,257 states, 32 paired repeats"
    )
    if extra:
        source = f"{extra}\n{source}"
    figure.supxlabel(source, color=MUTED, fontsize=6.5, linespacing=1.35)


def plot_opportunity_map(states: list[StateRow]) -> None:
    """Family x size maps of opportunity and persistence."""
    cells = group_cells(
        states,
        lambda row: f"{row.family}|{row.size}",
    )
    # group_cells indexes by the full key; make one matrix per family so
    # the rows stay in family order and the columns in size order.
    matrices: dict[tuple[int, str, str], np.ndarray] = {}
    for family in FAMILIES:
        for cut in (1, 8):
            for metric_name in ("beaten", "oracle", "selector"):
                matrix = np.full((len(SIZES),), np.nan)
                for size_index, size in enumerate(SIZES):
                    cell = cells.get((f"{family}|{size}", cut))
                    if cell is None:
                        continue
                    if metric_name == "beaten":
                        matrix[size_index] = 100.0 * cell.beaten_share
                    elif metric_name == "oracle":
                        matrix[size_index] = cell.median_oracle_pct
                    else:
                        matrix[size_index] = 100.0 * cell.selector_positive_share
                matrices[(cut, family, metric_name)] = matrix

    def stack(metric_name: str, cut: int) -> np.ndarray:
        return np.vstack([matrices[(cut, family, metric_name)] for family in FAMILIES])

    figure, axes_grid = plt.subplots(
        2,
        2,
        figsize=(12.4, 8.4),
        layout="constrained",
        facecolor=SURFACE,
    )
    panels = (
        (
            stack("beaten", 1),
            "Choice matters: greedy beaten at 1 slice",
            "Blues",
            0.0,
            100.0,
            0,
            "% of states",
        ),
        (
            stack("oracle", 1),
            "Oracle headroom at 1 slice",
            "YlOrRd",
            0.0,
            70.0,
            1,
            "median gap (%)",
        ),
        (
            stack("oracle", 8),
            "Headroom left at 8 slices (0.08 ms/city)",
            "YlOrRd",
            0.0,
            0.8,
            2,
            "median gap (%)",
        ),
        (
            stack("selector", 8),
            "First-slice selector beats greedy at 8 slices",
            "Blues",
            0.0,
            100.0,
            0,
            "% of states",
        ),
    )
    for axes, spec in zip(axes_grid.flat, panels, strict=True):
        matrix, title, cmap, vmin, vmax, decimals, colorbar_label = spec
        heatmap(
            figure,
            axes,
            matrix,
            list(FAMILY_LABELS[family] for family in FAMILIES),
            list(SIZE_LABELS),
            title=title,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            decimals=decimals,
            colorbar_label=colorbar_label,
        )
    figure.suptitle(
        "Where AOS has room: E1R 2026-11-05, R1 (0.01 ms/city slices), "
        "family and nominal size",
        color=INK,
        fontsize=11,
    )
    source_footer(
        figure,
        extra=(
            "Greedy = measurement-A snapshot leader; beaten = Holm at 0.05; "
            "oracle = cross-fitted best candidate"
        ),
    )
    path = FIGURES / "aos_opportunity_map.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def plot_horizon_by_family(states: list[StateRow]) -> None:
    """Oracle headroom and selector hit rate against the horizon, by family."""
    cells = group_cells(states, lambda row: row.family)
    figure, panels = plt.subplots(
        1,
        2,
        figsize=(11.6, 4.6),
        layout="constrained",
        facecolor=SURFACE,
    )
    handles = []
    for family in FAMILIES:
        oracle = [cells[(family, cut)].median_oracle_pct for cut in CUTS]
        hit_rate = [
            100.0 * cells[(family, cut)].selector_positive_share for cut in CUTS
        ]
        (line,) = panels[0].plot(
            CUTS,
            oracle,
            color=FAMILY_COLORS[family],
            marker=FAMILY_MARKERS[family],
            markersize=5,
            linewidth=1.8,
            label=FAMILY_LABELS[family],
        )
        handles.append(line)
        panels[1].plot(
            CUTS,
            hit_rate,
            color=FAMILY_COLORS[family],
            marker=FAMILY_MARKERS[family],
            markersize=5,
            linewidth=1.8,
            label=FAMILY_LABELS[family],
        )

    for axes in panels:
        style_axes(axes)
        axes.set_xscale("log", base=2)
        axes.set_xticks(CUTS, [str(cut) for cut in CUTS])
        axes.set_xlabel("horizon (slices, 0.01 ms/city each)", color=MUTED, fontsize=9)
    panels[0].set_yscale("symlog", linthresh=0.01, linscale=1.0)
    panels[0].set_yticks([0.0, 0.01, 0.1, 1.0, 10.0], ["0", "0.01", "0.1", "1", "10"])
    panels[0].set_ylabel("oracle headroom (median gap %)", color=MUTED, fontsize=9)
    panels[0].set_title("Headroom is large but short-lived", color=INK, fontsize=10)
    panels[0].annotate(
        "random matrix (38.8%)",
        xy=(1.0, 38.8),
        xytext=(1.25, 22.0),
        color=FAMILY_COLORS["random matrix"],
        fontsize=8,
        arrowprops={"arrowstyle": "-", "color": FAMILY_COLORS["random matrix"]},
    )
    panels[1].set_ylim(0.0, 100.0)
    panels[1].set_ylabel(
        "states where selector beats greedy (%)",
        color=MUTED,
        fontsize=9,
    )
    panels[1].set_title(
        "The first-slice selector wins early, less often later",
        color=INK,
        fontsize=10,
    )
    panels[0].legend(
        handles=handles,
        labels=list(FAMILY_LABELS.values()),
        loc="upper right",
        frameon=False,
        fontsize=7.5,
        labelcolor=INK,
    )
    figure.suptitle(
        "How the AOS opportunity decays with the horizon (R1, median over states)",
        color=INK,
        fontsize=11,
    )
    source_footer(
        figure,
        extra=(
            "At 1 slice oracle headroom and selector gain coincide by construction; "
            "later cuts show what a selector that only sees the first slice retains"
        ),
    )
    path = FIGURES / "aos_horizon_by_family.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def plot_gap_bins(states: list[StateRow]) -> None:
    """Oracle headroom and selector hit rate by starting gap and horizon."""
    cells = group_cells(states, lambda row: gap_bin(row.start_gap_pct))
    gap_labels = [label for label, _, _ in GAP_BINS]
    first_slice = {
        label: cells[(label, 1)] for label in gap_labels if (label, 1) in cells
    }
    column_labels = [
        f"{label}\n{100.0 * first_slice[label].beaten_share:.0f}% beaten"
        for label in gap_labels
    ]
    oracle = cell_matrix(
        cells,
        gap_labels,
        CUTS,
        lambda cell: cell.median_oracle_pct,
    ).T
    selector = cell_matrix(
        cells,
        gap_labels,
        CUTS,
        lambda cell: 100.0 * cell.selector_positive_share,
    ).T

    figure, panels = plt.subplots(
        1,
        2,
        figsize=(12.6, 5.2),
        layout="constrained",
        facecolor=SURFACE,
    )
    heatmap(
        figure,
        panels[0],
        oracle,
        [str(cut) for cut in CUTS],
        column_labels,
        title="Oracle headroom by starting gap (median gap %)",
        cmap="YlOrRd",
        vmin=0.0,
        vmax=12.0,
        decimals=2,
        colorbar_label="median gap (%)",
    )
    panels[0].set_ylabel("horizon (slices)", color=MUTED, fontsize=9)
    heatmap(
        figure,
        panels[1],
        selector,
        [str(cut) for cut in CUTS],
        column_labels,
        title="First-slice selector beats greedy (% of states)",
        cmap="Blues",
        vmin=0.0,
        vmax=100.0,
        decimals=0,
        colorbar_label="% of states",
    )
    panels[1].set_ylabel("")
    figure.suptitle(
        "Starting optimality gap is a strong predictor of AOS room "
        "(R1, 0.01 ms/city slices)",
        color=INK,
        fontsize=11,
    )
    source_footer(
        figure,
        extra=(
            "Starting gap = state-tour gap before the first slice, relative "
            "to the known optimum; 32 paired repeats"
        ),
    )
    path = FIGURES / "aos_gap_bins.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def plot_class_horizon(states: list[StateRow]) -> None:
    """Oracle headroom and selector hit rate by state class and horizon."""
    cells = group_cells(states, lambda row: row.class_name)
    first_slice = {
        class_name: cells[(class_name, 1)]
        for class_name in CLASSES
        if (class_name, 1) in cells
    }
    row_labels = [
        f"{class_name} ({first_slice[class_name].states} states)"
        for class_name in CLASSES
    ]
    oracle = cell_matrix(
        cells,
        CLASSES,
        CUTS,
        lambda cell: cell.median_oracle_pct,
    )
    selector = cell_matrix(
        cells,
        CLASSES,
        CUTS,
        lambda cell: 100.0 * cell.selector_positive_share,
    )

    figure, panels = plt.subplots(
        1,
        2,
        figsize=(11.8, 4.4),
        layout="constrained",
        facecolor=SURFACE,
    )
    heatmap(
        figure,
        panels[0],
        oracle,
        row_labels,
        [str(cut) for cut in CUTS],
        title="Oracle headroom by state class (median gap %)",
        cmap="YlOrRd",
        vmin=0.0,
        vmax=6.5,
        decimals=2,
        colorbar_label="median gap (%)",
    )
    panels[0].set_ylabel("state class", color=MUTED, fontsize=9)
    heatmap(
        figure,
        panels[1],
        selector,
        row_labels,
        [str(cut) for cut in CUTS],
        title="First-slice selector beats greedy (% of states)",
        cmap="Blues",
        vmin=0.0,
        vmax=100.0,
        decimals=0,
        colorbar_label="% of states",
    )
    panels[1].set_ylabel("")
    for axes in panels:
        axes.set_xlabel("horizon (slices)", color=MUTED, fontsize=9)
    figure.suptitle(
        "AOS room lives in open 2-opt states, not in LK-optimal tours "
        "(R1, 0.01 ms/city slices)",
        color=INK,
        fontsize=11,
    )
    source_footer(
        figure,
        extra=(
            "Class = deepest local-optimality test reached before the slice; "
            "2-opt open means a 2-opt move still improves"
        ),
    )
    path = FIGURES / "aos_class_horizon.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def plot_size_scaling(states: list[StateRow]) -> None:
    """Oracle headroom against nominal size for the Euclidean families."""
    cells = group_cells(
        states,
        lambda row: f"{row.family}|{row.size}",
    )
    euclidean = [family for family in FAMILIES if family != "random matrix"]
    figure, panels = plt.subplots(
        1,
        2,
        figsize=(11.2, 4.5),
        layout="constrained",
        facecolor=SURFACE,
    )
    handles = []
    for family in euclidean:
        one = [cells[(f"{family}|{size}", 1)].median_oracle_pct for size in SIZES]
        eight = [cells[(f"{family}|{size}", 8)].median_oracle_pct for size in SIZES]
        (line,) = panels[0].plot(
            SIZES,
            one,
            color=FAMILY_COLORS[family],
            marker=FAMILY_MARKERS[family],
            markersize=6,
            linewidth=1.8,
            label=FAMILY_LABELS[family],
        )
        handles.append(line)
        panels[1].plot(
            SIZES,
            eight,
            color=FAMILY_COLORS[family],
            marker=FAMILY_MARKERS[family],
            markersize=6,
            linewidth=1.8,
            label=FAMILY_LABELS[family],
        )

    for axes in panels:
        style_axes(axes)
        axes.set_xscale("log")
        axes.set_xticks(SIZES, [str(size) for size in SIZES])
        axes.minorticks_off()
        axes.set_xlabel("nominal instance size (cities)", color=MUTED, fontsize=9)
        axes.set_ylim(bottom=0.0)
    panels[0].set_ylabel("oracle headroom at 1 slice (%)", color=MUTED, fontsize=9)
    panels[0].set_title("At the first slice", color=INK, fontsize=10)
    panels[1].set_ylabel("oracle headroom at 8 slices (%)", color=MUTED, fontsize=9)
    panels[1].set_title("What survives 8 slices", color=INK, fontsize=10)
    panels[0].legend(
        handles=handles,
        labels=[FAMILY_LABELS[family] for family in euclidean],
        loc="lower center",
        ncol=2,
        frameon=False,
        fontsize=7.5,
        labelcolor=INK,
    )
    figure.suptitle(
        "Larger Euclidean instances keep AOS room longer "
        "(random matrix omitted: 19.8-65.8% at 1 slice)",
        color=INK,
        fontsize=11,
    )
    source_footer(
        figure,
        extra=(
            "TSPLIB instances are grouped into the nearest nominal size; "
            "32 paired repeats per state"
        ),
    )
    path = FIGURES / "aos_size_scaling.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def plot_best_first_operators(states: list[StateRow]) -> None:
    """Share of states where each operator is the best first operator."""
    first_slice = [row for row in states if row.slices == 1 and row.tied_best == 1]
    if not first_slice:
        raise ValueError("no untied first-slice states")

    def share_table(
        group_of: Callable[[StateRow], str],
        order: tuple[str, ...],
    ) -> np.ndarray:
        groups: dict[str, list[StateRow]] = {group: [] for group in order}
        for row in first_slice:
            group = group_of(row)
            if group not in groups:
                raise ValueError(f"unexpected group {group!r}")
            groups[group].append(row)
        matrix = np.zeros((len(OPERATORS), len(order)))
        for column_index, group in enumerate(order):
            rows = groups[group]
            counts = {operator: 0 for operator in OPERATORS}
            for row in rows:
                if row.best_operator not in counts:
                    raise ValueError(f"unexpected operator {row.best_operator!r}")
                counts[row.best_operator] += 1
            for row_index, operator in enumerate(OPERATORS):
                matrix[row_index, column_index] = 100.0 * counts[operator] / len(rows)
        return matrix

    by_family = share_table(lambda row: row.family, FAMILIES)
    by_class = share_table(lambda row: row.class_name, CLASSES)

    figure, panels = plt.subplots(
        1,
        2,
        figsize=(12.4, 4.8),
        layout="constrained",
        facecolor=SURFACE,
    )
    heatmap(
        figure,
        panels[0],
        by_family,
        list(OPERATORS),
        [FAMILY_LABELS[family] for family in FAMILIES],
        title="Best first operator by family (% of untied states)",
        cmap="Blues",
        vmin=0.0,
        vmax=80.0,
        decimals=1,
        colorbar_label="% of states",
    )
    panels[0].set_ylabel("operator", color=MUTED, fontsize=9)
    heatmap(
        figure,
        panels[1],
        by_class,
        list(OPERATORS),
        list(CLASSES),
        title="Best first operator by state class (% of untied states)",
        cmap="Blues",
        vmin=0.0,
        vmax=80.0,
        decimals=1,
        colorbar_label="% of states",
    )
    panels[1].set_ylabel("")
    figure.suptitle(
        "AOS would mostly choose between kicks and LK variants; "
        "greedy is best in only 14% of states",
        color=INK,
        fontsize=11,
    )
    source_footer(
        figure,
        extra=(
            "1,231 untied first-slice states (26 have tied best gaps); "
            "ties broken by time then plan order"
        ),
    )
    path = FIGURES / "aos_best_first_operators.png"
    figure.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(figure)
    LOGGER.info("wrote %s", path)


def write_summary(states: list[StateRow]) -> None:
    """Write the aggregate table behind the figures."""
    groupings: list[tuple[str, Callable[[StateRow], str]]] = [
        ("family", lambda row: row.family),
        ("family_size", lambda row: f"{row.family}|{row.size}"),
        ("size", lambda row: str(row.size)),
        ("gap_bin", lambda row: gap_bin(row.start_gap_pct)),
        ("class", lambda row: row.class_name),
    ]
    fieldnames = [
        "grouping",
        "group",
        "slices",
        "states",
        "median_start_gap_pct",
        "beaten_share",
        "greedy_best_share",
        "median_oracle_pct",
        "oracle_q25_pct",
        "oracle_q75_pct",
        "median_selector_pct",
        "selector_positive_share",
    ]
    table: list[dict[str, str | int | float]] = []
    for grouping, key_of in groupings:
        cells = group_cells(states, key_of)
        for (group, cut), cell in sorted(cells.items()):
            table.append(
                {
                    "grouping": grouping,
                    "group": group,
                    "slices": cut,
                    "states": cell.states,
                    "median_start_gap_pct": round(cell.median_start_gap_pct, 6),
                    "beaten_share": round(cell.beaten_share, 6),
                    "greedy_best_share": round(cell.greedy_best_share, 6),
                    "median_oracle_pct": round(cell.median_oracle_pct, 6),
                    "oracle_q25_pct": round(cell.oracle_q25_pct, 6),
                    "oracle_q75_pct": round(cell.oracle_q75_pct, 6),
                    "median_selector_pct": round(cell.median_selector_pct, 6),
                    "selector_positive_share": round(cell.selector_positive_share, 6),
                }
            )
    path = DATA / "aos_opportunity.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(table)
    LOGGER.info("wrote %s", path)


def main() -> None:
    """Load the states and draw every figure."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    states = load_states()
    plot_opportunity_map(states)
    plot_horizon_by_family(states)
    plot_gap_bins(states)
    plot_class_horizon(states)
    plot_size_scaling(states)
    plot_best_first_operators(states)
    write_summary(states)


if __name__ == "__main__":
    main()
