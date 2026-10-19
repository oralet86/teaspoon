"""E1 measurement A: exact marginal rates of every operator in every state.

For each state of an earlier E1 run (``--states``), every operator is applied
at every anchor (all cities, or a seeded sample sized by a budget), each
application from the state tour (:mod:`aos.marginal`).  Two stages:

1. **gains** (untimed, every CPU): each operator's gain at each anchor,
   once for the deterministic operators and ``repeats`` times for the
   others, with the state's exact local-optimality flags;
2. **times** (one pinned core, nothing else running): rounds that replay
   every (operator, anchor) and time it, with the garbage collector off,
   until the deterministic operators' round means agree within ``precision``
   (5%, the limit of E0's repeatability gate; 2 to 8 rounds).  Every
   replay must reproduce its gain.

Parallel timed workers would slow the operators unevenly (E0, also at a
fixed clock), so only the timing is kept on one core.

Writes, under ``runs/e1-marginal/<timestamp>/``:

- ``gains/<instance>/s<seed>/<state>.npz`` and ``.json``: stage 1 (the
  stage resumes by skipping states already written);
- ``cells.csv``: one row per (instance, seed, state, operator) with the
  marginal rate and its 95% interval, mean gain and mean time per
  application, P(improve), the number of anchors at which the operator
  improves, and how the timing stopped;
- ``nodes/<instance>/s<seed>/<state>.npz``: per operator and anchor, mean
  gain and mean time (for the per-node analysis);
- ``samples/<instance>/s<seed>/<state>.npz``: every application of both
  stages (``<operator>.gain.<column>`` and ``<operator>.time.<column>``);
- ``clock_times.json``: the CPU frequency settings when timing started.

``aos.analysis.e1_marginal`` turns a run into journal data and figures.

Example::

    uv run python -m aos.e1_marginal --states runs/e1/20261016-181749
    uv run python -m aos.e1_marginal --states runs/e1/20261016-181749 --quick
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import multiprocessing
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import REPOSITORY_ROOT
from .cores import clock_state, physical_cores, pin_to
from .marginal import (
    MarginalSettings,
    TimingSettings,
    clock_overhead_ns,
    gain_repeats,
    measure_gains,
    measure_times,
    summarise_passes,
)
from .operators import DETERMINISTIC, FAMILIES, OPERATORS
from .problem import Problem
from .records import new_run_directory, read_arrays, write_arrays, write_config
from .tour import Tour
from .work import Work

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings of one measurement-A run.

    Attributes:
        repeats: Stage-1 applications per (operator, anchor) of the
            operators that are random once the anchor is fixed (the
            deterministic ones run once).
        seed: Seed of the operators' random streams.
        bootstrap: Bootstrap resamples per interval.
        instances: Instance names to include (all when empty).
        seeds: State seeds to include (all when empty).
        stages: State names to include (all when empty).
        state_seconds: Target timing time per state (stage 2).  A short,
            untimed pilot estimates every operator's cost, and the number of
            anchors is set so that ``min_rounds`` rounds fit; all cities when
            that is affordable.  Also caps stage 2 once ``min_rounds`` are
            done.  ``None``: always all cities, no cap.
        min_anchors: Fewest anchors per state when the budget is tight.
        early_max_n: States in :data:`EARLY_STATES` are skipped on instances
            with more cities than this (kicks and descents there run over
            the whole tour and cost minutes per state even at
            ``min_anchors``).
        min_rounds: Timed rounds always made per state.
        max_rounds: Timed rounds at most.
        precision: Timing stops once every operator's mean time is known to
            within this fraction (95% interval over round means).
    """

    repeats: int
    seed: int
    bootstrap: int
    instances: tuple[str, ...]
    seeds: tuple[int, ...]
    stages: tuple[str, ...]
    state_seconds: float | None = None
    min_anchors: int = 32
    early_max_n: int | None = None
    min_rounds: int = 2
    max_rounds: int = 8
    precision: float = 0.05

    @property
    def marginal(self) -> MarginalSettings:
        """The settings both stages share."""
        return MarginalSettings(repeats=self.repeats, seed=self.seed)

    @property
    def timing(self) -> TimingSettings:
        """When stage 2 stops."""
        return TimingSettings(
            min_rounds=self.min_rounds,
            max_rounds=self.max_rounds,
            precision=self.precision,
            seconds=self.state_seconds,
        )


EARLY_STATES = frozenset({"random", "nearest_neighbour"})
"""States with many defects, skipped on large instances (see ``early_max_n``)."""

PILOT_ANCHORS = 6
"""Anchors per operator in the pilot that sizes a state."""

FULL = Settings(repeats=8, seed=0, bootstrap=1000, instances=(), seeds=(), stages=())

BUDGETED = Settings(
    repeats=8,
    seed=0,
    bootstrap=1000,
    instances=(),
    seeds=(),
    stages=(),
    state_seconds=3.0,
    min_anchors=32,
    early_max_n=1000,
)
"""Budgeted run: about 3 s of timing per state by default (the E1 main run
used ``--state-seconds 7.5 --repeats 32``)."""

QUICK = Settings(
    repeats=2,
    seed=0,
    bootstrap=200,
    instances=("kroA100",),
    seeds=(0,),
    stages=("random", "opt2", "lkopt"),
)


@dataclass(frozen=True, slots=True)
class StateTask:
    """One state to measure.

    Attributes:
        path: Instance path relative to the repository root.
        instance: Instance name.
        seed: State seed.
        state: The state record from the E1 states file.
    """

    path: str
    instance: str
    seed: int
    state: dict[str, Any]

    @property
    def label(self) -> str:
        """Identifier used in file names and random streams."""
        return f"{self.instance}:s{self.seed}:{self.state['name']}"


def load_state_tasks(
    states_dir: Path,
    instances: tuple[str, ...] = (),
    seeds: tuple[int, ...] = (),
    stages: tuple[str, ...] = (),
) -> list[StateTask]:
    """Read the states of an E1 run, skipping duplicates and filtered ones.

    Raises:
        FileNotFoundError: If the directory holds no states files.
    """
    files = sorted(states_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no states files in {states_dir}")
    tasks = []
    for state_file in files:
        payload = json.loads(state_file.read_text())
        seed = int(payload["state_seed"])
        if instances and payload["instance"] not in instances:
            continue
        if seeds and seed not in seeds:
            continue
        for state in payload["states"]:
            if state.get("duplicate_of"):
                continue
            if stages and state["name"] not in stages:
                continue
            tasks.append(StateTask(payload["path"], payload["instance"], seed, state))
    return tasks


def class_flags(problem: Problem, state: Tour) -> dict[str, bool]:
    """Exact local-optimality flags of a state, untimed.

    Whether some anchor improves under 2-opt (O7, else the full scan O5),
    neighbour-list Or-opt (O9), neighbour-list 3-opt (O10) and the LK step
    (O18 with alpha candidates if cached, else O16).  Each check
    stops at the first improving anchor, so open states are cheap.
    """
    rng = random.Random(0)
    flags = {}
    for flag, names in (
        ("improves_2opt", ("two_opt_nl", "two_opt_first")),
        ("improves_oropt", ("or_opt_nl",)),
        ("improves_3opt", ("three_opt_nl",)),
        (
            "improves_lk",
            ("lin_kernighan_alpha" if problem.alpha is not None else "lin_kernighan",),
        ),
    ):
        # A failed check leaves the tour unchanged and the first success
        # ends the check, so one scratch copy per flag is enough.
        scratch = state.copy()
        flags[flag] = any(
            OPERATORS[name](scratch, problem, rng, Work(), city)[0] < 0
            for name in names
            for city in range(problem.n)
        )
    return flags


def choose_anchors(
    problem: Problem, state: Tour, names: list[str], settings: Settings, label: str
) -> list[int]:
    """Return the anchors of a state: all cities, or a seeded random sample
    sized by an untimed pilot so that ``min_rounds`` timed rounds fit in
    ``settings.state_seconds``.

    The pilot runs in stage 1, possibly beside other workers, so its costs
    are if anything too high and the anchor count errs on the small side.
    """
    n = problem.n
    if settings.state_seconds is None:
        return list(range(n))
    rng = random.Random(f"anchors:{label}:{settings.seed}")
    pilot = rng.sample(range(n), min(PILOT_ANCHORS, n))
    pilot_arrays = measure_gains(
        problem,
        state,
        names,
        MarginalSettings(repeats=1, seed=settings.seed + 1),
        f"pilot:{label}",
        pilot,
    )
    per_anchor_ns = sum(
        float(arrays["wall_ns"].mean()) for arrays in pilot_arrays.values()
    )
    affordable = int(
        settings.state_seconds * 1e9 / (settings.min_rounds * per_anchor_ns)
    )
    count = min(n, max(settings.min_anchors, affordable))
    return list(range(n)) if count >= n else rng.sample(range(n), count)


def state_path(directory: Path, kind: str, task: StateTask, suffix: str) -> Path:
    """Return ``directory/kind/<instance>/s<seed>/<state><suffix>``."""
    return (
        directory / kind / task.instance / f"s{task.seed}" / f"{task.state['name']}"
        f"{suffix}"
    )


def gain_state(arguments: tuple[StateTask, Settings, Path]) -> str:
    """Stage-1 worker: flags, anchors and gains of one state, written to
    ``gains/``.  The arrays are written last, under a temporary name that is
    renamed when complete, so their presence marks a finished state."""
    task, settings, directory = arguments
    started = time.perf_counter()
    problem = Problem.from_tsplib(REPOSITORY_ROOT / task.path)
    state = Tour.from_order(task.state["order"], problem)
    names = list(OPERATORS)
    flags = class_flags(problem, state)
    anchors = choose_anchors(problem, state, names, settings, task.label)
    gains = measure_gains(problem, state, names, settings.marginal, task.label, anchors)
    meta = {
        "anchors": sorted(anchors),
        "sampled": len(anchors) < problem.n,
        "flags": flags,
        "seconds": time.perf_counter() - started,
    }
    final = state_path(directory, "gains", task, ".npz")
    final.parent.mkdir(parents=True, exist_ok=True)
    state_path(directory, "gains", task, ".json").write_text(json.dumps(meta) + "\n")
    partial = state_path(directory, "gains", task, ".partial.npz")
    write_arrays(
        partial,
        {
            f"{name}.{column}": values
            for name, columns in gains.items()
            for column, values in columns.items()
        },
    )
    partial.rename(final)
    LOGGER.info("gains %s: %.1f s", task.label, meta["seconds"])
    return task.label


def split_columns(
    data: dict[str, np.ndarray], names: list[str], prefix: str = ""
) -> dict[str, dict[str, np.ndarray]]:
    """Regroup ``{"<prefix><operator>.<column>": array}`` by operator."""
    grouped: dict[str, dict[str, np.ndarray]] = {name: {} for name in names}
    for key, values in data.items():
        if not key.startswith(prefix):
            continue
        name, column = key[len(prefix) :].split(".", 1)
        if name in grouped and "." not in column:
            grouped[name][column] = values
    return grouped


def per_anchor_means(arrays: dict[str, np.ndarray], column: str, n: int) -> np.ndarray:
    """Mean of ``column`` per anchor, as a length-``n`` vector (0 elsewhere)."""
    totals = np.bincount(
        arrays["anchor"], weights=arrays[column].astype(float), minlength=n
    )
    counts = np.bincount(arrays["anchor"], minlength=n)
    return np.divide(totals, counts, out=np.zeros(n), where=counts > 0)


def time_state(
    task: StateTask, settings: Settings, directory: Path
) -> list[dict[str, Any]]:
    """Stage 2 for one state, on the calling (pinned) process: time the
    replays, write ``samples/`` and ``nodes/``, and return the cell rows."""
    problem = Problem.from_tsplib(REPOSITORY_ROOT / task.path)
    state = Tour.from_order(task.state["order"], problem)
    names = list(OPERATORS)
    meta = json.loads(state_path(directory, "gains", task, ".json").read_text())
    gains = split_columns(
        read_arrays(state_path(directory, "gains", task, ".npz")), names
    )
    overhead = clock_overhead_ns()
    n = problem.n
    started = time.perf_counter()
    times, reason = measure_times(
        problem, state, gains, settings.marginal, settings.timing, overhead, task.label
    )
    seconds = time.perf_counter() - started
    relative = Path(task.instance) / f"s{task.seed}" / f"{task.state['name']}.npz"
    write_arrays(
        directory / "samples" / relative,
        {
            f"{name}.{kind}.{column}": values
            for kind, passes in (("gain", gains), ("time", times))
            for name, columns in passes.items()
            for column, values in columns.items()
        },
    )
    write_arrays(
        directory / "nodes" / relative,
        {
            "operators": np.asarray(names),
            "gain": np.stack(
                [per_anchor_means(gains[name], "gain", n) for name in names]
            ),
            "time_ns": np.stack(
                [per_anchor_means(times[name], "time_ns", n) for name in names]
            ),
            "order": np.asarray(task.state["order"]),
            "anchors": np.asarray(meta["anchors"]),
        },
    )
    boot_rng = np.random.default_rng(settings.seed)
    rounds = int(times[names[0]]["round"].max()) + 1
    rows = []
    for name in names:
        summary = summarise_passes(
            gains[name], times[name], n, settings.bootstrap, boot_rng, meta["sampled"]
        )
        rows.append(
            {
                "instance": task.instance,
                "n": n,
                "seed": task.seed,
                "state": task.state["name"],
                "gap": task.state["gap"],
                "operator": name,
                "family": FAMILIES[name],
                "deterministic": name in DETERMINISTIC,
                "applications": len(gains[name]["gain"]),
                "rate": summary.rate,
                "rate_low": summary.low,
                "rate_high": summary.high,
                "mean_gain": summary.mean_gain,
                "mean_time_ns": summary.mean_time_ns,
                "p_improve": summary.p_improve,
                "improving_anchors": summary.improving_anchors,
                "state_seconds": seconds,
                "anchors": len(meta["anchors"]),
                "sampled": meta["sampled"],
                **meta["flags"],
                "overhead_ns": overhead,
                "cpu": next(iter(os.sched_getaffinity(0))),
                "gain_repeats": gain_repeats(name, settings.repeats),
                "gain_applications": len(gains[name]["gain"]),
                "timed_applications": len(times[name]["time_ns"]),
                "timed_rounds": rounds,
                "stop_reason": reason,
                "gain_seconds": meta["seconds"],
            }
        )
    LOGGER.info("times %s: %d rounds (%s), %.1f s", task.label, rounds, reason, seconds)
    return rows


def select_tasks(settings: Settings, states_dir: Path) -> list[StateTask]:
    """The states to measure, early states on large instances dropped when
    ``early_max_n`` says so, expensive states first (large n; random and
    nearest-neighbour tours make the kicks run whole descents)."""
    tasks = load_state_tasks(
        states_dir, settings.instances, settings.seeds, settings.stages
    )
    if settings.early_max_n is not None:
        limit = settings.early_max_n
        kept = [
            task
            for task in tasks
            if not (
                len(task.state["order"]) > limit and task.state["name"] in EARLY_STATES
            )
        ]
        if len(kept) < len(tasks):
            LOGGER.info(
                "skipping %d early states on large instances", len(tasks) - len(kept)
            )
        tasks = kept
    tasks.sort(
        key=lambda task: (
            -len(task.state["order"]),
            task.state["name"] not in EARLY_STATES,
            task.instance,
            task.seed,
        )
    )
    return tasks


def run_gains(
    settings: Settings, tasks: list[StateTask], directory: Path, workers: int
) -> None:
    """Stage 1: gains of every state not yet done, on ``workers`` processes."""
    todo = [
        task
        for task in tasks
        if not state_path(directory, "gains", task, ".npz").is_file()
    ]
    LOGGER.info(
        "stage 1 (gains): %d states to do, %d done, %d workers",
        len(todo),
        len(tasks) - len(todo),
        workers,
    )
    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, maxtasksperchild=1) as pool:
        for done, _ in enumerate(
            pool.imap_unordered(
                gain_state,
                [(task, settings, directory) for task in todo],
                chunksize=1,
            ),
            start=1,
        ):
            if done % 50 == 0 or done == len(todo):
                LOGGER.info("gains progress: %d/%d states", done, len(todo))


def run_times(settings: Settings, tasks: list[StateTask], directory: Path) -> Path:
    """Stage 2: time every state on one pinned core; write ``cells.csv``.

    Raises:
        FileNotFoundError: If a state has no stage-1 result.
    """
    missing = [
        task.label
        for task in tasks
        if not state_path(directory, "gains", task, ".npz").is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} states have no gains yet (first: {missing[0]}); "
            "run --stage gains"
        )
    cpu = physical_cores()[0]
    pin_to(cpu)
    (directory / "clock_times.json").write_text(
        json.dumps(clock_state([cpu]), indent=2) + "\n"
    )
    LOGGER.info("stage 2 (times): %d states on cpu %d", len(tasks), cpu)
    partial = directory / "cells.partial.csv"
    with partial.open("w", newline="") as handle:
        writer: csv.DictWriter[str] | None = None
        for done, task in enumerate(tasks, start=1):
            rows = time_state(task, settings, directory)
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            if done % 50 == 0 or done == len(tasks):
                LOGGER.info("times progress: %d/%d states", done, len(tasks))
    path = directory / "cells.csv"
    partial.rename(path)
    LOGGER.info("finished: %s", path)
    return path


def resummarise(directory: Path, bootstrap: int, seed: int) -> Path:
    """Recompute the rates and intervals of ``cells.csv`` from the stored
    samples (after a change to :func:`aos.marginal.summarise_passes`).  The
    previous file is kept as ``cells.previous.csv``.
    """
    path = directory / "cells.csv"
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    boot_rng = np.random.default_rng(seed)
    cache: dict[tuple[str, str, str], dict[str, np.ndarray]] = {}
    for row in rows:
        key = (row["instance"], row["seed"], row["state"])
        if key not in cache:
            cache.clear()
            cache[key] = read_arrays(
                directory / "samples" / key[0] / f"s{key[1]}" / f"{key[2]}.npz"
            )
        data = cache[key]
        name = row["operator"]
        summary = summarise_passes(
            {
                column: data[f"{name}.gain.{column}"]
                for column in ("anchor", "repeat", "gain")
            },
            {
                column: data[f"{name}.time.{column}"]
                for column in ("anchor", "round", "time_ns")
            },
            int(row["n"]),
            bootstrap,
            boot_rng,
            row.get("sampled") == "True",
        )
        row["rate"] = str(summary.rate)
        row["rate_low"] = str(summary.low)
        row["rate_high"] = str(summary.high)
    path.rename(directory / "cells.previous.csv")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    LOGGER.info("recomputed %d cells: %s", len(rows), path)
    return path


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.e1_marginal", description=__doc__
    )
    parser.add_argument(
        "--states", type=Path, default=None, help="E1 run directory with states/"
    )
    parser.add_argument(
        "--resummarise",
        type=Path,
        default=None,
        help="recompute cells.csv of this run from its samples, then exit",
    )
    parser.add_argument("--quick", action="store_true", help="dry run")
    parser.add_argument(
        "--budgeted",
        action="store_true",
        help="time budget per state, anchor sampling, skip early states on "
        "large instances (instance-set runs)",
    )
    parser.add_argument(
        "--state-seconds", type=float, default=None, help="timing budget per state"
    )
    parser.add_argument(
        "--stage",
        choices=("gains", "times", "both"),
        default="both",
        help="stage 1 (gains, every CPU), stage 2 (times, one core), or both "
        "one after the other",
    )
    parser.add_argument(
        "--gain-workers",
        type=int,
        default=None,
        help="stage-1 processes (default: every CPU this process may use)",
    )
    parser.add_argument("--out", type=Path, default=None, help="run directory")
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--min-rounds", type=int, default=None)
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--precision", type=float, default=None)
    parser.add_argument("--instances", nargs="*", default=None)
    parser.add_argument("--seeds", nargs="*", type=int, default=None)
    parser.add_argument("--stages", nargs="*", default=None)
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = QUICK if arguments.quick else BUDGETED if arguments.budgeted else FULL
    if arguments.resummarise is not None:
        resummarise(arguments.resummarise, settings.bootstrap, settings.seed)
        return 0
    if arguments.states is None:
        parser.error("--states is required unless --resummarise is given")
    overrides: dict[str, Any] = {}
    for name in ("repeats", "state_seconds", "min_rounds", "max_rounds", "precision"):
        value = getattr(arguments, name)
        if value is not None:
            overrides[name] = value
    for name in ("instances", "seeds", "stages"):
        value = getattr(arguments, name)
        if value is not None:
            overrides[name] = tuple(value)
    settings = Settings(**{**asdict(settings), **overrides})
    gain_workers = arguments.gain_workers or len(os.sched_getaffinity(0))
    if gain_workers < 1:
        parser.error("--gain-workers must be positive")
    if arguments.out is None:
        directory = new_run_directory(
            "e1-marginal-quick" if arguments.quick else "e1-marginal"
        )
    else:
        directory = arguments.out
        directory.mkdir(parents=True, exist_ok=True)
    config = {
        "experiment": "E1 measurement A (marginal rates, two stages)",
        "settings": asdict(settings),
        "states": str(arguments.states),
    }
    existing = directory / "config.json"
    if existing.is_file():
        previous = json.loads(existing.read_text())["settings"]
        if previous != json.loads(json.dumps(config, default=str)):
            parser.error(
                f"{directory} was started with other settings; both stages "
                "must use the same ones (see its config.json)"
            )
    else:
        write_config(directory, config)
    tasks = select_tasks(settings, arguments.states / "states")
    if arguments.stage in ("gains", "both"):
        run_gains(settings, tasks, directory, gain_workers)
    if arguments.stage in ("times", "both"):
        run_times(settings, tasks, directory)
    LOGGER.info("run directory: %s", directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
