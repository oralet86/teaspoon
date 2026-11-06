"""Rollouts from a fixed tour on a virtual clock (Claim 1.4, experiment E1R).

A rollout runs one *first slice* of a chosen operator from a state, then a
continuation policy, until a horizon.  Comparing rollouts that differ only in
the first operator estimates V_t(s, o), the expected tour length at time t
after first choosing o (``ideas/claims.md``, Claim 1.4).

**Virtual clock.**  E0 showed that timed work cannot run in parallel
(parallel workers slow the operators unevenly), and rollouts are too many to
time on one core.  So, as E1 separated untimed gains from single-core
timing, rollouts run untimed on every CPU and charge each application a time
predicted from its counted work, :class:`CostModel`, fitted per instance and
operator on E1's single-core timings: time = a + b * lookups + c * moved.
A rollout is then fully determined by its seeds, so it can be replayed
exactly, which :func:`rollout` with ``timed=True`` uses to check the clock
against real single-core time.

**Continuation policy.**  :class:`GreedyByLevel` applies, in every slice, the
E1 leader of the tour's current local-optimality class.  The class is found
exactly (:func:`classify`, the same flags as E1) and is not charged, like the
E1 map itself: it is reference knowledge, not part of the search.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from .operators import EPSILON, OPERATORS
from .problem import Problem
from .tour import Tour
from .work import Work

CLASSES = ("2-opt open", "2-opt optimum", "3-opt optimum", "LK optimum")
"""Local-optimality classes in order of search progress; the same labels and
order as :data:`aos.analysis.e1.CLASSES`."""

OPERATOR_NAMES: tuple[str, ...] = tuple(OPERATORS)
"""Operator names; logs refer to operators by index into this."""


@dataclass(frozen=True, slots=True)
class Coefficients:
    """Predicted time of one application: ``intercept + per_lookup * lookups
    + per_moved * moved`` nanoseconds (all non-negative)."""

    intercept: float
    per_lookup: float
    per_moved: float


SUM_FIELDS = ("count", "l", "m", "ll", "lm", "mm", "t", "tl", "tm")
"""Sufficient statistics of a least-squares fit of time t on counted work
(lookups l, moved m): the count and the sums of the products named."""


def work_sums(
    lookups: np.ndarray, moved: np.ndarray, times_ns: np.ndarray
) -> list[float]:
    """The sums of :data:`SUM_FIELDS` over a set of applications.

    Raises:
        ValueError: If the arrays differ in length.
    """
    if not len(lookups) == len(moved) == len(times_ns):
        raise ValueError("lookups, moved and times must be equally long")
    work_l = lookups.astype(float)
    work_m = moved.astype(float)
    time_t = times_ns.astype(float)
    return [
        float(len(time_t)),
        float(work_l.sum()),
        float(work_m.sum()),
        float((work_l * work_l).sum()),
        float((work_l * work_m).sum()),
        float((work_m * work_m).sum()),
        float(time_t.sum()),
        float((time_t * work_l).sum()),
        float((time_t * work_m).sum()),
    ]


def add_application(
    sums: list[float], lookups: int, moved: int, time_ns: float
) -> None:
    """Add one application to sums laid out as :data:`SUM_FIELDS`."""
    sums[0] += 1
    sums[1] += lookups
    sums[2] += moved
    sums[3] += lookups * lookups
    sums[4] += lookups * moved
    sums[5] += moved * moved
    sums[6] += time_ns
    sums[7] += time_ns * lookups
    sums[8] += time_ns * moved


def fit_sums(sums: Sequence[float]) -> Coefficients:
    """Least-squares fit of application time on counted work from its
    sufficient statistics, with every coefficient kept non-negative (a
    feature whose coefficient comes out negative is dropped and the rest
    refitted).

    Raises:
        ValueError: If there are no applications or the fit predicts no
            positive time.
    """
    count, s_l, s_m, s_ll, s_lm, s_mm, s_t, s_tl, s_tm = sums
    if count <= 0:
        raise ValueError("no applications to fit")
    gram = np.array([[count, s_l, s_m], [s_l, s_ll, s_lm], [s_m, s_lm, s_mm]])
    moment = np.array([s_t, s_tl, s_tm])
    active = [0, 1, 2]
    values = [0.0, 0.0, 0.0]
    while active:
        solution, *_ = np.linalg.lstsq(
            gram[np.ix_(active, active)], moment[active], rcond=None
        )
        negative = [k for k, value in zip(active, solution, strict=True) if value < 0]
        if not negative:
            for k, value in zip(active, solution, strict=True):
                values[k] = float(value)
            break
        active = [k for k in active if k not in negative]
    coefficients = Coefficients(*values)
    if coefficients.intercept <= 0 and coefficients.per_lookup <= 0:
        raise ValueError("cost fit predicts no positive time")
    return coefficients


def fit_coefficients(
    lookups: np.ndarray, moved: np.ndarray, times_ns: np.ndarray
) -> Coefficients:
    """:func:`fit_sums` on the applications given as arrays.

    Raises:
        ValueError: If the arrays differ in length or are empty, or the fit
            predicts no positive time.
    """
    return fit_sums(work_sums(lookups, moved, times_ns))


@dataclass(frozen=True, slots=True)
class CostModel:
    """Per-operator cost coefficients of one instance."""

    instance: str
    operators: Mapping[str, Coefficients]

    def cost_ns(self, operator: str, lookups: int, moved: int) -> float:
        """Predicted time of one application of ``operator``.

        Raises:
            KeyError: If the operator has no fitted coefficients.
            ValueError: If the prediction is not positive.
        """
        c = self.operators[operator]
        cost = c.intercept + c.per_lookup * lookups + c.per_moved * moved
        if cost <= 0:
            raise ValueError(
                f"{self.instance}: non-positive cost {cost} for {operator} "
                f"({lookups} lookups, {moved} moved)"
            )
        return cost

    def to_json(self) -> dict[str, list[float]]:
        """Coefficients as ``{operator: [intercept, per_lookup, per_moved]}``."""
        return {
            name: [c.intercept, c.per_lookup, c.per_moved]
            for name, c in self.operators.items()
        }

    @classmethod
    def from_json(
        cls, instance: str, payload: Mapping[str, Sequence[float]]
    ) -> CostModel:
        """Inverse of :meth:`to_json`."""
        return cls(
            instance, {name: Coefficients(*values) for name, values in payload.items()}
        )


def lk_operator(problem: Problem) -> str:
    """The LK step E1 used on this instance: with alpha candidates if cached."""
    return "lin_kernighan_alpha" if problem.alpha is not None else "lin_kernighan"


def classify(tour: Tour, problem: Problem) -> int:
    """Exact local-optimality class of a tour, as an index into :data:`CLASSES`.

    The same flags and rule as E1 (``aos.e1_marginal.class_flags`` and
    ``aos.analysis.e1_marginal.exact_class``): 2-opt open if O7 or O5
    improves at some city; else 2-opt optimum if O9 or O10 does; else 3-opt
    optimum if the LK step does; else LK optimum.  Checks stop at the first
    improving city, so open tours are cheap.  The tour is not changed.
    """
    rng = random.Random(0)
    scratch = tour.copy()
    cities = range(problem.n)

    def improves(names: Sequence[str]) -> bool:
        return any(
            OPERATORS[name](scratch, problem, rng, Work(), city)[0] < 0
            for name in names
            for city in cities
        )

    if improves(("two_opt_nl", "two_opt_first")):
        return 0
    if improves(("or_opt_nl", "three_opt_nl")):
        return 1
    if improves((lk_operator(problem),)):
        return 2
    return 3


@dataclass(slots=True)
class GreedyByLevel:
    """Continuation policy: the E1 leader of the current class.

    Attributes:
        leaders: Operator name per class index.
        cached_length: Length of the tour last classified.  Operators only
            ever shorten the tour, so an unchanged length means an unchanged
            tour and the class is reused.
        cached_class: Its class.
    """

    leaders: Sequence[str]
    cached_length: float | None = None
    cached_class: int = -1

    def choose(self, tour: Tour, problem: Problem) -> tuple[str, int]:
        """Return the operator for the next slice and the tour's class."""
        if self.cached_length is None or tour.length != self.cached_length:
            self.cached_class = classify(tour, problem)
            self.cached_length = tour.length
        return self.leaders[self.cached_class], self.cached_class


@dataclass(slots=True)
class RolloutResult:
    """What one rollout did.

    Attributes:
        event_ns: Virtual time after each improving application.
        event_length: Tour length after it.
        slice_start_ns: Virtual start time of every slice.
        slice_operator: Operator index (into :data:`OPERATOR_NAMES`).
        slice_class: Class index of the tour at the slice's start (-1 for
            the first slice, which is not chosen by the policy).
        slice_applications: Applications made in the slice.
        end_ns: Virtual time when the rollout stopped.
        final_length: Length of the final tour.
        reached_optimum: Whether it stopped because the optimum was reached.
        real_ns: Measured time of the applications alone (``timed=True``),
            else 0.
        real_by_operator: With ``timed=True``, measured time per operator.
        virtual_by_operator: With ``timed=True``, virtual time per operator.
        sums: With ``timed=True``, per operator, the work and measured time
            of every application as :data:`SUM_FIELDS` (to refit the clock).
    """

    event_ns: list[float] = field(default_factory=list)
    event_length: list[float] = field(default_factory=list)
    slice_start_ns: list[float] = field(default_factory=list)
    slice_operator: list[int] = field(default_factory=list)
    slice_class: list[int] = field(default_factory=list)
    slice_applications: list[int] = field(default_factory=list)
    end_ns: float = 0.0
    final_length: float = 0.0
    reached_optimum: bool = False
    real_ns: float = 0.0
    real_by_operator: dict[str, float] = field(default_factory=dict)
    virtual_by_operator: dict[str, float] = field(default_factory=dict)
    sums: dict[str, list[float]] = field(default_factory=dict)


def _run_slice(
    name: str,
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    model: CostModel,
    clock_ns: float,
    end_ns: float,
    result: RolloutResult,
    timed: bool,
    overhead_ns: float,
) -> tuple[float, int]:
    """Apply ``name`` until the virtual clock passes ``end_ns``; the
    application running then is finished and charged in full.  With
    ``timed``, every application is also timed, less ``overhead_ns`` (the
    timer's own cost, as in E1).

    Returns:
        The new clock and the number of applications.
    """
    operator = OPERATORS[name]
    work = Work()
    applications = 0
    clock = time.perf_counter_ns
    real = 0.0
    virtual = 0.0
    sums = result.sums.setdefault(name, [0.0] * len(SUM_FIELDS)) if timed else []
    while True:
        lookups, moved = work.lookups, work.moved
        if timed:
            started = clock()
            delta = operator(tour, problem, rng, work).delta
            elapsed = clock() - started - overhead_ns
            real += elapsed
            add_application(sums, work.lookups - lookups, work.moved - moved, elapsed)
        else:
            delta = operator(tour, problem, rng, work).delta
        cost = model.cost_ns(name, work.lookups - lookups, work.moved - moved)
        clock_ns += cost
        virtual += cost
        applications += 1
        if timed and clock_ns >= end_ns:
            result.real_ns += real
            result.real_by_operator[name] = (
                result.real_by_operator.get(name, 0.0) + real
            )
            result.virtual_by_operator[name] = (
                result.virtual_by_operator.get(name, 0.0) + virtual
            )
        if delta < 0:
            result.event_ns.append(clock_ns)
            result.event_length.append(tour.length)
        if clock_ns >= end_ns:
            return clock_ns, applications


def rollout(
    problem: Problem,
    order: Sequence[int],
    first: str,
    policy: GreedyByLevel,
    model: CostModel,
    slice_ns: float,
    horizon_ns: float,
    first_rng: random.Random,
    continuation_rng: random.Random,
    timed: bool = False,
    overhead_ns: float = 0.0,
) -> RolloutResult:
    """One slice of ``first`` from ``order``, then ``policy`` until the
    virtual clock reaches ``horizon_ns`` or the tour is optimal.

    Args:
        problem: The instance.
        order: The state tour (not changed).
        first: Operator of the first slice.
        policy: Continuation policy (its class cache is reset).
        model: Virtual clock.
        slice_ns: Length of every slice.
        horizon_ns: Total virtual time, first slice included.
        first_rng: Random stream of the first slice.
        continuation_rng: Random stream of every later slice (the same for
            every first operator, so rollouts are paired).
        timed: Also measure the applications' real time (single-core
            calibration and validation only; the result is otherwise
            identical).
        overhead_ns: Timer cost subtracted from every timed application.

    Raises:
        ValueError: If the slice or horizon is not positive, or the
            operator is unknown.
    """
    if slice_ns <= 0 or horizon_ns < slice_ns:
        raise ValueError(f"need 0 < slice ({slice_ns}) <= horizon ({horizon_ns})")
    if first not in OPERATORS:
        raise ValueError(f"unknown operator {first!r}")
    tour = Tour.from_order(list(order), problem)
    optimum = problem.optimum
    policy.cached_length = None
    result = RolloutResult()
    clock_ns = 0.0
    name = first
    category = -1
    rng = first_rng
    while True:
        result.slice_start_ns.append(clock_ns)
        result.slice_operator.append(OPERATOR_NAMES.index(name))
        result.slice_class.append(category)
        clock_ns, applications = _run_slice(
            name,
            tour,
            problem,
            rng,
            model,
            clock_ns,
            clock_ns + slice_ns,
            result,
            timed,
            overhead_ns,
        )
        result.slice_applications.append(applications)
        if optimum is not None and tour.length <= optimum + EPSILON:
            result.reached_optimum = True
            break
        if clock_ns >= horizon_ns:
            break
        name, category = policy.choose(tour, problem)
        rng = continuation_rng
    result.end_ns = clock_ns
    result.final_length = tour.length
    tour.check(problem)
    return result


def length_at(
    initial: float, event_ns: np.ndarray, event_length: np.ndarray, at_ns: float
) -> float:
    """Tour length at virtual time ``at_ns``: the length after the last
    improvement at or before it, else ``initial``.  ``event_ns`` must be
    sorted."""
    index = int(np.searchsorted(event_ns, at_ns, side="right")) - 1
    return initial if index < 0 else float(event_length[index])
