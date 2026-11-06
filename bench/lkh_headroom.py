"""LKH-3 headroom kill test: can a change of setting mid-run beat the best fixed one?

An adaptive operator selector inside LKH-3 can only help if *some* change of
setting during a run beats the best fixed setting at equal time.  This
module measures that upper bound with an oracle that is allowed to look at
the results, before anything is built.

Design (``ideas/lkh-headroom.md``):

- **Arms** are the four TSP settings of the LKH pilot
  (:data:`bench.lkh_pilot.ARMS`): move type 5 or 3, crossed with LKH's own
  walk-based trial or a random 4-swap kick.  None of them changes the
  preprocessing, so one candidate set and one pi vector per instance serve
  every run.
- **Budget** ``T`` is CPU seconds of search (LKH's ``TIME_LIMIT``, which
  counts process CPU time and excludes preprocessing), proportional to the
  number of cities.
- **Runs** per (instance, seed): every arm for ``T`` without a break
  (``full``); every arm for ``T/2`` (``first``); then from each first
  half's tour, every arm for ``T/2`` (``second``), restarted with
  ``INITIAL_TOUR_FILE``.  A schedule ``a -> b`` is a first half of ``a``
  followed by a second half of ``b``; ``a -> a`` is the fixed arm with the
  same restart, so comparing schedules with ``a -> a`` charges both sides
  the same restart cost.
- **Headroom** is the gap of the best fixed arm minus the gap of the best
  schedule, chosen on half the seeds and scored on the other half
  (cross-fitting removes the winner's curse of picking the best of 16).

Every run appends one JSON record to ``results.jsonl``; a stopped run
resumes and skips the records already written.
"""

from __future__ import annotations

import csv
import heapq
import itertools
import json
import logging
import math
import re
import statistics
import subprocess
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from solvers.base import find_executable

from .lkh_pilot import ARMS, Arm

LOGGER = logging.getLogger(__name__)

DEFAULT_ARM = "m5_walk"
"""LKH's default TSP setting (``MOVE_TYPE = 5``, ``KICK_TYPE = 0``)."""
SECOND_SEED_OFFSET = 100_000
"""Added to a run's seed for its second half, the same for every arm."""
_UNBOUNDED_TRIALS = 1_000_000_000
_TRIAL_PATTERN = re.compile(
    r"^\*\s+(\d+):\s+Cost\s*=\s*(-?\d+),.*?Time\s*=\s*([\d.]+)\s*sec"
)
_TRIALS_PATTERN = re.compile(r"^Trials\.min\s*=\s*(\d+)", re.MULTILINE)
_ASCENT_PATTERN = re.compile(r"Preprocessing time\s*=\s*([\d.]+)")
# Wall-clock allowance beyond TIME_LIMIT before a search run is killed: LKH
# reads the instance, the candidates and the pi file before its clock starts.
_SLACK_SECONDS = 120.0


class Kind(StrEnum):
    """Kinds of LKH run in the test."""

    PREPROCESS = "preprocess"
    FULL = "full"
    FIRST = "first"
    SECOND = "second"


@dataclass(frozen=True, slots=True)
class HeadroomInstance:
    """One instance of the test.

    Attributes:
        name: Unique name, used in file names.
        family: Distribution family, e.g. ``"uniform"``.
        n: Number of cities.
        path: TSPLIB problem file.
    """

    name: str
    family: str
    n: int
    path: Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Protocol settings shared by every run of a test.

    Attributes:
        seeds: LKH seeds; an even count, split by position for cross-fitting.
        seconds_per_kcity: Search budget ``T`` in CPU seconds per 1,000 cities.
        arms: The settings compared.
    """

    seeds: tuple[int, ...]
    seconds_per_kcity: float
    arms: tuple[Arm, ...] = ARMS

    def budget(self, n: int) -> float:
        """Return ``T``, the whole search budget for an instance of ``n`` cities."""
        return self.seconds_per_kcity * n / 1000.0

    def arm(self, name: str) -> Arm:
        """Return the arm called ``name``.

        Raises:
            KeyError: If no arm has that name.
        """
        for arm in self.arms:
            if arm.name == name:
                return arm
        raise KeyError(f"no arm named {name!r}")


@dataclass(frozen=True, slots=True)
class Job:
    """One LKH run.

    Attributes:
        instance: The instance solved.
        kind: What the run is for.
        seed: LKH seed of the run's (instance, seed) block.
        first: Arm of a full run or of the first half.
        second: Arm of the second half, for :attr:`Kind.SECOND` only.
        time_limit: ``TIME_LIMIT`` in CPU seconds.
    """

    instance: HeadroomInstance
    kind: Kind
    seed: int = 0
    first: str = ""
    second: str = ""
    time_limit: float = 0.0

    @property
    def label(self) -> str:
        """Short name of the run within its (instance, seed) block."""
        if self.kind is Kind.SECOND:
            return f"second-{self.first}-{self.second}"
        if self.kind is Kind.PREPROCESS:
            return "preprocess"
        return f"{self.kind.value}-{self.first}"

    @property
    def key(self) -> str:
        """Unique key of the run, used to resume."""
        if self.kind is Kind.PREPROCESS:
            return f"{self.instance.name}/preprocess"
        return f"{self.instance.name}/s{self.seed}/{self.label}"

    @property
    def priority(self) -> float:
        """Larger first: the length of the chain of work this run starts."""
        if self.kind is Kind.PREPROCESS:
            return math.inf
        if self.kind is Kind.FIRST:
            return 2 * self.time_limit
        return self.time_limit


@dataclass(slots=True)
class _Queue:
    """Ready jobs, largest priority first, in submission order on ties."""

    heap: list[tuple[float, int, Job]] = field(default_factory=list)
    counter: int = 0

    def push(self, job: Job) -> None:
        heapq.heappush(self.heap, (-job.priority, self.counter, job))
        self.counter += 1

    def pop(self) -> Job:
        return heapq.heappop(self.heap)[2]

    def __len__(self) -> int:
        return len(self.heap)


class HeadroomError(RuntimeError):
    """Raised when runs of the test failed."""


def load_manifest(path: Path) -> tuple[tuple[HeadroomInstance, ...], Settings]:
    """Read a test manifest.

    The manifest is JSON with ``seeds``, ``seconds_per_kcity`` and
    ``instances`` (each with ``name``, ``family``, ``n`` and ``path``; a
    relative path is relative to the manifest).

    Raises:
        ValueError: If names repeat, the seed count is odd or a file is missing.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    instances = tuple(
        HeadroomInstance(
            name=entry["name"],
            family=entry["family"],
            n=int(entry["n"]),
            path=(path.parent / entry["path"]).resolve(),
        )
        for entry in data["instances"]
    )
    names = [instance.name for instance in instances]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: instance names repeat")
    missing = [
        str(instance.path) for instance in instances if not instance.path.is_file()
    ]
    if missing:
        raise ValueError(f"{path}: missing problem files: {missing}")
    seeds = tuple(int(seed) for seed in data["seeds"])
    if len(seeds) < 2 or len(seeds) % 2:
        raise ValueError(
            f"{path}: cross-fitting needs an even number of seeds, got {seeds}"
        )
    settings = Settings(seeds=seeds, seconds_per_kcity=float(data["seconds_per_kcity"]))
    return instances, settings


def search_jobs(instance: HeadroomInstance, settings: Settings) -> list[Job]:
    """Return the full and first-half runs of an instance; second halves come later."""
    budget = settings.budget(instance.n)
    return [
        Job(instance, kind, seed, arm.name, "", limit)
        for seed in settings.seeds
        for arm in settings.arms
        for kind, limit in ((Kind.FIRST, budget / 2), (Kind.FULL, budget))
    ]


def second_jobs(first: Job, settings: Settings) -> list[Job]:
    """Return the second halves that continue a first-half run, one per arm."""
    return [
        Job(
            first.instance,
            Kind.SECOND,
            first.seed,
            first.first,
            arm.name,
            first.time_limit,
        )
        for arm in settings.arms
    ]


def estimate(
    instances: Sequence[HeadroomInstance],
    settings: Settings,
    jobs: int,
    preprocess_seconds: Mapping[int, float],
) -> tuple[float, float]:
    """Estimate the CPU seconds and the wall seconds of a test.

    Args:
        instances: The instances.
        settings: The protocol.
        jobs: Parallel LKH processes.
        preprocess_seconds: Measured preprocessing time by size; sizes in
            between are interpolated on a log-log scale.

    Returns:
        ``(cpu_seconds, wall_seconds)``; the wall estimate is the larger of
        the CPU total over ``jobs`` and the longest single chain.
    """
    arms = len(settings.arms)
    seeds = len(settings.seeds)
    cpu = 0.0
    longest = 0.0
    for instance in instances:
        budget = settings.budget(instance.n)
        preprocess = _interpolate(preprocess_seconds, instance.n)
        cpu += preprocess + seeds * (
            arms * budget + arms * budget / 2 + arms * arms * budget / 2
        )
        longest = max(longest, preprocess + budget)
    return cpu, max(cpu / jobs, longest)


def _interpolate(points: Mapping[int, float], n: int) -> float:
    """Interpolate ``points`` (size to seconds) at ``n`` on a log-log scale."""
    sizes = sorted(points)
    if n <= sizes[0]:
        return points[sizes[0]] * n / sizes[0]
    for low, high in itertools.pairwise(sizes):
        if n <= high:
            slope = math.log(points[high] / points[low]) / math.log(high / low)
            return points[low] * (n / low) ** slope
    low, high = sizes[-2], sizes[-1]
    slope = math.log(points[high] / points[low]) / math.log(high / low)
    return points[high] * (n / high) ** slope


def run_test(
    instances: Sequence[HeadroomInstance],
    settings: Settings,
    out: Path,
    *,
    jobs: int,
    executable: str | Path | None = None,
) -> None:
    """Run every LKH run of the test that ``out/results.jsonl`` does not hold yet.

    Raises:
        HeadroomError: If any run failed; the others still ran.
    """
    binary = find_executable("lkh", executable)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    done = {str(record["key"]): record for record in read_results(results_path)}
    LOGGER.info("%d runs already recorded in %s", len(done), results_path)

    queue = _Queue()
    for instance in instances:
        preprocess = Job(instance, Kind.PREPROCESS)
        if preprocess.key in done and _preprocessed(out, instance):
            _enqueue_search(queue, instance, settings, done)
        else:
            queue.push(preprocess)

    failures: list[str] = []
    started = time.monotonic()
    with (
        ThreadPoolExecutor(max_workers=jobs) as pool,
        results_path.open("a") as results,
    ):
        running: dict[Future[dict[str, object]], Job] = {}
        while queue or running:
            while queue and len(running) < jobs:
                job = queue.pop()
                running[pool.submit(_run_job, job, binary, out, settings)] = job
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                job = running.pop(future)
                record = future.result()
                results.write(json.dumps(record) + "\n")
                results.flush()
                done[job.key] = record
                if record["error"]:
                    failures.append(f"{job.key}: {record['error']}")
                    LOGGER.error("%s failed: %s", job.key, record["error"])
                    continue
                if job.kind is Kind.PREPROCESS:
                    _enqueue_search(queue, job.instance, settings, done)
                elif job.kind is Kind.FIRST:
                    for follow in second_jobs(job, settings):
                        if follow.key not in done:
                            queue.push(follow)
                LOGGER.info(
                    "%s cost %s (%.0f min elapsed, %d running, %d queued)",
                    job.key,
                    record["cost"],
                    (time.monotonic() - started) / 60,
                    len(running),
                    len(queue),
                )
    if failures:
        raise HeadroomError(f"{len(failures)} runs failed:\n" + "\n".join(failures))


def _enqueue_search(
    queue: _Queue,
    instance: HeadroomInstance,
    settings: Settings,
    done: Mapping[str, Mapping[str, object]],
) -> None:
    """Queue an instance's runs that are not recorded yet, second halves included."""
    for job in search_jobs(instance, settings):
        if job.key not in done:
            queue.push(job)
        elif job.kind is Kind.FIRST and not done[job.key]["error"]:
            for follow in second_jobs(job, settings):
                if follow.key not in done:
                    queue.push(follow)


def _preprocessed(out: Path, instance: HeadroomInstance) -> bool:
    """Whether the candidate and pi files of an instance exist."""
    candidates, pi = _preprocess_paths(out, instance)
    return candidates.is_file() and pi.is_file()


def _preprocess_paths(out: Path, instance: HeadroomInstance) -> tuple[Path, Path]:
    directory = out / "preprocess"
    return directory / f"{instance.name}.cand", directory / f"{instance.name}.pi"


def _tour_path(out: Path, job: Job) -> Path:
    return out / "tours" / job.instance.name / f"s{job.seed}" / f"{job.label}.tour"


def _parameters(job: Job, out: Path, settings_arm: Arm | None) -> list[str]:
    """Build the LKH parameter file of a run."""
    candidates, pi = _preprocess_paths(out, job.instance)
    lines = [
        f"PROBLEM_FILE = {job.instance.path}",
        f"CANDIDATE_FILE = {candidates}",
        f"PI_FILE = {pi}",
        "RUNS = 1",
        "TRACE_LEVEL = 1",
    ]
    if job.kind is Kind.PREPROCESS:
        # A search of zero seconds: LKH writes both files and stops.
        return [*lines, "SEED = 1", "TIME_LIMIT = 0"]
    if settings_arm is None:
        raise ValueError(f"{job.key}: a search run needs an arm")
    seed = job.seed + SECOND_SEED_OFFSET if job.kind is Kind.SECOND else job.seed
    lines += [
        f"SEED = {seed}",
        f"TIME_LIMIT = {job.time_limit:g}",
        f"MAX_TRIALS = {_UNBOUNDED_TRIALS}",
        f"MOVE_TYPE = {settings_arm.move_type}",
        f"KICK_TYPE = {settings_arm.kick_type}",
        "KICKS = 1",
        f"OUTPUT_TOUR_FILE = {_tour_path(out, job)}",
    ]
    if job.kind is Kind.SECOND:
        start = Job(job.instance, Kind.FIRST, job.seed, job.first, "", job.time_limit)
        lines.append(f"INITIAL_TOUR_FILE = {_tour_path(out, start)}")
    return lines


def _run_job(
    job: Job, binary: Path, out: Path, settings: Settings
) -> dict[str, object]:
    """Run one LKH process and return its record; failures are recorded, not raised."""
    arm_name = job.second if job.kind is Kind.SECOND else job.first
    arm = settings.arm(arm_name) if job.kind is not Kind.PREPROCESS else None
    record: dict[str, object] = {
        "key": job.key,
        "instance": job.instance.name,
        "family": job.instance.family,
        "n": job.instance.n,
        "kind": job.kind.value,
        "seed": job.seed,
        "first": job.first,
        "second": job.second,
        "time_limit": job.time_limit,
        "cost": None,
        "trials": None,
        "trace": [],
        "preprocess_seconds": None,
        "wall_seconds": None,
        "error": "",
    }
    if job.kind is not Kind.PREPROCESS:
        _tour_path(out, job).parent.mkdir(parents=True, exist_ok=True)
    else:
        (out / "preprocess").mkdir(parents=True, exist_ok=True)
    parameter_path = out / "parameters" / f"{job.key.replace('/', '__')}.par"
    parameter_path.parent.mkdir(parents=True, exist_ok=True)
    parameter_path.write_text(
        "\n".join(_parameters(job, out, arm)) + "\n", encoding="utf-8"
    )
    # Preprocessing has no TIME_LIMIT of its own; the ascent of 20,000
    # cities takes minutes, so it gets a generous fixed deadline.
    deadline = (
        3600.0 if job.kind is Kind.PREPROCESS else job.time_limit + _SLACK_SECONDS
    )
    began = time.monotonic()
    try:
        completed = subprocess.run(
            [str(binary), str(parameter_path)],
            capture_output=True,
            text=True,
            timeout=deadline,
            check=False,
        )
    except subprocess.TimeoutExpired:
        record["error"] = f"killed after {deadline:.0f} s"
        return record
    record["wall_seconds"] = round(time.monotonic() - began, 3)
    if completed.returncode != 0:
        record["error"] = (
            f"exit {completed.returncode}: "
            f"{completed.stdout[-500:]} {completed.stderr[-500:]}"
        )
        return record
    ascent = _ASCENT_PATTERN.search(completed.stdout)
    record["preprocess_seconds"] = float(ascent.group(1)) if ascent else None
    if job.kind is Kind.PREPROCESS:
        if not _preprocessed(out, job.instance):
            record["error"] = "LKH wrote no candidate or pi file"
        return record
    trace = [
        (float(match.group(3)), int(match.group(2)))
        for match in map(_TRIAL_PATTERN.match, completed.stdout.splitlines())
        if match is not None
    ]
    if not trace:
        record["error"] = f"no trial lines in the output: {completed.stdout[-500:]}"
        return record
    if not _tour_path(out, job).is_file():
        record["error"] = "LKH wrote no tour"
        return record
    trials = _TRIALS_PATTERN.search(completed.stdout)
    record["trace"] = trace
    record["cost"] = min(cost for _, cost in trace)
    record["trials"] = int(trials.group(1)) if trials else None
    return record


def read_results(path: Path) -> list[dict[str, object]]:
    """Read the records of ``results.jsonl``; a missing file holds none.

    A record that failed is kept, so a rerun retries it only after it is
    removed by hand; records later in the file win over earlier ones.
    """
    if not path.is_file():
        return []
    latest: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                record = json.loads(line)
                latest[str(record["key"])] = record
    return list(latest.values())


# Analysis -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstanceResult:
    """Headroom of one instance, in percentage points of gap to the best tour found.

    Attributes:
        instance: Instance name.
        family: Distribution family.
        n: Number of cities.
        budget: ``T`` in CPU seconds.
        reference: Best tour length over every run of the instance.
        default_gap: Mean gap of the default arm run for ``T``.
        second_half_value: What the second half of ``T`` buys the default
            arm: its mean gap at ``T/2`` minus at ``T``.  The unit of the
            normalised headroom.
        restart_cost: Mean gap of default restarted at ``T/2`` minus
            default without a break.
        tuning_headroom: Default minus the best fixed arm (both without a
            break), cross-fitted: what offline tuning would give.
        best_fixed: The fixed arm with the lowest mean gap (restarted).
        best_schedule: The schedule with the lowest mean gap.
        upper_headroom: Best restarted fixed arm minus best schedule, chosen
            and scored on all seeds (biased upwards; an upper bound).
        crossfit_headroom: The same, chosen on one half of the seeds and
            scored on the other, mean of both directions.
        crossfit_directions: The two directions separately.
        crossfit_against_full: Best fixed arm *without* a break minus best
            schedule, cross-fitted: what an oracle switcher that pays the
            restart would gain.
        standard_error: Standard error over seeds of the in-sample paired
            difference behind ``upper_headroom``.
    """

    instance: str
    family: str
    n: int
    budget: float
    reference: int
    default_gap: float
    second_half_value: float
    restart_cost: float
    tuning_headroom: float
    best_fixed: str
    best_schedule: str
    upper_headroom: float
    crossfit_headroom: float
    crossfit_directions: tuple[float, float]
    crossfit_against_full: float
    standard_error: float

    def normalised(self, value: float) -> float:
        """Return ``value`` in second-half values; ``nan`` if saturated."""
        if self.second_half_value <= SATURATED_GAP:
            return math.nan
        return value / self.second_half_value

    @property
    def verdict(self) -> str:
        """``room``, ``none``, ``saturated`` or ``unclear`` (see the design note)."""
        if self.second_half_value <= SATURATED_GAP:
            return "saturated"
        if self.normalised(self.upper_headroom) < KILL_FRACTION:
            return "none"
        if (
            self.normalised(self.crossfit_headroom) >= GO_FRACTION
            and min(self.crossfit_directions) > 0
        ):
            return "room"
        return "unclear"


SATURATED_GAP = 0.001
"""Second-half value (percentage points) below which an instance is solved."""
KILL_FRACTION = 0.25
"""Upper-bound headroom below this many second halves means no room."""
GO_FRACTION = 0.5
"""Cross-fitted headroom of at least this many second halves means room."""
LARGE_N = 5000
"""Instances from this size on decide the overall verdict."""


def analyse(out: Path, settings: Settings) -> list[InstanceResult]:
    """Compute the headroom of every instance with complete records.

    Raises:
        HeadroomError: If an instance's records are incomplete.
    """
    records = [
        record
        for record in read_results(out / "results.jsonl")
        if record["kind"] != Kind.PREPROCESS.value
    ]
    by_instance: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        by_instance[str(record["instance"])].append(record)
    return [
        _analyse_instance(rows, settings) for _, rows in sorted(by_instance.items())
    ]


def _analyse_instance(
    rows: Sequence[Mapping[str, object]], settings: Settings
) -> InstanceResult:
    """Compute :class:`InstanceResult` for the records of one instance."""
    first_row = rows[0]
    name = str(first_row["instance"])
    failed = [str(row["key"]) for row in rows if row["error"]]
    if failed:
        raise HeadroomError(f"{name}: failed runs {failed}")
    reference = min(_as_int(row["cost"]) for row in rows)
    arms = [arm.name for arm in settings.arms]
    seeds = list(settings.seeds)
    budget = settings.budget(_as_int(first_row["n"]))

    def gap(cost: int) -> float:
        return 100.0 * (cost / reference - 1.0)

    full: dict[tuple[str, int], float] = {}
    half_default: dict[int, float] = {}
    schedule: dict[tuple[str, str, int], float] = {}
    for row in rows:
        seed = _as_int(row["seed"])
        if row["kind"] == Kind.FULL.value:
            full[(str(row["first"]), seed)] = gap(_as_int(row["cost"]))
            if row["first"] == DEFAULT_ARM:
                half_default[seed] = gap(_cost_at(row["trace"], budget / 2))
        elif row["kind"] == Kind.SECOND.value:
            schedule[(str(row["first"]), str(row["second"]), seed)] = gap(
                _as_int(row["cost"])
            )
    expected_full = {(arm, seed) for arm in arms for seed in seeds}
    expected_schedule = {(a, b, seed) for a in arms for b in arms for seed in seeds}
    if set(full) != expected_full or set(schedule) != expected_schedule:
        raise HeadroomError(
            f"{name}: incomplete records ({len(full)}/{len(expected_full)} full, "
            f"{len(schedule)}/{len(expected_schedule)} second halves)"
        )

    schedules = [(a, b) for a in arms for b in arms]

    def mean_schedule(pair: tuple[str, str], subset: Iterable[int]) -> float:
        return statistics.fmean(schedule[(*pair, seed)] for seed in subset)

    def mean_full(arm: str, subset: Iterable[int]) -> float:
        return statistics.fmean(full[(arm, seed)] for seed in subset)

    best_fixed = min(arms, key=lambda arm: mean_schedule((arm, arm), seeds))
    best_pair = min(schedules, key=lambda pair: mean_schedule(pair, seeds))
    upper = mean_schedule((best_fixed, best_fixed), seeds) - mean_schedule(
        best_pair, seeds
    )
    paired = [
        schedule[(best_fixed, best_fixed, seed)] - schedule[(*best_pair, seed)]
        for seed in seeds
    ]
    standard_error = statistics.stdev(paired) / math.sqrt(len(paired))

    halves = (seeds[0::2], seeds[1::2])
    directions: list[float] = []
    against_full: list[float] = []
    tuning: list[float] = []
    for choose, score in (halves, halves[::-1]):
        fixed = min(arms, key=lambda arm: mean_schedule((arm, arm), choose))
        fixed_full = min(arms, key=lambda arm: mean_full(arm, choose))
        pair = min(schedules, key=lambda candidate: mean_schedule(candidate, choose))
        directions.append(
            mean_schedule((fixed, fixed), score) - mean_schedule(pair, score)
        )
        against_full.append(mean_full(fixed_full, score) - mean_schedule(pair, score))
        tuning.append(mean_full(DEFAULT_ARM, score) - mean_full(fixed_full, score))

    return InstanceResult(
        instance=name,
        family=str(first_row["family"]),
        n=_as_int(first_row["n"]),
        budget=budget,
        reference=reference,
        default_gap=mean_full(DEFAULT_ARM, seeds),
        second_half_value=statistics.fmean(half_default[seed] for seed in seeds)
        - mean_full(DEFAULT_ARM, seeds),
        restart_cost=mean_schedule((DEFAULT_ARM, DEFAULT_ARM), seeds)
        - mean_full(DEFAULT_ARM, seeds),
        tuning_headroom=statistics.fmean(tuning),
        best_fixed=best_fixed,
        best_schedule=f"{best_pair[0]}->{best_pair[1]}",
        upper_headroom=upper,
        crossfit_headroom=statistics.fmean(directions),
        crossfit_directions=(directions[0], directions[1]),
        crossfit_against_full=statistics.fmean(against_full),
        standard_error=standard_error,
    )


def _as_int(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError(f"expected an integer, got {value!r}")
    return value


def _cost_at(trace: object, seconds: float) -> int:
    """Return the best cost a run's trace had reached by ``seconds`` of search."""
    if not isinstance(trace, list):
        raise TypeError(f"expected a trace list, got {trace!r}")
    reached = [int(cost) for at, cost in trace if float(at) <= seconds]
    if not reached:
        raise HeadroomError(f"no trial finished within {seconds:g} s")
    return min(reached)


def overall_verdict(results: Sequence[InstanceResult]) -> str:
    """Return ``GO``, ``KILL`` or ``INCONCLUSIVE`` from the large instances."""
    large = [result.verdict for result in results if result.n >= LARGE_N]
    if not large:
        raise HeadroomError(f"no instance with n >= {LARGE_N}")
    if sum(verdict == "room" for verdict in large) >= 2:
        return "GO"
    if all(verdict in ("none", "saturated") for verdict in large):
        return "KILL"
    return "INCONCLUSIVE"


def write_results(results: Sequence[InstanceResult], path: Path) -> None:
    """Write one CSV row per instance, absolute and normalised headroom side by side."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "instance", "family", "n", "budget_s", "default_gap", "second_half_value",
        "restart_cost", "tuning_headroom", "best_fixed", "best_schedule",
        "upper_headroom", "crossfit_headroom", "crossfit_a", "crossfit_b",
        "crossfit_against_full", "standard_error", "upper_norm", "crossfit_norm",
        "verdict",
    ]  # fmt: skip
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for result in results:
            writer.writerow(
                [
                    result.instance,
                    result.family,
                    result.n,
                    f"{result.budget:g}",
                    *(
                        f"{value:.5f}"
                        for value in (
                            result.default_gap,
                            result.second_half_value,
                            result.restart_cost,
                            result.tuning_headroom,
                        )
                    ),
                    result.best_fixed,
                    result.best_schedule,
                    *(
                        f"{value:.5f}"
                        for value in (
                            result.upper_headroom,
                            result.crossfit_headroom,
                            *result.crossfit_directions,
                            result.crossfit_against_full,
                            result.standard_error,
                            result.normalised(result.upper_headroom),
                            result.normalised(result.crossfit_headroom),
                        )
                    ),
                    result.verdict,
                ]
            )


# Instances ----------------------------------------------------------------

GENERATED_SIZES = (1000, 2000, 5000, 10000)
"""Sizes of every generated family."""
LARGE_FAMILIES = ("uniform", "dimacs-clustered", "lgcp")
"""Generated families that also get a 20,000-city instance."""
REAL_INSTANCES: Mapping[str, tuple[str, str]] = {
    "pr1002": ("tsplib", "ALL_tsp/pr1002.tsp"),
    "d2103": ("tsplib", "ALL_tsp/d2103.tsp"),
    "fl3795": ("tsplib", "ALL_tsp/fl3795.tsp"),
    "rl5915": ("tsplib", "ALL_tsp/rl5915.tsp"),
    "rl11849": ("tsplib", "ALL_tsp/rl11849.tsp"),
    "d18512": ("tsplib", "ALL_tsp/d18512.tsp"),
    "fi10639": ("national", "downloaded/fi10639.tsp"),
    "xmc10150": ("vlsi", "downloaded/xmc10150.tsp"),
}
"""Fixed real-world instances: name to (family, path under ``data/``)."""


def _generate(family: str, n: int, seed: int, directory: Path) -> Path:
    """Generate one instance of a family; see the design note for the choices."""
    from generators.dimacs import DimacsGenerator, generate_dimacs
    from generators.netgen import generate_netgen
    from generators.points import lgcp, shaken_grid, write_tsplib
    from generators.tspgen import generate_tspgen

    if family == "uniform":
        return generate_dimacs(DimacsGenerator.PORTGEN, n, seed, directory)
    if family == "dimacs-clustered":
        return _portcgen(n, seed, directory)
    if family == "tspgen":
        return generate_tspgen(n, seed, directory)
    if family == "netgen-clustered":
        # The wrapper's scale multiplies netgen's [0, 100] box, so its
        # points already lie in the DIMACS square [0, 10^6]^2.  A larger
        # scale overflows LKH's integer costs (PRECISION = 100).
        return generate_netgen(n, seed, directory)
    if family == "shaken-grid":
        points = shaken_grid(n, seed, shake=0.25)
        name = f"shaken-grid-{n}-{seed}"
        comment = f"shaken grid n={n} seed={seed} shake=0.25"
        return write_tsplib(points, name, comment, directory / f"{name}.tsp")
    if family == "lgcp":
        points = lgcp(n, seed, variance=4.0, correlation=0.05)
        name = f"lgcp-{n}-{seed}"
        comment = f"LGCP n={n} seed={seed} variance=4 correlation=0.05"
        return write_tsplib(points, name, comment, directory / f"{name}.tsp")
    raise ValueError(f"unknown family {family!r}")


def _portcgen(n: int, seed: int, directory: Path) -> Path:
    """Run the DIMACS clustered generator (``n / 100`` Gaussian clusters)."""
    from generators.dimacs import find_dimacs_directory

    binary = find_dimacs_directory() / "portcgen"
    completed = subprocess.run(
        [str(binary), str(n), str(seed)], check=True, capture_output=True, text=True
    )
    path = directory / f"portcgen-{n}-{seed}.tsp"
    path.write_text(completed.stdout, encoding="ascii")
    return path


def build_instances(directory: Path, data_root: Path, seeds: Sequence[int]) -> Path:
    """Generate the instances, write ``manifest.json`` beside them, return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    families = ("uniform", "dimacs-clustered", "netgen-clustered", "tspgen")
    families += ("shaken-grid", "lgcp")
    entries: list[dict[str, object]] = []
    for family in families:
        sizes = GENERATED_SIZES + ((20000,) if family in LARGE_FAMILIES else ())
        for n in sizes:
            path = _generate(family, n, 1, directory)
            LOGGER.info("generated %s", path.name)
            entries.append(
                {
                    "name": path.stem,
                    "family": family,
                    "n": n,
                    "path": str(path.resolve()),
                }
            )
    for name, (family, relative) in REAL_INSTANCES.items():
        source = data_root / relative
        text = source.read_text(encoding="ascii")
        match = re.search(r"^DIMENSION\s*:\s*(\d+)", text, re.MULTILINE)
        if match is None:
            raise ValueError(f"{source}: no DIMENSION line")
        entries.append(
            {
                "name": name,
                "family": family,
                "n": int(match.group(1)),
                "path": str(source.resolve()),
            }
        )
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"seeds": list(seeds), "seconds_per_kcity": 2.0, "instances": entries},
            indent=1,
        ),
        encoding="utf-8",
    )
    return manifest


# Command line -------------------------------------------------------------

# Preprocessing (candidate set and subgradient ascent) in CPU seconds by
# size, measured on uniform instances with the clock locked (2026-11-05).
MEASURED_PREPROCESS_SECONDS: Mapping[int, float] = {
    5_000: 22.0,
    10_000: 84.4,
    20_000: 470.0,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``estimate``, ``run`` or ``analyse`` on a manifest.

    Examples::

        uv run python -m bench.lkh_headroom estimate --manifest M.json --jobs 8
        uv run python -m bench.lkh_headroom run --manifest M.json --out runs/X
        uv run python -m bench.lkh_headroom analyse --manifest M.json --out runs/X
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m bench.lkh_headroom", description=__doc__
    )
    parser.add_argument("stage", choices=("build", "estimate", "run", "analyse"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--jobs", type=int, default=8)
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if arguments.stage == "build":
        build_instances(arguments.manifest.parent, Path("data"), seeds=(1, 2, 3, 4))
        return 0
    instances, settings = load_manifest(arguments.manifest)

    if arguments.stage == "estimate":
        cpu, wall = estimate(
            instances, settings, arguments.jobs, MEASURED_PREPROCESS_SECONDS
        )
        LOGGER.info(
            "%d instances, %d seeds, %d arms: %.0f CPU min, ~%.0f min on %d jobs",
            len(instances), len(settings.seeds), len(settings.arms), cpu / 60,
            wall / 60, arguments.jobs,
        )  # fmt: skip
        return 0
    if arguments.out is None:
        parser.error("--out is required for run and analyse")
    if arguments.stage == "run":
        run_test(instances, settings, arguments.out, jobs=arguments.jobs)
        return 0
    results = analyse(arguments.out, settings)
    write_results(results, arguments.out / "analysis" / "instances.csv")
    for result in results:
        LOGGER.info(
            "%-28s n=%6d V=%.4f upper=%.4f cf=%.4f (%.2f V) %s",
            result.instance, result.n, result.second_half_value, result.upper_headroom,
            result.crossfit_headroom, result.normalised(result.crossfit_headroom),
            result.verdict,
        )  # fmt: skip
    LOGGER.info("overall: %s", overall_verdict(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
