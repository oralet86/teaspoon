"""TSPLIB parser producing format-agnostic instance objects.

Covers the instance families bundled under ``data/``:

* symmetric TSP with ``EUC_2D``, ``CEIL_2D``, ``ATT``, ``GEO``, ``GEOM`` and
  ``EXPLICIT`` edge weights in every matrix layout, including ``FULL_MATRIX``,
* CVRP with coordinates, demands, depot and capacity.

Files may be plain text or gzip-compressed.  Tour files such as
``a280.opt.tour`` and CVRPLIB solutions such as ``A-n32-k5.sol`` are loaded
into :class:`~instances.model.Sequence` objects with their length computed
from the instance's edge weights.
"""

from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Iterable
from pathlib import Path

import numpy as np

from .model import Instance, MatrixLayer, NodeSet, Sequence
from .registry import DatasetEntry, register_format

logger = logging.getLogger(__name__)

_GEO_RADIUS_KM = 6378.388
_GEOM_RADIUS_METRES = 6378388.0

_INSTANCE_SUFFIXES = (".tsp", ".vrp")
_TOUR_SUFFIXES = (".opt.tour", ".tour", ".sol")

_ROUTE_LINE_PATTERN = re.compile(r"^Route\s*#\s*(\d+)\s*:\s*(.*)$", re.IGNORECASE)

_SECTION_KEYWORDS = frozenset(
    {
        "NODE_COORD_SECTION",
        "DISPLAY_DATA_SECTION",
        "EDGE_WEIGHT_SECTION",
        "DEMAND_SECTION",
        "DEPOT_SECTION",
        "FIXED_EDGES_SECTION",
        "EDGE_DATA_SECTION",
        "TOUR_SECTION",
        "EOF",
    }
)

_TRIANGLE_FORMATS = frozenset(
    {
        "UPPER_ROW",
        "LOWER_ROW",
        "UPPER_DIAG_ROW",
        "LOWER_DIAG_ROW",
        "UPPER_COL",
        "LOWER_COL",
        "UPPER_DIAG_COL",
        "LOWER_DIAG_COL",
    }
)

_GEOGRAPHICAL_TYPES = frozenset({"GEO", "GEOM"})


class TsplibError(ValueError):
    """Raised when a TSPLIB file cannot be parsed."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


# ---------------------------------------------------------------------------
# Edge weight functions (TSPLIB distance definitions)
# ---------------------------------------------------------------------------


def _nint(values: np.ndarray) -> np.ndarray:
    """Round to the nearest integer the way the TSPLIB reference does."""
    return np.floor(values + 0.5)


def _euclidean_2d(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return _nint(np.sqrt(((first - second) ** 2).sum(axis=1)))


def _ceil_2d(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return np.ceil(np.sqrt(((first - second) ** 2).sum(axis=1)))


def _manhattan_2d(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return np.abs(first - second).sum(axis=1)


def _maximum_2d(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return np.abs(first - second).max(axis=1)


def _att(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Pseudo-Euclidean distance from the TSPLIB ``ATT`` definition."""
    raw = np.sqrt(((first - second) ** 2).sum(axis=1) / 10.0)
    rounded = _nint(raw)
    return np.where(rounded < raw, rounded + 1.0, rounded)


def _degrees_to_radians(values: np.ndarray) -> np.ndarray:
    """Convert TSPLIB degree/minute coordinates to radians."""
    degrees = np.trunc(values)
    minutes = values - degrees
    return np.pi * (degrees + 5.0 * minutes / 3.0) / 180.0


def _geographical(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Great-circle distance from the TSPLIB ``GEO`` definition.

    The first coordinate is latitude and the second is longitude, matching
    the TSPLIB reference implementation.
    """
    latitude_first = _degrees_to_radians(first[:, 0])
    longitude_first = _degrees_to_radians(first[:, 1])
    latitude_second = _degrees_to_radians(second[:, 0])
    longitude_second = _degrees_to_radians(second[:, 1])
    q1 = np.cos(longitude_first - longitude_second)
    q2 = np.cos(latitude_first - latitude_second)
    q3 = np.cos(latitude_first + latitude_second)
    argument = np.clip(0.5 * ((1.0 + q1) * q2 - (1.0 - q1) * q3), -1.0, 1.0)
    return np.floor(_GEO_RADIUS_KM * np.arccos(argument) + 1.0)


def _geometric(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Great-circle distance in metres from Concorde's ``GEOM`` definition.

    This transcribes the C routine ``geom_edgelen``: plain-degree latitude
    and longitude (first and second coordinate respectively), a sphere of
    radius 6378388 metres, and truncation to whole metres.  Unlike ``GEO``,
    which expects degree/minute notation, ``GEOM`` takes decimal degrees as
    used by ``world.tsp``.
    """
    latitude_first = np.radians(first[:, 0])
    latitude_second = np.radians(second[:, 0])
    longitude_first = np.radians(first[:, 1])
    longitude_second = np.radians(second[:, 1])
    longitude_delta = longitude_first - longitude_second
    q1 = np.cos(latitude_second) * np.sin(longitude_delta)
    q3 = np.sin(longitude_delta / 2.0)
    q4 = np.cos(longitude_delta / 2.0)
    q2 = (
        np.sin(latitude_first + latitude_second) * q3 * q3
        - np.sin(latitude_first - latitude_second) * q4 * q4
    )
    q5 = (
        np.cos(latitude_first - latitude_second) * q4 * q4
        - np.cos(latitude_first + latitude_second) * q3 * q3
    )
    return np.floor(
        _GEOM_RADIUS_METRES * np.arctan2(np.sqrt(q1 * q1 + q2 * q2), q5) + 1.0
    )


_PAIRWISE_DISTANCES = {
    "EUC_2D": _euclidean_2d,
    "CEIL_2D": _ceil_2d,
    "MAN_2D": _manhattan_2d,
    "MAX_2D": _maximum_2d,
    "ATT": _att,
    "GEO": _geographical,
    "GEOM": _geometric,
}


def _consecutive_pairs(
    node_indices: np.ndarray, closed: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Return the start and end node indices of every hop in a sequence."""
    if node_indices.size < 2:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    if closed:
        return node_indices, np.roll(node_indices, -1)
    return node_indices[:-1], node_indices[1:]


def _make_length_function(
    edge_weight_type: str,
    coordinates: np.ndarray | None,
    matrix: np.ndarray | None,
):
    """Build a callable measuring the length of a node-index sequence."""
    if edge_weight_type == "EXPLICIT":
        if matrix is None:
            return None

        def explicit_length(node_indices: np.ndarray, closed: bool) -> float:
            starts, ends = _consecutive_pairs(node_indices, closed)
            if starts.size == 0:
                return 0.0
            return float(matrix[starts, ends].sum())

        return explicit_length

    distance = _PAIRWISE_DISTANCES.get(edge_weight_type)
    if distance is None or coordinates is None:
        return None

    def computed_length(node_indices: np.ndarray, closed: bool) -> float:
        starts, ends = _consecutive_pairs(node_indices, closed)
        if starts.size == 0:
            return 0.0
        return float(distance(coordinates[starts], coordinates[ends]).sum())

    return computed_length


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    """Read a possibly gzipped TSPLIB file as text."""
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8")


def _looks_numeric(line: str) -> bool:
    try:
        int(line.split(maxsplit=1)[0])
    except IndexError, ValueError:
        return False
    return True


def _split_header_and_sections(
    text: str, path: Path
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Split TSPLIB text into header fields and named sections."""
    lines = text.splitlines()
    header: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        keyword = line.split(maxsplit=1)[0].upper()
        if keyword in _SECTION_KEYWORDS:
            break
        if ":" not in line:
            if _looks_numeric(line):
                # Tour files may omit every header field.
                sections = {
                    "TOUR_SECTION": [
                        remaining.strip()
                        for remaining in lines[index:]
                        if remaining.strip()
                    ]
                }
                return header, sections
            raise TsplibError(path, f"unrecognized header line {index + 1}: {line!r}")
        key, value = line.split(":", 1)
        header[key.strip().upper()] = value.strip()
        index += 1

    sections: dict[str, list[str]] = {}
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        keyword = line.split(maxsplit=1)[0].upper()
        if keyword == "EOF":
            break
        if keyword not in _SECTION_KEYWORDS:
            raise TsplibError(path, f"unrecognized section at line {index}: {line!r}")
        content: list[str] = []
        while index < len(lines):
            candidate = lines[index].strip()
            candidate_keyword = (
                candidate.split(maxsplit=1)[0].upper() if candidate else ""
            )
            if candidate_keyword in _SECTION_KEYWORDS:
                break
            if candidate:
                content.append(candidate)
            index += 1
        sections[keyword] = content
    return header, sections


def _parse_numbers(rows: list[str], path: Path, section: str) -> np.ndarray:
    """Parse whitespace-separated numbers from section rows."""
    if not rows:
        return np.empty(0, dtype=np.float64)
    try:
        return np.array(" ".join(rows).split(), dtype=np.float64)
    except ValueError as error:
        raise TsplibError(path, f"{section} contains a non-numeric value") from error


def _require_dimension(header: dict[str, str], path: Path) -> int:
    if "DIMENSION" not in header:
        raise TsplibError(path, "missing DIMENSION field")
    try:
        dimension = int(header["DIMENSION"])
    except ValueError as error:
        raise TsplibError(
            path, f"invalid DIMENSION value {header['DIMENSION']!r}"
        ) from error
    if dimension <= 0:
        raise TsplibError(path, f"DIMENSION must be positive, got {dimension}")
    return dimension


def _parse_coordinates(
    rows: list[str], dimension: int, path: Path, section: str
) -> tuple[np.ndarray, np.ndarray]:
    """Parse node coordinates, returning node ids and an ``(n, 2)`` array."""
    values = _parse_numbers(rows, path, section)
    if values.size == dimension * 3:
        node_ids = values[0::3]
        coordinates = np.column_stack((values[1::3], values[2::3]))
    elif values.size == dimension * 2:
        node_ids = np.arange(1, dimension + 1, dtype=np.float64)
        coordinates = values.reshape(dimension, 2)
    else:
        expected = f"{dimension * 3} (or {dimension * 2})"
        raise TsplibError(
            path, f"{section} has {values.size} values, expected {expected}"
        )
    return node_ids, coordinates


def _triangle_indices(
    dimension: int, edge_weight_format: str
) -> tuple[np.ndarray, np.ndarray]:
    """Return the matrix positions covered by a packed triangle format."""
    upper = edge_weight_format.startswith("UPPER")
    diagonal = "DIAG" in edge_weight_format
    column_major = edge_weight_format.endswith("_COL")
    if upper:
        offset = 0 if diagonal else 1
        row_indices, column_indices = np.triu_indices(dimension, offset)
    else:
        offset = 0 if diagonal else -1
        row_indices, column_indices = np.tril_indices(dimension, offset)
    if column_major:
        ordering = np.lexsort((row_indices, column_indices))
        row_indices = row_indices[ordering]
        column_indices = column_indices[ordering]
    return row_indices, column_indices


def _unpack_edge_weights(
    values: np.ndarray, dimension: int, edge_weight_format: str, path: Path
) -> np.ndarray:
    """Expand packed TSPLIB edge weights into a full matrix."""
    if edge_weight_format == "FULL_MATRIX":
        if values.size != dimension * dimension:
            raise TsplibError(
                path,
                "EDGE_WEIGHT_SECTION has "
                f"{values.size} values, expected {dimension * dimension}",
            )
        return values.reshape(dimension, dimension)
    if edge_weight_format not in _TRIANGLE_FORMATS:
        raise TsplibError(
            path, f"unsupported EDGE_WEIGHT_FORMAT {edge_weight_format!r}"
        )
    row_indices, column_indices = _triangle_indices(dimension, edge_weight_format)
    if values.size != row_indices.size:
        raise TsplibError(
            path,
            f"EDGE_WEIGHT_SECTION has {values.size} values, "
            f"expected {row_indices.size} for {edge_weight_format}",
        )
    matrix = np.zeros((dimension, dimension), dtype=np.float64)
    matrix[row_indices, column_indices] = values
    diagonal = np.diag(matrix).copy()
    return matrix + matrix.T - np.diag(diagonal)


def _parse_demands(rows: list[str], dimension: int, path: Path) -> np.ndarray:
    """Parse the demand section into an array indexed by node index."""
    values = _parse_numbers(rows, path, "DEMAND_SECTION")
    if values.size != dimension * 2:
        raise TsplibError(
            path,
            f"DEMAND_SECTION has {values.size} values, expected {dimension * 2}",
        )
    node_ids = values[0::2].astype(np.int64)
    if np.any(node_ids < 1) or np.any(node_ids > dimension):
        raise TsplibError(path, "DEMAND_SECTION contains an out-of-range node id")
    demands = np.zeros(dimension, dtype=np.float64)
    demands[node_ids - 1] = values[1::2]
    return demands


def _parse_depots(rows: list[str], dimension: int, path: Path) -> tuple[int, ...]:
    """Parse the depot section into zero-based node indices."""
    depots: list[int] = []
    for row in rows:
        for token in row.split():
            try:
                value = int(token)
            except ValueError as error:
                raise TsplibError(path, f"non-integer depot value {token!r}") from error
            if value == -1:
                break
            if value < 1 or value > dimension:
                raise TsplibError(path, f"depot id {value} out of range")
            depots.append(value - 1)
    return tuple(depots)


def _parse_tour_arrays(rows: list[str], dimension: int, path: Path) -> list[np.ndarray]:
    """Parse tour sections into zero-based node index arrays."""
    tours: list[np.ndarray] = []
    current: list[int] = []
    for row in rows:
        for token in row.split():
            try:
                value = int(token)
            except ValueError as error:
                raise TsplibError(path, f"non-integer tour value {token!r}") from error
            if value == -1:
                if current:
                    tours.append(np.array(current, dtype=np.int64) - 1)
                    current = []
                continue
            if value < 1:
                raise TsplibError(path, f"tour value {value} out of range")
            current.append(value)
    if current:
        tours.append(np.array(current, dtype=np.int64) - 1)
    for tour in tours:
        if tour.size != dimension:
            raise TsplibError(
                path,
                f"tour has {tour.size} stops, expected {dimension}",
            )
        if np.unique(tour).size != tour.size:
            raise TsplibError(path, "tour visits a node more than once")
    return tours


def _parse_cvrp_routes(text: str, path: Path) -> list[tuple[int, list[int]]]:
    """Parse the ``Route #N: ...`` lines of a CVRPLIB solution file.

    Lines that do not start with ``Route`` (such as ``Cost``) are ignored,
    matching the CVRPLIB convention.

    Returns:
        Routes as ``(route_number, customer_numbers)`` pairs sorted by
        route number.

    Raises:
        TsplibError: If no route line is found, a value is not a positive
            integer, or a route number appears more than once.
    """
    routes: dict[int, list[int]] = {}
    for line in text.splitlines():
        match = _ROUTE_LINE_PATTERN.match(line.strip())
        if match is None:
            continue
        number = int(match.group(1))
        if number in routes:
            raise TsplibError(path, f"route number {number} appears more than once")
        customers: list[int] = []
        for token in match.group(2).split():
            try:
                value = int(token)
            except ValueError as error:
                raise TsplibError(
                    path, f"route {number} contains a non-integer value {token!r}"
                ) from error
            if value < 1:
                raise TsplibError(
                    path, f"route {number} contains an out-of-range customer {value}"
                )
            customers.append(value)
        if not customers:
            raise TsplibError(path, f"route {number} is empty")
        routes[number] = customers
    if not routes:
        raise TsplibError(path, "no 'Route #N:' lines found")
    return sorted(routes.items())


def _strip_instance_suffixes(name: str) -> str:
    without_gz = name.removesuffix(".gz")
    stem, _separator, _suffix = without_gz.rpartition(".")
    return stem or without_gz


def _find_tour_companion(instance_path: Path) -> Path | None:
    """Find a tour or CVRPLIB solution next to an instance file, if any."""
    stem = _strip_instance_suffixes(instance_path.name)
    for tour_suffix in _TOUR_SUFFIXES:
        for suffix in (".gz", ""):
            candidate = instance_path.parent / f"{stem}{tour_suffix}{suffix}"
            if candidate.exists():
                return candidate
    return None


# ---------------------------------------------------------------------------
# Public loading API
# ---------------------------------------------------------------------------


def _single_depot_index(instance: Instance, path: Path) -> int:
    """Return the zero-based index of a CVRP instance's single depot.

    Raises:
        TsplibError: If the instance carries no depot category or more than
            one depot node.
    """
    nodes = instance.nodes
    if nodes is None or nodes.categories is None or "depot" not in nodes.category_names:
        raise TsplibError(path, "cannot locate the depot in the instance")
    depot_category = nodes.category_names.index("depot")
    depots = [
        int(index) for index in np.flatnonzero(nodes.categories == depot_category)
    ]
    if len(depots) != 1:
        raise TsplibError(path, f"expected exactly one depot, found {len(depots)}")
    return depots[0]


def load_solution(instance: Instance, path: Path) -> tuple[Sequence, ...]:
    """Load a CVRPLIB ``.sol`` file as sequences for a CVRP instance.

    CVRPLIB solutions number customers ``1..n-1`` and omit the depot.  The
    depot is prepended to every route, so the sequences are closed round
    trips in the viewer's zero-based node-index convention.

    Args:
        instance: The CVRP instance the solution was computed for.
        path: Solution file, plain or gzipped.

    Returns:
        One closed :class:`~instances.model.Sequence` per route.

    Raises:
        TsplibError: If the instance is not CVRP, the file has no usable
            routes, or the routes do not cover every customer exactly once.
        OSError: If the file cannot be read.
    """
    if instance.kind != "CVRP":
        raise TsplibError(
            path, f"a CVRPLIB solution needs a CVRP instance, got {instance.kind}"
        )
    if instance.dimension is None:
        raise TsplibError(path, "cannot validate a solution without a dimension")
    depot = _single_depot_index(instance, path)
    customer_indices = [index for index in range(instance.dimension) if index != depot]
    routes = _parse_cvrp_routes(_read_text(path), path)

    sequences: list[Sequence] = []
    covered: list[int] = []
    for number, customer_numbers in routes:
        node_indices = [depot]
        for customer_number in customer_numbers:
            if customer_number > len(customer_indices):
                raise TsplibError(
                    path,
                    f"route {number} customer {customer_number} is out of range "
                    f"1..{len(customer_indices)}",
                )
            node_indices.append(customer_indices[customer_number - 1])
        covered.extend(node_indices[1:])
        indices = np.array(node_indices, dtype=np.int64)
        length = None
        if instance.length_of is not None:
            length = float(instance.length_of(indices, True))
        label = path.name if len(routes) == 1 else f"{path.name} route {number}"
        sequences.append(
            Sequence(node_indices=indices, label=label, closed=True, length=length)
        )
    if sorted(covered) != customer_indices:
        raise TsplibError(path, "routes do not cover every customer exactly once")
    return tuple(sequences)


def load_instance(path: Path, tour_path: Path | None = None) -> Instance:
    """Load a TSPLIB instance, optionally attaching one tour file.

    Args:
        path: TSPLIB instance file, plain or gzipped.
        tour_path: Optional TSPLIB tour or CVRPLIB ``.sol`` file to attach
            as sequences.

    Raises:
        TsplibError: If the file is not valid TSPLIB.
        OSError: If the file cannot be read.
    """
    text = _read_text(path)
    header, sections = _split_header_and_sections(text, path)
    dimension = _require_dimension(header, path)
    raw_type = header.get("TYPE", "TSP").strip()
    problem_type = raw_type.split(maxsplit=1)[0].upper() if raw_type else "TSP"
    edge_weight_type = header.get("EDGE_WEIGHT_TYPE", "EXPLICIT").upper()
    edge_weight_format = header.get("EDGE_WEIGHT_FORMAT", "").upper() or None

    node_ids: np.ndarray | None = None
    raw_coordinates: np.ndarray | None = None
    if "NODE_COORD_SECTION" in sections:
        node_ids, raw_coordinates = _parse_coordinates(
            sections["NODE_COORD_SECTION"], dimension, path, "NODE_COORD_SECTION"
        )
    elif "DISPLAY_DATA_SECTION" in sections:
        node_ids, raw_coordinates = _parse_coordinates(
            sections["DISPLAY_DATA_SECTION"], dimension, path, "DISPLAY_DATA_SECTION"
        )

    matrix: np.ndarray | None = None
    if "EDGE_WEIGHT_SECTION" in sections:
        if edge_weight_format is None:
            raise TsplibError(
                path, "EDGE_WEIGHT_SECTION present without EDGE_WEIGHT_FORMAT"
            )
        matrix = _unpack_edge_weights(
            _parse_numbers(
                sections["EDGE_WEIGHT_SECTION"], path, "EDGE_WEIGHT_SECTION"
            ),
            dimension,
            edge_weight_format,
            path,
        )
    elif edge_weight_type == "EXPLICIT":
        raise TsplibError(path, "EXPLICIT edge weights without EDGE_WEIGHT_SECTION")

    demands: np.ndarray | None = None
    if "DEMAND_SECTION" in sections:
        demands = _parse_demands(sections["DEMAND_SECTION"], dimension, path)

    depots: tuple[int, ...] = ()
    if "DEPOT_SECTION" in sections:
        depots = _parse_depots(sections["DEPOT_SECTION"], dimension, path)

    labels: tuple[str, ...]
    display_coordinates: np.ndarray | None = None
    coordinates_source = ""
    if raw_coordinates is not None and node_ids is not None:
        labels = tuple(f"{value:g}" for value in node_ids)
        display_coordinates = raw_coordinates
        coordinates_source = "stored in file"
        if edge_weight_type in _GEOGRAPHICAL_TYPES and raw_coordinates.shape[1] == 2:
            # TSPLIB stores (latitude, longitude); show map-like axes instead.
            display_coordinates = raw_coordinates[:, ::-1].copy()
            coordinates_source = "longitude, latitude"
    else:
        # Matrix-only instances such as brazil58.tsp keep their annotations
        # but have no coordinates to draw.
        labels = tuple(str(index) for index in range(1, dimension + 1))

    categories: np.ndarray | None = None
    category_names: tuple[str, ...] = ()
    if depots:
        categories = np.zeros(dimension, dtype=np.int16)
        categories[list(depots)] = 1
        category_names = ("customer", "depot")

    attributes: dict[str, np.ndarray] = {}
    if demands is not None:
        attributes["demand"] = demands

    matrices: tuple[MatrixLayer, ...] = ()
    if matrix is not None:
        matrices = (
            MatrixLayer(
                values=matrix,
                label=f"edge weights ({edge_weight_format or edge_weight_type})",
                row_labels=labels or None,
                column_labels=labels or None,
            ),
        )

    nodes = NodeSet(
        coordinates=display_coordinates,
        labels=labels,
        categories=categories,
        category_names=category_names,
        attributes=attributes,
    )

    length_of = _make_length_function(edge_weight_type, raw_coordinates, matrix)
    if length_of is None:
        logger.warning(
            "no length function for EDGE_WEIGHT_TYPE %r in %s",
            edge_weight_type,
            path.name,
        )

    metadata = {
        "Name": header.get("NAME", _strip_instance_suffixes(path.name)),
        "Type": problem_type,
        "Comment": header.get("COMMENT", ""),
        "Dimension": str(dimension),
        "Edge weight type": edge_weight_type,
        "Edge weight format": edge_weight_format or "",
        "Capacity": header.get("CAPACITY", ""),
        "Coordinates": coordinates_source,
        "Length function": ""
        if length_of is not None
        else f"unavailable for {edge_weight_type}",
    }
    metadata = {key: value for key, value in metadata.items() if value}

    instance = Instance(
        name=header.get("NAME", _strip_instance_suffixes(path.name)),
        kind=problem_type,
        source_path=path,
        format_key=TsplibFormat.key,
        dimension=dimension,
        nodes=nodes,
        matrices=matrices,
        metadata=metadata,
        length_of=length_of,
    )
    if tour_path is not None:
        instance = instance.with_sequences(
            *TsplibFormat().load_tours(instance, tour_path)
        )
    return instance


def load_tours(path: Path, dimension: int) -> list[np.ndarray]:
    """Load all tours from a TSPLIB tour file as zero-based index arrays.

    Raises:
        TsplibError: If the file is not a valid tour file or a tour is not a
            permutation of ``dimension`` nodes.
    """
    text = _read_text(path)
    _header, sections = _split_header_and_sections(text, path)
    rows = sections.get("TOUR_SECTION")
    if rows is None:
        raise TsplibError(path, "no TOUR_SECTION found")
    return _parse_tour_arrays(rows, dimension, path)


class TsplibFormat:
    """Catalog format plugin for TSPLIB instances, tours and CVRPLIB solutions."""

    key = "tsplib"
    display_name = "TSPLIB"

    def matches(self, path: Path) -> bool:
        name = path.name.removesuffix(".gz").lower()
        return name.endswith(_INSTANCE_SUFFIXES)

    def scan_directory(self, directory: Path, group: str) -> Iterable[DatasetEntry]:
        for path in sorted(directory.iterdir()):
            if not path.is_file() or not self.matches(path):
                continue
            companions: dict[str, Path] = {}
            tour = _find_tour_companion(path)
            if tour is not None:
                companions["tour"] = tour
            yield DatasetEntry(
                label=path.name,
                path=path,
                group=group,
                format_key=self.key,
                companions=companions,
            )

    def load(self, entry: DatasetEntry) -> Instance:
        tour_path = entry.companions.get("tour") or _find_tour_companion(entry.path)
        return load_instance(entry.path, tour_path=tour_path)

    def load_tours(self, instance: Instance, path: Path) -> tuple[Sequence, ...]:
        if path.name.removesuffix(".gz").lower().endswith(".sol"):
            return load_solution(instance, path)
        if instance.dimension is None:
            raise TsplibError(path, "cannot validate tours without a dimension")
        arrays = load_tours(path, instance.dimension)
        sequences = []
        for position, array in enumerate(arrays):
            label = path.name if len(arrays) == 1 else f"{path.name} #{position + 1}"
            length = instance.length_of(array, True) if instance.length_of else None
            sequences.append(
                Sequence(node_indices=array, label=label, closed=True, length=length)
            )
        return tuple(sequences)


TSPLIB_FORMAT = TsplibFormat()
register_format(TSPLIB_FORMAT)
