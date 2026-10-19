"""Shared pieces of the E1 analysis: leaders, counts and figures.

Used by :mod:`aos.analysis.e1_marginal`, which reads a measurement-A run and
writes the journal data and figures; this module has no command line of its
own.

- :func:`leaders`: the leading operator of every state and its rival (the
  second-best operator), their margin and whether their 95% intervals are
  separated;
- :func:`family_counts`: how often each operator leads, per instance and
  class or stage;
- :func:`plot_rates`, :func:`plot_leaders`, :func:`plot_family_shares`.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

from aos.operators import FAMILIES, OPERATOR_IDS, OPERATORS

COLOURS = plt.get_cmap("tab20")
"""Up to twenty distinguishable colours, one per operator in protocol order."""

STAGES = (
    "random",
    "nearest_neighbour",
    "d25",
    "d50",
    "d75",
    "d90",
    "opt2",
    "oropt",
    "opt3",
    "lkopt",
    "ils1",
    "ils2",
)
"""State names of :mod:`aos.e1_states` in order of search progress."""

CLASSES = ("2-opt open", "2-opt optimum", "3-opt optimum", "LK optimum")
"""Local-optimality classes in order of search progress."""


def operator_colour(name: str) -> tuple[float, float, float, float]:
    """Return the colour of an operator, the same in every E1 figure."""
    return COLOURS(list(OPERATORS).index(name) % COLOURS.N)


@dataclass(frozen=True)
class Grouping:
    """How leaders are grouped for counting and colouring.

    Attributes:
        groups: Group key to (label, colour), in display order; includes
            ``none`` for states where no operator improves.
        key_of: Group key of an operator name.
    """

    groups: dict[str, tuple[str, Any]]
    key_of: Callable[[str], str]


NONE_GROUP = ("no operator improves", "#b9b8b2")
"""Label and colour of states where no operator improves."""


def operator_grouping() -> Grouping:
    """One group per operator, coloured as in the rate plots, plus ``none``."""
    groups: dict[str, tuple[str, Any]] = {
        name: (f"{OPERATOR_IDS[name]} {name}", operator_colour(name))
        for name in OPERATORS
    }
    groups["none"] = NONE_GROUP
    return Grouping(groups, lambda name: name)


def leader_group(row: dict[str, Any], grouping: Grouping) -> str:
    """Return the group of a state's leader, or ``none`` if no operator
    improved (the leader is then an arbitrary rate-zero operator)."""
    return grouping.key_of(row["leader"]) if row["any_improvement"] else "none"


def stage_rank(state: str) -> int:
    """Position of a state name in :data:`STAGES` (unknown names last)."""
    return STAGES.index(state) if state in STAGES else len(STAGES)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write dictionaries with identical keys as CSV."""
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def leaders(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The leading operator of every (instance, seed, state) and its rival,
    the second-best operator of any family.

    Raises:
        ValueError: If a state has fewer than two operators.
    """
    groups: dict[tuple[str, int, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in cells:
        groups[(row["instance"], int(row["seed"]), row["state"])][row["operator"]] = row
    result = []
    for (instance, seed, state), group in groups.items():
        ranked = sorted(group.values(), key=lambda row: -row["rate"])
        top = ranked[0]
        if len(ranked) < 2:
            raise ValueError(f"{instance} s{seed} {state}: no rival operator")
        rival = ranked[1]
        result.append(
            {
                "instance": instance,
                "n": int(top["n"]),
                "seed": seed,
                "state": state,
                "gap": top["gap"],
                "leader": top["operator"],
                "leader_id": OPERATOR_IDS[top["operator"]],
                "leader_family": FAMILIES[top["operator"]],
                "leader_rate": top["rate"],
                "rival": rival["operator"],
                "rival_id": OPERATOR_IDS[rival["operator"]],
                "rival_rate": rival["rate"],
                "margin": top["rate"] / rival["rate"]
                if rival["rate"] > 0
                else float("inf"),
                "separated": top["rate_low"] > rival["rate_high"],
                "any_improvement": top["rate"] > 0,
                "o7_p_improve": group["two_opt_nl"]["p_improve"],
                "stop_reason": top.get("stop_reason", ""),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            row["n"],
            row["instance"],
            row["seed"],
            stage_rank(row["state"]),
        ),
    )


def family_counts(
    leader_rows: list[dict[str, Any]],
    key: str,
    grouping: Grouping,
) -> list[dict[str, Any]]:
    """Count leading groups per (instance, ``key``) and, under the instance
    name ``all``, over all instances."""
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    separated: dict[tuple[str, str], int] = defaultdict(int)
    for row in leader_rows:
        for instance in (row["instance"], "all"):
            cell = (instance, row[key])
            counts[cell][leader_group(row, grouping)] += 1
            separated[cell] += int(row["separated"])
    order = {CLASSES[i]: i for i in range(len(CLASSES))} if key == "class" else None
    result = []
    for (instance, value), counter in sorted(
        counts.items(),
        key=lambda item: (
            item[0][0] == "all",
            item[0][0],
            order[item[0][1]] if order else stage_rank(item[0][1]),
        ),
    ):
        result.append(
            {
                "instance": instance,
                key: value,
                "states": sum(counter.values()),
                "separated": separated[(instance, value)],
                **{group: counter.get(group, 0) for group in grouping.groups},
            }
        )
    return result


def plot_rates(cells: list[dict[str, Any]], path: Path, note: str) -> None:
    """Gain per second against gap, one panel per instance.  Each state is a
    faint point; lines join the per-stage medians over seeds."""
    instances = sorted(
        {row["instance"] for row in cells},
        key=lambda name: next(row["n"] for row in cells if row["instance"] == name),
    )
    columns = min(3, len(instances))
    rows_count = (len(instances) + columns - 1) // columns
    figure, axes = plt.subplots(
        rows_count, columns, figsize=(5.5 * columns, 4.5 * rows_count), squeeze=False
    )
    floor = 1e-3
    for axis, instance in zip(axes.flat, instances, strict=False):
        for name in OPERATORS:
            colour = operator_colour(name)
            points = [
                row
                for row in cells
                if row["instance"] == instance and row["operator"] == name
            ]
            axis.scatter(
                [row["gap"] * 100 for row in points],
                [max(row["rate"], floor) for row in points],
                s=8,
                color=colour,
                alpha=0.35,
                linewidths=0,
            )
            by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in points:
                by_stage[row["state"]].append(row)
            medians = sorted(
                (
                    median(row["gap"] for row in group) * 100,
                    max(median(row["rate"] for row in group), floor),
                )
                for group in by_stage.values()
            )
            axis.plot(
                [point[0] for point in medians],
                [point[1] for point in medians],
                marker="o",
                markersize=3,
                linewidth=1.5,
                color=colour,
                label=f"{OPERATOR_IDS[name]} {name}",
            )
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.invert_xaxis()
        axis.set_xlabel("gap to optimum (%) — search progresses to the right")
        axis.set_ylabel("gain per second (length units/s)")
        axis.set_title(instance)
        axis.grid(True, which="both", alpha=0.3)
    for axis in list(axes.flat)[len(instances) :]:
        axis.axis("off")
    axes.flat[0].legend(fontsize=7)
    figure.suptitle(
        "E1: gain per second of each operator by search state "
        "(points: states; lines: medians per stage)"
    )
    figure.text(0.01, 0.005, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.02, 1, 1))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_leaders(
    leader_rows: list[dict[str, Any]],
    path: Path,
    note: str,
    grouping: Grouping,
) -> None:
    """Grid of the leading operator per (instance, seed) row and stage column,
    coloured by group; faded where the leader is not separated from its
    rival."""
    runs = sorted(
        {(row["n"], row["instance"], row["seed"]) for row in leader_rows}, reverse=True
    )
    stages = sorted({row["state"] for row in leader_rows}, key=stage_rank)
    height = 1.6 + 0.32 * len(runs)
    figure, axis = plt.subplots(figsize=(11, height))
    index = {(row["instance"], row["seed"], row["state"]): row for row in leader_rows}
    for row_index, (_n, instance, seed) in enumerate(runs):
        for column, state in enumerate(stages):
            row = index.get((instance, seed, state))
            if row is None:
                continue
            colour = grouping.groups[leader_group(row, grouping)][1]
            alpha = 1.0 if row["separated"] else 0.35
            axis.add_patch(
                Rectangle(
                    (column + 0.03, row_index + 0.05),
                    0.94,
                    0.9,
                    color=colour,
                    alpha=alpha,
                    linewidth=0,
                )
            )
            label = row["leader_id"] if row["any_improvement"] else "none"
            axis.text(
                column + 0.5,
                row_index + 0.5,
                f"{label}  {row['gap'] * 100:.1f}%",
                ha="center",
                va="center",
                fontsize=6,
            )
    axis.set_xlim(0, len(stages))
    axis.set_ylim(0, len(runs))
    axis.set_yticks(
        [index + 0.5 for index in range(len(runs))],
        [f"{instance} s{seed}" for _n, instance, seed in runs],
        fontsize=6,
    )
    axis.set_xticks([index + 0.5 for index in range(len(stages))], stages, fontsize=8)
    axis.tick_params(length=0)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.set_xlabel("stage (decreasing typical gap); label: leader and state gap")
    axis.set_title(
        "E1: leading operator per state; faded = not separated from the rival"
    )
    figure.legend(
        handles=[
            Patch(color=colour, label=label)
            for label, colour in grouping.groups.values()
        ],
        loc="upper center",
        ncol=min(len(grouping.groups), 8),
        fontsize=7,
        frameon=False,
    )
    figure.text(0.01, 0.003, note, fontsize=7)
    top = 1 - 0.45 / height
    bottom = 0.25 / height
    figure.tight_layout(rect=(0, bottom, 1, top))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_family_shares(
    stage_rows: list[dict[str, Any]],
    class_rows: list[dict[str, Any]],
    path: Path,
    note: str,
    grouping: Grouping,
) -> None:
    """Stacked bars: share of states each group leads, per stage (one panel
    per instance) and per local-optimality class (all instances)."""
    instances = [
        name
        for name in dict.fromkeys(row["instance"] for row in stage_rows)
        if name != "all"
    ]
    panels = [*instances, "by class"]
    columns = 3
    rows_count = (len(panels) + columns - 1) // columns
    figure, axes = plt.subplots(
        rows_count, columns, figsize=(5.5 * columns, 3.6 * rows_count), squeeze=False
    )
    for axis, panel in zip(axes.flat, panels, strict=False):
        if panel == "by class":
            rows = [row for row in class_rows if row["instance"] == "all"]
            labels = [f"{row['class']}\n(n={row['states']})" for row in rows]
            title = "all instances, by local-optimality class"
        else:
            rows = [row for row in stage_rows if row["instance"] == panel]
            labels = [row["state"].replace("nearest_neighbour", "NN") for row in rows]
            title = panel
        positions = list(range(len(rows)))
        bottoms = [0.0] * len(rows)
        for group, (label, colour) in grouping.groups.items():
            shares = [row[group] / row["states"] for row in rows]
            axis.bar(
                positions,
                shares,
                bottom=bottoms,
                color=colour,
                width=0.8,
                edgecolor="white",
                linewidth=1,
                label=label,
            )
            for position, share, bottom in zip(positions, shares, bottoms, strict=True):
                if share >= 0.2:
                    axis.text(
                        position,
                        bottom + share / 2,
                        f"{share:.0%}",
                        ha="center",
                        va="center",
                        fontsize=6,
                        color="#1a1a19",
                    )
            bottoms = [
                bottom + share for bottom, share in zip(bottoms, shares, strict=True)
            ]
        axis.set_xticks(positions, labels, fontsize=7)
        axis.set_ylim(0, 1)
        axis.set_ylabel("share of states led")
        axis.set_title(title, fontsize=10)
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(True, axis="y", alpha=0.25)
    for axis in list(axes.flat)[len(panels) :]:
        axis.axis("off")
    handles = [
        Patch(color=colour, label=label) for label, colour in grouping.groups.values()
    ]
    figure.legend(
        handles=handles,
        loc="upper center",
        ncol=min(len(grouping.groups), 8),
        fontsize=8,
        frameon=False,
    )
    figure.text(0.01, 0.005, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.02, 1, 0.95))
    figure.savefig(path, dpi=150)
    plt.close(figure)
