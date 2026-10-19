"""A symmetric TSP instance in the plain-list form the operators use."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from instances import load_distance_matrix, load_path

DEFAULT_NEIGHBOURS = 10


def listed_optimum(listing: Path, name: str) -> float | None:
    """Return ``name``'s length from a TSPLIB ``solution.txt`` list, or
    ``None`` if the file or the entry is missing.

    Raises:
        ValueError: If the entry exists but is not a number.
    """
    if not listing.is_file():
        return None
    for line in listing.read_text().splitlines():
        key, _, value = line.partition(":")
        if key.strip() == name:
            return float(value.split()[0])
    return None


@dataclass(frozen=True, slots=True)
class Problem:
    """Distances, neighbour lists and the optimum of one instance.

    Attributes:
        name: Instance name.
        dist: ``dist[i][j]`` is the distance between cities ``i`` and ``j``.
            Integer when every distance is integral, which is faster.
        neighbours: For each city, the ``k`` nearest other cities in order of
            increasing distance.
        optimum: Optimal (or best-known) tour length, if known.
        alpha: The same instance with alpha-nearness candidate lists
            (:mod:`aos.candidates`) as its neighbour lists, for the operators
            that search alpha candidates; ``None`` if not computed.
    """

    name: str
    dist: list[list[int]] | list[list[float]]
    neighbours: list[list[int]]
    optimum: float | None
    alpha: Problem | None = None

    def with_alpha(self, lists: list[list[int]]) -> Problem:
        """Return this instance with ``lists`` attached as alpha candidates.

        Raises:
            ValueError: If there is not one list per city.
        """
        if len(lists) != self.n:
            raise ValueError(f"{len(lists)} candidate lists for {self.n} cities")
        view = Problem(self.name, self.dist, lists, self.optimum)
        return Problem(self.name, self.dist, self.neighbours, self.optimum, view)

    @property
    def n(self) -> int:
        """Number of cities."""
        return len(self.dist)

    def tour_length(self, order: Sequence[int]) -> float:
        """Return the length of the closed tour visiting ``order``."""
        dist = self.dist
        return sum(dist[order[i - 1]][order[i]] for i in range(len(order)))

    def gap(self, length: float) -> float:
        """Return ``length`` relative to the optimum, e.g. 0.05 for 5% above.

        Raises:
            ValueError: If the optimum is unknown.
        """
        if self.optimum is None:
            raise ValueError(f"{self.name}: optimum unknown, gap undefined")
        return length / self.optimum - 1.0

    @classmethod
    def from_matrix(
        cls,
        name: str,
        matrix: np.ndarray,
        optimum: float | None = None,
        neighbours: int = DEFAULT_NEIGHBOURS,
        alpha_candidates: int = 0,
    ) -> Problem:
        """Build a problem from a square symmetric distance matrix, with
        ``alpha_candidates`` alpha-nearness candidates per city if positive
        (computed, not cached).

        Raises:
            ValueError: If the matrix is not square and symmetric or has
                fewer than eight cities (the double bridge needs room).
        """
        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
            raise ValueError(f"distance matrix must be square, got {matrix.shape}")
        if matrix.shape[0] < 8:
            raise ValueError("at least eight cities are needed")
        if not np.array_equal(matrix, matrix.T):
            raise ValueError("distance matrix must be symmetric")
        integral = bool(np.all(matrix == np.round(matrix)))
        dist = (
            matrix.astype(np.int64).tolist()
            if integral
            else matrix.astype(float).tolist()
        )
        k = min(neighbours, matrix.shape[0] - 1)
        masked = matrix.astype(float) + np.diag(np.full(matrix.shape[0], np.inf))
        nearest = np.argsort(masked, axis=1, kind="stable")[:, :k]
        problem = cls(name, dist, nearest.tolist(), optimum)
        if alpha_candidates > 0:
            from .candidates import alpha_candidates as compute_alpha
            from .candidates import ascend

            weights = matrix.astype(float)
            lists = compute_alpha(weights, ascend(weights), alpha_candidates)
            problem = problem.with_alpha(lists)
        return problem

    @classmethod
    def from_tsplib(cls, path: Path, neighbours: int = DEFAULT_NEIGHBOURS) -> Problem:
        """Load a symmetric TSPLIB instance, its optimum and, if cached, its
        alpha-nearness candidate lists (see :mod:`aos.candidates`).

        The optimum comes from a companion tour (``.opt.tour``) if present,
        else from a TSPLIB ``solution.txt`` list (``name : length`` lines) in
        the same directory, else it is unknown.

        Raises:
            ValueError: If the file is not a symmetric TSP.
            OSError: If the file cannot be read.
        """
        instance = load_path(path)
        if instance.kind != "TSP":
            raise ValueError(f"{path.name} is {instance.kind}, not a symmetric TSP")
        lengths = [instance.compute_length(sequence) for sequence in instance.sequences]
        optimum = next((length for length in lengths if length), None)
        if optimum is None:
            optimum = listed_optimum(path.parent / "solution.txt", instance.name)
        problem = cls.from_matrix(
            instance.name, load_distance_matrix(path), optimum, neighbours
        )
        from .candidates import cached

        lists = cached(instance.name, problem.n)
        return problem if lists is None else problem.with_alpha(lists)
