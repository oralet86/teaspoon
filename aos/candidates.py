"""Alpha-nearness candidate sets (Helsgaun 2000), as used by LKH.

The alpha value of an edge (i, j) is how much the minimum 1-tree grows when
it is forced to contain (i, j).  Edges of optimal tours tend to have small
alpha values, so the k alpha-nearest cities are a better candidate list than
the k nearest ones.  Before the alpha values are computed, node penalties
pi are tuned by subgradient ascent on the Held-Karp 1-tree bound, with
distances transformed as d'(i, j) = d(i, j) + pi_i + pi_j.

Computing everything costs O(n^2) memory and roughly O(n^2) Python steps per
ascent iteration and once more for the alpha values, so lists are computed
once per instance and cached on disk (``data/cache/candidates``).

The candidate lists returned are each city's ``k`` alpha-nearest cities,
**ordered by distance** (not by alpha), so the neighbour-list rule of the
operators (stop at the first candidate whose new edge is not shorter than
the removed one) stays valid.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

LOGGER = logging.getLogger(__name__)

CACHE = Path(__file__).resolve().parents[1] / "data" / "cache" / "candidates"
"""Directory of cached candidate lists."""


@dataclass(frozen=True, slots=True)
class AscentSettings:
    """Subgradient ascent on the 1-tree bound (LKH-like defaults).

    Attributes:
        max_iterations: Hard cap on ascent iterations.
        initial_step: Initial step size T.
    """

    max_iterations: int = 300
    initial_step: float = 1.0


def minimum_one_tree(
    weights: np.ndarray, special: int = 0
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """Minimum 1-tree: a minimum spanning tree on all cities but ``special``
    (Prim, dense, NumPy), plus the two cheapest edges at ``special``.

    Returns:
        ``parent`` (tree parent of each city; -1 for the root and
        ``special``), the visiting order of Prim (a topological order, root
        first, without ``special``), the 1-tree's total weight, and every
        city's degree in the 1-tree.
    """
    n = len(weights)
    others = np.ones(n, dtype=bool)
    others[special] = False
    root = 1 if special == 0 else 0
    parent = np.full(n, -1, dtype=np.int64)
    best = np.where(others, weights[root], np.inf)
    best_from = np.full(n, root, dtype=np.int64)
    in_tree = ~others
    in_tree[root] = True
    best[in_tree] = np.inf
    order = [root]
    total = 0.0
    degree = np.zeros(n, dtype=np.int64)
    for _ in range(n - 2):
        city = int(np.argmin(best))
        total += best[city]
        parent[city] = best_from[city]
        degree[city] += 1
        degree[best_from[city]] += 1
        in_tree[city] = True
        order.append(city)
        best[city] = np.inf
        row = weights[city]
        closer = (~in_tree) & (row < best)
        best[closer] = row[closer]
        best_from[closer] = city
    special_row = weights[special].copy()
    special_row[special] = np.inf
    two = np.argpartition(special_row, 2)[:2]
    total += float(special_row[two].sum())
    degree[special] = 2
    degree[two] += 1
    return parent, np.asarray(order, dtype=np.int64), float(total), degree


def ascend(dist: np.ndarray, settings: AscentSettings | None = None) -> np.ndarray:
    """Subgradient ascent on the Held-Karp 1-tree bound; return the penalties
    pi of the best bound found (Helsgaun 2000; LKH ``Ascent.c``).

    pi += T * (0.7 v + 0.3 v_previous) with v = degree - 2.  The step T is
    constant within a period and halves with the period (initially
    max(n / 2, 100)).  In the initial phase T doubles after every
    improvement, until more than half a period passes without one; then
    T becomes 3T/4 and the phase ends.  If the last iteration of a period
    improves, the period doubles (capped at its initial value).  Stops when
    the 1-tree is a tour, T or the period reaches zero, or after
    ``max_iterations`` (a cap LKH does not have; it keeps the run affordable
    on dense matrices of 10,000+ cities).
    """
    settings = settings or AscentSettings()
    n = len(dist)
    pi = np.zeros(n)
    best_pi = pi.copy()
    best_bound = -np.inf
    step = settings.initial_step
    initial_period = max(n // 2, 100)
    period = initial_period
    previous = np.zeros(n)
    initial = True
    iteration = 0
    while step > 1e-9 and period > 0:
        since_improvement = 0
        position = 0
        while position < period:
            position += 1
            iteration += 1
            weights = dist + pi[:, None] + pi[None, :]
            _parent, _order, total, degree = minimum_one_tree(weights)
            bound = total - 2.0 * pi.sum()
            v = degree - 2
            if bound > best_bound + 1e-9:
                best_bound = bound
                best_pi = pi.copy()
                since_improvement = 0
                if initial:
                    step *= 2.0
                if position == period:
                    period = min(2 * period, initial_period)
            else:
                since_improvement += 1
                if initial and since_improvement > period // 2:
                    initial = False
                    step = 0.75 * step
                    position = 0
            if not v.any() or iteration >= settings.max_iterations:
                return best_pi
            pi = pi + step * (0.7 * v + 0.3 * previous)
            previous = v
        step /= 2.0
        period //= 2
    return best_pi


def alpha_candidates(dist: np.ndarray, pi: np.ndarray, k: int) -> list[list[int]]:
    """Each city's ``k`` alpha-nearest cities (Helsgaun 2000), ordered by
    distance.

    alpha(i, j) = d'(i, j) - beta(i, j) for cities other than the special
    one, where beta is the largest tree edge on the tree path from i to j;
    computed for one i at a time in O(n) by walking Prim's topological order.
    For the special city, alpha(s, j) = d'(s, j) - (second smallest d'(s,.)).
    Ties in alpha are broken by distance.
    """
    n = len(dist)
    weights = dist + pi[:, None] + pi[None, :]
    parent, order, _total, _degree = minimum_one_tree(weights, special=0)
    parent_list = parent.tolist()
    order_list = order.tolist()
    root = order_list[0]
    edge_weight = [
        float(weights[city, parent_list[city]]) if parent_list[city] >= 0 else 0.0
        for city in range(n)
    ]
    candidates: list[list[int]] = [[] for _ in range(n)]
    beta = [0.0] * n
    mark = [-1] * n
    for i in order_list:
        # beta along the path from i to the root.
        beta[i] = -np.inf
        city = i
        while city != root:
            up = parent_list[city]
            beta[up] = max(beta[city], edge_weight[city])
            mark[up] = i
            city = up
        for j in order_list[1:]:
            if j != i and mark[j] != i:
                beta[j] = max(beta[parent_list[j]], edge_weight[j])
        row = weights[i].copy()
        beta_row = np.asarray(beta)
        alpha = row - beta_row
        alpha[i] = np.inf
        alpha[0] = max(row[0] - _second_smallest(weights[0], exclude=0), 0.0)
        candidates[i] = _best_k(alpha, dist[i], k)
    special = weights[0].copy()
    special[0] = np.inf
    second = _second_smallest(weights[0], exclude=0)
    alpha_special = np.maximum(special - second, 0.0)
    alpha_special[0] = np.inf
    candidates[0] = _best_k(alpha_special, dist[0], k)
    return candidates


def _second_smallest(row: np.ndarray, exclude: int) -> float:
    """Second smallest entry of ``row`` ignoring position ``exclude``."""
    values = row.copy()
    values[exclude] = np.inf
    return float(np.partition(values, 1)[1])


def _best_k(alpha: np.ndarray, distances: np.ndarray, k: int) -> list[int]:
    """Indices of the ``k`` smallest alpha values (ties by distance), ordered
    by distance."""
    ranked = np.lexsort((distances, alpha))[:k]
    return [int(city) for city in sorted(ranked.tolist(), key=lambda c: distances[c])]


def cached(
    name: str, n: int, k: int = 5, cache: Path = CACHE
) -> list[list[int]] | None:
    """Return the cached alpha candidate lists of ``name``, or ``None``.

    Raises:
        ValueError: If the cached file has the wrong number of cities.
    """
    path = cache / f"{name}-alpha{k}.json"
    if not path.is_file():
        return None
    lists = json.loads(path.read_text())
    if len(lists) != n:
        raise ValueError(f"{path}: {len(lists)} lists for {n} cities")
    return lists


def main(argv: list[str] | None = None) -> int:
    """Compute and cache alpha candidates for TSPLIB files (one process per
    file, in parallel)."""
    import argparse
    import multiprocessing

    parser = argparse.ArgumentParser(prog="python -m aos.candidates")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    with multiprocessing.get_context("spawn").Pool(arguments.workers) as pool:
        pool.map(_cache_file, arguments.paths, chunksize=1)
    return 0


def _cache_file(path: Path) -> None:
    """Compute and cache the candidates of one TSPLIB file (worker)."""
    from instances import load_distance_matrix, load_path

    name = load_path(path).name
    load_or_compute(name, load_distance_matrix(path))


def load_or_compute(
    name: str, dist: np.ndarray, k: int = 5, cache: Path = CACHE
) -> list[list[int]]:
    """Return the cached alpha candidate lists of instance ``name``, computing
    and caching them if absent.

    Raises:
        ValueError: If a cached file has the wrong number of cities.
    """
    path = cache / f"{name}-alpha{k}.json"
    if path.is_file():
        lists = json.loads(path.read_text())
        if len(lists) != len(dist):
            raise ValueError(f"{path}: {len(lists)} lists for {len(dist)} cities")
        return lists
    pi = ascend(dist.astype(float))
    lists = alpha_candidates(dist.astype(float), pi, k)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lists))
    LOGGER.info("%s: alpha candidates cached in %s", name, path)
    return lists


if __name__ == "__main__":
    import sys

    sys.exit(main())
