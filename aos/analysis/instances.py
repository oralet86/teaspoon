"""Figures of the test instances and of the E1 state tours measured on them.

Point instances (``EUC_2D``, ``GEO``, ``ATT``) are drawn on their
coordinate axes.  Matrix instances (``EXPLICIT``) have no coordinates: the
instance is drawn as its distance matrix (city against city), and a tour as
the length of each of its edges in visiting order.

- ``--instances``: one PNG per instance, ``<name>.png``.
- ``--states``: one PNG per (instance, seed) of an E1 states run, a panel
  per state (random start to the ILS states), ``<name>_s<seed>.png``.

Examples::

    uv run python -m aos.analysis.instances --instances data/ALL_tsp/a280.tsp
    uv run python -m aos.analysis.instances --states runs/e1-main/<timestamp> \\
        --out runs/e1-main/<timestamp>/figures
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes

from aos.problem import Problem
from instances import load_distance_matrix, load_path

from . import REPOSITORY_ROOT, journal_paths, source_note

LOGGER = logging.getLogger(__name__)
SCRIPT = "aos/analysis/instances.py"


def coordinates(path: Path) -> np.ndarray | None:
    """Return the ``(n, 2)`` city coordinates, or ``None`` for a matrix."""
    nodes = load_path(path).nodes
    if nodes is None or nodes.coordinates is None:
        return None
    return np.asarray(nodes.coordinates, dtype=float)


def describe(problem: Problem, length: float | None = None) -> str:
    """Return a title line: name, size, and the optimum or a tour's gap."""
    text = f"{problem.name}  (n = {problem.n})"
    if length is None:
        if problem.optimum is not None:
            text += f"  optimum {problem.optimum:,.0f}"
    elif problem.optimum is not None:
        text += f"  length {length:,.0f}, gap {problem.gap(length):.2%}"
    return text


def draw_points(axes: Axes, points: np.ndarray, order: list[int] | None) -> None:
    """Draw the cities and, if given, the closed tour through them."""
    if order is not None:
        cycle = points[order + order[:1]]
        axes.plot(cycle[:, 0], cycle[:, 1], color="#1f5f99", linewidth=0.6)
    size = 6 if len(points) <= 200 else 3
    axes.scatter(points[:, 0], points[:, 1], s=size, color="#202020", zorder=3)
    axes.set_aspect("equal", adjustable="datalim")
    axes.set_xlabel("x")
    axes.set_ylabel("y")


def draw_tour_edges(axes: Axes, matrix: np.ndarray, order: list[int]) -> None:
    """Draw a matrix tour as the length of each edge in visiting order."""
    edges = matrix[order, order[1:] + order[:1]]
    axes.bar(np.arange(len(edges)), edges, width=1.0, color="#1f5f99")
    axes.set_xlabel("edge in visiting order")
    axes.set_ylabel("edge length")


def plot_instance(path: Path, directory: Path) -> Path:
    """Write the figure of one instance and return its path."""
    problem = Problem.from_tsplib(path)
    points = coordinates(path)
    figure, axes = plt.subplots(figsize=(7, 7))
    if points is None:
        image = axes.imshow(load_distance_matrix(path), cmap="viridis")
        figure.colorbar(image, ax=axes, shrink=0.8, label="distance")
        axes.set_xlabel("city")
        axes.set_ylabel("city")
    else:
        draw_points(axes, points, None)
    axes.set_title(describe(problem), fontsize=10)
    figure.text(
        0.01,
        0.005,
        source_note([display_path(path)], SCRIPT, "no seeds"),
        fontsize=6,
    )
    figure.tight_layout(rect=(0, 0.02, 1, 1))
    output = directory / f"{problem.name}.png"
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output


def plot_states(states_file: Path, directory: Path) -> Path:
    """Write the figure of every state of one (instance, seed)."""
    record: dict[str, Any] = json.loads(states_file.read_text())
    path = REPOSITORY_ROOT / record["path"]
    problem = Problem.from_tsplib(path)
    points = coordinates(path)
    matrix = load_distance_matrix(path) if points is None else None
    states = record["states"]
    columns = 4
    rows = math.ceil(len(states) / columns)
    figure, grid = plt.subplots(
        rows, columns, figsize=(4 * columns, 4 * rows), squeeze=False
    )
    for axes, state in zip(grid.flat, states, strict=False):
        if points is not None:
            draw_points(axes, points, state["order"])
        else:
            assert matrix is not None
            draw_tour_edges(axes, matrix, state["order"])
        gap = "" if state["gap"] is None else f", gap {state['gap']:.2%}"
        duplicate = f" (= {state['duplicate_of']})" if state.get("duplicate_of") else ""
        axes.set_title(f"{state['name']}{gap}{duplicate}", fontsize=9)
    for axes in list(grid.flat)[len(states) :]:
        axes.set_visible(False)
    seed = record["state_seed"]
    figure.suptitle(f"{describe(problem)}  ·  state seed {seed}", fontsize=11)
    figure.text(
        0.01,
        0.005,
        source_note([display_path(states_file)], SCRIPT, f"state seed {seed}"),
        fontsize=7,
    )
    figure.tight_layout(rect=(0, 0.02, 1, 0.97))
    output = directory / f"{states_file.stem}.png"
    figure.savefig(output, dpi=110)
    plt.close(figure)
    return output


def display_path(path: Path) -> str:
    """Return ``path`` relative to the repository when it lies inside it."""
    try:
        return str(path.resolve().relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.analysis.instances", description=__doc__
    )
    parser.add_argument("--instances", nargs="*", type=Path, default=[])
    parser.add_argument(
        "--states", type=Path, default=None, help="E1 run directory with states/"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: journal figures, instances/)",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not arguments.instances and arguments.states is None:
        parser.error("give --instances, --states or both")
    directory = arguments.out or journal_paths()[1] / "instances"
    directory.mkdir(parents=True, exist_ok=True)
    for path in arguments.instances:
        LOGGER.info("wrote %s", plot_instance(path, directory))
    if arguments.states is not None:
        files = sorted((arguments.states / "states").glob("*.json"))
        if not files:
            parser.error(f"no states files in {arguments.states / 'states'}")
        for states_file in files:
            LOGGER.info("wrote %s", plot_states(states_file, directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
