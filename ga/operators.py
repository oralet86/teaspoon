"""Mutation and crossover operators on TSP tours.

A tour is a permutation of node indices stored in a NumPy array.  Every
mutation changes the tour in place and returns the change in tour length,
computed from the few edges it touches instead of re-measuring the tour.
To add an operator, write a function with the :data:`Mutation` signature
and add it to :data:`MUTATIONS`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np

Mutation = Callable[[np.ndarray, np.ndarray, np.random.Generator], float]
"""``(tour, distances, rng) -> length change``; the tour is changed in place."""


def tour_length(tour: np.ndarray, distances: np.ndarray) -> float:
    """Return the length of a closed tour."""
    return float(distances[tour, np.roll(tour, -1)].sum())


def swap(tour: np.ndarray, distances: np.ndarray, rng: np.random.Generator) -> float:
    """Exchange the cities at two random positions."""
    size = tour.size
    first, second = rng.choice(size, size=2, replace=False)
    # The edges next to the two positions, as a set so that neighbouring
    # positions do not count their shared edge twice.
    starts = np.array(sorted({(first - 1) % size, first, (second - 1) % size, second}))
    ends = (starts + 1) % size
    before = distances[tour[starts], tour[ends]].sum()
    tour[first], tour[second] = tour[second], tour[first]
    return float(distances[tour[starts], tour[ends]].sum() - before)


def inversion(
    tour: np.ndarray, distances: np.ndarray, rng: np.random.Generator
) -> float:
    """Reverse a random segment, which is a random 2-opt move."""
    size = tour.size
    start, end = sorted(rng.choice(size, size=2, replace=False))
    if start == 0 and end == size - 1:
        # Reversing the whole tour leaves the cycle unchanged.
        return 0.0
    before_start, after_end = tour[start - 1], tour[(end + 1) % size]
    delta = (
        distances[before_start, tour[end]]
        + distances[tour[start], after_end]
        - distances[before_start, tour[start]]
        - distances[tour[end], after_end]
    )
    tour[start : end + 1] = tour[start : end + 1][::-1]
    return float(delta)


def insertion(
    tour: np.ndarray, distances: np.ndarray, rng: np.random.Generator
) -> float:
    """Move one random city to a random place elsewhere in the tour."""
    size = tour.size
    source = int(rng.integers(size))
    city = tour[source]
    rest = np.delete(tour, source)
    previous, following = rest[source - 1], rest[source % (size - 1)]
    removal = (
        distances[previous, following]
        - distances[previous, city]
        - distances[city, following]
    )
    # Insert before position ``target`` of the remaining tour, skipping the
    # place the city came from so that the tour always changes.
    target = int(rng.integers(size - 2))
    if target >= source:
        target += 1
    left, right = rest[target - 1], rest[target % (size - 1)]
    addition = distances[left, city] + distances[city, right] - distances[left, right]
    tour[:] = np.insert(rest, target, city)
    return float(removal + addition)


def two_opt(tour: np.ndarray, distances: np.ndarray, rng: np.random.Generator) -> float:
    """Apply the best improving 2-opt move at one random edge, if there is one.

    Unlike :func:`inversion`, the move is chosen for its gain, so it costs a
    scan over the tour but never makes the tour longer.
    """
    size = tour.size
    # Rotating the tour puts the chosen edge first without changing the cycle.
    rotated = np.roll(tour, -int(rng.integers(size)))
    first, second = rotated[0], rotated[1]
    # Reversing rotated[1..end] replaces the edges (first, second) and
    # (rotated[end], rotated[end + 1]).
    ends = np.arange(2, size - 1)
    gains = (
        distances[first, rotated[ends]]
        + distances[second, rotated[ends + 1]]
        - distances[first, second]
        - distances[rotated[ends], rotated[ends + 1]]
    )
    if gains.size == 0 or gains.min() >= 0:
        return 0.0
    best = int(gains.argmin())
    end = int(ends[best])
    rotated[1 : end + 1] = rotated[1 : end + 1][::-1]
    tour[:] = rotated
    return float(gains[best])


def order_crossover(
    first: np.ndarray, second: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Return a child with a segment of ``first`` and the order of ``second``.

    A random segment is copied from the first parent in place; the remaining
    positions are filled with the missing cities in the order they appear in
    the second parent.
    """
    size = first.size
    start, end = sorted(rng.choice(size + 1, size=2, replace=False))
    child = np.empty_like(first)
    child[start:end] = first[start:end]
    inherited = np.zeros(size, dtype=bool)
    inherited[first[start:end]] = True
    remaining = second[~inherited[second]]
    child[:start] = remaining[:start]
    child[end:] = remaining[start:]
    return child


MUTATIONS: Mapping[str, Mutation] = {
    "swap": swap,
    "inversion": inversion,
    "insertion": insertion,
    "two_opt": two_opt,
}
"""The mutation operators a selector chooses between, by name."""
