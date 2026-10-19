"""The operators O1-O20 of the Claim 1 protocol, in scalar Python.

Each operator makes one *application* to a tour in place, starting from an
anchor city (given, or drawn from the operator's random stream), and returns
the length change it made and the anchor (:class:`Application`).  The change
is zero or negative: the moves (O1-O7, O9, O10, O14-O18, O20) only apply an
improving move, and the trials (O8, O11-O13, O19) keep their result only if
the tour got shorter, so a non-improving application leaves the tour exactly
as it was.  E1 measures single applications (:mod:`aos.marginal`); the
population runs repeat them in time slices (:mod:`aos.slices`).

O1-O4 are random moves at the anchor and O5-O6 full scans; the others search
the anchor's neighbour list (the k nearest cities,
:class:`aos.problem.Problem`) or, for O17 and O18, its alpha-nearness
candidates.

| ID | Name | Move |
|----|------|------|
| O1 | ``swap`` | exchange the anchor with a city at a random position |
| O2 | ``insertion`` | move the anchor to a random edge |
| O3 | ``inversion`` | reverse a random segment from the anchor (random 2-opt) |
| O4 | ``or_opt`` | move the 1-3 cities at the anchor to a random edge |
| O5 | ``two_opt_first`` | first improving 2-opt move at the anchor, full scan |
| O6 | ``two_opt_best`` | best improving 2-opt move at the anchor, full scan |
| O7 | ``two_opt_nl`` | first improving 2-opt move at the anchor, neighbour list |
| O8 | ``kick`` | segment exchange (3 edges) at the anchor's edge, then O7 descent |
| O9 | ``or_opt_nl`` | first improving Or-opt move at the anchor, neighbour list |
| O10 | ``three_opt_nl`` | first improving sequential 3-opt move at the anchor |
| O11 | ``ruin_recreate`` | remove the anchor and its nearest neighbours, reinsert |
| O12 | ``double_bridge`` | double bridge at the anchor and 3 random cuts, then O7 |
| O13 | ``double_bridge_local`` | double bridge at the anchor and 3 neighbours, O7 |
| O14 | ``two_opt_descent`` | O7 descent with a work queue, started at the anchor |
| O15 | ``two_opt_targeted`` | O7 at the worst-edge city of a tournament of 8 |
| O16 | ``lin_kernighan`` | Lin-Kernighan step of up to 25 chained 2-opt moves |
| O17 | ``two_opt_alpha`` | O7 over 5 alpha-nearness candidates (LKH's candidate set) |
| O18 | ``lin_kernighan_alpha`` | O16 over 5 alpha-nearness candidates |
| O19 | ``string_removal`` | SISR string removal (1-3 strings) and blink recreate |
| O20 | ``segment_exchange`` | swap 1-3-city segments at the anchor and a neighbour |

Counted work (:class:`aos.work.Work`) records distance lookups and tour
positions rewritten for each application.
"""

from __future__ import annotations

import math
import random
from collections import deque
from collections.abc import Iterable, Mapping
from typing import NamedTuple, Protocol

from .problem import Problem
from .tour import Tour
from .work import Work

EPSILON = 1e-9
"""Improvements smaller than this are ignored (guards float round-off)."""


class Application(NamedTuple):
    """Outcome of one operator application.

    Attributes:
        delta: Change in tour length; negative if the tour improved, zero if
            nothing was applied.
        anchor: The city the move was centred on, or -1.
    """

    delta: float
    anchor: int


class Operator(Protocol):
    """One operator application: ``(tour, problem, rng, work, anchor)``.

    Changes ``tour`` in place, and only when the application improves it, so
    a non-improving application leaves the tour exactly as it was.  ``anchor``
    is the city the move starts from; when omitted, a uniformly random city
    is drawn from ``rng`` first.  Operators that are deterministic once the
    anchor is fixed (O5, O6, O7, O9, O10) do not use ``rng`` further.
    """

    def __call__(
        self,
        tour: Tour,
        problem: Problem,
        rng: random.Random,
        work: Work,
        anchor: int | None = None,
    ) -> Application:
        """Apply the operator once and return what it did."""
        ...


def pick_anchor(tour: Tour, rng: random.Random, anchor: int | None) -> int:
    """Return ``anchor``, or a uniformly random city if it is ``None``."""
    if anchor is None:
        return tour.order[rng.randrange(len(tour.order))]
    return anchor


DETERMINISTIC = frozenset(
    {
        "two_opt_first",
        "two_opt_best",
        "two_opt_nl",
        "or_opt_nl",
        "three_opt_nl",
        "two_opt_descent",
        "lin_kernighan",
        "two_opt_alpha",
        "lin_kernighan_alpha",
        "segment_exchange",
    }
)
"""Operators whose application is fully determined by the anchor."""


def swap(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O1: exchange the anchor with a city at a random other position if that
    shortens the tour."""
    order = tour.order
    dist = problem.dist
    n = len(order)
    first = tour.pos[pick_anchor(tour, rng, anchor)]
    second = rng.randrange(n - 1)
    if second >= first:
        second += 1
    # Edge k joins positions k and k + 1; a set so neighbouring positions do
    # not count their shared edge twice.
    edges = {(first - 1) % n, first, (second - 1) % n, second}
    before = 0
    for k in edges:
        before += dist[order[k]][order[(k + 1) % n]]
    city_first = order[first]
    city_second = order[second]
    order[first] = city_second
    order[second] = city_first
    after = 0
    for k in edges:
        after += dist[order[k]][order[(k + 1) % n]]
    work.lookups += 2 * len(edges)
    delta = after - before
    if delta < -EPSILON:
        tour.pos[city_first] = second
        tour.pos[city_second] = first
        tour.length += delta
        work.moved += 2
        return Application(delta, city_first)
    order[first] = city_first
    order[second] = city_second
    return Application(0.0, city_first)


def insertion(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O2: move the anchor between two other adjacent cities, chosen at
    random, if shorter."""
    order = tour.order
    dist = problem.dist
    n = len(order)
    source = tour.pos[pick_anchor(tour, rng, anchor)]
    city = order[source]
    before_city = order[source - 1]
    after_city = order[(source + 1) % n]
    # Target edge (target, target + 1), excluding the two edges at the city.
    target = (source + 1 + rng.randrange(n - 2)) % n
    left = order[target]
    right = order[(target + 1) % n]
    removal = (
        dist[before_city][city] + dist[city][after_city] - dist[before_city][after_city]
    )
    addition = dist[left][city] + dist[city][right] - dist[left][right]
    work.lookups += 6
    delta = addition - removal
    if delta >= -EPSILON:
        return Application(0.0, city)
    order.pop(source)
    left_index = target if target < source else target - 1
    order.insert(left_index + 1, city)
    low = min(source, left_index + 1)
    high = max(source, left_index + 1)
    pos = tour.pos
    for index in range(low, high + 1):
        pos[order[index]] = index
    tour.length += delta
    work.moved += high - low + 1
    return Application(delta, city)


def inversion(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O3: reverse a segment of random length starting at the anchor (a random
    2-opt move) if that shortens the tour."""
    order = tour.order
    dist = problem.dist
    n = len(order)
    start = tour.pos[pick_anchor(tour, rng, anchor)]
    # Segments of length 1 or n - 1 leave the cycle unchanged.
    end = (start + rng.randrange(2, n - 1) - 1) % n
    before_start = order[start - 1]
    first = order[start]
    last = order[end]
    after_end = order[(end + 1) % n]
    delta = (
        dist[before_start][last]
        + dist[first][after_end]
        - dist[before_start][first]
        - dist[last][after_end]
    )
    work.lookups += 4
    if delta >= -EPSILON:
        return Application(0.0, first)
    work.moved += tour.reverse(start, end)
    tour.length += delta
    return Application(delta, first)


def or_opt(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O4: move the segment of 1-3 cities (size random) that starts at the
    anchor to a random edge, in its better orientation, if that shortens the
    tour.

    The target edge is any edge not touching the segment or its two tour
    neighbours; moving the segment by one city is an insertion of that city
    (O2).
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    n = len(order)
    first = pick_anchor(tour, rng, anchor)
    size = rng.randint(1, 3)
    place = pos[first]
    last = order[(place + size - 1) % n]
    outside = order[place - 1]
    after = order[(place + size) % n]
    target = place + size + 1 + rng.randrange(n - size - 3)
    left = order[target % n]
    right = order[(target + 1) % n]
    removal = dist[outside][first] + dist[last][after] - dist[outside][after]
    forward = dist[left][first] + dist[last][right] - dist[left][right]
    backward = dist[left][last] + dist[first][right] - dist[left][right]
    work.lookups += 8
    reverse = backward < forward
    delta = (backward if reverse else forward) - removal
    if delta >= -EPSILON:
        return Application(0.0, first)
    if reverse:
        # left, last .. first, right: ``first`` sits next to ``right``.
        moved = insert_segment(tour, outside, first, last, after, right, left, size)
    else:
        moved = insert_segment(tour, outside, first, last, after, left, right, size)
    tour.length += delta
    work.moved += moved
    return Application(delta, first)


def two_opt_first(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O5: from the anchor edge (anchor, successor), scan the tour and apply
    the first improving 2-opt move.  Costs O(n) when nothing improves.
    """
    order = tour.order
    dist = problem.dist
    n = len(order)
    city_a = pick_anchor(tour, rng, anchor)
    anchor = tour.pos[city_a]
    city_b = order[(anchor + 1) % n]
    row_a = dist[city_a]
    row_b = dist[city_b]
    base = row_a[city_b]
    for step in range(2, n - 1):
        index = (anchor + step) % n
        city_c = order[index]
        city_d = order[(index + 1) % n]
        delta = row_a[city_c] + row_b[city_d] - base - dist[city_c][city_d]
        if delta < -EPSILON:
            work.lookups += 3 * (step - 1) + 1
            work.moved += tour.reverse((anchor + 1) % n, index)
            tour.length += delta
            return Application(delta, city_a)
    work.lookups += 3 * (n - 3) + 1
    return Application(0.0, city_a)


def two_opt_best(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O6: from the anchor edge (anchor, successor), scan the whole tour and
    apply the best improving 2-opt move.  Always costs O(n).
    """
    order = tour.order
    dist = problem.dist
    n = len(order)
    city_a = pick_anchor(tour, rng, anchor)
    anchor = tour.pos[city_a]
    city_b = order[(anchor + 1) % n]
    row_a = dist[city_a]
    row_b = dist[city_b]
    base = row_a[city_b]
    best_delta = -EPSILON
    best_index = -1
    for step in range(2, n - 1):
        index = (anchor + step) % n
        city_c = order[index]
        city_d = order[(index + 1) % n]
        delta = row_a[city_c] + row_b[city_d] - base - dist[city_c][city_d]
        if delta < best_delta:
            best_delta = delta
            best_index = index
    work.lookups += 3 * (n - 3) + 1
    if best_index < 0:
        return Application(0.0, city_a)
    work.moved += tour.reverse((anchor + 1) % n, best_index)
    tour.length += best_delta
    return Application(best_delta, city_a)


def improve_city(
    tour: Tour, problem: Problem, city: int, work: Work
) -> tuple[float, tuple[int, ...]]:
    """Apply the first improving 2-opt move that removes an edge at ``city``,
    searching ``city``'s neighbour list in both tour directions.

    Uses the neighbour-list rule of Johnson and McGeoch (1997): a new edge
    from ``city`` must be shorter than the removed one, so the search along
    the sorted list stops at the first neighbour that is not.

    Returns:
        The length change (zero if no move was found) and the four cities
        whose tour neighbours changed.
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    n = len(order)
    row = dist[city]
    place = pos[city]
    for successor in (True, False):
        partner = order[(place + 1) % n] if successor else order[place - 1]
        removed = row[partner]
        work.lookups += 1
        for candidate in problem.neighbours[city]:
            added = row[candidate]
            work.lookups += 1
            if added >= removed:
                break
            candidate_place = pos[candidate]
            other = (
                order[(candidate_place + 1) % n]
                if successor
                else order[candidate_place - 1]
            )
            if candidate == partner or other == city:
                continue
            delta = added + dist[partner][other] - removed - dist[candidate][other]
            work.lookups += 2
            if delta < -EPSILON:
                if successor:
                    moved = tour.reverse(pos[partner], candidate_place)
                else:
                    moved = tour.reverse(place, pos[other])
                tour.length += delta
                work.moved += moved
                return delta, (city, partner, candidate, other)
    return 0.0, ()


def two_opt_nl(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O7: the first improving 2-opt move at the anchor, over its neighbour
    list (k nearest).  Same move as O5, different implementation cost.
    """
    city = pick_anchor(tour, rng, anchor)
    delta, _touched = improve_city(tour, problem, city, work)
    return Application(delta, city)


def descend(
    tour: Tour,
    problem: Problem,
    work: Work,
    start: Iterable[int] | None = None,
    stop_gain: float | None = None,
) -> float:
    """Apply neighbour-list 2-opt moves until none improves (a local optimum
    with respect to O7), processing cities from a queue.

    Cities whose search fails leave the queue; cities touched by an applied
    move re-enter it (don't-look bits, Bentley 1992).

    Args:
        tour: Tour to improve in place.
        problem: The instance.
        work: Counter for the work done.
        start: Cities to start from; all cities when omitted.
        stop_gain: Stop as soon as the total gain reaches this value (used
            to build partly descended tours); run to a local optimum when
            omitted.

    Returns:
        The total length change.
    """
    n = len(tour.order)
    queue = deque(range(n) if start is None else start)
    queued = [False] * n
    for city in queue:
        queued[city] = True
    total = 0.0
    while queue:
        city = queue.popleft()
        queued[city] = False
        delta, touched = improve_city(tour, problem, city, work)
        if delta < 0:
            total += delta
            if stop_gain is not None and -total >= stop_gain:
                break
            for other in touched:
                if not queued[other]:
                    queued[other] = True
                    queue.append(other)
    return total


def kick(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O8: a segment exchange cutting the edge after the anchor and two random
    other edges, then O7 descent from the cities it touched; the result
    replaces the tour only if it is shorter.

    Reading the tour from the anchor's successor as X Y R (R ends with the
    anchor), the exchange gives R Y X.  That replaces three edges, so it is a
    pure sequential 3-opt move (a type O10 searches), not the four-edge
    double bridge of Johnson and McGeoch (1997), which is O12.
    """
    order = tour.order
    dist = problem.dist
    n = len(order)
    city = pick_anchor(tour, rng, anchor)
    place = tour.pos[city]
    rotated = order[place + 1 :] + order[: place + 1]
    cut_2, cut_3 = sorted(rng.sample(range(1, n), 2))
    x_start, x_end = rotated[0], rotated[cut_2 - 1]
    y_start, y_end = rotated[cut_2], rotated[cut_3 - 1]
    r_start = rotated[cut_3]
    kick_delta = (
        dist[city][y_start]
        + dist[y_end][x_start]
        + dist[x_end][r_start]
        - dist[city][x_start]
        - dist[x_end][y_start]
        - dist[y_end][r_start]
    )
    work.lookups += 6
    new_order = rotated[cut_3:] + rotated[cut_2:cut_3] + rotated[:cut_2]
    pos = [0] * n
    for index, other in enumerate(new_order):
        pos[other] = index
    work.moved += n
    trial = Tour(new_order, pos, tour.length + kick_delta)
    descend(trial, problem, work, (city, x_start, x_end, y_start, y_end, r_start))
    delta = trial.length - tour.length
    if delta < -EPSILON:
        tour.assign(trial)
        return Application(delta, city)
    return Application(0.0, city)


def exchange(tour: Tour, x1: int, x2: int, y1: int, y2: int) -> int:
    """Apply the 2-opt move that removes edges (x1, x2) and (y1, y2) and adds
    (x1, y1) and (x2, y2).

    ``x2`` must follow ``x1`` and ``y2`` follow ``y1`` in the same direction of
    travel; either direction works, which lets longer moves be written as a
    chain of 2-opt moves without tracking how :meth:`Tour.reverse` oriented
    the array.  The length is not changed here.

    Returns:
        The number of positions rewritten.
    """
    order = tour.order
    pos = tour.pos
    if order[(pos[x1] + 1) % len(order)] == x2:
        return tour.reverse(pos[x2], pos[y1])
    return tour.reverse(pos[y1], pos[x2])


def insert_segment(
    tour: Tour,
    outside: int,
    first: int,
    last: int,
    after: int,
    target: int,
    other: int,
    size: int,
) -> int:
    """Move the tour segment ``first`` .. ``last`` between the adjacent cities
    ``target`` and ``other``, with ``first`` next to ``target``.

    ``outside`` and ``after`` are the segment's tour neighbours beyond
    ``first`` and ``last``; the edge (target, other) must not touch the
    segment, ``outside`` or ``after``.  Done as a chain of two or three 2-opt
    moves, so it works whichever way the array is oriented.  The length is
    not changed here.

    Returns:
        The number of positions rewritten.
    """
    order = tour.order
    pos = tour.pos
    n = len(order)
    # Direction of travel from ``first`` towards ``last``.
    step = 1 if order[(pos[outside] + 1) % n] == first else -1
    if order[(pos[target] + step) % n] == other:
        # outside S after .. target other -> outside after .. target S other
        moved = exchange(tour, outside, first, target, other)
        moved += exchange(tour, outside, target, after, last)
        if size > 1:
            moved += exchange(tour, target, last, first, other)
        return moved
    # outside S after .. other target -> outside after .. other S' target
    moved = exchange(tour, outside, first, other, target)
    moved += exchange(tour, outside, other, after, last)
    return moved


MAX_SEGMENT = 3
"""Longest segment O9 moves (the classic Or-opt limit)."""


def or_opt_nl(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O9: the first improving Or-opt move of a segment that starts at the
    anchor, searched over the anchor's neighbour list.

    The segment holds 1-3 cities starting at the city in either tour
    direction.  It is reinserted, in either orientation, so that the city
    sits next to one of its k nearest neighbours.  The targeted counterpart
    of the random moves O2 and O4.  As for O7, the neighbour list is cut off
    at the first neighbour whose new edge is at least as long as the gain
    from taking the segment out (Johnson and McGeoch 1997).
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    n = len(order)
    city = pick_anchor(tour, rng, anchor)
    row = dist[city]
    place = pos[city]
    for step in (1, -1):
        outside = order[(place - step) % n]
        segment = [city]
        for size in range(1, MAX_SEGMENT + 1):
            if size > 1:
                segment.append(order[(place + step * (size - 1)) % n])
            last = segment[-1]
            after = order[(place + step * size) % n]
            removal = dist[outside][city] + dist[last][after] - dist[outside][after]
            work.lookups += 3
            if removal <= EPSILON:
                continue
            for target in problem.neighbours[city]:
                added = row[target]
                work.lookups += 1
                if added >= removal:
                    break
                if target in (outside, after) or target in segment:
                    continue
                target_place = pos[target]
                for other in (
                    order[(target_place + step) % n],
                    order[(target_place - step) % n],
                ):
                    if other in (outside, after):
                        continue
                    delta = added + dist[last][other] - dist[target][other] - removal
                    work.lookups += 2
                    if delta >= -EPSILON:
                        continue
                    moved = insert_segment(
                        tour, outside, city, last, after, target, other, size
                    )
                    tour.length += delta
                    work.moved += moved
                    return Application(delta, city)
    return Application(0.0, city)


def three_opt_nl(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O10: the first improving sequential 3-opt move that removes an edge at
    the anchor, searched over neighbour lists with the Lin-Kernighan gain
    criterion (an LK step of depth 3, Johnson and McGeoch 1997).

    Each added edge is taken from a neighbour list and kept only while the
    partial gain stays positive.  Improving 2-opt closures at depth 2 are
    applied as well, so the neighbourhood contains O7's 2-opt moves plus all
    sequential 3-opt reconnections, among them O8's segment exchange.
    """
    city = pick_anchor(tour, rng, anchor)
    for step in (1, -1):
        delta = _three_opt_at(tour, problem, city, step, work)
        if delta < 0:
            return Application(delta, city)
    return Application(0.0, city)


def _three_opt_at(
    tour: Tour, problem: Problem, t1: int, step: int, work: Work
) -> float:
    """Search and apply the first improving sequential 3-opt move that removes
    the edge from ``t1`` to its neighbour in direction ``step``.

    Notation of Lin and Kernighan: removed edges (t1, t2), (t3, t4), (t5, t6),
    added edges (t2, t3), (t4, t5), (t6, t1); "succ" and "pred" follow
    ``step``.

    Returns:
        The length change, or zero if no move was found.
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    neighbours = problem.neighbours
    n = len(order)

    def succ(city: int) -> int:
        return order[(pos[city] + step) % n]

    def pred(city: int) -> int:
        return order[(pos[city] - step) % n]

    def between(first: int, middle: int, last: int) -> bool:
        """Whether ``middle`` lies on the path from ``first`` to ``last``."""
        return (pos[middle] - pos[first]) * step % n <= (
            pos[last] - pos[first]
        ) * step % n

    t2 = succ(t1)
    row_t2 = dist[t2]
    g0 = dist[t1][t2]
    work.lookups += 1
    for t3 in neighbours[t2]:
        g1 = g0 - row_t2[t3]
        work.lookups += 1
        if g1 <= EPSILON:
            break
        if t3 == t1 or t3 == succ(t2):
            continue
        # t4 = pred(t3): a 2-opt move closes the tour.
        t4 = pred(t3)
        row_t4 = dist[t4]
        open_gain = g1 + dist[t3][t4]
        gain = open_gain - row_t4[t1]
        work.lookups += 2
        if gain > EPSILON:
            work.moved += exchange(tour, t1, t2, t4, t3)
            tour.length -= gain
            return -gain
        # After that 2-opt move the tour reads t1 t4 .. t2 t3 .. t1; a second
        # 2-opt move removes (t1, t4) and (t6, t5) with t6 before t5.
        skip = pred(t4)
        for t5 in neighbours[t4]:
            g2 = open_gain - row_t4[t5]
            work.lookups += 1
            if g2 <= EPSILON:
                break
            if t5 in (t1, t3, skip):
                continue
            t6 = succ(t5) if between(t2, t5, t4) else pred(t5)
            gain = g2 + dist[t5][t6] - dist[t6][t1]
            work.lookups += 2
            if gain > EPSILON:
                moved = exchange(tour, t1, t2, t4, t3)
                moved += exchange(tour, t1, t4, t6, t5)
                work.moved += moved
                tour.length -= gain
                return -gain
        # t4 = succ(t3): no 2-opt closure; t5 must lie on t2 .. t3, which the
        # first two changes cut off as a separate cycle.
        t4 = succ(t3)
        if t4 == t1:
            continue
        row_t4 = dist[t4]
        open_gain = g1 + dist[t3][t4]
        work.lookups += 1
        for t5 in neighbours[t4]:
            g2 = open_gain - row_t4[t5]
            work.lookups += 1
            if g2 <= EPSILON:
                break
            if t5 == t3 or not between(t2, t5, t3):
                continue
            for t6 in (succ(t5), pred(t5)):
                if t6 == t1:
                    continue
                gain = g2 + dist[t5][t6] - dist[t6][t1]
                work.lookups += 2
                if gain <= EPSILON:
                    continue
                if t6 == succ(t5):
                    # t1 [t2 .. t5] [t6 .. t3] t4 -> t1 [t6 .. t3] [t2 .. t5] t4
                    moved = exchange(tour, t1, t2, t5, t6)
                    moved += exchange(tour, t1, t5, t3, t4)
                    moved += exchange(tour, t1, t3, t6, t2)
                else:
                    # t1 [t2 .. t6] [t5 .. t3] t4 -> both segments reversed
                    moved = exchange(tour, t1, t2, t6, t5)
                    moved += exchange(tour, t2, t5, t3, t4)
                work.moved += moved
                tour.length -= gain
                return -gain
    return 0.0


RUIN_SIZE = 10
"""Cities removed by O11: a random city and its nearest neighbours."""


def ruin_recreate(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O11: radial ruin and greedy recreate (Schrimpf et al. 2000).

    Removes the anchor and its nearest neighbours (up to
    :data:`RUIN_SIZE` cities in all), then reinserts them in random order,
    each at the cheapest edge next to one of its neighbour-list cities that
    is in the tour (all edges if none is).  The result replaces the tour only
    if it is shorter.  Changes are kept in an overlay of successor and
    predecessor links, so a rejected trial costs O(RUIN_SIZE · k) and only an
    accepted one rewrites the tour.
    """
    neighbours = problem.neighbours
    centre = pick_anchor(tour, rng, anchor)
    size = min(RUIN_SIZE, len(neighbours[centre]) + 1, len(tour.order) // 2)
    ruined = [centre, *neighbours[centre][: size - 1]]
    delta = _ruin_and_recreate(tour, problem, rng, work, ruined, 0.0, centre)
    return Application(delta, centre)


def _ruin_and_recreate(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    ruined: list[int],
    blink: float,
    start: int,
) -> float:
    """Remove ``ruined`` from the tour and reinsert them in random order, each
    at the cheapest edge next to one of its neighbour-list cities that is in
    the tour (all edges if none is), skipping each candidate edge with
    probability ``blink`` (SISR's blinks).  The result replaces the tour only
    if it is shorter.

    Changes are kept in an overlay of successor and predecessor links, so a
    rejected trial costs O(len(ruined) · k) and only an accepted one rewrites
    the tour.  ``start`` is any city; the rebuilt order starts there.

    Returns:
        The length change (negative), or zero if the trial was rejected.
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    neighbours = problem.neighbours
    n = len(order)
    successor: dict[int, int] = {}
    predecessor: dict[int, int] = {}

    def succ(city: int) -> int:
        return successor[city] if city in successor else order[(pos[city] + 1) % n]

    def pred(city: int) -> int:
        return predecessor[city] if city in predecessor else order[pos[city] - 1]

    delta = 0.0
    removed = set()
    for city in ruined:
        before = pred(city)
        after = succ(city)
        successor[before] = after
        predecessor[after] = before
        delta += dist[before][after] - dist[before][city] - dist[city][after]
        removed.add(city)
    work.lookups += 3 * len(ruined)
    rng.shuffle(ruined)
    for city in ruined:
        row = dist[city]
        best_cost = math.inf
        best_left = -1
        for other in neighbours[city]:
            if other in removed:
                continue
            for left, right in ((other, succ(other)), (pred(other), other)):
                if blink and rng.random() < blink:
                    continue
                cost = row[left] + row[right] - dist[left][right]
                if cost < best_cost:
                    best_cost = cost
                    best_left = left
            work.lookups += 6
        if best_left < 0:
            first = next(other for other in order if other not in removed)
            left = first
            for _ in range(n - len(removed)):
                right = succ(left)
                cost = row[left] + row[right] - dist[left][right]
                if cost < best_cost:
                    best_cost = cost
                    best_left = left
                left = right
            work.lookups += 3 * (n - len(removed))
        right = succ(best_left)
        successor[best_left] = city
        predecessor[city] = best_left
        successor[city] = right
        predecessor[right] = city
        removed.discard(city)
        delta += best_cost
    if delta >= -EPSILON:
        return 0.0
    new_order = [start]
    city = succ(start)
    while city != start:
        new_order.append(city)
        city = succ(city)
    new_pos = [0] * n
    for index, city in enumerate(new_order):
        new_pos[city] = index
    work.moved += n
    tour.assign(Tour(new_order, new_pos, tour.length + delta))
    return delta


def _double_bridge_trial(
    tour: Tour, problem: Problem, work: Work, cuts: list[int]
) -> float:
    """Apply a double bridge after the four sorted positions ``cuts``, run an
    O7 descent from the eight cities at the cut edges, and keep the result
    only if it is shorter.

    With segments S0 = (q4 .. q1] (wrapping), S1 = (q1 .. q2],
    S2 = (q2 .. q3] and S3 = (q3 .. q4], the new tour is S0 S3 S2 S1: all
    four cut edges are replaced, the non-sequential move that 2-opt and
    3-opt cannot undo (Martin, Otto and Felten 1991).

    Returns:
        The length change, or zero if the trial was rejected.
    """
    order = tour.order
    dist = problem.dist
    n = len(order)
    q1, q2, q3, q4 = cuts
    a1, a2, a3, a4 = order[q1], order[q2], order[q3], order[q4]
    b1, b2, b3, b4 = order[q1 + 1], order[q2 + 1], order[q3 + 1], order[(q4 + 1) % n]
    kick_delta = (
        dist[a1][b3]
        + dist[a4][b2]
        + dist[a3][b1]
        + dist[a2][b4]
        - dist[a1][b1]
        - dist[a2][b2]
        - dist[a3][b3]
        - dist[a4][b4]
    )
    work.lookups += 8
    new_order = (
        order[q4 + 1 :]
        + order[: q1 + 1]
        + order[q3 + 1 : q4 + 1]
        + order[q2 + 1 : q3 + 1]
        + order[q1 + 1 : q2 + 1]
    )
    pos = [0] * n
    for index, city in enumerate(new_order):
        pos[city] = index
    work.moved += n
    trial = Tour(new_order, pos, tour.length + kick_delta)
    descend(trial, problem, work, (a1, a2, a3, a4, b1, b2, b3, b4))
    delta = trial.length - tour.length
    if delta < -EPSILON:
        tour.assign(trial)
        return delta
    return 0.0


def double_bridge(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O12: a double bridge cutting the edge after the anchor and three random
    other edges, then O7 descent; kept only if shorter (one iterated-LK-style
    trial, Johnson and McGeoch 1997).
    """
    city = pick_anchor(tour, rng, anchor)
    place = tour.pos[city]
    others = [
        index if index < place else index + 1
        for index in rng.sample(range(len(tour.order) - 1), 3)
    ]
    cuts = sorted([place, *others])
    return Application(_double_bridge_trial(tour, problem, work, cuts), city)


def double_bridge_local(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O13: a double bridge cutting the edges after the anchor and three
    random cities of its neighbour list, then O7 descent; kept only if shorter.

    Geometrically local kicks keep the damage, and the repair, in one region,
    which Applegate, Cook and Rohe (2003) found far better than random cuts
    on large instances.
    """
    centre = pick_anchor(tour, rng, anchor)
    cities = [centre, *rng.sample(problem.neighbours[centre], 3)]
    cuts = sorted(tour.pos[city] for city in cities)
    return Application(_double_bridge_trial(tour, problem, work, cuts), centre)


def two_opt_descent(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O14: an O7 descent started at the anchor: apply O7 at the anchor, then
    at every city an applied move touched, until the work queue is empty.

    The descent inside the kicks (O8, O12, O13) without the kick, so that a
    kick leading can be told apart from "a work queue beats a random anchor".
    Deterministic given the anchor; one application can make many moves.
    """
    city = pick_anchor(tour, rng, anchor)
    return Application(descend(tour, problem, work, (city,)), city)


TOURNAMENT = 8
"""Cities O15 compares before choosing where to apply O7."""


def two_opt_targeted(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O15: O7 at the city with the worst local edge among the anchor and
    :data:`TOURNAMENT` - 1 random other cities.

    A city's score is its longer tour edge minus the distance to its nearest
    neighbour: how far its tour edges are from the best edge it could have.
    E1 measurement A found that 2-opt pays at cities whose tour edges are not
    among their nearest neighbours; this operator spends O(TOURNAMENT) to aim
    there.  The returned anchor is the given (or drawn) anchor, not the city
    O7 was applied at.
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    neighbours = problem.neighbours
    n = len(order)
    city = pick_anchor(tour, rng, anchor)
    best_city = city
    best_score = -math.inf
    for candidate in (city, *(order[rng.randrange(n)] for _ in range(TOURNAMENT - 1))):
        place = pos[candidate]
        row = dist[candidate]
        score = (
            max(row[order[(place + 1) % n]], row[order[place - 1]])
            - row[neighbours[candidate][0]]
        )
        if score > best_score:
            best_score = score
            best_city = candidate
    work.lookups += 3 * TOURNAMENT
    delta, _touched = improve_city(tour, problem, best_city, work)
    return Application(delta, city)


LK_DEPTH = 25
"""Most 2-opt moves one O16 step chains (Applegate, Cook and Rohe 2003 cap
flip sequences at 25)."""

LK_BREADTH = (4, 3, 3, 2, 1)
"""Alternatives tried at levels 1, 2, 3, 4 and deeper before backtracking
(the default breadth of Applegate, Cook and Rohe's chained LK)."""


def lin_kernighan(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O16: a Lin-Kernighan step from the anchor, built from 2-opt moves.

    Removes an edge (t1, t2) at the anchor and grows a sequential k-opt
    move one 2-opt move (flip) at a time, as in LKH with 2-opt basis moves
    (Helsgaun 2000; Lin and Kernighan 1973): at each level a new edge from
    the current end t2i to a candidate t3 must keep the partial gain
    positive (gain criterion), the edge (t3, t4) that makes the flip valid
    is removed, and the tour would close with (t4, t1).  The first closing
    that improves the tour is committed (first improvement, as in LKH).
    Otherwise the alternatives with the largest partial gain are explored,
    :data:`LK_BREADTH` per level, up to :data:`LK_DEPTH` flips; added edges
    are never removed again.  Unproductive flips are undone.  Deterministic
    given the anchor; both tour directions are tried.
    """
    city = pick_anchor(tour, rng, anchor)
    for step in (1, -1):
        gain = _lk_from(tour, problem, city, step, work)
        if gain > EPSILON:
            tour.length -= gain
            return Application(-gain, city)
    return Application(0.0, city)


def _lk_from(tour: Tour, problem: Problem, t1: int, step: int, work: Work) -> float:
    """One LK search from ``t1`` removing the edge to its ``step`` neighbour.

    Returns:
        The gain of the committed move, or zero (tour unchanged).
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    neighbours = problem.neighbours
    n = len(order)
    applied: list[tuple[int, int, int, int]] = []
    added: set[tuple[int, int]] = set()

    def edge(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a < b else (b, a)

    def flip(last: int, t3: int, t4: int) -> None:
        work.moved += exchange(tour, t1, last, t4, t3)
        applied.append((t1, t4, last, t3))

    def search(last: int, gain: float, level: int) -> float:
        """Extend the chain from ``last`` (adjacent to t1); return the gain
        of a committed improvement, or zero with everything below undone."""
        if level > LK_DEPTH:
            return 0.0
        row = dist[last]
        forward = order[(pos[t1] + 1) % n] == last
        succ_last = order[(pos[last] + 1) % n]
        pred_last = order[pos[last] - 1]
        options: list[tuple[float, int, int]] = []
        for t3 in neighbours[last]:
            partial = gain - row[t3]
            work.lookups += 1
            if partial <= EPSILON:
                break
            if t3 in (t1, succ_last, pred_last) or edge(last, t3) in added:
                continue
            # The flip removing (t1, last) and (t3, t4) keeps one cycle only
            # if t4 lies before t3 in the direction t1 -> last.
            t4 = order[pos[t3] - 1] if forward else order[(pos[t3] + 1) % n]
            if t4 == t1 or edge(t3, t4) in added:
                continue
            opened = partial + dist[t3][t4]
            work.lookups += 2
            closed = opened - dist[t4][t1]
            if closed > EPSILON:
                flip(last, t3, t4)
                return closed
            options.append((opened, t3, t4))
        options.sort(reverse=True)
        breadth = LK_BREADTH[min(level, len(LK_BREADTH)) - 1]
        for opened, t3, t4 in options[:breadth]:
            flip(last, t3, t4)
            added.add(edge(last, t3))
            result = search(t4, opened, level + 1)
            if result > EPSILON:
                return result
            added.discard(edge(last, t3))
            work.moved += exchange(tour, *applied.pop())
        return 0.0

    t2 = order[(pos[t1] + step) % n]
    work.lookups += 1
    return search(t2, dist[t1][t2], 1)


def _alpha_view(problem: Problem) -> Problem:
    """The instance with alpha-nearness candidate lists.

    Raises:
        ValueError: If no alpha candidates are attached (compute them with
            ``python -m aos.candidates``).
    """
    if problem.alpha is None:
        raise ValueError(
            f"{problem.name}: no alpha candidates; run python -m aos.candidates"
        )
    return problem.alpha


def two_opt_alpha(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O17: O7 over the anchor's 5 alpha-nearest candidates (Helsgaun 2000)
    instead of its 10 nearest neighbours; same move, LKH's candidate set."""
    city = pick_anchor(tour, rng, anchor)
    delta, _touched = improve_city(tour, _alpha_view(problem), city, work)
    return Application(delta, city)


def lin_kernighan_alpha(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O18: O16 over the 5 alpha-nearest candidates: the LKH combination of
    a Lin-Kernighan step and alpha-nearness candidate edges."""
    return lin_kernighan(tour, _alpha_view(problem), rng, work, anchor)


STRING_MAX = 10
"""Longest string O19 removes (SISR's L_max)."""

STRING_COUNT = 4 * 10 / (1 + STRING_MAX) - 1
"""SISR's k_s^max = 4 c / (1 + L_max) - 1 with c = 10 cities removed on
average; O19 removes floor(U(1, k_s^max + 1)) strings, i.e. 1 to 3."""

STRING_BLINK = 0.01
"""SISR's blink rate: each candidate insertion position is skipped with
this probability."""


def string_removal(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O19: SISR-style string removal and blink recreate (Christiaens and
    Vanden Berghe 2020), adapted to one tour.

    Removes 1-3 strings (contiguous tour segments) of 1-10 cities: the first
    contains the anchor, the others contain the next of the anchor's
    nearest neighbours not yet removed, each at a random offset.  The
    cities are reinserted in random order at their cheapest candidate
    position, skipping each position with probability 0.01 (blinks); the
    result is kept only if shorter.  SISR's split-string variant and its
    customer orderings by demand or depot distance (VRP-specific) are
    omitted.
    """
    order = tour.order
    pos = tour.pos
    n = len(order)
    seed = pick_anchor(tour, rng, anchor)
    strings = int(rng.uniform(1, STRING_COUNT + 1))
    limit = max(1, min(STRING_MAX, n // 8))
    taken: set[int] = set()
    ruined: list[int] = []
    for city in (seed, *problem.neighbours[seed]):
        if strings == 0:
            break
        if city in taken:
            continue
        length = int(rng.uniform(1, limit + 1))
        first = pos[city] - rng.randrange(length)
        cities = [order[(first + index) % n] for index in range(length)]
        if taken.intersection(cities):
            continue
        taken.update(cities)
        ruined.extend(cities)
        strings -= 1
    start = next(city for city in order if city not in taken)
    delta = _ruin_and_recreate(tour, problem, rng, work, ruined, STRING_BLINK, start)
    return Application(delta, seed)


def segment_exchange(
    tour: Tour,
    problem: Problem,
    rng: random.Random,
    work: Work,
    anchor: int | None = None,
) -> Application:
    """O20: the first improving exchange of the segment of 1-3 cities that
    starts at the anchor with a segment of 1-3 cities that starts at one of
    the anchor's nearest neighbours, each placed in its better orientation
    (the neighbour-list segment exchange of FILO and HGS-CVRP, within one
    tour).  The two segments must not touch.  Deterministic given the
    anchor; an applied exchange rebuilds the O(n) array.
    """
    order = tour.order
    pos = tour.pos
    dist = problem.dist
    n = len(order)
    a = pick_anchor(tour, rng, anchor)
    start_a = pos[a]
    p = order[start_a - 1]
    for size_a in range(1, 4):
        a_first = a
        a_last = order[(start_a + size_a - 1) % n]
        q = order[(start_a + size_a) % n]
        old_a = dist[p][a_first] + dist[a_last][q]
        for b in problem.neighbours[a]:
            offset = (pos[b] - start_a) % n
            for size_b in range(1, 4):
                if offset < size_a + 1 or offset + size_b > n - 1:
                    continue
                b_first = b
                b_last = order[(pos[b] + size_b - 1) % n]
                r = order[pos[b] - 1]
                s = order[(pos[b] + size_b) % n]
                old = old_a + dist[r][b_first] + dist[b_last][s]
                into_a = min(
                    dist[p][b_first] + dist[b_last][q],
                    dist[p][b_last] + dist[b_first][q],
                )
                into_b = min(
                    dist[r][a_first] + dist[a_last][s],
                    dist[r][a_last] + dist[a_first][s],
                )
                work.lookups += 12
                delta = into_a + into_b - old
                if delta >= -EPSILON:
                    continue
                rotated = order[start_a:] + order[:start_a]
                segment_a = rotated[:size_a]
                middle = rotated[size_a:offset]
                segment_b = rotated[offset : offset + size_b]
                tail = rotated[offset + size_b :]
                if (
                    dist[p][b_last] + dist[b_first][q]
                    < dist[p][b_first] + dist[b_last][q]
                ):
                    segment_b = segment_b[::-1]
                if (
                    dist[r][a_last] + dist[a_first][s]
                    < dist[r][a_first] + dist[a_last][s]
                ):
                    segment_a = segment_a[::-1]
                new_order = segment_b + middle + segment_a + tail
                new_pos = [0] * n
                for index, city in enumerate(new_order):
                    new_pos[city] = index
                work.moved += n
                tour.assign(Tour(new_order, new_pos, tour.length + delta))
                return Application(delta, a)
    return Application(0.0, a)


OPERATORS: Mapping[str, Operator] = {
    "swap": swap,
    "insertion": insertion,
    "inversion": inversion,
    "or_opt": or_opt,
    "two_opt_first": two_opt_first,
    "two_opt_best": two_opt_best,
    "two_opt_nl": two_opt_nl,
    "kick": kick,
    "or_opt_nl": or_opt_nl,
    "three_opt_nl": three_opt_nl,
    "ruin_recreate": ruin_recreate,
    "double_bridge": double_bridge,
    "double_bridge_local": double_bridge_local,
    "two_opt_descent": two_opt_descent,
    "two_opt_targeted": two_opt_targeted,
    "lin_kernighan": lin_kernighan,
    "two_opt_alpha": two_opt_alpha,
    "lin_kernighan_alpha": lin_kernighan_alpha,
    "string_removal": string_removal,
    "segment_exchange": segment_exchange,
}
"""Operators O1-O20 by name, in protocol order."""

OPERATOR_IDS: Mapping[str, str] = {
    name: f"O{index}" for index, name in enumerate(OPERATORS, start=1)
}
"""Protocol identifiers (``O1`` … ``O20``) by operator name."""

FAMILIES: Mapping[str, str] = {
    name: (
        "two_opt"
        if name
        in (
            "two_opt_first",
            "two_opt_best",
            "two_opt_nl",
            "two_opt_descent",
            "two_opt_targeted",
        )
        else "kick"
        if name in ("kick", "double_bridge", "double_bridge_local")
        else name
    )
    for name in OPERATORS
}
"""Operator family by name.  Operators of one family make the same move (the
2-opt variants O5-O7) or the same kick-plus-descent trial (O8, O12, O13);
their rates are nearly equal by construction, so E1's stopping rule tests the
leader only against other families."""
