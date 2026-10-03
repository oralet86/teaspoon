"""Best-known-solution-relative anytime metrics.

All functions work on the wall-clock trace of a solver run: a sequence of
:class:`~solvers.base.TracePoint` observations, each holding the best cost
found up to that moment.  The trace is sorted internally, so callers do not
need to guarantee ordering.
"""

from __future__ import annotations

from collections.abc import Sequence

from solvers import TracePoint


def best_cost_at(trace: Sequence[TracePoint], seconds: float) -> float | None:
    """Return the best cost observed at or before ``seconds``.

    Returns:
        The best cost, or ``None`` when ``seconds`` precedes the first
        observation.

    Raises:
        ValueError: If the trace is empty.
    """
    ordered = _ordered(trace)
    best: float | None = None
    for point in ordered:
        if point.wall_seconds > seconds:
            break
        best = point.best_cost
    return best


def gap_at(trace: Sequence[TracePoint], bks: float, seconds: float) -> float | None:
    """Return the optimality gap of the best solution at ``seconds``.

    The gap is ``(best_cost - bks) / bks``, matching the convention used in
    the benchmark protocol.

    Raises:
        ValueError: If the trace is empty.
    """
    cost = best_cost_at(trace, seconds)
    if cost is None:
        return None
    return (cost - bks) / bks


def time_to_target(
    trace: Sequence[TracePoint],
    bks: float,
    target_gap: float,
    *,
    budget_seconds: float,
) -> float | None:
    """Return the first time the gap reaches ``target_gap``.

    Args:
        trace: Best-cost observations of one run.
        bks: Best-known solution cost.
        target_gap: Target relative gap, e.g. ``0.01`` for one percent.
        budget_seconds: Runs that never reach the target inside the budget
            are right-censored.

    Returns:
        The first wall-clock time the target is reached, or ``None`` when
        the run is censored.

    Raises:
        ValueError: If the trace is empty or ``target_gap`` is negative.
    """
    if target_gap < 0:
        raise ValueError(f"target_gap must be non-negative, got {target_gap}")
    ordered = _ordered(trace)
    for point in ordered:
        if point.wall_seconds > budget_seconds:
            break
        if (point.best_cost - bks) / bks <= target_gap:
            return point.wall_seconds
    return None


def primal_integral(
    trace: Sequence[TracePoint],
    bks: float,
    *,
    budget_seconds: float,
    normalize: bool = True,
) -> float:
    """Integrate the optimality gap over the run budget.

    The gap is treated as a step function: between two observations it stays
    at the earlier value, and the unknown period before the first
    observation is charged at the first observed gap.

    Args:
        trace: Best-cost observations of one run.
        bks: Best-known solution cost.
        budget_seconds: Integration horizon; observations past it are
            ignored.
        normalize: Divide the integral by the budget so runs with different
            budgets stay comparable.

    Returns:
        The primal integral, normalized by default.

    Raises:
        ValueError: If the trace is empty or the budget is not positive.
    """
    if budget_seconds <= 0:
        raise ValueError(f"budget_seconds must be positive, got {budget_seconds}")
    ordered = _ordered(trace)
    events: list[tuple[float, float]] = [(0.0, ordered[0].best_cost)]
    for point in ordered[1:]:
        if point.wall_seconds >= budget_seconds:
            break
        events.append((point.wall_seconds, point.best_cost))
    total = 0.0
    for index, (start, cost) in enumerate(events):
        end = events[index + 1][0] if index + 1 < len(events) else budget_seconds
        total += (end - start) * ((cost - bks) / bks)
    if normalize:
        return total / budget_seconds
    return total


def _ordered(trace: Sequence[TracePoint]) -> tuple[TracePoint, ...]:
    """Return the trace sorted by wall-clock time.

    Raises:
        ValueError: If the trace is empty.
    """
    if not trace:
        raise ValueError("the trace is empty")
    return tuple(sorted(trace, key=lambda point: point.wall_seconds))
