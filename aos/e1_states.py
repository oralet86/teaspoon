"""Build E1 measurement states by local-optimality level instead of gap.

E1 showed that which neighbourhoods are exhausted, not the gap to the
optimum, decides the leading operator.  This builder therefore makes tours
along one search path per (instance, seed), without needing a known
optimum:

``random``, ``nearest_neighbour``
    a random permutation; the nearest-neighbour tour from city ``seed % n``;
``d25``, ``d50``, ``d75``, ``d90``
    the nearest-neighbour tour after an O7 descent stopped once it has made
    25, 50, 75 and 90% of the gain of the full descent (2-opt still
    improves, at moderate gaps; descents from a random tour stay far from
    optimal until they finish);
``opt2``
    the full O7 descent from the nearest-neighbour tour: a neighbour-list
    2-opt local optimum;
``oropt``
    ``opt2`` improved by O7 and O9 at every city until neither improves
    (2-opt and Or-opt exhausted, 3-opt usually not);
``opt3``
    ``oropt`` improved by O7, O9 and O10 until none of them improves
    anywhere (a VND local optimum);
``lkopt``
    ``opt3`` improved by O7, O9, O10 and the LK step (O18 with alpha
    candidates if cached, else O16) until none improves (an LK optimum);
``ils1``, ``ils2``
    ``lkopt`` after n and then 4n further O8 kick trials (iterated local
    search, keep if shorter), each followed by the same LK VND, so both are
    better LK optima.

The descent visits cities in a seeded random order, so seeds differ beyond
the starting tour.  Output matches :mod:`aos.e1` (``states/<instance>_s<seed>.json``),
so :mod:`aos.e1_marginal` reads it unchanged.
Building is untimed and runs in parallel.

Example::

    uv run python -m aos.e1_states --instances data/ALL_tsp/gr202.tsp --seeds 0 1 2
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import random
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .analysis import REPOSITORY_ROOT
from .cores import physical_cores, pin_to
from .operators import OPERATORS, descend, improve_city
from .problem import Problem
from .records import new_run_directory, write_config
from .tour import Tour
from .work import Work

LOGGER = logging.getLogger(__name__)

FRACTIONS = (0.25, 0.5, 0.75, 0.9)
"""Shares of the full descent's gain at which partial descents stop."""

ILS_ROUNDS = (1, 4)
"""Kick trials per round of iterated local search, in multiples of n."""


def nearest_neighbour_tour(problem: Problem, start: int = 0) -> list[int]:
    """Return the nearest-neighbour tour from ``start``: always move to the
    closest unvisited city (the classic greedy start of a local search)."""
    dist = problem.dist
    unvisited = set(range(problem.n))
    unvisited.remove(start)
    order = [start]
    current = start
    while unvisited:
        row = dist[current]
        current = min(unvisited, key=row.__getitem__)
        unvisited.remove(current)
        order.append(current)
    return order


def vnd(
    tour: Tour,
    problem: Problem,
    order: Sequence[int],
    names: Sequence[str] = ("or_opt_nl", "three_opt_nl"),
) -> None:
    """Apply O7 and the operators ``names`` at every city (in ``order``)
    until none of them improves at any city."""
    operators = [OPERATORS[name] for name in names]
    rng = random.Random(0)
    work = Work()
    improved = True
    while improved:
        improved = False
        for city in order:
            if improve_city(tour, problem, city, work)[0] < 0:
                improved = True
            for operator in operators:
                if operator(tour, problem, rng, work, city)[0] < 0:
                    improved = True


def build_states(path: str, seed: int, cpu: int) -> dict[str, Any]:
    """Build the states of one instance and seed (worker)."""
    pin_to(cpu)
    problem = Problem.from_tsplib(REPOSITORY_ROOT / path)
    n = problem.n
    rng = random.Random(f"e1-states:{problem.name}:{seed}")
    order = list(range(n))
    rng.shuffle(order)
    visit = list(range(n))
    rng.shuffle(visit)
    states: list[dict[str, Any]] = []

    def add(name: str, tour_order: list[int], source: str) -> None:
        length = problem.tour_length(tour_order)
        # Identical tours are kept but marked, and measurement skips them.
        duplicate = next(
            (state["name"] for state in states if state["order"] == tour_order),
            None,
        )
        states.append(
            {
                "name": name,
                "gap": None if problem.optimum is None else problem.gap(length),
                "length": length,
                "source": source,
                "order": list(tour_order),
                "duplicate_of": duplicate,
            }
        )

    add("random", order, "random permutation")
    start = seed % n
    greedy = nearest_neighbour_tour(problem, start)
    add("nearest_neighbour", greedy, f"nearest neighbour from city {start}")
    full = Tour.from_order(greedy, problem)
    total = -descend(full, problem, Work(), visit)
    for fraction in FRACTIONS:
        partial = Tour.from_order(greedy, problem)
        descend(partial, problem, Work(), visit, stop_gain=fraction * total)
        add(
            f"d{round(fraction * 100):02d}",
            partial.order,
            f"O7 descent from nearest neighbour stopped at {fraction:.0%} of "
            "its full gain",
        )
    add("opt2", full.order, "full O7 descent from the nearest-neighbour tour")
    tour = full
    vnd(tour, problem, visit, ("or_opt_nl",))
    add("oropt", tour.order, "VND (O7, O9) from opt2")
    vnd(tour, problem, visit)
    add("opt3", tour.order, "VND (O7, O9, O10) from oropt")
    lk = "lin_kernighan_alpha" if problem.alpha is not None else "lin_kernighan"
    lk_names = ("or_opt_nl", "three_opt_nl", lk)
    vnd(tour, problem, visit, lk_names)
    add("lkopt", tour.order, f"VND (O7, O9, O10, {lk}) from opt3")
    kick = OPERATORS["kick"]
    kick_rng = random.Random(f"e1-states-ils:{problem.name}:{seed}")
    for index, rounds in enumerate(ILS_ROUNDS, start=1):
        for _ in range(rounds * n):
            kick(tour, problem, kick_rng, Work())
        vnd(tour, problem, visit, lk_names)
        add(
            f"ils{index}",
            tour.order,
            f"lkopt after {sum(ILS_ROUNDS[:index])}n O8 kick trials, then LK VND",
        )
    for state in states:
        Tour.from_order(state["order"], problem).check(problem)
    return {
        "instance": problem.name,
        "path": path,
        "n": n,
        "optimum": problem.optimum,
        "state_seed": seed,
        "states": states,
    }


def run(
    paths: Sequence[str],
    seeds: Sequence[int],
    directory: Path,
    workers: int,
    single_seed_above: int,
) -> None:
    """Build every (instance, seed) in parallel and write the states files.

    Instances with more than ``single_seed_above`` cities get only the first
    seed (their distance matrix needs gigabytes per process).
    """
    tasks = []
    for path in paths:
        n = Problem.from_tsplib(REPOSITORY_ROOT / path).n
        for seed in seeds if n <= single_seed_above else seeds[:1]:
            tasks.append((path, seed))
    cores = physical_cores()[:workers]
    arguments = [
        (path, seed, cores[index % len(cores)])
        for index, (path, seed) in enumerate(tasks)
    ]
    states_dir = directory / "states"
    states_dir.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, maxtasksperchild=1) as pool:
        for result in pool.starmap(build_states, arguments, chunksize=1):
            stem = f"{result['instance']}_s{result['state_seed']}"
            (states_dir / f"{stem}.json").write_text(json.dumps(result) + "\n")
            LOGGER.info(
                "%s: %s",
                stem,
                ", ".join(
                    f"{state['name']} {state['length']:.0f}"
                    for state in result["states"]
                ),
            )


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.e1_states", description=__doc__
    )
    parser.add_argument("--instances", nargs="+", required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=4, help="untimed workers")
    parser.add_argument(
        "--single-seed-above",
        type=int,
        default=5000,
        help="instances larger than this get only the first seed",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    directory = arguments.out or new_run_directory("e1-states")
    directory.mkdir(parents=True, exist_ok=True)
    write_config(
        directory,
        {
            "experiment": "E1 states by local-optimality level",
            "instances": arguments.instances,
            "seeds": arguments.seeds,
            "fractions": FRACTIONS,
            "ils_rounds": ILS_ROUNDS,
            "single_seed_above": arguments.single_seed_above,
        },
    )
    run(
        arguments.instances,
        arguments.seeds,
        directory,
        max(1, min(arguments.workers, len(physical_cores()))),
        arguments.single_seed_above,
    )
    LOGGER.info("states directory: %s", directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
