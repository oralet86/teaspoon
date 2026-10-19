"""Steady-state population search with one operator slice per step.

Each step picks a tour by tournament, copies it, gives the copy one slice
with the operator the arm chooses, and replaces the worst tour if the copy
is shorter and its length is not already present.  There is no crossover
(decision of 2026-10-16, ``ideas/benchmark-protocol.md``).

Reproducibility: the run uses four independent random streams derived from
the seed (initial tours, tournament selection, the arm, the operators).  The
initial population therefore depends only on the seed, so arms are paired.
A time-bounded run is not bit-reproducible, but its log records every
slice's operator and application count; :class:`ReplayArm` re-runs it
exactly in count mode.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .operators import EPSILON, OPERATORS
from .problem import Problem
from .slices import SliceRecord, run_slice
from .tour import Tour

OPERATOR_NAMES: tuple[str, ...] = tuple(OPERATORS)
"""Operator names; arms and logs refer to operators by index into this."""

_OPERATOR_FUNCTIONS = tuple(OPERATORS.values())


class Arm(Protocol):
    """Chooses the operator for each slice."""

    def next(self, rng: random.Random) -> tuple[int, int | None]:
        """Return the operator index and, optionally, an application count
        that overrides the configured slice limit (used by replays)."""
        ...

    def update(self, operator: int, record: SliceRecord) -> None:
        """Learn from the slice just run (no-op for non-adaptive arms)."""
        ...


class FixedArm:
    """Always the same operator."""

    def __init__(self, name: str) -> None:
        if name not in OPERATOR_NAMES:
            raise ValueError(
                f"unknown operator {name!r}; expected one of {OPERATOR_NAMES}"
            )
        self._index = OPERATOR_NAMES.index(name)

    def next(self, rng: random.Random) -> tuple[int, int | None]:
        """Return the fixed operator."""
        return self._index, None

    def update(self, operator: int, record: SliceRecord) -> None:
        """Ignore the slice."""


class UniformArm:
    """Each operator with equal probability; the baseline without learning."""

    def __init__(self, names: Sequence[str] = OPERATOR_NAMES) -> None:
        unknown = [name for name in names if name not in OPERATOR_NAMES]
        if unknown or not names:
            raise ValueError(f"unknown or empty operator list: {list(names)}")
        self._indices = [OPERATOR_NAMES.index(name) for name in names]

    def next(self, rng: random.Random) -> tuple[int, int | None]:
        """Return a uniformly chosen operator."""
        return self._indices[rng.randrange(len(self._indices))], None

    def allow(self, name: str) -> None:
        """Add an operator to the choice set (no-op if already present).

        Raises:
            ValueError: If the operator is unknown.
        """
        if name not in OPERATOR_NAMES:
            raise ValueError(f"unknown operator {name!r}")
        index = OPERATOR_NAMES.index(name)
        if index not in self._indices:
            self._indices.append(index)

    def update(self, operator: int, record: SliceRecord) -> None:
        """Ignore the slice."""


FLOOR_SHARE = 0.2
"""Default share of probability the adaptive arms' floors hold together."""

Reward = Callable[[SliceRecord], float]
"""``slice record -> reward``; what an adaptive arm learns from."""


def gain_rate(record: SliceRecord) -> float:
    """Return the slice's length reduction per second (never negative).

    Time is charged in the reward as in the budget, so an operator that ran
    for longer (count mode, or an overshoot) must earn proportionally more.
    """
    return record.gain / max(record.duration_ns, 1) * 1e9


def _operator_indices(names: Sequence[str]) -> list[int]:
    """Return the indices of ``names`` into :data:`OPERATOR_NAMES`.

    Raises:
        ValueError: If the list is empty, has duplicates, or names an
            unknown operator.
    """
    unknown = [name for name in names if name not in OPERATOR_NAMES]
    if unknown or not names or len(set(names)) != len(names):
        raise ValueError(f"unknown, duplicate or empty operator list: {list(names)}")
    return [OPERATOR_NAMES.index(name) for name in names]


class ProbabilityMatching:
    """Picks operators in proportion to their estimated quality.

    Each operator keeps an exponentially smoothed estimate of its reward;
    selection probabilities follow the estimates, with a floor so that no
    operator is ever dropped (Goldberg 1990; Fialho 2010).

    Args:
        names: Operators to choose between.
        minimum_probability: Floor on every operator's probability; by
            default ``FLOOR_SHARE / count``.
        learning_rate: Weight of the newest reward in the quality estimate.
        reward: Credit of a slice; must not be negative.

    Raises:
        ValueError: If the operator list is invalid or the floor leaves no
            probability to distribute.
    """

    def __init__(
        self,
        names: Sequence[str] = OPERATOR_NAMES,
        minimum_probability: float | None = None,
        learning_rate: float = 0.1,
        reward: Reward = gain_rate,
    ) -> None:
        self._indices = _operator_indices(names)
        count = len(self._indices)
        if minimum_probability is None:
            minimum_probability = FLOOR_SHARE / count
        if not 0.0 <= minimum_probability * count < 1.0:
            raise ValueError(
                f"minimum_probability {minimum_probability} too large for "
                f"{count} operators"
            )
        self._local = {operator: k for k, operator in enumerate(self._indices)}
        self._minimum = minimum_probability
        self._learning_rate = learning_rate
        self._reward = reward
        self.quality = [0.0] * count
        self.probabilities = [1.0 / count] * count

    def next(self, rng: random.Random) -> tuple[int, int | None]:
        """Sample an operator from the current probabilities."""
        return rng.choices(self._indices, weights=self.probabilities)[0], None

    def update(self, operator: int, record: SliceRecord) -> None:
        """Move the operator's quality towards the reward and re-normalise.

        Raises:
            ValueError: If the reward is negative, which the proportional
                rule cannot represent.
        """
        reward = self._reward(record)
        if reward < 0:
            raise ValueError(f"probability matching needs rewards >= 0, got {reward}")
        local = self._local[operator]
        self.quality[local] += self._learning_rate * (reward - self.quality[local])
        total = sum(self.quality)
        count = len(self.quality)
        if total <= 0:
            self.probabilities = [1.0 / count] * count
            return
        spread = 1.0 - count * self._minimum
        self.probabilities = [
            self._minimum + spread * quality / total for quality in self.quality
        ]


class AdaptivePursuit:
    """Pushes probability towards the operator with the best quality.

    Where probability matching spreads probability by relative quality,
    pursuit moves it to the current leader, so it commits faster and reacts
    faster when the leader changes (Thierens 2005).  Only the ranking of the
    quality estimates matters, so the reward's scale and sign do not.

    Args:
        names: Operators to choose between.
        minimum_probability: Floor on every operator's probability; by
            default ``FLOOR_SHARE / count``.
        learning_rate: Weight of the newest reward in the quality estimate.
        pursuit_rate: How far probabilities move towards their target on
            each update.
        reward: Credit of a slice.

    Raises:
        ValueError: If the operator list is invalid or the floor leaves no
            probability for the leader.
    """

    def __init__(
        self,
        names: Sequence[str] = OPERATOR_NAMES,
        minimum_probability: float | None = None,
        learning_rate: float = 0.1,
        pursuit_rate: float = 0.1,
        reward: Reward = gain_rate,
    ) -> None:
        self._indices = _operator_indices(names)
        count = len(self._indices)
        if minimum_probability is None:
            minimum_probability = FLOOR_SHARE / count
        if not 0.0 <= minimum_probability * count < 1.0:
            raise ValueError(
                f"minimum_probability {minimum_probability} too large for "
                f"{count} operators"
            )
        self._local = {operator: k for k, operator in enumerate(self._indices)}
        self._minimum = minimum_probability
        self._maximum = 1.0 - (count - 1) * minimum_probability
        self._learning_rate = learning_rate
        self._pursuit_rate = pursuit_rate
        self._reward = reward
        self.quality = [0.0] * count
        self.probabilities = [1.0 / count] * count

    def next(self, rng: random.Random) -> tuple[int, int | None]:
        """Sample an operator from the current probabilities."""
        return rng.choices(self._indices, weights=self.probabilities)[0], None

    def update(self, operator: int, record: SliceRecord) -> None:
        """Update the quality estimate and pursue the best operator."""
        local = self._local[operator]
        self.quality[local] += self._learning_rate * (
            self._reward(record) - self.quality[local]
        )
        leader = max(range(len(self.quality)), key=self.quality.__getitem__)
        self.probabilities = [
            probability
            + self._pursuit_rate
            * ((self._maximum if k == leader else self._minimum) - probability)
            for k, probability in enumerate(self.probabilities)
        ]


class ReplayArm:
    """Replays a logged run: the same operators with the same application
    counts, slice by slice."""

    def __init__(self, operators: Sequence[int], applications: Sequence[int]) -> None:
        if len(operators) != len(applications):
            raise ValueError("operators and applications must have the same length")
        self._operators = list(operators)
        self._applications = list(applications)
        self._next = 0

    def __len__(self) -> int:
        return len(self._operators)

    def next(self, rng: random.Random) -> tuple[int, int | None]:
        """Return the next logged operator and its application count.

        Raises:
            IndexError: If the replay is exhausted.
        """
        index = self._next
        self._next += 1
        return self._operators[index], self._applications[index]

    def update(self, operator: int, record: SliceRecord) -> None:
        """Ignore the slice."""


@dataclass(frozen=True, slots=True)
class PopulationConfig:
    """Settings of one run.

    Exactly one slice limit and at least one budget must be set.

    Attributes:
        size: Number of tours.
        tournament: Tours compared when picking the tour to work on.
        slice_seconds: Wall-clock length of a slice.
        slice_applications: Applications per slice (count mode).
        budget_seconds: Wall-clock budget of the whole run.
        budget_applications: Total operator applications allowed.
        budget_slices: Number of slices allowed.
        seed: Seed of all random streams.
    """

    size: int = 20
    tournament: int = 3
    slice_seconds: float | None = None
    slice_applications: int | None = None
    budget_seconds: float | None = None
    budget_applications: int | None = None
    budget_slices: int | None = None
    seed: int = 0

    def validate(self) -> None:
        """Raise if the settings are inconsistent.

        Raises:
            ValueError: Describing the problem.
        """
        if (self.slice_seconds is None) == (self.slice_applications is None):
            raise ValueError("set exactly one of slice_seconds and slice_applications")
        budgets = (self.budget_seconds, self.budget_applications, self.budget_slices)
        if all(budget is None for budget in budgets):
            raise ValueError("set at least one budget")
        if not 2 <= self.tournament <= self.size:
            raise ValueError("tournament must be between 2 and size")


@dataclass(slots=True)
class SliceLog:
    """Per-slice columns of a run (protocol section 9).

    ``operator`` and ``previous`` are indices into :data:`OPERATOR_NAMES`
    (``previous`` is -1 for the first slice).  Times are nanoseconds since the
    start of the run.
    """

    start_ns: list[int] = field(default_factory=list)
    duration_ns: list[int] = field(default_factory=list)
    overshoot_ns: list[int] = field(default_factory=list)
    operator: list[int] = field(default_factory=list)
    previous: list[int] = field(default_factory=list)
    applications: list[int] = field(default_factory=list)
    improving: list[int] = field(default_factory=list)
    gain: list[float] = field(default_factory=list)
    length_before: list[float] = field(default_factory=list)
    length_after: list[float] = field(default_factory=list)
    accepted: list[bool] = field(default_factory=list)
    lookups: list[int] = field(default_factory=list)
    moved: list[int] = field(default_factory=list)
    anchor: list[int] = field(default_factory=list)
    best_length: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.operator)

    def to_arrays(self) -> dict[str, np.ndarray]:
        """Return the columns as NumPy arrays (for ``np.savez``)."""
        return {name: np.asarray(getattr(self, name)) for name in self.__slots__}


@dataclass(frozen=True, slots=True)
class PopulationResult:
    """Outcome of a run.

    Attributes:
        best: Best tour found.
        elapsed_seconds: Wall-clock time of the run.
        log: Per-slice log.
        stopped_early: Whether the ``on_slice`` callback ended the run.
    """

    best: Tour
    elapsed_seconds: float
    log: SliceLog
    stopped_early: bool


SliceCallback = Callable[[int, Tour, float], bool]
"""``(slice index, best tour, elapsed seconds) -> stop?``"""


def initial_population(problem: Problem, size: int, seed: int) -> list[Tour]:
    """Return ``size`` random tours drawn from the seed's initial stream."""
    rng = random.Random(f"{seed}:init")
    tours = []
    for _ in range(size):
        order = list(range(problem.n))
        rng.shuffle(order)
        tours.append(Tour.from_order(order, problem))
    return tours


def run_population(
    problem: Problem,
    arm: Arm,
    config: PopulationConfig,
    on_slice: SliceCallback | None = None,
) -> PopulationResult:
    """Run the population search until a budget is used up.

    Args:
        problem: The instance.
        arm: Operator choice per slice.
        config: Run settings.
        on_slice: Called after every slice with the slice index, the best
            tour and the elapsed seconds; returning ``True`` ends the run.

    Raises:
        ValueError: If the configuration is invalid.
    """
    config.validate()
    population = initial_population(problem, config.size, config.seed)
    lengths = [tour.length for tour in population]
    select_rng = random.Random(f"{config.seed}:select")
    arm_rng = random.Random(f"{config.seed}:arm")
    operator_rng = random.Random(f"{config.seed}:operator")
    log = SliceLog()
    best_index = min(range(config.size), key=lengths.__getitem__)
    previous = -1
    total_applications = 0
    stopped_early = False
    clock = time.perf_counter_ns
    started = clock()
    slice_index = 0
    while True:
        elapsed_ns = clock() - started
        if (
            config.budget_seconds is not None
            and elapsed_ns >= config.budget_seconds * 1e9
        ):
            break
        if (
            config.budget_applications is not None
            and total_applications >= config.budget_applications
        ):
            break
        if config.budget_slices is not None and slice_index >= config.budget_slices:
            break
        contenders = select_rng.sample(range(config.size), config.tournament)
        parent = min(contenders, key=lengths.__getitem__)
        child = population[parent].copy()
        operator, override = arm.next(arm_rng)
        start_ns = clock() - started
        if override is not None:
            record = run_slice(
                _OPERATOR_FUNCTIONS[operator],
                child,
                problem,
                operator_rng,
                applications=override,
            )
        else:
            record = run_slice(
                _OPERATOR_FUNCTIONS[operator],
                child,
                problem,
                operator_rng,
                seconds=config.slice_seconds,
                applications=config.slice_applications,
            )
        arm.update(operator, record)
        worst = max(range(config.size), key=lengths.__getitem__)
        accepted = (
            child.length < lengths[worst] - EPSILON and child.length not in lengths
        )
        if accepted:
            population[worst] = child
            lengths[worst] = child.length
            if child.length < lengths[best_index]:
                best_index = worst
        total_applications += record.applications
        log.start_ns.append(start_ns)
        log.duration_ns.append(record.duration_ns)
        log.overshoot_ns.append(record.overshoot_ns)
        log.operator.append(operator)
        log.previous.append(previous)
        log.applications.append(record.applications)
        log.improving.append(record.improving)
        log.gain.append(record.gain)
        log.length_before.append(child.length + record.gain)
        log.length_after.append(child.length)
        log.accepted.append(accepted)
        log.lookups.append(record.lookups)
        log.moved.append(record.moved)
        log.anchor.append(record.anchor)
        log.best_length.append(lengths[best_index])
        previous = operator
        slice_index += 1
        if on_slice is not None and on_slice(
            slice_index - 1, population[best_index], (clock() - started) / 1e9
        ):
            stopped_early = True
            break
    return PopulationResult(
        best=population[best_index],
        elapsed_seconds=(clock() - started) / 1e9,
        log=log,
        stopped_early=stopped_early,
    )


def replay(
    problem: Problem, log: SliceLog, config: PopulationConfig
) -> PopulationResult:
    """Re-run a logged run exactly, in count mode.

    Uses the logged operators and application counts with the original
    seed; the result's tours equal those of the original run.
    """
    arm = ReplayArm(log.operator, log.applications)
    replay_config = PopulationConfig(
        size=config.size,
        tournament=config.tournament,
        slice_applications=1,
        budget_slices=len(arm),
        seed=config.seed,
    )
    return run_population(problem, arm, replay_config)
