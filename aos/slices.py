"""Time slices: one operator applied repeatedly to one tour.

A slice is bounded either by wall-clock time or by a number of applications.
Time-bounded slices are what the experiments measure; count-bounded slices
make runs reproducible (tests, replays of logged runs, and the
evaluation-budget twin runs).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .operators import Operator
from .problem import Problem
from .tour import Tour
from .work import Work


@dataclass(frozen=True, slots=True)
class SliceRecord:
    """What one slice did.

    Attributes:
        duration_ns: Wall-clock time of the slice, including any overshoot.
        overshoot_ns: Time past the deadline taken by the last application
            (zero in count mode).
        applications: Operator applications made.
        improving: Applications that shortened the tour.
        gain: Total length reduction (non-negative).
        lookups: Distance lookups (counted work).
        moved: Tour positions rewritten (counted work).
        anchor: Anchor city of the last improving application, or -1.
    """

    duration_ns: int
    overshoot_ns: int
    applications: int
    improving: int
    gain: float
    lookups: int
    moved: int
    anchor: int


def run_slice(
    operator: Operator,
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    *,
    seconds: float | None = None,
    applications: int | None = None,
) -> SliceRecord:
    """Apply ``operator`` to ``tour`` until the time or the count is used up.

    Exactly one of ``seconds`` and ``applications`` must be given.  In time
    mode at least one application is always made, and an application running
    when the deadline passes is finished; the excess is reported as
    ``overshoot_ns``.

    Raises:
        ValueError: If neither or both limits are given, or a limit is not
            positive.
    """
    if (seconds is None) == (applications is None):
        raise ValueError("give exactly one of seconds and applications")
    work = Work()
    count = 0
    improving = 0
    anchor = -1
    length_before = tour.length
    clock = time.perf_counter_ns
    started = clock()
    if applications is not None:
        if applications <= 0:
            raise ValueError(f"applications must be positive, got {applications}")
        for _ in range(applications):
            delta, city = operator(tour, problem, rng, work)
            if delta < 0:
                improving += 1
                anchor = city
        count = applications
        finished = clock()
        overshoot = 0
    else:
        assert seconds is not None
        if seconds <= 0:
            raise ValueError(f"seconds must be positive, got {seconds}")
        deadline = started + int(seconds * 1e9)
        while True:
            delta, city = operator(tour, problem, rng, work)
            count += 1
            if delta < 0:
                improving += 1
                anchor = city
            finished = clock()
            if finished >= deadline:
                break
        overshoot = finished - deadline
    return SliceRecord(
        duration_ns=finished - started,
        overshoot_ns=overshoot,
        applications=count,
        improving=improving,
        gain=length_before - tour.length,
        lookups=work.lookups,
        moved=work.moved,
        anchor=anchor,
    )
