"""Format-agnostic data model for routing instances.

Every instance format is translated into these objects.
The viewer consumes only this model, so adding a new data source is easy.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np


@dataclass(eq=False, frozen=True, slots=True)
class NodeSet:
    """Positions and per-node annotations shared by every instance format.

    Attributes:
        coordinates: Array of shape ``(num_nodes, 2)`` holding display
            coordinates, or ``None`` when the file stores no positions.
        labels: Human-readable label for each node, e.g. the TSPLIB node id.
        categories: Optional array of shape ``(num_nodes,)`` with small
            integer indices into ``category_names`` (depot vs. customer).
        category_names: Names for the category indices.
        attributes: Optional named numeric series of shape ``(num_nodes,)``,
            e.g. ``{"demand": ...}`` for vehicle routing instances.
    """

    coordinates: np.ndarray | None
    labels: tuple[str, ...]
    categories: np.ndarray | None = None
    category_names: tuple[str, ...] = ()
    attributes: Mapping[str, np.ndarray] = field(default_factory=dict)

    @property
    def num_nodes(self) -> int:
        """Number of nodes in the set."""
        if self.coordinates is not None:
            return int(self.coordinates.shape[0])
        return len(self.labels)


@dataclass(eq=False, frozen=True, slots=True)
class Sequence:
    """An ordered visit of nodes, for example a TSP tour or a delivery route.

    Attributes:
        node_indices: Zero-based indices into the owning instance's node set.
        label: Human-readable name shown in the viewer.
        closed: Whether the sequence returns to its first node.
        length: Precomputed length when the format supplies one.
    """

    node_indices: np.ndarray
    label: str
    closed: bool = True
    length: float | None = None

    @property
    def num_stops(self) -> int:
        """Number of visits in the sequence."""
        return int(self.node_indices.size)


@dataclass(eq=False, frozen=True, slots=True)
class MatrixLayer:
    """A square or rectangular numeric matrix, e.g. explicit edge weights.

    Attributes:
        values: The matrix itself, shape ``(num_rows, num_columns)``.
        label: Name shown in the viewer's matrix tab.
        row_labels: Optional label for each row.
        column_labels: Optional label for each column.
    """

    values: np.ndarray
    label: str
    row_labels: tuple[str, ...] | None = None
    column_labels: tuple[str, ...] | None = None

    @property
    def shape(self) -> tuple[int, int]:
        """Matrix shape as ``(num_rows, num_columns)``."""
        return (int(self.values.shape[0]), int(self.values.shape[1]))


LengthFunction = Callable[[np.ndarray, bool], float]
"""Computes the length of a node-index sequence; the flag marks closed tours."""


@dataclass(eq=False, frozen=True, slots=True)
class Instance:
    """A single routing instance in a viewer-ready, format-agnostic form.

    Attributes:
        name: Display name of the instance.
        kind: Problem family, e.g. ``"TSP"`` or ``"CVRP"``.
        source_path: File the instance was loaded from.
        format_key: Registry key of the format that produced the instance.
        dimension: Expected number of nodes, when known.
        nodes: Node labels and annotations, or ``None`` when the format
            supplies no node information.  Coordinates inside the set are
            ``None`` for matrix-only instances such as ``brazil58``.
        sequences: Visit sequences, e.g. optimal tours or delivery routes.
        matrices: Numeric matrices such as explicit edge weights.
        metadata: Ordered key/value pairs shown in the viewer's stats panel.
        length_of: Optional callable computing the length of a sequence.
    """

    name: str
    kind: str
    source_path: Path
    format_key: str
    dimension: int | None = None
    nodes: NodeSet | None = None
    sequences: tuple[Sequence, ...] = ()
    matrices: tuple[MatrixLayer, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    length_of: LengthFunction | None = None

    def with_sequences(self, *sequences: Sequence) -> Instance:
        """Return a copy with additional sequences appended."""
        return replace(self, sequences=self.sequences + sequences)

    def compute_length(self, sequence: Sequence) -> float | None:
        """Return the sequence length, using the stored value when present."""
        if sequence.length is not None:
            return sequence.length
        if self.length_of is None:
            return None
        return self.length_of(sequence.node_indices, sequence.closed)
