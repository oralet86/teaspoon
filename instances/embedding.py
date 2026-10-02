"""Coordinate layouts for instances whose files carry no coordinates.

Some TSPLIB instances (for example ``brazil58.tsp``) store only a distance
matrix.  A classical multidimensional scaling of that matrix gives a
map-like layout for the viewer while remaining clearly a derived artifact.
"""

from __future__ import annotations

import numpy as np


def classical_mds(distance_matrix: np.ndarray, num_components: int = 2) -> np.ndarray:
    """Embed a distance matrix in Euclidean space with classical MDS.

    Also known as principal coordinates analysis.  Asymmetric matrices, such
    as ATSP distance matrices, are symmetrized by averaging with their
    transpose.  The diagonal is ignored, which removes the large sentinel
    values that TSPLIB uses there.

    Args:
        distance_matrix: Square matrix of shape
            ``(num_nodes, num_nodes)``.
        num_components: Number of layout dimensions to return.

    Returns:
        Array of shape ``(num_nodes, num_components)`` with the derived
        coordinates.

    Raises:
        ValueError: If the matrix is not square.
    """
    values = np.asarray(distance_matrix, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError(f"distance matrix must be square, got shape {values.shape}")
    num_nodes = values.shape[0]
    distances = (values + values.T) / 2.0
    np.fill_diagonal(distances, 0.0)

    centering = np.eye(num_nodes) - np.full((num_nodes, num_nodes), 1.0 / num_nodes)
    gram = -0.5 * centering @ (distances**2) @ centering
    eigenvalues, eigenvectors = np.linalg.eigh(gram)

    order = np.argsort(eigenvalues)[::-1]
    leading_values = np.clip(eigenvalues[order][:num_components], 0.0, None)
    leading_vectors = eigenvectors[:, order][:, :num_components]
    return leading_vectors * np.sqrt(leading_values)
