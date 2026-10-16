"""Run the genetic algorithm on a TSPLIB instance.

Examples::

    uv run python -m ga data/ALL_tsp/kroA100.tsp
    uv run python -m ga data/ALL_tsp/kroA100.tsp --selector uniform --seed 3
    uv run python -m ga data/ALL_tsp/kroA100.tsp --selector fixed:two_opt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from instances import load_distance_matrix, load_path

from .algorithm import GaConfig, GaResult, run_ga
from .operators import MUTATIONS
from .selectors import SELECTORS, make_selector

_PHASES = 4
_DEFAULTS = GaConfig()


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m ga", description=__doc__)
    parser.add_argument("instance", type=Path, help="symmetric TSPLIB instance")
    parser.add_argument(
        "--selector",
        default="adaptive-pursuit",
        help=f"{', '.join(SELECTORS)} or fixed:<operator> (default: adaptive-pursuit)",
    )
    parser.add_argument("--evaluations", type=int, default=_DEFAULTS.max_evaluations)
    parser.add_argument("--population", type=int, default=_DEFAULTS.population_size)
    parser.add_argument("--time-limit", type=float, default=None, help="seconds")
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        distances = load_distance_matrix(arguments.instance)
        instance = load_path(arguments.instance)
        selector = make_selector(arguments.selector, tuple(MUTATIONS))
        result = run_ga(
            distances,
            selector,
            GaConfig(
                population_size=arguments.population,
                max_evaluations=arguments.evaluations,
                time_limit_seconds=arguments.time_limit,
                seed=arguments.seed,
            ),
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"{instance.name}: length {result.length:g} after {result.evaluations} "
        f"evaluations in {result.runtime_seconds:.1f} s "
        f"(selector {arguments.selector}, seed {arguments.seed})"
    )
    # The instance's own length function is an independent check of the
    # matrix-based length, and the companion tour gives the best-known value.
    if instance.length_of is not None:
        measured = instance.length_of(result.tour, True)
        if not np.isclose(measured, result.length):
            print(f"error: instance measures {measured:g}", file=sys.stderr)
            return 1
    known = [instance.compute_length(sequence) for sequence in instance.sequences]
    if known and known[0]:
        print(f"best known {known[0]:g}, gap {result.length / known[0] - 1:.2%}")
    print(operator_table(result))
    return 0


def operator_table(result: GaResult) -> str:
    """Return per-operator usage, success and reward, with usage over time.

    The run is cut into equal phases; an adaptive selector shows up as a
    usage share that shifts from one phase to the next.
    """
    phases = np.array_split(result.log_operators, _PHASES)
    header = (
        f"{'operator':<11} {'uses':>7} {'improving':>10} {'mean gain':>10}  "
        + " ".join(f"{f'phase {phase + 1}':>8}" for phase in range(_PHASES))
    )
    lines = [header, "-" * len(header)]
    for index, name in enumerate(result.operator_names):
        used = result.log_operators == index
        deltas = result.log_deltas[used]
        improving = (deltas < 0).mean() if deltas.size else float("nan")
        gain = -deltas[deltas < 0].mean() if (deltas < 0).any() else 0.0
        shares = " ".join(
            f"{(phase == index).mean() if phase.size else float('nan'):>8.0%}"
            for phase in phases
        )
        lines.append(
            f"{name:<11} {int(used.sum()):>7} {improving:>10.1%} {gain:>10.1f}  "
            f"{shares}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
