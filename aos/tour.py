"""A closed tour with a position index and an incrementally tracked length."""

from __future__ import annotations

import math

from .problem import Problem


class Tour:
    """Visiting order, position of every city, and the current length.

    ``order[k]`` is the city at position ``k``; ``pos[c]`` is the position of
    city ``c``.  Operators keep both and ``length`` consistent; :meth:`check`
    verifies that against a full recomputation.
    """

    __slots__ = ("length", "order", "pos")

    def __init__(self, order: list[int], pos: list[int], length: float) -> None:
        self.order = order
        self.pos = pos
        self.length = length

    @classmethod
    def from_order(cls, order: list[int], problem: Problem) -> Tour:
        """Build a tour from a visiting order, computing positions and length."""
        pos = [0] * len(order)
        for index, city in enumerate(order):
            pos[city] = index
        return cls(list(order), pos, problem.tour_length(order))

    def copy(self) -> Tour:
        """Return an independent copy."""
        return Tour(self.order[:], self.pos[:], self.length)

    def assign(self, other: Tour) -> None:
        """Make this tour equal to ``other`` (in place)."""
        self.order = other.order
        self.pos = other.pos
        self.length = other.length

    def reverse(self, start: int, end: int) -> int:
        """Reverse the cyclic stretch of positions ``start`` .. ``end``.

        The stretch runs forward from ``start`` and may wrap around.  When it
        is longer than half the tour, the complementary stretch is reversed
        instead, which gives the same cycle.  The length is not changed here.

        Returns:
            The number of positions rewritten.
        """
        order = self.order
        pos = self.pos
        n = len(order)
        span = (end - start) % n + 1
        if 2 * span > n:
            start, end = (end + 1) % n, (start - 1) % n
            span = n - span
        for _ in range(span // 2):
            first = order[start]
            second = order[end]
            order[start] = second
            pos[second] = start
            order[end] = first
            pos[first] = end
            start = start + 1 if start + 1 < n else 0
            end = end - 1 if end > 0 else n - 1
        return span - span % 2

    def check(self, problem: Problem) -> None:
        """Raise if the tour is not a permutation or its state is inconsistent.

        Raises:
            AssertionError: Describing the first inconsistency found.
        """
        n = problem.n
        if sorted(self.order) != list(range(n)):
            raise AssertionError("order is not a permutation of the cities")
        for index, city in enumerate(self.order):
            if self.pos[city] != index:
                raise AssertionError(
                    f"pos[{city}] = {self.pos[city]}, expected {index}"
                )
        recomputed = problem.tour_length(self.order)
        if not math.isclose(self.length, recomputed, rel_tol=1e-9, abs_tol=1e-6):
            raise AssertionError(f"tracked length {self.length} != {recomputed}")
