"""E0c: do parallel timed workers change what measurement A measures?

E0's contention step (:mod:`aos.e0`, step 4) times three cheap operators
for a few seconds per worker count.  That cannot show slowdowns that only
the heavy operators suffer (shared cache and memory bandwidth) or that
build up over minutes (a laptop lowering its clock as it heats up).  This
test runs the measurement the main E1 run makes, all twenty operators
through :func:`aos.marginal.measure_marginal` on states of the main run,
for minutes per worker count.

Every worker repeats identical work: rounds of ``measure_marginal`` on the
chosen states, with the same anchors and random streams in every round, so
an operator's time per application can be compared across worker counts
and over time on exactly the same applications.  After each round the
worker records the average frequency its core ran at.  Phases run one
after another (default 1, 7, 1 workers); the closing single-worker phase
shows whether the machine itself drifted during the test.  Workers stop at
a shared deadline, so all of them are busy for the whole phase.

Rows go to ``runs/e0-contention/<timestamp>/rounds.csv``;
:mod:`aos.analysis.e0_contention` summarises them.

Example::

    uv run python -m aos.e0_contention \\
        --states runs/e1-main/<timestamp>/states/portgen-500-0_s0.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import multiprocessing
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .analysis import REPOSITORY_ROOT
from .cores import average_khz, clock_state, physical_cores, pin_to
from .marginal import MarginalSettings, clock_overhead_ns, measure_marginal
from .operators import OPERATORS
from .problem import Problem
from .records import new_run_directory, write_config
from .tour import Tour

LOGGER = logging.getLogger(__name__)

DEFAULT_STAGES = ("random", "opt2", "lkopt")
"""One early, one middle and one late state: the kicks run whole descents
on the random tour, the 2-opt family is cheap near a 2-opt optimum, and the
LK operators dominate near the end."""

COLUMNS: tuple[str, ...] = (
    "phase",
    "workers",
    "worker",
    "cpu",
    "round",
    "elapsed_s",
    "state",
    "operator",
    "applications",
    "mean_time_ns",
    "average_khz",
)


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings of one contention test.

    Attributes:
        states_file: An E1 states file (one instance, one state seed).
        stages: Names of the states to measure from that file.
        phases: Worker count of each phase, run in this order.
        seconds: Length of each phase.
        anchors: Anchors per state and round (a fixed, seeded sample).
        seed: Seed of the anchors and of the operators' random streams.
    """

    states_file: Path
    stages: tuple[str, ...]
    phases: tuple[int, ...]
    seconds: float
    anchors: int
    seed: int


def load_states(settings: Settings) -> tuple[Path, dict[str, list[int]]]:
    """Return the instance path and the chosen states' tours.

    Raises:
        KeyError: If a stage is not in the states file.
    """
    payload = json.loads(settings.states_file.read_text())
    tours = {state["name"]: state["order"] for state in payload["states"]}
    missing = [stage for stage in settings.stages if stage not in tours]
    if missing:
        raise KeyError(f"{settings.states_file} has no states {missing}")
    return REPOSITORY_ROOT / payload["path"], {
        stage: tours[stage] for stage in settings.stages
    }


def _worker(
    arguments: tuple[Settings, int, int, int, int, float],
) -> list[dict[str, Any]]:
    """Run in a child process: pin, then repeat identical rounds until the deadline."""
    settings, phase, workers, worker, cpu, deadline = arguments
    pin_to(cpu)
    path, orders = load_states(settings)
    problem = Problem.from_tsplib(path)
    if problem.alpha is None:
        raise RuntimeError(f"no cached alpha candidates for {problem.name}")
    states = {stage: Tour.from_order(order, problem) for stage, order in orders.items()}
    rng = random.Random(f"e0c-anchors:{settings.seed}")
    anchors = rng.sample(range(problem.n), min(settings.anchors, problem.n))
    names = list(OPERATORS)
    overhead = clock_overhead_ns()
    marginal = MarginalSettings(repeats=1, seed=settings.seed)
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    round_index = 0
    while time.time() < deadline:
        for stage, state in states.items():
            arrays = measure_marginal(
                problem, state, names, marginal, overhead, f"e0c:{stage}", anchors
            )
            khz = average_khz(cpu)
            elapsed = time.monotonic() - started
            rows.extend(
                {
                    "phase": phase,
                    "workers": workers,
                    "worker": worker,
                    "cpu": cpu,
                    "round": round_index,
                    "elapsed_s": elapsed,
                    "state": stage,
                    "operator": name,
                    "applications": len(arrays[name]["time_ns"]),
                    "mean_time_ns": float(arrays[name]["time_ns"].mean()),
                    "average_khz": khz,
                }
                for name in names
            )
        round_index += 1
    return rows


def run(settings: Settings, directory: Path) -> Path:
    """Run every phase and write ``rounds.csv`` to ``directory``.

    Workers use the first physical cores; the main process sits on the last
    one, which no worker uses as long as a phase leaves a core free.

    Raises:
        ValueError: If a phase asks for more workers than physical cores.
    """
    cores = physical_cores()
    if max(settings.phases) > len(cores):
        raise ValueError(f"{max(settings.phases)} workers but {len(cores)} cores")
    pin_to(cores[-1])
    load_states(settings)
    write_config(
        directory,
        {
            "experiment": "E0c",
            "settings": asdict(settings),
            "main_cpu": cores[-1],
            "clock": clock_state(cores),
        },
    )
    context = multiprocessing.get_context("spawn")
    output = directory / "rounds.csv"
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for phase, workers in enumerate(settings.phases):
            LOGGER.info(
                "phase %d: %d workers for %.0f s", phase, workers, settings.seconds
            )
            with context.Pool(workers) as pool:
                # Start the clock once every worker process exists.
                deadline = time.time() + settings.seconds + 5.0
                arguments = [
                    (settings, phase, workers, worker, cores[worker], deadline)
                    for worker in range(workers)
                ]
                results = pool.map(_worker, arguments)
            for rows in results:
                writer.writerows(rows)
            handle.flush()
            rounds = [
                max((row["round"] for row in rows), default=-1) + 1 for rows in results
            ]
            LOGGER.info("phase %d done: rounds per worker %s", phase, rounds)
    (directory / "clock_end.json").write_text(
        json.dumps(clock_state(cores), indent=2) + "\n"
    )
    LOGGER.info("E0c finished: %s", directory)
    return directory


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.e0_contention", description=__doc__
    )
    parser.add_argument("--states", type=Path, required=True, help="E1 states file")
    parser.add_argument("--stages", nargs="+", default=list(DEFAULT_STAGES))
    parser.add_argument(
        "--workers",
        nargs="+",
        type=int,
        default=[1, 7, 1],
        help="worker count per phase, in order",
    )
    parser.add_argument("--seconds", type=float, default=300.0, help="per phase")
    parser.add_argument("--anchors", type=int, default=16, help="per state and round")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None, help="run directory")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if min(arguments.workers) < 1:
        parser.error("--workers must be positive")
    settings = Settings(
        states_file=arguments.states,
        stages=tuple(arguments.stages),
        phases=tuple(arguments.workers),
        seconds=arguments.seconds,
        anchors=arguments.anchors,
        seed=arguments.seed,
    )
    directory = arguments.out or new_run_directory("e0-contention")
    directory.mkdir(parents=True, exist_ok=True)
    run(settings, directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
