"""Synthetic symmetric TSP instances from recognised generators.

- ``uniform``: DIMACS ``portgen``, integer points uniform on the
  10^6 x 10^6 square (the Challenge's ``E`` instances);
- ``clustered``: netgen ``generateClusteredNetwork`` with 5 clusters, the
  procedure of the NETGEN set (Kerschke et al., Evol. Comput. 2018);
- ``random_matrix``: DIMACS ``portmgen``, symmetric matrices with entries
  uniform on 0..10^6 - 1, no triangle inequality (``M`` instances);
- ``tspgen``: the tspgen mutation-based generator (Bossek et al., FOGA
  2019) with the settings of the paper's TSPGEN set.

The DIMACS generators are those of the 8th DIMACS Implementation Challenge
(Johnson and McGeoch 2002; see :mod:`generators.dimacs`); netgen and tspgen
run in R (see :mod:`generators.netgen` and :mod:`generators.tspgen`).  All
point instances lie in [0, 10^6]^2.  Optionally each instance is solved to
optimality with Concorde and the tour is written next to it as
``<name>.opt.tour``, the companion file the loaders pick up automatically.

Examples::

    uv run python -m generators.tsp --kind uniform --n 200 --seed 0 \\
        --out data/generated/tsp --solve
    uv run python -m generators.tsp --kind tspgen --n 500 --count 5 --seed 0 \\
        --out data/generated/tsp
"""

from __future__ import annotations

import argparse
import logging
import sys
from enum import StrEnum
from pathlib import Path

from solvers import solve_concorde

from .dimacs import DimacsGenerator, generate_dimacs
from .netgen import generate_netgen
from .tspgen import generate_tspgen

LOGGER = logging.getLogger(__name__)


class Kind(StrEnum):
    """Instance family."""

    UNIFORM = "uniform"
    CLUSTERED = "clustered"
    RANDOM_MATRIX = "random_matrix"
    TSPGEN = "tspgen"


_DIMACS_GENERATORS: dict[Kind, DimacsGenerator] = {
    Kind.UNIFORM: DimacsGenerator.PORTGEN,
    Kind.RANDOM_MATRIX: DimacsGenerator.PORTMGEN,
}


def generate(kind: Kind, n: int, seed: int, output_dir: Path) -> Path:
    """Generate one instance and return its path.

    The file is named after the generator: ``portgen-<n>-<seed>.tsp``,
    ``netgen-…``, ``portmgen-…`` or ``tspgen-<n>-<seed>.tsp``.

    Raises:
        ValueError: If ``n`` is smaller than 8.
        FileNotFoundError: If the generator is not installed or built.
        DimacsError, NetgenError, TspgenError: If the generator fails.
    """
    if kind is Kind.TSPGEN:
        return generate_tspgen(n, seed, output_dir)
    if kind is Kind.CLUSTERED:
        return generate_netgen(n, seed, output_dir)
    return generate_dimacs(_DIMACS_GENERATORS[kind], n, seed, output_dir)


def solve_and_store(path: Path, timeout_seconds: float | None = None) -> Path:
    """Solve an instance with Concorde and write ``<name>.opt.tour`` beside it."""
    result = solve_concorde(path, seed=0, timeout_seconds=timeout_seconds)
    destination = path.with_name(f"{path.stem}.opt.tour")
    result.save_solution(destination)
    LOGGER.info("%s: optimum %s written to %s", path.name, result.length, destination)
    return destination


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m generators.tsp", description=__doc__
    )
    parser.add_argument("--kind", type=Kind, choices=list(Kind), required=True)
    parser.add_argument("--n", type=int, required=True, help="number of cities")
    parser.add_argument("--count", type=int, default=1, help="instances (seeds seed..)")
    parser.add_argument("--seed", type=int, default=0, help="first seed")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument(
        "--solve", action="store_true", help="solve with Concorde, write .opt.tour"
    )
    parser.add_argument("--timeout", type=float, default=None, help="Concorde seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Generate (and optionally solve) instances; return an exit status."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = parse_arguments(argv)
    try:
        for seed in range(arguments.seed, arguments.seed + arguments.count):
            path = generate(arguments.kind, arguments.n, seed, arguments.out)
            LOGGER.info("wrote %s", path)
            if arguments.solve:
                solve_and_store(path, arguments.timeout)
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
