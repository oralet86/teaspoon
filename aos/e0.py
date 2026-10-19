"""E0: can the clock be trusted, and what does each operator cost?

Measures, on generated uniform instances of several sizes (DIMACS
``portgen``; E0 runs before 2026-10-18 used the earlier in-house generator
on [0, 1000]^2):

1. **Cost per application** of every operator, on two states (a random tour
   and an O7 local optimum), in timed batches on fresh copies of the state.
   Repeats are interleaved over the whole sweep so slow drift in machine
   speed shows up as spread between repeats.
2. **Full O7 descents** from random tours (budget option B).

``config.json`` and ``clock_end.json`` hold the boost, SMT and frequency
settings, so a run on a fixed clock can be told from one with boost on.
Whether parallel timed workers change the costs is measured separately, on
all twenty operators, by :mod:`aos.e0_contention`.

Raw batches and summaries go to ``runs/e0/<timestamp>/``; ``aos.analysis.e0``
turns them into journal data and figures.

Example::

    uv run python -m aos.e0            # full run (minutes)
    uv run python -m aos.e0 --quick    # dry run (seconds)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from generators.tsp import Kind, generate

from .cores import clock_state, physical_cores, pin_to
from .operators import OPERATORS, descend
from .problem import Problem
from .records import new_run_directory, write_arrays, write_config
from .tour import Tour
from .work import Work

LOGGER = logging.getLogger(__name__)

BATCH_SIZES = {
    "swap": 50,
    "insertion": 50,
    "inversion": 50,
    "or_opt": 50,
    "two_opt_first": 10,
    "two_opt_best": 10,
    "two_opt_nl": 50,
    "kick": 1,
}
"""Applications per timed batch; small enough that the state barely drifts,
large enough that timer overhead is negligible.  The cost sweep times these
eight (O1-O8, the operators E0 was designed for); the contention test
:mod:`aos.e0_contention` covers all twenty."""

STATES = ("random", "local_optimum")


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings of one E0 run."""

    sizes: tuple[int, ...]
    repeats: int
    block_seconds: float
    max_batches: int
    descents: int
    instance_seed: int


FULL = Settings(
    sizes=(100, 200, 300, 500, 783, 1000),
    repeats=5,
    block_seconds=0.5,
    max_batches=2000,
    descents=10,
    instance_seed=0,
)

QUICK = Settings(
    sizes=(100,),
    repeats=2,
    block_seconds=0.05,
    max_batches=200,
    descents=2,
    instance_seed=0,
)


def make_states(problem: Problem, seed: int) -> dict[str, Tour]:
    """Return the two measurement states: a random tour and its O7 descent."""
    order = list(range(problem.n))
    random.Random(f"e0-state:{problem.n}:{seed}").shuffle(order)
    random_tour = Tour.from_order(order, problem)
    optimum = random_tour.copy()
    descend(optimum, problem, Work())
    return {"random": random_tour, "local_optimum": optimum}


def time_block(
    problem: Problem,
    state: Tour,
    name: str,
    rng: random.Random,
    block_seconds: float,
    max_batches: int,
) -> np.ndarray:
    """Time batches of applications on fresh copies of ``state``.

    Copying is outside the timed region.  Stops after ``block_seconds`` of
    timed work or ``max_batches`` batches (at least one batch).

    Returns:
        Array of shape (batches, 5): applications, nanoseconds, lookups,
        positions moved, improving applications.
    """
    operator = OPERATORS[name]
    size = BATCH_SIZES[name]
    clock = time.perf_counter_ns
    budget = block_seconds * 1e9
    spent = 0
    rows = []
    while not rows or (spent < budget and len(rows) < max_batches):
        tour = state.copy()
        work = Work()
        improving = 0
        started = clock()
        for _ in range(size):
            if operator(tour, problem, rng, work).delta < 0:
                improving += 1
        elapsed = clock() - started
        spent += elapsed
        rows.append((size, elapsed, work.lookups, work.moved, improving))
    return np.asarray(rows, dtype=np.int64)


def measure_costs(
    problems: dict[int, Problem],
    states: dict[int, dict[str, Tour]],
    settings: Settings,
) -> tuple[list[dict[str, object]], dict[str, np.ndarray]]:
    """Step 1: cost per application, repeats interleaved over the sweep."""
    summary: list[dict[str, object]] = []
    raw: dict[str, np.ndarray] = {}
    for repeat in range(settings.repeats):
        LOGGER.info("cost sweep, repeat %d/%d", repeat + 1, settings.repeats)
        for n, problem in problems.items():
            for name in BATCH_SIZES:
                for state_name in STATES:
                    rng = random.Random(f"e0-ops:{n}:{name}:{state_name}:{repeat}")
                    rows = time_block(
                        problem,
                        states[n][state_name],
                        name,
                        rng,
                        settings.block_seconds,
                        settings.max_batches,
                    )
                    raw[f"{n}/{name}/{state_name}/{repeat}"] = rows
                    applications = rows[:, 0].sum()
                    summary.append(
                        {
                            "n": n,
                            "operator": name,
                            "state": state_name,
                            "repeat": repeat,
                            "batches": len(rows),
                            "applications": int(applications),
                            "ns_per_application": rows[:, 1].sum() / applications,
                            "lookups_per_application": rows[:, 2].sum() / applications,
                            "moved_per_application": rows[:, 3].sum() / applications,
                            "improving_fraction": rows[:, 4].sum() / applications,
                        }
                    )
    return summary, raw


def measure_descents(
    problems: dict[int, Problem], repeats: int
) -> list[dict[str, object]]:
    """Step 2: time full O7 descents from random tours."""
    rows: list[dict[str, object]] = []
    for n, problem in problems.items():
        for repeat in range(repeats):
            order = list(range(n))
            random.Random(f"e0-descent:{n}:{repeat}").shuffle(order)
            tour = Tour.from_order(order, problem)
            start_length = tour.length
            work = Work()
            started = time.perf_counter_ns()
            descend(tour, problem, work)
            elapsed = time.perf_counter_ns() - started
            rows.append(
                {
                    "n": n,
                    "repeat": repeat,
                    "seconds": elapsed / 1e9,
                    "start_length": start_length,
                    "end_length": tour.length,
                    "lookups": work.lookups,
                    "moved": work.moved,
                }
            )
        LOGGER.info("descents done for n=%d", n)
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> Path:
    """Write dictionaries with identical keys as CSV."""
    if not rows:
        raise ValueError(f"no rows to write to {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def run(settings: Settings, directory: Path) -> Path:
    """Run both E0 steps and write the results to ``directory``."""
    cores = physical_cores()
    pin_to(cores[0])
    write_config(
        directory,
        {
            "experiment": "E0",
            "settings": asdict(settings),
            "main_cpu": cores[0],
            "clock": clock_state(cores),
        },
    )
    instance_dir = directory / "instances"
    paths = {
        n: generate(Kind.UNIFORM, n, settings.instance_seed, instance_dir)
        for n in settings.sizes
    }
    problems = {n: Problem.from_tsplib(path) for n, path in paths.items()}
    states = {
        n: make_states(problem, settings.instance_seed)
        for n, problem in problems.items()
    }
    LOGGER.info("instances and states ready: n = %s", list(problems))
    summary, raw = measure_costs(problems, states, settings)
    write_csv(directory / "costs.csv", summary)
    write_arrays(directory / "costs.npz", raw)
    write_csv(directory / "descents.csv", measure_descents(problems, settings.descents))
    (directory / "clock_end.json").write_text(
        json.dumps(clock_state(cores), indent=2) + "\n"
    )
    LOGGER.info("E0 finished: %s", directory)
    return directory


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(prog="python -m aos.e0", description=__doc__)
    parser.add_argument("--quick", action="store_true", help="dry run in seconds")
    parser.add_argument("--out", type=Path, default=None, help="run directory")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = QUICK if arguments.quick else FULL
    directory = arguments.out or new_run_directory(
        "e0-quick" if arguments.quick else "e0"
    )
    directory.mkdir(parents=True, exist_ok=True)
    run(settings, directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
