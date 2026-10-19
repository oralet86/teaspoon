"""Marginal rates: what one more application of each operator gains, and
costs, from a fixed tour.

Every operator is applied ``repeats`` times at every city as anchor, each
application starting from the same tour, and each application timed on its
own.  An application that improves changes its working copy, which is then
restored (untimed); one that does not improve leaves the tour unchanged (see
:class:`aos.operators.Operator`), so nothing is copied for it.

The rate of an operator in a state is mean gain over mean time over all its
applications (equal counts for every operator, so this is also the ratio of
sums).  For the operators that are deterministic once the anchor is fixed,
enumerating every anchor gives that rate exactly, up to timing noise.

E1 measures in two passes, because only timing needs a core of its own
(parallel timed workers slow the operators unevenly, see E0):

1. :func:`measure_gains`, untimed and safe to run on every CPU at once:
   every application's gain, work and resulting tour.  Deterministic
   operators run once per anchor, the others ``repeats`` times.
2. :func:`measure_times`, on one pinned core: rounds that replay every
   (operator, anchor) from its own random stream
   (:func:`application_rng`) and time it, until the round means agree
   (:class:`TimingSettings`), with the garbage collector off.  Every
   replay must reproduce its pass-1 gain and
   tour, else :class:`ReplayError`.

:func:`summarise_passes` combines them.  :func:`measure_marginal` (gain
and time from the same applications) remains for E0's contention test.
"""

from __future__ import annotations

import gc
import math
import random
import statistics
import time
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .operators import DETERMINISTIC, OPERATORS
from .problem import Problem
from .tour import Tour
from .work import Work

COLUMNS = ("anchor", "repeat", "gain", "time_ns", "lookups", "moved")
"""Per-application columns of a marginal measurement."""

GAIN_COLUMNS = ("anchor", "repeat", "gain", "lookups", "moved", "tour_crc", "wall_ns")
"""Per-application columns of pass 1.  ``tour_crc`` identifies the improved
tour (0 when the application does not improve); ``wall_ns`` is untimed-
quality wall time, used only to size budgets."""

TIME_COLUMNS = ("anchor", "round", "repeat", "time_ns")
"""Per-application columns of pass 2."""

T_975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228)
"""Two-sided 95% Student-t quantiles for 1 to 10 degrees of freedom."""


class ReplayError(ValueError):
    """Raised when a timed replay does not reproduce its pass-1 result."""


def clock_overhead_ns(samples: int = 20_000) -> int:
    """Return the median time between two back-to-back clock reads.

    This is the timer's own cost inside every timed application; it is
    subtracted from application times (and stored next to them).
    """
    clock = time.perf_counter_ns
    gaps = []
    for _ in range(samples):
        started = clock()
        gaps.append(clock() - started)
    return int(np.median(gaps))


@dataclass(frozen=True, slots=True)
class MarginalSettings:
    """How a state is measured.

    Attributes:
        repeats: Applications per (operator, anchor); the same for every
            operator.
        seed: Seed of the operators' random streams and of the visiting
            order.
    """

    repeats: int
    seed: int


def measure_marginal(
    problem: Problem,
    state: Tour,
    names: Sequence[str],
    settings: MarginalSettings,
    overhead_ns: int,
    label: str,
    anchors: Sequence[int] | None = None,
) -> dict[str, dict[str, np.ndarray]]:
    """Apply every operator ``settings.repeats`` times at every anchor (or at
    every city of ``anchors``), each time from ``state``, and time each
    application.

    The visiting order is interleaved to spread drift in machine speed over
    all operators: for each repeat, anchors in a shuffled order, and at each
    anchor the operators in a shuffled order.

    Args:
        problem: The instance.
        state: The tour to measure from; not modified.
        names: Operator names (keys of :data:`aos.operators.OPERATORS`).
        settings: Repeats and seed.
        overhead_ns: Clock overhead subtracted from every application time.
        label: Identifies the state in the random streams (for example
            ``"kroA100:s3:gap05"``), so each state gets its own streams.
        anchors: The anchors to use, the same for every operator; all cities
            when omitted.

    Returns:
        Per operator, the columns of :data:`COLUMNS`, one row per
        application, ``time_ns`` already corrected for the clock overhead.

    Raises:
        ValueError: If ``repeats`` is not positive, or an operator lengthens
            the tour or changes it without improving.
    """
    if settings.repeats <= 0:
        raise ValueError(f"repeats must be positive, got {settings.repeats}")
    n = problem.n
    order_rng = random.Random(f"marginal-order:{label}:{settings.seed}")
    rngs = {
        name: random.Random(f"marginal:{label}:{name}:{settings.seed}")
        for name in names
    }
    operators = [(name, OPERATORS[name]) for name in names]
    columns: dict[str, dict[str, list[float]]] = {
        name: {column: [] for column in COLUMNS} for name in names
    }
    working = state.copy()
    length = state.length
    clock = time.perf_counter_ns
    anchors = list(range(n)) if anchors is None else list(anchors)
    for repeat in range(settings.repeats):
        order_rng.shuffle(anchors)
        for anchor in anchors:
            order_rng.shuffle(operators)
            for name, operator in operators:
                work = Work()
                rng = rngs[name]
                started = clock()
                delta, _ = operator(working, problem, rng, work, anchor)
                elapsed = clock() - started
                if delta > 0:
                    raise ValueError(f"{name} lengthened the tour by {delta}")
                if delta < 0:
                    working = state.copy()
                elif working.length != length:
                    raise ValueError(f"{name} changed the tour without improving")
                cell = columns[name]
                cell["anchor"].append(anchor)
                cell["repeat"].append(repeat)
                cell["gain"].append(-delta)
                cell["time_ns"].append(elapsed - overhead_ns)
                cell["lookups"].append(work.lookups)
                cell["moved"].append(work.moved)
    return {
        name: {
            column: np.asarray(values, dtype=float if column == "gain" else np.int64)
            for column, values in cell.items()
        }
        for name, cell in columns.items()
    }


def application_rng(
    label: str, name: str, anchor: int, repeat: int, seed: int
) -> random.Random:
    """Return the random stream of one application.

    One stream per (state, operator, anchor, repeat), so any application can
    be replayed on its own, in any order, in either pass.
    """
    return random.Random(f"marginal:{label}:{name}:{anchor}:{repeat}:{seed}")


def gain_repeats(name: str, repeats: int) -> int:
    """Return how often pass 1 applies ``name`` per anchor: once when the
    operator is deterministic given the anchor, else ``repeats`` times."""
    return 1 if name in DETERMINISTIC else repeats


def tour_crc(tour: Tour) -> int:
    """Return a CRC32 of the tour's visiting order."""
    return zlib.crc32(np.asarray(tour.order, dtype=np.int32).tobytes())


def measure_gains(
    problem: Problem,
    state: Tour,
    names: Sequence[str],
    settings: MarginalSettings,
    label: str,
    anchors: Sequence[int] | None = None,
) -> dict[str, dict[str, np.ndarray]]:
    """Pass 1: apply every operator at every anchor from ``state``, untimed.

    Deterministic operators run once per anchor, the others
    ``settings.repeats`` times, each from its own :func:`application_rng`
    stream.  Nothing here is timed precisely, so this may run on every CPU
    at once.

    Args:
        problem: The instance.
        state: The tour to measure from; not modified.
        names: Operator names (keys of :data:`aos.operators.OPERATORS`).
        settings: Repeats of the random operators, and seed.
        label: Identifies the state in the random streams.
        anchors: The anchors to use; all cities when omitted.

    Returns:
        Per operator, the columns of :data:`GAIN_COLUMNS`, one row per
        application.

    Raises:
        ValueError: If ``repeats`` is not positive, or an operator lengthens
            the tour or changes it without improving.
    """
    if settings.repeats <= 0:
        raise ValueError(f"repeats must be positive, got {settings.repeats}")
    anchors = list(range(problem.n)) if anchors is None else list(anchors)
    working = state.copy()
    length = state.length
    clock = time.perf_counter_ns
    result = {}
    for name in names:
        operator = OPERATORS[name]
        cell: dict[str, list[float]] = {column: [] for column in GAIN_COLUMNS}
        for anchor in anchors:
            for repeat in range(gain_repeats(name, settings.repeats)):
                rng = application_rng(label, name, anchor, repeat, settings.seed)
                work = Work()
                started = clock()
                delta, _ = operator(working, problem, rng, work, anchor)
                elapsed = clock() - started
                crc = 0
                if delta > 0:
                    raise ValueError(f"{name} lengthened the tour by {delta}")
                if delta < 0:
                    crc = tour_crc(working)
                    working = state.copy()
                elif working.length != length:
                    raise ValueError(f"{name} changed the tour without improving")
                cell["anchor"].append(anchor)
                cell["repeat"].append(repeat)
                cell["gain"].append(-delta)
                cell["lookups"].append(work.lookups)
                cell["moved"].append(work.moved)
                cell["tour_crc"].append(crc)
                cell["wall_ns"].append(elapsed)
        result[name] = {
            column: np.asarray(values, dtype=float if column == "gain" else np.int64)
            for column, values in cell.items()
        }
    return result


@dataclass(frozen=True, slots=True)
class TimingSettings:
    """When pass 2 stops timing a state.

    Attributes:
        min_rounds: Rounds always timed (at least 2, to see the spread).
        max_rounds: Rounds at most.
        precision: Stop once, for every deterministic operator, the 95%
            interval of the mean over round means is within this fraction
            of it.  Their rounds replay identical work, so the spread is
            timing noise alone.  A random operator replays other
            applications each round; its spread is mostly its own
            randomness, which the rate's bootstrap interval carries, so it
            does not hold up the stop (all operators count when none is
            deterministic).
        seconds: Stop after this much wall time (checked after a round, and
            only once ``min_rounds`` are done); ``None`` for no cap.
    """

    min_rounds: int = 2
    max_rounds: int = 8
    precision: float = 0.05
    seconds: float | None = None


def precise(round_means: Sequence[float], precision: float) -> bool:
    """Whether the 95% t-interval of the mean of ``round_means`` lies within
    ``precision`` times that mean."""
    count = len(round_means)
    if count < 2:
        return False
    mean = statistics.fmean(round_means)
    spread = statistics.stdev(round_means)
    quantile = T_975[min(count - 1, len(T_975)) - 1]
    return quantile * spread / math.sqrt(count) <= precision * abs(mean)


def measure_times(
    problem: Problem,
    state: Tour,
    gains: dict[str, dict[str, np.ndarray]],
    settings: MarginalSettings,
    timing: TimingSettings,
    overhead_ns: int,
    label: str,
) -> tuple[dict[str, dict[str, np.ndarray]], str]:
    """Pass 2: replay pass 1 in rounds on this core and time each application.

    Round ``r`` replays every (operator, anchor) of ``gains`` once, with
    the stream of repeat ``r mod R`` (``R`` = the operator's pass-1 repeats),
    anchors shuffled per round and operators per anchor.  Each replay must
    reproduce its pass-1 gain, and its tour when it improves.

    Python's garbage collector is off while timing and runs, untimed,
    before every round (the convention of :mod:`timeit`).  With it on, a
    collection runs inside whichever application crosses the allocation
    threshold, counting the harness's allocations too, and costs whatever
    the heap holds: at n = 500 that was about a third of
    ``string_removal``'s measured time and 8-15% of LK's, with identical
    replays differing by up to 10% between rounds (2026-10-18).  Freezing
    the heap (:func:`gc.freeze`) removed the full collections but not the
    young ones.  Times therefore leave out all collector work, including an
    operator's own garbage.

    Args:
        problem: The instance.
        state: The tour pass 1 measured from; not modified.
        gains: Pass-1 result of :func:`measure_gains` for this state.
        settings: The repeats and seed pass 1 used.
        timing: When to stop.
        overhead_ns: Clock overhead subtracted from every application time.
        label: The label pass 1 used.

    Returns:
        Per operator, the columns of :data:`TIME_COLUMNS`, and the stop
        reason: ``"precision"``, ``"budget"`` or ``"max_rounds"``.

    Raises:
        ValueError: If the rounds settings are invalid or the operators do
            not share one anchor set.
        ReplayError: If a replay does not reproduce pass 1.
    """
    if not 2 <= timing.min_rounds <= timing.max_rounds:
        raise ValueError(
            f"need 2 <= min_rounds <= max_rounds, got {timing.min_rounds}, "
            f"{timing.max_rounds}"
        )
    names = list(gains)
    anchors = sorted({int(anchor) for anchor in gains[names[0]]["anchor"]})
    expected: dict[str, dict[tuple[int, int], tuple[float, int]]] = {}
    for name in names:
        arrays = gains[name]
        if sorted({int(anchor) for anchor in arrays["anchor"]}) != anchors:
            raise ValueError(f"{name} has other anchors than {names[0]}")
        expected[name] = {
            (int(anchor), int(repeat)): (float(gain), int(crc))
            for anchor, repeat, gain, crc in zip(
                arrays["anchor"],
                arrays["repeat"],
                arrays["gain"],
                arrays["tour_crc"],
                strict=True,
            )
        }
    repeats_of = {name: gain_repeats(name, settings.repeats) for name in names}
    operators = [(name, OPERATORS[name]) for name in names]
    columns: dict[str, dict[str, list[int]]] = {
        name: {column: [] for column in TIME_COLUMNS} for name in names
    }
    round_means: dict[str, list[float]] = {name: [] for name in names}
    judged = [name for name in names if name in DETERMINISTIC] or names
    order_rng = random.Random(f"marginal-order:{label}:{settings.seed}")
    working = state.copy()
    length = state.length
    clock = time.perf_counter_ns
    seed = settings.seed
    started_all = time.monotonic()
    reason = "max_rounds"
    collector_was_on = gc.isenabled()
    gc.disable()
    try:
        for round_index in range(timing.max_rounds):
            gc.collect()
            sums = dict.fromkeys(names, 0)
            order_rng.shuffle(anchors)
            for anchor in anchors:
                order_rng.shuffle(operators)
                for name, operator in operators:
                    repeat = round_index % repeats_of[name]
                    rng = application_rng(label, name, anchor, repeat, seed)
                    work = Work()
                    started = clock()
                    delta, _ = operator(working, problem, rng, work, anchor)
                    elapsed = clock() - started - overhead_ns
                    gain, crc = expected[name][(anchor, repeat)]
                    if -delta != gain:
                        raise ReplayError(
                            f"{label}: {name} at anchor {anchor}, repeat {repeat} "
                            f"gained {-delta}, pass 1 gained {gain}"
                        )
                    if delta < 0:
                        if tour_crc(working) != crc:
                            raise ReplayError(
                                f"{label}: {name} at anchor {anchor}, repeat {repeat} "
                                "reached another tour than in pass 1"
                            )
                        working = state.copy()
                    elif working.length != length:
                        raise ValueError(f"{name} changed the tour without improving")
                    cell = columns[name]
                    cell["anchor"].append(anchor)
                    cell["round"].append(round_index)
                    cell["repeat"].append(repeat)
                    cell["time_ns"].append(elapsed)
                    sums[name] += elapsed
            for name in names:
                round_means[name].append(sums[name] / len(anchors))
            if round_index + 1 < timing.min_rounds:
                continue
            if all(precise(round_means[name], timing.precision) for name in judged):
                reason = "precision"
                break
            if (
                timing.seconds is not None
                and time.monotonic() - started_all >= timing.seconds
            ):
                reason = "budget"
                break
    finally:
        if collector_was_on:
            gc.enable()
    return {
        name: {
            column: np.asarray(values, dtype=np.int64)
            for column, values in cell.items()
        }
        for name, cell in columns.items()
    }, reason


@dataclass(frozen=True, slots=True)
class RateSummary:
    """Marginal rate of one operator in one state.

    Attributes:
        rate: Mean gain over mean time (length units per second).
        low: Lower bound of the 95% bootstrap interval.
        high: Upper bound of the 95% bootstrap interval.
        mean_gain: Mean gain per application.
        mean_time_ns: Mean time per application (overhead-corrected).
        p_improve: Fraction of applications that improved.
        improving_anchors: Number of anchors with at least one improving
            application.
    """

    rate: float
    low: float
    high: float
    mean_gain: float
    mean_time_ns: float
    p_improve: float
    improving_anchors: int


def _grid(
    arrays: dict[str, np.ndarray], value: str, within: str, anchors: np.ndarray
) -> np.ndarray:
    """Arrange ``arrays[value]`` as one row per anchor (in ``anchors`` order)
    and one column per ``within`` index (repeat or round).

    Raises:
        ValueError: If the anchors differ from ``anchors`` or do not all have
            the same number of entries.
    """
    visited, index = np.unique(arrays["anchor"], return_inverse=True)
    if not np.array_equal(visited, anchors):
        raise ValueError("pass 1 and pass 2 cover different anchors")
    counts = np.bincount(index, minlength=len(visited))
    if np.any(counts != counts[0]):
        raise ValueError(f"every anchor needs the same number of {within}s")
    order = np.lexsort((arrays[within], index))
    return arrays[value][order].astype(float).reshape(len(visited), int(counts[0]))


def summarise_passes(
    gains: dict[str, np.ndarray],
    times: dict[str, np.ndarray],
    n: int,
    resamples: int,
    rng: np.random.Generator,
    sampled: bool = False,
) -> RateSummary:
    """Summarise one operator in one state from its two passes.

    The rate is the mean over anchors of the per-anchor mean gain (pass 1)
    over the mean over anchors of the per-anchor mean time (pass 2).  With
    the same number of applications at every anchor, as both passes make,
    this is mean gain over mean time.

    When the anchors are a random sample (``sampled``), anchors are
    resampled with their gains and times together (a cluster bootstrap).
    When every city is an anchor, anchors stay fixed, and within each anchor
    the gain repeats and the timed rounds are resampled separately (a
    stratified bootstrap); a deterministic operator's single gain per anchor
    is then exact, and only timing noise remains.

    Args:
        gains: The operator's pass-1 columns (``anchor``, ``repeat``,
            ``gain``).
        times: The operator's pass-2 columns (``anchor``, ``round``,
            ``time_ns``).
        n: Number of cities.
        resamples: Bootstrap resamples.
        rng: Random generator for the bootstrap.
        sampled: Whether the anchors are a sample rather than all cities.

    Raises:
        ValueError: If the passes cover different anchors, or an anchor has
            a different number of repeats or rounds than the others.
    """
    anchors = np.unique(gains["anchor"])
    count = len(anchors)
    if count == 0 or count > n:
        raise ValueError(f"{count} distinct anchors for {n} cities")
    gain_grid = _grid(gains, "gain", "repeat", anchors)
    time_grid = _grid(times, "time_ns", "round", anchors)
    gain_means = gain_grid.mean(axis=1)
    time_means = time_grid.mean(axis=1)
    rate = gain_means.mean() / (time_means.mean() / 1e9)
    if sampled:
        picks = rng.integers(0, count, size=(resamples, count))
        rates = gain_means[picks].mean(axis=1) / (time_means[picks].mean(axis=1) / 1e9)
    else:
        rates = _stratified_means(gain_grid, resamples, rng) / (
            _stratified_means(time_grid, resamples, rng) / 1e9
        )
    low, high = np.quantile(rates, [0.025, 0.975])
    return RateSummary(
        rate=float(rate),
        low=float(low),
        high=float(high),
        mean_gain=float(gain_means.mean()),
        mean_time_ns=float(time_means.mean()),
        p_improve=float(np.mean(gain_grid > 0)),
        improving_anchors=int(np.count_nonzero((gain_grid > 0).any(axis=1))),
    )


def _stratified_means(
    grid: np.ndarray, resamples: int, rng: np.random.Generator
) -> np.ndarray:
    """Bootstrap means of ``grid`` with rows fixed and columns resampled
    within each row; one mean per resample."""
    count, width = grid.shape
    if width == 1:
        return np.full(resamples, grid.mean())
    picks = rng.integers(0, width, size=(resamples, count, width))
    rows = np.arange(count)[None, :, None]
    return grid[rows, picks].mean(axis=(1, 2))
