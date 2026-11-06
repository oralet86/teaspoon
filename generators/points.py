"""Point-process TSP generators written in numpy.

Two families that none of the vendored generators cover:

- ``shaken_grid``: a square lattice with every point moved by Gaussian
  noise of a given fraction of the lattice spacing, as in Fischer, Stützle,
  Hoos and Merz, "An analysis of the hardness of TSP instances for two
  high performance algorithms" (MIC 2005).  Many near-equal edges; the
  fraction dials between a perfect grid and near-uniform points.
- ``lgcp``: a log-Gaussian Cox process, the standard model of spatial
  statistics for points whose density varies smoothly at several scales
  (Møller, Syversveen and Waagepetersen, Scand. J. Stat. 1998).  The
  intensity is ``exp(Z)`` for a stationary Gaussian field ``Z`` with
  Gaussian covariance; the point count is fixed at ``n`` (the process
  conditioned on its count).

Points lie in ``[0, 10^6]^2`` with integer coordinates, the DIMACS range,
and are written as ``EUC_2D`` TSPLIB.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

SIDE = 1_000_000
"""Points lie in [0, SIDE]^2."""
LGCP_GRID = 512
"""Cells per side of the grid on which the Gaussian field is drawn."""


def shaken_grid(n: int, seed: int, shake: float) -> np.ndarray:
    """Return ``n`` points of a jittered square lattice.

    The lattice has ``ceil(sqrt(n))^2`` sites; the surplus sites are removed
    at random, then each point is moved by ``N(0, (shake * spacing)^2)``
    per axis and clipped to the square.

    Args:
        n: Number of points.
        seed: Seed of numpy's default generator.
        shake: Standard deviation of the jitter as a fraction of the spacing.

    Raises:
        ValueError: If ``n`` < 8 or ``shake`` is negative.
    """
    if n < 8:
        raise ValueError(f"n must be at least 8, got {n}")
    if shake < 0:
        raise ValueError(f"shake must be non-negative, got {shake}")
    rng = np.random.default_rng(seed)
    per_side = math.ceil(math.sqrt(n))
    spacing = SIDE / per_side
    rows, columns = np.divmod(np.arange(per_side * per_side), per_side)
    sites = np.column_stack(((columns + 0.5) * spacing, (rows + 0.5) * spacing))
    kept = sites[np.sort(rng.choice(len(sites), size=n, replace=False))]
    jittered = kept + rng.normal(0.0, shake * spacing, size=kept.shape)
    return np.clip(np.rint(jittered), 0, SIDE).astype(np.int64)


def lgcp(n: int, seed: int, variance: float, correlation: float) -> np.ndarray:
    """Return ``n`` points of a log-Gaussian Cox process on the square.

    The field is drawn on a periodic ``LGCP_GRID``-square grid by circulant
    embedding (FFT) with covariance ``variance * exp(-(d / correlation)^2)``;
    a cell is chosen with probability proportional to ``exp(Z)`` and the
    point is placed uniformly inside it.

    Args:
        n: Number of points.
        seed: Seed of numpy's default generator.
        variance: Variance of the Gaussian field; larger gives sharper
            contrast between dense and empty regions.
        correlation: Correlation length as a fraction of the side; smaller
            gives more, smaller dense regions.

    Raises:
        ValueError: If an argument is out of range.
    """
    if n < 8:
        raise ValueError(f"n must be at least 8, got {n}")
    if variance <= 0 or not 0 < correlation < 0.5:
        raise ValueError(
            f"need variance > 0 and 0 < correlation < 0.5, "
            f"got {variance} and {correlation}"
        )
    rng = np.random.default_rng(seed)
    offsets = np.minimum(np.arange(LGCP_GRID), LGCP_GRID - np.arange(LGCP_GRID))
    distance = np.hypot(offsets[:, None], offsets[None, :]) / LGCP_GRID
    covariance = variance * np.exp(-((distance / correlation) ** 2))
    spectrum = np.clip(np.fft.fft2(covariance).real, 0.0, None)
    noise = rng.normal(size=(LGCP_GRID, LGCP_GRID))
    field = np.fft.ifft2(np.sqrt(spectrum) * np.fft.fft2(noise)).real
    weights = np.exp(field - field.max()).ravel()
    cells = rng.choice(weights.size, size=n, p=weights / weights.sum())
    rows, columns = np.divmod(cells, LGCP_GRID)
    cell = SIDE / LGCP_GRID
    within = rng.random((n, 2))
    points = np.column_stack((columns + within[:, 0], rows + within[:, 1])) * cell
    return np.clip(np.rint(points), 0, SIDE).astype(np.int64)


def write_tsplib(points: np.ndarray, name: str, comment: str, path: Path) -> Path:
    """Write points as an ``EUC_2D`` TSPLIB file and return its path.

    Raises:
        ValueError: If ``points`` is not an ``(n, 2)`` array.
    """
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError(f"points must have shape (n, 2), got {points.shape}")
    lines = [
        f"NAME : {name}",
        f"COMMENT : {comment}",
        "TYPE : TSP",
        f"DIMENSION : {len(points)}",
        "EDGE_WEIGHT_TYPE : EUC_2D",
        "NODE_COORD_SECTION",
        *(f"{index} {x} {y}" for index, (x, y) in enumerate(points.tolist(), start=1)),
        "EOF",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path
