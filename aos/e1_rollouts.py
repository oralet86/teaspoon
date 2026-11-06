"""E1R: does the greedy E1 choice pay off later?  Rollouts for Claim 1.4.

For a sample of E1 states s, every candidate first operator o (the E1
leader G(s), the continuation policy's choice, and the next best operators
by E1 rate) gets ``repeats`` rollouts (:mod:`aos.rollouts`): one slice of o,
then greedy by level, on a virtual clock, up to a horizon.  The analysis
(:mod:`aos.analysis.e1_rollouts`) compares the tour lengths at several
horizons, paired by repeat.

Four stages, like E1's split of untimed and timed work:

1. ``plan`` (seconds): chooses states and candidates, builds the
   greedy-by-level map from the E1 leaders, and fits a provisional virtual
   clock per instance and operator on E1's single-core timings (seeds
   ``fit_seeds``), checked on a held-out seed (``cost_check.csv``);
2. ``calibrate`` (one pinned core, garbage collector off): one short timed
   rollout per (state, candidate); the clock is refitted on the time of
   every application in them (``cost_models.json``, ``calibration.csv``).
   E1 timed applications one at a time inside its measurement loop, which
   made cheap operators look slower, relative to the kicks, than they are
   when they run back to back in a rollout;
3. ``run`` (untimed, every CPU, resumable): the rollouts, one task per
   (state, candidate), written atomically under ``results/``;
4. ``validate`` (one pinned core, garbage collector off): replays some
   rollouts with real timing of every application and checks that each one
   reproduces its stored result; writes ``validate.csv``.

Example::

    uv run python -m aos.e1_rollouts --e1-run runs/e1-main/20261018-144422 \
        --stage plan --out runs/e1-rollouts/test
    uv run python -m aos.e1_rollouts --out runs/e1-rollouts/test --stage calibrate
    uv run python -m aos.e1_rollouts --out runs/e1-rollouts/test --stage run
    uv run python -m aos.e1_rollouts --out runs/e1-rollouts/test --stage validate
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import logging
import multiprocessing
import os
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import REPOSITORY_ROOT
from .cores import clock_state, physical_cores, pin_to
from .marginal import clock_overhead_ns
from .problem import Problem
from .records import read_arrays, write_arrays, write_config
from .rollouts import (
    CLASSES,
    OPERATOR_NAMES,
    CostModel,
    GreedyByLevel,
    RolloutResult,
    fit_coefficients,
    fit_sums,
    rollout,
)

LOGGER = logging.getLogger(__name__)

SIZES = (100, 200, 500)
"""Size groups of the E1 main run; an instance belongs to the nearest."""

STATES = (
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
"""E1 state names."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings of one E1R run.

    Attributes:
        e1_run: The E1 run directory (``states/`` and ``marginal/``).
        leaders: E1 leaders table with the exact class per state.
        instances_per_cell: Instances drawn per (family, size) cell.
        states_per_class: States drawn per instance and class (fewer if the
            instance has fewer states with an improvement in that class).
        candidates: First operators per state.
        repeats: Rollouts per (state, candidate), paired by repeat.
        slice_ns_per_city: Slice length per city (the slice grows with n).
        horizon_slices: Horizon in slices, first slice included.
        fit_seeds: E1 state seeds whose timings fit the virtual clock.
        check_seed: E1 state seed on which the fit is checked.
        seed: Seed of the plan's random draws and of the rollouts' streams.
        validate_rollouts: Rollouts replayed with real timing.
        calibration_slices: Horizon of the calibration rollouts, in slices.
    """

    e1_run: str
    leaders: str
    instances_per_cell: int = 2
    states_per_class: int = 2
    candidates: int = 5
    repeats: int = 32
    slice_ns_per_city: float = 100_000.0
    horizon_slices: int = 64
    fit_seeds: tuple[int, ...] = (0, 1, 2)
    check_seed: int = 3
    seed: int = 0
    validate_rollouts: int = 24
    calibration_slices: int = 16


def size_group(n: int) -> int:
    """The size group nearest to ``n``."""
    return min(SIZES, key=lambda size: abs(size - n))


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into a list of rows."""
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def mode(names: list[str]) -> str:
    """The most frequent name; ties go to the earlier operator (O-number)."""
    counts = Counter(names)
    return min(counts, key=lambda name: (-counts[name], OPERATOR_NAMES.index(name)))


def greedy_map(
    leaders: list[dict[str, str]], instance: str, family_of: dict[str, str]
) -> list[str]:
    """Greedy-by-level leaders of one instance: per class, the operator that
    leads most E1 states of that class on the instance; if the instance has
    none, on its (family, size) cell, then its family, then all instances.
    States in which nothing improves are left out.

    Raises:
        ValueError: If some class has no leader anywhere.
    """
    improving = [row for row in leaders if row["any_improvement"] == "True"]
    n_of = {row["instance"]: int(row["n"]) for row in leaders}
    family = family_of[instance]
    size = size_group(n_of[instance])
    scopes = (
        lambda row: row["instance"] == instance,
        lambda row: (
            family_of[row["instance"]] == family
            and size_group(n_of[row["instance"]]) == size
        ),
        lambda row: family_of[row["instance"]] == family,
        lambda _row: True,
    )
    result = []
    for label in CLASSES:
        in_class = [row for row in improving if row["class"] == label]
        for scope in scopes:
            names = [row["leader"] for row in in_class if scope(row)]
            if names:
                result.append(mode(names))
                break
        else:
            raise ValueError(f"no E1 leader for class {label!r}")
    return result


def load_samples(
    samples: Path, instance: str, seeds: tuple[int, ...]
) -> dict[str, tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray]]]:
    """Per operator, the counted work and measured time of every timed E1
    application of ``instance`` on the given state seeds.

    Pass 2 replays repeat ``r mod repeats`` of an anchor (``aos.marginal``),
    so each timed row is joined to its pass-1 row by (anchor, repeat).

    Returns:
        ``{operator: (lookups, moved, times)}``, one array per state.
    """
    result: dict[str, tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray]]]
    result = defaultdict(lambda: ([], [], []))
    for seed in seeds:
        for state in STATES:
            path = samples / instance / f"s{seed}" / f"{state}.npz"
            if not path.is_file():
                continue  # duplicate states were not measured
            arrays = read_arrays(path)
            for name in OPERATOR_NAMES:
                anchors = arrays[f"{name}.gain.anchor"]
                repeats = arrays[f"{name}.gain.repeat"]
                count = int(repeats.max()) + 1
                index = {
                    int(key): row
                    for row, key in enumerate(
                        anchors.astype(np.int64) * count + repeats
                    )
                }
                rows = np.asarray(
                    [
                        index[int(anchor) * count + int(repeat) % count]
                        for anchor, repeat in zip(
                            arrays[f"{name}.time.anchor"],
                            arrays[f"{name}.time.repeat"],
                            strict=True,
                        )
                    ],
                    dtype=np.int64,
                )
                lookups, moved, times = result[name]
                lookups.append(arrays[f"{name}.gain.lookups"][rows])
                moved.append(arrays[f"{name}.gain.moved"][rows])
                times.append(arrays[f"{name}.time.time_ns"].astype(float))
    return result


def fit_cost_model(
    samples: Path, instance: str, settings: Settings
) -> tuple[CostModel, list[dict[str, Any]]]:
    """Fit the virtual clock of one instance and check it on the held-out
    seed: per operator and held-out state, predicted against measured mean
    time per application.

    Raises:
        ValueError: If an operator has no timings to fit.
    """
    fit = load_samples(samples, instance, settings.fit_seeds)
    check = load_samples(samples, instance, (settings.check_seed,))
    coefficients = {}
    rows = []
    for name in OPERATOR_NAMES:
        if name not in fit:
            raise ValueError(f"{instance}: no E1 timings of {name}")
        lookups, moved, times = (np.concatenate(part) for part in fit[name])
        coefficients[name] = fit_coefficients(lookups, moved, times)
        model = CostModel(instance, {name: coefficients[name]})
        errors = [
            float(
                np.mean(
                    [
                        model.cost_ns(name, int(a), int(b))
                        for a, b in zip(state_lookups, state_moved, strict=True)
                    ]
                )
                / state_times.mean()
                - 1.0
            )
            for state_lookups, state_moved, state_times in zip(
                *check.get(name, ([], [], [])), strict=True
            )
        ]
        rows.append(
            {
                "instance": instance,
                "operator": name,
                "intercept_ns": coefficients[name].intercept,
                "per_lookup_ns": coefficients[name].per_lookup,
                "per_moved_ns": coefficients[name].per_moved,
                "fit_applications": len(times),
                "check_states": len(errors),
                "check_median_abs_error": float(np.median(np.abs(errors)))
                if errors
                else None,
                "check_max_abs_error": float(np.max(np.abs(errors)))
                if errors
                else None,
            }
        )
    return CostModel(instance, coefficients), rows


def plan(settings: Settings, directory: Path) -> Path:
    """Stage 1: choose states and candidates, build the greedy map, fit the
    provisional virtual clock; write ``plan.json``, ``cost_models_e1.json``
    and ``cost_check.csv``."""
    from .analysis.e1 import CLASSES as E1_CLASSES
    from .analysis.e1_marginal import instance_family

    if tuple(E1_CLASSES) != CLASSES:
        raise ValueError(f"class labels differ from E1's: {E1_CLASSES}")
    e1_run = REPOSITORY_ROOT / settings.e1_run
    leaders = read_csv(REPOSITORY_ROOT / settings.leaders)
    family_of = {row["instance"]: instance_family(row["instance"]) for row in leaders}
    n_of = {row["instance"]: int(row["n"]) for row in leaders}
    rng = random.Random(f"e1r-plan:{settings.seed}")

    cells: dict[tuple[str, int], list[str]] = defaultdict(list)
    for instance in sorted(n_of):
        cells[(family_of[instance], size_group(n_of[instance]))].append(instance)
    chosen = sorted(
        instance
        for key in sorted(cells)
        for instance in rng.sample(
            cells[key], min(settings.instances_per_cell, len(cells[key]))
        )
    )

    rates: dict[tuple[str, int, str], dict[str, float]] = defaultdict(dict)
    for row in read_csv(e1_run / "marginal" / "cells.csv"):
        if row["instance"] in chosen:
            key = (row["instance"], int(row["seed"]), row["state"])
            rates[key][row["operator"]] = float(row["rate"])

    paths = {}
    for states_file in sorted((e1_run / "states").glob("*.json")):
        payload = json.loads(states_file.read_text())
        paths[payload["instance"]] = payload["path"]

    states = []
    maps = {}
    for instance in chosen:
        maps[instance] = greedy_map(leaders, instance, family_of)
        for class_index, label in enumerate(CLASSES):
            pool = sorted(
                (
                    row
                    for row in leaders
                    if row["instance"] == instance
                    and row["class"] == label
                    and row["any_improvement"] == "True"
                ),
                key=lambda row: (int(row["seed"]), STATES.index(row["state"])),
            )
            for row in rng.sample(pool, min(settings.states_per_class, len(pool))):
                seed = int(row["seed"])
                state_rates = rates[(instance, seed, row["state"])]
                greedy = row["leader"]
                if state_rates[greedy] < max(state_rates.values()):
                    raise ValueError(
                        f"{instance} s{seed} {row['state']}: leaders table and "
                        "cells disagree on the leader"
                    )
                candidates = [greedy]
                policy_choice = maps[instance][class_index]
                if policy_choice != greedy and state_rates[policy_choice] > 0:
                    candidates.append(policy_choice)
                for name in sorted(
                    state_rates, key=lambda name: (-state_rates[name], name)
                ):
                    if len(candidates) >= settings.candidates:
                        break
                    if name not in candidates and state_rates[name] > 0:
                        candidates.append(name)
                states.append(
                    {
                        "instance": instance,
                        "path": paths[instance],
                        "family": family_of[instance],
                        "n": n_of[instance],
                        "seed": seed,
                        "state": row["state"],
                        "class": label,
                        "greedy": greedy,
                        "candidates": candidates,
                        "rates": {name: state_rates[name] for name in candidates},
                    }
                )

    samples = e1_run / "marginal" / "samples"
    models = {}
    check_rows: list[dict[str, Any]] = []
    for instance in chosen:
        LOGGER.info("fitting the virtual clock of %s", instance)
        model, rows = fit_cost_model(samples, instance, settings)
        models[instance] = model.to_json()
        check_rows.extend(rows)
    (directory / "cost_models_e1.json").write_text(json.dumps(models, indent=1) + "\n")
    with (directory / "cost_check.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(check_rows[0]))
        writer.writeheader()
        writer.writerows(check_rows)

    payload = {"instances": chosen, "maps": maps, "states": states}
    path = directory / "plan.json"
    path.write_text(json.dumps(payload, indent=1) + "\n")
    rollouts = sum(len(state["candidates"]) for state in states) * settings.repeats
    virtual = sum(
        len(state["candidates"])
        * settings.repeats
        * settings.horizon_slices
        * settings.slice_ns_per_city
        * state["n"]
        for state in states
    )
    LOGGER.info(
        "plan: %d instances, %d states, %d rollouts, at most %.1f virtual core-hours",
        len(chosen),
        len(states),
        rollouts,
        virtual / 3.6e12,
    )
    return path


@dataclass(frozen=True, slots=True)
class Task:
    """One (state, candidate) pair: ``repeats`` rollouts."""

    state: dict[str, Any]
    candidate: str

    @property
    def label(self) -> str:
        """Identifier of the state, used in random streams."""
        return f"{self.state['instance']}:s{self.state['seed']}:{self.state['state']}"


def result_path(directory: Path, task: Task) -> Path:
    """``results/<instance>/s<seed>/<state>/<candidate>.npz``."""
    state = task.state
    return (
        directory
        / "results"
        / state["instance"]
        / f"s{state['seed']}"
        / state["state"]
        / f"{task.candidate}.npz"
    )


_PROBLEMS: dict[str, Problem] = {}
_ORDERS: dict[tuple[str, int, str], list[int]] = {}


def _problem(path: str) -> Problem:
    """Load an instance once per worker process."""
    if path not in _PROBLEMS:
        _PROBLEMS.clear()  # tasks are ordered by instance; keep one
        _PROBLEMS[path] = Problem.from_tsplib(REPOSITORY_ROOT / path)
    return _PROBLEMS[path]


def _order(e1_run: Path, state: dict[str, Any]) -> list[int]:
    """The tour of an E1 state."""
    key = (state["instance"], state["seed"], state["state"])
    if key not in _ORDERS:
        payload = json.loads(
            (
                e1_run / "states" / f"{state['instance']}_s{state['seed']}.json"
            ).read_text()
        )
        for record in payload["states"]:
            _ORDERS[(state["instance"], state["seed"], record["name"])] = record[
                "order"
            ]
    return _ORDERS[key]


def run_task(
    task: Task,
    settings: Settings,
    model: CostModel,
    leaders: list[str],
    repeats: range,
    timed: bool = False,
    overhead_ns: float = 0.0,
) -> dict[str, np.ndarray]:
    """Run the rollouts of one (state, candidate) for the given repeats.

    Returns:
        Concatenated per-event, per-slice and per-repeat arrays.
    """
    state = task.state
    problem = _problem(state["path"])
    order = _order(REPOSITORY_ROOT / settings.e1_run, state)
    initial = problem.tour_length(order)
    slice_ns = settings.slice_ns_per_city * problem.n
    horizon_ns = slice_ns * settings.horizon_slices
    columns: dict[str, list[Any]] = defaultdict(list)
    timing: dict[str, float] = defaultdict(float)
    for repeat in repeats:
        result = rollout(
            problem,
            order,
            task.candidate,
            GreedyByLevel(leaders),
            model,
            slice_ns,
            horizon_ns,
            random.Random(f"e1r:first:{settings.seed}:{task.label}:{repeat}"),
            random.Random(f"e1r:continue:{settings.seed}:{task.label}:{repeat}"),
            timed=timed,
            overhead_ns=overhead_ns,
        )
        columns["event_repeat"] += [repeat] * len(result.event_ns)
        columns["event_ns"] += result.event_ns
        columns["event_length"] += result.event_length
        columns["slice_repeat"] += [repeat] * len(result.slice_start_ns)
        columns["slice_start_ns"] += result.slice_start_ns
        columns["slice_operator"] += result.slice_operator
        columns["slice_class"] += result.slice_class
        columns["slice_applications"] += result.slice_applications
        columns["repeat"].append(repeat)
        columns["end_ns"].append(result.end_ns)
        columns["final_length"].append(result.final_length)
        columns["reached_optimum"].append(result.reached_optimum)
        columns["real_ns"].append(result.real_ns)
        for name, real in result.real_by_operator.items():
            timing[f"real_by_operator.{name}"] += real
            timing[f"virtual_by_operator.{name}"] += result.virtual_by_operator[name]
    arrays = {
        name: np.asarray(values, dtype=float if "ns" in name else None)
        for name, values in columns.items()
    }
    arrays["initial_length"] = np.asarray(initial)
    arrays["slice_ns"] = np.asarray(slice_ns)
    arrays["horizon_ns"] = np.asarray(horizon_ns)
    for name, value in timing.items():
        arrays[name] = np.asarray(value)
    return arrays


def _worker(arguments: tuple[Task, Settings, Path, dict[str, Any], list[str]]) -> str:
    """Run-stage worker: all repeats of one task, written atomically."""
    task, settings, directory, model_json, leaders = arguments
    started = time.perf_counter()
    model = CostModel.from_json(task.state["instance"], model_json)
    arrays = run_task(task, settings, model, leaders, range(settings.repeats))
    path = result_path(directory, task)
    partial = path.with_name(path.stem + ".partial.npz")
    write_arrays(partial, arrays)
    partial.rename(path)
    return f"{task.label}:{task.candidate} {time.perf_counter() - started:.1f}s"


def load_plan(
    directory: Path, models_file: str = "cost_models.json"
) -> tuple[dict[str, Any], dict[str, CostModel], dict]:
    """Read ``plan.json`` and a cost-model file of a run directory.

    Raises:
        FileNotFoundError: If the cost models do not exist yet (the
            calibrated ones are written by ``--stage calibrate``).
    """
    payload = json.loads((directory / "plan.json").read_text())
    models_path = directory / models_file
    if not models_path.is_file():
        raise FileNotFoundError(f"{models_path} missing; run --stage calibrate first")
    models_json = json.loads(models_path.read_text())
    models = {
        name: CostModel.from_json(name, value) for name, value in models_json.items()
    }
    return payload, models, models_json


def tasks_of(payload: dict[str, Any]) -> list[Task]:
    """All tasks, largest instances first (the slowest go first)."""
    tasks = [
        Task(state, candidate)
        for state in payload["states"]
        for candidate in state["candidates"]
    ]
    tasks.sort(key=lambda task: (-task.state["n"], task.label, task.candidate))
    return tasks


def _timed_rollout(
    task: Task,
    settings: Settings,
    model: CostModel,
    leaders: list[str],
    first: str,
    horizon_slices: int,
    overhead_ns: float,
) -> RolloutResult:
    """One rollout with every application timed, the collector off; its
    streams are separate from the run's (``calibrate`` labels)."""
    state = task.state
    problem = _problem(state["path"])
    order = _order(REPOSITORY_ROOT / settings.e1_run, state)
    slice_ns = settings.slice_ns_per_city * problem.n
    gc.collect()
    gc.disable()
    try:
        return rollout(
            problem,
            order,
            first,
            GreedyByLevel(leaders),
            model,
            slice_ns,
            slice_ns * horizon_slices,
            random.Random(f"e1r:calibrate-first:{settings.seed}:{task.label}:{first}"),
            random.Random(f"e1r:calibrate:{settings.seed}:{task.label}:{first}"),
            timed=True,
            overhead_ns=overhead_ns,
        )
    finally:
        gc.enable()


def calibrate(settings: Settings, directory: Path) -> Path:
    """Stage 2: refit the virtual clock on one pinned core.

    One timed rollout of ``calibration_slices`` slices per (state,
    candidate), on the provisional clock.  Every operator a run can use on
    an instance (its candidates and its greedy map) must get timings; one
    that the rollouts never reached gets a timed slice from each of the
    instance's states.  Writes ``cost_models.json`` and ``calibration.csv``
    (per instance and operator: applications, coefficients, and how far the
    E1 fit was from the measured total).

    Raises:
        RuntimeError: If an operator a run needs still has no timings.
    """
    payload, provisional, _ = load_plan(directory, "cost_models_e1.json")
    cpu = physical_cores()[0]
    pin_to(cpu)
    (directory / "clock_calibrate.json").write_text(
        json.dumps(clock_state([cpu]), indent=2) + "\n"
    )
    overhead = clock_overhead_ns()
    sums: dict[str, dict[str, list[float]]] = defaultdict(dict)

    def collect(task: Task, first: str, horizon_slices: int) -> None:
        instance = task.state["instance"]
        result = _timed_rollout(
            task,
            settings,
            provisional[instance],
            payload["maps"][instance],
            first,
            horizon_slices,
            overhead,
        )
        for name, values in result.sums.items():
            total = sums[instance].setdefault(name, [0.0] * len(values))
            for index, value in enumerate(values):
                total[index] += value

    tasks = tasks_of(payload)
    started = time.perf_counter()
    for done, task in enumerate(tasks, start=1):
        collect(task, task.candidate, settings.calibration_slices)
        if done % 25 == 0 or done == len(tasks):
            LOGGER.info(
                "calibrate: %d/%d rollouts (%.0f s)",
                done,
                len(tasks),
                time.perf_counter() - started,
            )
    models = {}
    rows = []
    for instance in payload["instances"]:
        states = [state for state in payload["states"] if state["instance"] == instance]
        needed = set(payload["maps"][instance]) | {
            name for state in states for name in state["candidates"]
        }
        for name in sorted(needed - set(sums[instance])):
            LOGGER.info(
                "calibrate: %s never ran %s; timing it directly", instance, name
            )
            for state in states:
                collect(Task(state, name), name, 1)
        missing = needed - set(sums[instance])
        if missing:
            raise RuntimeError(f"{instance}: no timings for {sorted(missing)}")
        coefficients = {name: fit_sums(sums[instance][name]) for name in sorted(needed)}
        models[instance] = CostModel(instance, coefficients).to_json()
        for name, fitted in coefficients.items():
            count, s_l, s_m, *_rest = sums[instance][name]
            measured = sums[instance][name][6]
            e1 = provisional[instance].operators[name]
            predicted_e1 = (
                e1.intercept * count + e1.per_lookup * s_l + e1.per_moved * s_m
            )
            rows.append(
                {
                    "instance": instance,
                    "operator": name,
                    "applications": int(count),
                    "measured_ns": measured,
                    "intercept_ns": fitted.intercept,
                    "per_lookup_ns": fitted.per_lookup,
                    "per_moved_ns": fitted.per_moved,
                    "e1_fit_over_measured": predicted_e1 / measured,
                }
            )
    (directory / "cost_models.json").write_text(json.dumps(models, indent=1) + "\n")
    path = directory / "calibration.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    ratios: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        ratios[row["operator"]].append(row["e1_fit_over_measured"])
    for name in sorted(ratios):
        LOGGER.info(
            "E1 fit / measured, %s: median %.3f over %d instances",
            name,
            float(np.median(ratios[name])),
            len(ratios[name]),
        )
    return path


def run(settings: Settings, directory: Path, workers: int) -> None:
    """Stage 2: every task not yet written, on ``workers`` processes."""
    payload, _, models_json = load_plan(directory)
    tasks = tasks_of(payload)
    todo = [task for task in tasks if not result_path(directory, task).is_file()]
    LOGGER.info(
        "run: %d tasks to do, %d done, %d workers",
        len(todo),
        len(tasks) - len(todo),
        workers,
    )
    started = time.perf_counter()
    context = multiprocessing.get_context("spawn")
    with context.Pool(workers) as pool:
        for done, message in enumerate(
            pool.imap_unordered(
                _worker,
                [
                    (
                        task,
                        settings,
                        directory,
                        models_json[task.state["instance"]],
                        payload["maps"][task.state["instance"]],
                    )
                    for task in todo
                ],
                chunksize=1,
            ),
            start=1,
        ):
            elapsed = time.perf_counter() - started
            LOGGER.info(
                "%d/%d %s (elapsed %.0f min, about %.0f min left)",
                done,
                len(todo),
                message,
                elapsed / 60,
                elapsed / done * (len(todo) - done) / 60,
            )


def validate(settings: Settings, directory: Path) -> Path:
    """Stage 3: replay rollouts on one pinned core with every application
    timed; check that each reproduces its stored result and compare real
    with virtual time.

    Raises:
        FileNotFoundError: If the run stage is not finished.
        RuntimeError: If a replay does not reproduce its stored result.
    """
    payload, models, _ = load_plan(directory)
    tasks = tasks_of(payload)
    missing = [task for task in tasks if not result_path(directory, task).is_file()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} tasks not run yet")
    cpu = physical_cores()[0]
    pin_to(cpu)
    (directory / "clock_validate.json").write_text(
        json.dumps(clock_state([cpu]), indent=2) + "\n"
    )
    overhead = clock_overhead_ns()
    rng = random.Random(f"e1r-validate:{settings.seed}")
    picks = rng.sample(
        [(task, repeat) for task in tasks for repeat in range(settings.repeats)],
        settings.validate_rollouts,
    )
    rows = []
    for task, repeat in picks:
        stored = read_arrays(result_path(directory, task))
        gc.collect()
        gc.disable()
        try:
            arrays = run_task(
                task,
                settings,
                models[task.state["instance"]],
                payload["maps"][task.state["instance"]],
                range(repeat, repeat + 1),
                timed=True,
                overhead_ns=overhead,
            )
        finally:
            gc.enable()
        index = int(np.flatnonzero(stored["repeat"] == repeat)[0])
        if (
            arrays["final_length"][0] != stored["final_length"][index]
            or arrays["end_ns"][0] != stored["end_ns"][index]
        ):
            raise RuntimeError(f"{task.label}:{task.candidate}:{repeat} not reproduced")
        totals = {"all": (float(arrays["real_ns"][0]), float(arrays["end_ns"][0]))}
        for key in arrays:
            if key.startswith("real_by_operator."):
                name = key.removeprefix("real_by_operator.")
                totals[name] = (
                    float(arrays[key]),
                    float(arrays[f"virtual_by_operator.{name}"]),
                )
        for name, (real, virtual) in totals.items():
            rows.append(
                {
                    "instance": task.state["instance"],
                    "n": task.state["n"],
                    "seed": task.state["seed"],
                    "state": task.state["state"],
                    "candidate": task.candidate,
                    "repeat": repeat,
                    "operator": name,
                    "virtual_ns": virtual,
                    "real_ns": real,
                    "real_over_virtual": real / virtual,
                }
            )
        LOGGER.info(
            "validate %s:%s:%d real/virtual %.3f",
            task.label,
            task.candidate,
            repeat,
            totals["all"][0] / totals["all"][1],
        )
    path = directory / "validate.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for name in sorted({row["operator"] for row in rows}):
        ratios = [row["real_over_virtual"] for row in rows if row["operator"] == name]
        LOGGER.info(
            "real/virtual %s: median %.3f, min %.3f, max %.3f (%d rollouts)",
            name,
            float(np.median(ratios)),
            min(ratios),
            max(ratios),
            len(ratios),
        )
    return path


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m aos.e1_rollouts", description=__doc__
    )
    parser.add_argument("--out", type=Path, required=True, help="run directory")
    parser.add_argument(
        "--stage", choices=("plan", "calibrate", "run", "validate"), required=True
    )
    parser.add_argument("--e1-run", default="runs/e1-main/20261018-144422")
    parser.add_argument("--leaders", default="journal/data/claim-1/e1main_leaders.csv")
    parser.add_argument("--instances-per-cell", type=int, default=None)
    parser.add_argument("--states-per-class", type=int, default=None)
    parser.add_argument("--candidates", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--slice-ns-per-city", type=float, default=None)
    parser.add_argument("--horizon-slices", type=int, default=None)
    parser.add_argument("--calibration-slices", type=int, default=None)
    parser.add_argument(
        "--validate-rollouts",
        type=int,
        default=None,
        help="at plan or validate (the only setting a later stage may change)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="run-stage processes (default: every CPU this process may use)",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    directory: Path = arguments.out
    config_path = directory / "config.json"
    plan_options = (
        "instances_per_cell",
        "states_per_class",
        "candidates",
        "repeats",
        "slice_ns_per_city",
        "horizon_slices",
        "calibration_slices",
    )
    overrides = {
        name: getattr(arguments, name)
        for name in (*plan_options, "validate_rollouts")
        if getattr(arguments, name) is not None
    }
    if arguments.stage == "plan":
        settings = Settings(
            e1_run=arguments.e1_run, leaders=arguments.leaders, **overrides
        )
        if config_path.is_file():
            parser.error(f"{directory} already has a plan; use a new directory")
        directory.mkdir(parents=True, exist_ok=True)
        write_config(
            directory,
            {"experiment": "E1R rollouts (Claim 1.4)", "settings": asdict(settings)},
        )
        plan(settings, directory)
        return 0
    if not config_path.is_file():
        parser.error(f"{directory} has no plan; run --stage plan first")
    stored = json.loads(config_path.read_text())["settings"]["settings"]
    stored["fit_seeds"] = tuple(stored["fit_seeds"])
    settings = Settings(**stored)
    late = sorted(set(overrides) & set(plan_options))
    if late:
        parser.error(f"{late} can only be set at --stage plan")
    if arguments.validate_rollouts is not None:
        if arguments.stage != "validate":
            parser.error("--validate-rollouts applies to plan or validate only")
        settings = replace(settings, validate_rollouts=arguments.validate_rollouts)
    if arguments.stage == "run":
        workers = arguments.workers or len(os.sched_getaffinity(0))
        if workers < 1:
            parser.error("--workers must be positive")
        run(settings, directory, workers)
    elif arguments.stage == "calibrate":
        calibrate(settings, directory)
    else:
        validate(settings, directory)
    LOGGER.info("run directory: %s", directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
