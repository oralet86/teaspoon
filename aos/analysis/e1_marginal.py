"""Analysis of E1 measurement A (exact marginal rates).

Reads a run of :mod:`aos.e1_marginal` and writes, with a file-name prefix
(default ``e1m``):

- ``<prefix>_cells.csv``: every (instance, seed, state, operator) rate;
- ``<prefix>_leaders.csv``: leader and rival (the second-best operator,
  of any kind) per state, with the exact local-optimality class;
- ``<prefix>_classes.csv`` and ``<prefix>_stages.csv``: how often each
  operator leads, per class and per stage;
- ``<prefix>_nodes.csv``: per class and node feature, which operator leads at
  the node (Claim 1.3: does the leader differ between regions of a tour?);
- ``<prefix>_states_nodes.csv``: per state, the share of improving nodes led
  by each operator;
- figures under ``journal/figures/claim-1/``.

Exact classes, from every anchor of the deterministic operators:
``2-opt open`` (some O5, O6 or O7 application improves), ``2-opt optimum``
(none does, but O9 or O10 improves somewhere), ``3-opt optimum`` (none of
O5, O6, O7, O9, O10 improves at any anchor: a local optimum of 2-opt,
neighbour-list Or-opt and neighbour-list 3-opt) and, for runs that record
it, ``LK optimum`` (the LK step does not improve either).

Example::

    uv run python -m aos.analysis.e1_marginal runs/e1-main/<timestamp>/marginal \\
        --prefix e1main
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

from aos.problem import Problem
from aos.records import read_arrays

from . import REPOSITORY_ROOT, journal_paths, source_note
from .e1 import (
    CLASSES,
    family_counts,
    leader_group,
    leaders,
    operator_grouping,
    plot_family_shares,
    plot_leaders,
    plot_rates,
    write_csv,
)

LOGGER = logging.getLogger(__name__)

GROUPING = operator_grouping()
"""Leaders are counted per operator: no family grouping."""

SCRIPT = "aos/analysis/e1_marginal.py"

TWO_OPT = ("two_opt_first", "two_opt_best", "two_opt_nl")
DEEPER = ("or_opt_nl", "three_opt_nl")


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read a CSV, converting every value that parses as a number."""

    def convert(value: str) -> Any:
        if value in ("True", "False"):
            return value == "True"
        try:
            return float(value)
        except ValueError:
            return value

    with path.open(newline="") as handle:
        return [
            {key: convert(value) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def exact_class(group: dict[str, dict[str, Any]]) -> str:
    """Local-optimality class of a state.

    From the state-level flags of a budgeted run (checked at every city),
    or else from the improving-anchor counts of a run that visited every
    anchor.
    """
    row = next(iter(group.values()))
    if "improves_2opt" in row:
        if row["improves_2opt"]:
            return CLASSES[0]
        if row["improves_oropt"] or row["improves_3opt"]:
            return CLASSES[1]
        if row.get("improves_lk", True):
            return CLASSES[2]
        return CLASSES[3]
    if any(group[name]["improving_anchors"] > 0 for name in TWO_OPT):
        return CLASSES[0]
    if any(group[name]["improving_anchors"] > 0 for name in DEEPER):
        return CLASSES[1]
    return CLASSES[2]


def state_key(row: dict[str, Any]) -> tuple[str, int, str]:
    """(instance, seed, state) of a row."""
    return row["instance"], int(row["seed"]), row["state"]


def leader_rows_with_classes(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Leaders per state (:func:`aos.analysis.e1.leaders`) with the exact
    local-optimality class."""
    groups: dict[tuple[str, int, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in cells:
        groups[state_key(row)][row["operator"]] = row
    rows = leaders(cells)
    for row in rows:
        group = groups[state_key(row)]
        row["class"] = exact_class(group)
        row["improving_anchors_2opt"] = max(
            int(group[name]["improving_anchors"]) for name in TWO_OPT
        )
        row["improving_anchors_deeper"] = max(
            int(group[name]["improving_anchors"]) for name in DEEPER
        )
    return rows


def node_features(problem: Problem, order: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Static and dynamic features of every city.

    Returns:
        The tercile (0-2) of the distance to the nearest neighbour within the
        instance (a density proxy: 0 = densest third), and how many of the
        city's two tour edges go to one of its k nearest neighbours (0-2).
    """
    n = problem.n
    nearest = np.asarray(
        [problem.dist[city][problem.neighbours[city][0]] for city in range(n)]
    )
    ranks = np.argsort(np.argsort(nearest, kind="stable"), kind="stable")
    tercile = np.minimum(ranks * 3 // n, 2)
    position = np.empty(n, dtype=np.int64)
    position[order] = np.arange(n)
    good = np.zeros(n, dtype=np.int64)
    for city in range(n):
        place = position[city]
        listed = set(problem.neighbours[city])
        good[city] = int(order[(place + 1) % n] in listed) + int(
            order[place - 1] in listed
        )
    return tercile, good


def node_analysis(
    run_directory: Path, leader_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-node leaders, aggregated by class and node feature, and per state.

    A node's rate for an operator is its gain over its time at that anchor
    (means in two-stage runs, sums in older ones; the ratio is the same when
    every application at the anchor counts once); the node's leader is the
    best operator there, or ``none`` if nothing improves at the node.
    """
    config = json.loads((run_directory / "config.json").read_text())
    states_dir = REPOSITORY_ROOT / config["settings"]["states"] / "states"
    paths = {
        json.loads(path.read_text())["instance"]: json.loads(path.read_text())["path"]
        for path in states_dir.glob("*.json")
    }
    problems: dict[str, Problem] = {}
    by_feature: dict[tuple[str, str, int], Counter[str]] = defaultdict(Counter)
    per_state = []
    for row in leader_rows:
        instance, seed, state = state_key(row)
        if instance not in problems:
            problems[instance] = Problem.from_tsplib(REPOSITORY_ROOT / paths[instance])
        problem = problems[instance]
        data = read_arrays(
            run_directory / "nodes" / instance / f"s{seed}" / f"{state}.npz"
        )
        names = [str(name) for name in data["operators"]]
        visited = data["anchors"] if "anchors" in data else np.arange(problem.n)
        gain = data["gain"][:, visited]
        rate = gain / np.maximum(data["time_ns"][:, visited], 1.0)
        best = np.argmax(rate, axis=0)
        improving = gain.max(axis=0) > 0
        groups = [
            names[index] if ok else "none"
            for index, ok in zip(best, improving, strict=True)
        ]
        tercile, good = node_features(problem, data["order"])
        tercile, good = tercile[visited], good[visited]
        for city, group in enumerate(groups):
            by_feature[(row["class"], "density tercile", int(tercile[city]))][
                group
            ] += 1
            by_feature[(row["class"], "tour edges in k-NN", int(good[city]))][
                group
            ] += 1
        counts = Counter(group for group in groups if group != "none")
        total = sum(counts.values())
        per_state.append(
            {
                "instance": instance,
                "seed": seed,
                "state": state,
                "class": row["class"],
                "state_leader": leader_group(row, GROUPING),
                "improving_nodes": total,
                "share_led_by_state_leader": (
                    counts.get(leader_group(row, GROUPING), 0) / total
                    if total
                    else float("nan")
                ),
                **{
                    group: counts.get(group, 0)
                    for group in GROUPING.groups
                    if group != "none"
                },
            }
        )
    feature_rows = []
    for (cls, feature, value), counter in sorted(
        by_feature.items(),
        key=lambda item: (CLASSES.index(item[0][0]), item[0][1], item[0][2]),
    ):
        feature_rows.append(
            {
                "class": cls,
                "feature": feature,
                "value": value,
                "nodes": sum(counter.values()),
                **{group: counter.get(group, 0) for group in GROUPING.groups},
            }
        )
    return feature_rows, per_state


FAMILY_PREFIXES = (
    ("uniform", "portgen-"),
    ("clustered", "netgen-"),
    ("tspgen", "tspgen-"),
    ("random matrix", "portmgen-"),
)
"""Instance families of generated instances, by the generator prefix of the
name (``<generator>-<n>-<seed>``, ``generators.tsp``)."""

FAMILIES_ORDER = ("uniform", "clustered", "tspgen", "TSPLIB", "random matrix")
"""Families in the order the journal reports them."""


def instance_family(name: str) -> str:
    """Family of an instance: its generator's, or ``TSPLIB`` for any other
    name."""
    for family, prefix in FAMILY_PREFIXES:
        if name.startswith(prefix):
            return family
    return "TSPLIB"


def instance_features(problem: Problem, samples: int = 20_000) -> dict[str, Any]:
    """Features the literature links to local-search behaviour.

    Coefficient of variation of nearest-neighbour distances (CVND, Mersmann
    et al. 2012), the share of cities whose two nearest neighbours are tied,
    and the share of random triples violating the triangle inequality (zero
    for metric instances; Johnson and McGeoch's random matrices violate it
    often).
    """
    rng = np.random.default_rng(0)
    dist = problem.dist
    nearest = np.asarray(
        [dist[city][problem.neighbours[city][0]] for city in range(problem.n)],
        dtype=float,
    )
    second = np.asarray(
        [dist[city][problem.neighbours[city][1]] for city in range(problem.n)],
        dtype=float,
    )
    triples = rng.integers(0, problem.n, size=(samples, 3))
    triples = triples[
        (triples[:, 0] != triples[:, 1])
        & (triples[:, 1] != triples[:, 2])
        & (triples[:, 0] != triples[:, 2])
    ]
    violations = sum(
        dist[a][c] > dist[a][b] + dist[b][c] for a, b, c in triples.tolist()
    )
    return {
        "instance": problem.name,
        "family": instance_family(problem.name),
        "n": problem.n,
        "cvnd": float(nearest.std() / nearest.mean()) if nearest.mean() else 0.0,
        "tied_nearest": float(np.mean(nearest == second)),
        "triangle_violations": violations / len(triples),
    }


def order_rows(leader_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per instance and class: states, how many seeds, and the leaders with
    their counts (the table the Claim 1.2 criterion is judged on)."""
    table: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    seeds: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in leader_rows:
        key = (row["instance"], row["class"])
        table[key][leader_group(row, GROUPING)] += 1
        seeds[key].add(int(row["seed"]))
    return [
        {
            "family": instance_family(instance),
            "instance": instance,
            "class": cls,
            "states": sum(counter.values()),
            "seeds": len(seeds[(instance, cls)]),
            "leaders": "; ".join(
                f"{name} {count}" for name, count in counter.most_common()
            ),
        }
        for (instance, cls), counter in sorted(
            table.items(),
            key=lambda item: (
                FAMILIES_ORDER.index(instance_family(item[0][0])),
                item[0][0],
                CLASSES.index(item[0][1]),
            ),
        )
    ]


def plot_nodes(feature_rows: list[dict[str, Any]], path: Path, note: str) -> None:
    """Stacked bars: which operator leads at improving nodes, by class (columns)
    and node feature (rows)."""
    features = ("tour edges in k-NN", "density tercile")
    figure, axes = plt.subplots(
        len(features), len(CLASSES), figsize=(15, 7.5), squeeze=False
    )
    shown = [group for group in GROUPING.groups if group != "none"]
    for row_index, feature in enumerate(features):
        for column, cls in enumerate(CLASSES):
            axis = axes[row_index][column]
            rows = [
                row
                for row in feature_rows
                if row["class"] == cls and row["feature"] == feature
            ]
            bottoms = [0.0] * len(rows)
            totals = [sum(row[group] for group in shown) for row in rows]
            positions = list(range(len(rows)))
            for group in shown:
                label, colour = GROUPING.groups[group]
                shares = [
                    row[group] / total if total else 0.0
                    for row, total in zip(rows, totals, strict=True)
                ]
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
                for position, share, bottom in zip(
                    positions, shares, bottoms, strict=True
                ):
                    if share >= 0.15:
                        axis.text(
                            position,
                            bottom + share / 2,
                            f"{share:.0%}",
                            ha="center",
                            va="center",
                            fontsize=6,
                            color="#1a1a19",
                        )
                bottoms = [b + s for b, s in zip(bottoms, shares, strict=True)]
            axis.set_xticks(
                positions,
                [
                    f"{row['value']}\n(n={total})"
                    for row, total in zip(rows, totals, strict=True)
                ],
                fontsize=7,
            )
            axis.set_ylim(0, 1)
            axis.set_xlabel(
                "tour edges to k nearest (0-2)"
                if feature == "tour edges in k-NN"
                else "nearest-neighbour distance tercile (0 = densest)",
                fontsize=8,
            )
            if column == 0:
                axis.set_ylabel("share of improving nodes led")
            axis.set_title(cls if row_index == 0 else "", fontsize=10)
            axis.spines[["top", "right"]].set_visible(False)
            axis.grid(True, axis="y", alpha=0.25)
    figure.legend(
        handles=[
            Patch(color=GROUPING.groups[g][1], label=GROUPING.groups[g][0])
            for g in shown
        ],
        loc="upper center",
        ncol=min(len(shown), 8),
        fontsize=8,
        frameon=False,
    )
    figure.text(0.01, 0.005, note, fontsize=7)
    figure.tight_layout(rect=(0, 0.02, 1, 0.95))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def analyse(run_directory: Path, prefix: str) -> None:
    """Write all journal data and figures of a measurement-A run."""
    data_dir, figure_dir = journal_paths(1)
    cells = read_rows(run_directory / "cells.csv")
    leader_rows = leader_rows_with_classes(cells)
    class_rows = family_counts(leader_rows, "class", GROUPING)
    stage_rows = family_counts(leader_rows, "state", GROUPING)
    feature_rows, per_state = node_analysis(run_directory, leader_rows)
    config = json.loads((run_directory / "config.json").read_text())
    states_dir = REPOSITORY_ROOT / config["settings"]["states"] / "states"
    instance_paths = {}
    for path in states_dir.glob("*.json"):
        payload = json.loads(path.read_text())
        instance_paths[payload["instance"]] = payload["path"]
    measured = sorted({row["instance"] for row in cells})
    features = [
        instance_features(Problem.from_tsplib(REPOSITORY_ROOT / instance_paths[name]))
        for name in measured
    ]
    write_csv(data_dir / f"{prefix}_cells.csv", cells)
    write_csv(data_dir / f"{prefix}_leaders.csv", leader_rows)
    write_csv(data_dir / f"{prefix}_classes.csv", class_rows)
    write_csv(data_dir / f"{prefix}_stages.csv", stage_rows)
    write_csv(data_dir / f"{prefix}_nodes.csv", feature_rows)
    write_csv(data_dir / f"{prefix}_instances.csv", features)
    write_csv(data_dir / f"{prefix}_order.csv", order_rows(leader_rows))
    write_csv(data_dir / f"{prefix}_states_nodes.csv", per_state)
    seeds = sorted({int(row["seed"]) for row in cells})
    if "timed_rounds" in cells[0]:
        repeats = int(max(row["gain_repeats"] for row in cells))
        rounds = sorted({int(row["timed_rounds"]) for row in cells})
        applications = (
            f"gains: 1 application per deterministic operator and anchor, "
            f"{repeats} per random one; {rounds[0]}-{rounds[-1]} timed rounds"
        )
    else:
        repeats = int(
            cells[0]["applications"] // cells[0].get("anchors", cells[0]["n"])
        )
        applications = f"{repeats} applications per operator and anchor"
    caption = (
        f"state seeds {seeds[0]}-{seeds[-1]}; {applications}; run {run_directory.name}"
    )
    plot_rates(
        cells,
        figure_dir / f"{prefix}_rates.png",
        source_note([f"{prefix}_cells.csv"], SCRIPT, caption),
    )
    plot_leaders(
        leader_rows,
        figure_dir / f"{prefix}_leaders.png",
        source_note([f"{prefix}_leaders.csv"], SCRIPT, caption),
        GROUPING,
    )
    plot_family_shares(
        stage_rows,
        class_rows,
        figure_dir / f"{prefix}_families.png",
        source_note([f"{prefix}_stages.csv", f"{prefix}_classes.csv"], SCRIPT, caption),
        GROUPING,
    )
    plot_nodes(
        feature_rows,
        figure_dir / f"{prefix}_nodes.png",
        source_note([f"{prefix}_nodes.csv"], SCRIPT, caption),
    )
    LOGGER.info(
        "%d cells, %d states (%d separated)",
        len(cells),
        len(leader_rows),
        sum(row["separated"] for row in leader_rows),
    )


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.analysis.e1_marginal", description=__doc__
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--prefix", default="e1m")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    analyse(arguments.run_directory, arguments.prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
