"""Parser tests against the bundled TSPLIB corpus."""

import math
from pathlib import Path

import numpy as np
import pytest

from instances.tsplib import TsplibError, load_instance, load_tours

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
TSP_ROOT = DATA_ROOT / "ALL_tsp"


def test_euclidean_instance_and_optimal_tour() -> None:
    instance = load_instance(
        TSP_ROOT / "a280.tsp", tour_path=TSP_ROOT / "a280.opt.tour"
    )
    assert instance.kind == "TSP"
    assert instance.dimension == 280
    assert instance.nodes is not None
    assert instance.nodes.coordinates.shape == (280, 2)
    assert instance.length_of is not None
    (sequence,) = instance.sequences
    assert sequence.closed
    assert sequence.num_stops == 280
    assert sequence.length == pytest.approx(2579.0)


def test_att_tour_length() -> None:
    instance = load_instance(
        TSP_ROOT / "att48.tsp", tour_path=TSP_ROOT / "att48.opt.tour"
    )
    (sequence,) = instance.sequences
    assert sequence.length == pytest.approx(10628.0)


def test_geo_tour_length_and_display_axes() -> None:
    instance = load_instance(
        TSP_ROOT / "gr96.tsp", tour_path=TSP_ROOT / "gr96.opt.tour"
    )
    (sequence,) = instance.sequences
    assert sequence.length == pytest.approx(55209.0)
    assert instance.metadata["Coordinates"] == "longitude, latitude"


def test_explicit_tour_length() -> None:
    instance = load_instance(
        TSP_ROOT / "gr48.tsp", tour_path=TSP_ROOT / "gr48.opt.tour"
    )
    (sequence,) = instance.sequences
    assert sequence.length == pytest.approx(5046.0)


def test_explicit_lower_column_matrix_for_cvrp() -> None:
    instance = load_instance(DATA_ROOT / "ALL_vrp" / "eil7.vrp")
    assert instance.kind == "CVRP"
    (layer,) = instance.matrices
    matrix = layer.values
    assert matrix.shape == (7, 7)
    assert matrix[1, 0] == 10  # d(2,1)
    assert matrix[6, 0] == 10  # d(7,1)
    assert matrix[2, 1] == 12  # d(3,2)
    assert matrix[6, 5] == 12  # d(7,6)
    assert np.array_equal(matrix, matrix.T)


def test_explicit_lower_diag_row_matrix() -> None:
    instance = load_instance(TSP_ROOT / "gr17.tsp")
    assert instance.kind == "TSP"
    assert instance.nodes is not None
    assert instance.nodes.coordinates.shape == (17, 2)
    assert instance.metadata["Coordinates"].startswith("MDS layout")
    (layer,) = instance.matrices
    matrix = layer.values
    assert matrix.shape == (17, 17)
    assert matrix[1, 0] == 633
    assert np.array_equal(matrix, matrix.T)
    assert np.allclose(np.diag(matrix), 0)


def test_atsp_full_matrix_is_asymmetric(tmp_path: Path) -> None:
    instance_path = tmp_path / "tiny.atsp"
    instance_path.write_text(
        "NAME : tiny\nTYPE : ATSP\nDIMENSION : 3\nEDGE_WEIGHT_TYPE : EXPLICIT\n"
        "EDGE_WEIGHT_FORMAT : FULL_MATRIX\nEDGE_WEIGHT_SECTION\n"
        "0 1 4\n2 0 5\n3 6 0\nEOF\n"
    )
    instance = load_instance(instance_path)
    assert instance.kind == "ATSP"
    assert instance.nodes is not None
    assert instance.nodes.coordinates.shape == (3, 2)
    (layer,) = instance.matrices
    matrix = layer.values
    assert matrix.shape == (3, 3)
    assert matrix[0, 2] == 4
    assert matrix[2, 0] == 3
    assert instance.length_of is not None
    assert instance.length_of(np.array([0, 1]), False) == pytest.approx(1)


def test_matrix_only_instance_gets_derived_layout() -> None:
    instance = load_instance(TSP_ROOT / "brazil58.tsp")
    assert instance.kind == "TSP"
    assert instance.nodes is not None
    assert instance.nodes.coordinates.shape == (58, 2)
    assert instance.metadata["Coordinates"].startswith("MDS layout")


def test_cvrp_demands_depot_and_capacity() -> None:
    instance = load_instance(DATA_ROOT / "ALL_vrp" / "eil22.vrp")
    assert instance.kind == "CVRP"
    assert instance.metadata["Capacity"] == "6000"
    assert instance.nodes is not None
    assert instance.nodes.categories is not None
    assert instance.nodes.categories[0] == 1
    assert int((instance.nodes.categories == 1).sum()) == 1
    demand = instance.nodes.attributes["demand"]
    assert demand.shape == (22,)
    assert demand[0] == 0
    assert demand.sum() > 0


def test_invalid_tour_is_rejected(tmp_path: Path) -> None:
    tour_path = tmp_path / "bad.tour"
    tour_path.write_text(
        "NAME : bad\nTYPE : TOUR\nDIMENSION : 3\nTOUR_SECTION\n1\n1\n2\n-1\nEOF\n"
    )
    with pytest.raises(TsplibError, match="more than once"):
        load_tours(tour_path, 3)


def test_non_numeric_coordinates_are_rejected(tmp_path: Path) -> None:
    instance_path = tmp_path / "broken.tsp"
    instance_path.write_text(
        "NAME : broken\nTYPE : TSP\nDIMENSION : 2\nEDGE_WEIGHT_TYPE : EUC_2D\n"
        "NODE_COORD_SECTION\n1 0 0\n2 a b\n"
    )
    with pytest.raises(TsplibError, match="non-numeric"):
        load_instance(instance_path)


def test_unknown_edge_weight_type_has_no_length_function(tmp_path: Path) -> None:
    instance_path = tmp_path / "custom.tsp"
    instance_path.write_text(
        "NAME : custom\nTYPE : TSP\nDIMENSION : 2\nEDGE_WEIGHT_TYPE : XRAY1\n"
        "NODE_COORD_SECTION\n1 10 20\n2 11 21\n"
    )
    instance = load_instance(instance_path)
    assert instance.length_of is None
    assert "Length function" in instance.metadata


def _concorde_geom_length(
    latitude_first: float,
    longitude_first: float,
    latitude_second: float,
    longitude_second: float,
) -> int:
    """Scalar transcription of Concorde's geom_edgelen for cross-checking."""
    lati = math.pi * latitude_first / 180.0
    latj = math.pi * latitude_second / 180.0
    longi = math.pi * longitude_first / 180.0
    longj = math.pi * longitude_second / 180.0
    q1 = math.cos(latj) * math.sin(longi - longj)
    q3 = math.sin((longi - longj) / 2.0)
    q4 = math.cos((longi - longj) / 2.0)
    q2 = math.sin(lati + latj) * q3 * q3 - math.sin(lati - latj) * q4 * q4
    q5 = math.cos(lati - latj) * q4 * q4 - math.cos(lati + latj) * q3 * q3
    return int(6378388.0 * math.atan2(math.sqrt(q1 * q1 + q2 * q2), q5) + 1.0)


def test_geom_length_matches_concorde_reference(tmp_path: Path) -> None:
    coordinate_pairs = [
        ((42.129085, -88.027485), (36.49, 7.49)),
        ((-54.9333, -67.6167), (86.2167, 180.0)),
        ((0.0, 0.0), (0.0, 179.9)),
        ((-77.51, 166.4), (-64.43, 64.03)),
    ]
    lines = [
        "NAME : geom_test",
        "TYPE : TSP",
        "DIMENSION : 8",
        "EDGE_WEIGHT_TYPE : GEOM",
        "NODE_COORD_SECTION",
    ]
    expected_distances = []
    node_id = 1
    for first, second in coordinate_pairs:
        lines.append(f"{node_id} {first[0]} {first[1]}")
        node_id += 1
        lines.append(f"{node_id} {second[0]} {second[1]}")
        node_id += 1
        expected_distances.append(_concorde_geom_length(*first, *second))
    instance_path = tmp_path / "geom.tsp"
    instance_path.write_text("\n".join(lines) + "\n")

    instance = load_instance(instance_path)
    assert instance.length_of is not None
    for position, expected in enumerate(expected_distances):
        pair = np.array([2 * position, 2 * position + 1])
        assert instance.length_of(pair, False) == pytest.approx(expected, abs=1)


@pytest.mark.integration
def test_world_instance_size() -> None:
    instance = load_instance(DATA_ROOT / "world.tsp")
    assert instance.dimension == 1_904_711
    assert instance.nodes is not None
    assert instance.nodes.coordinates.shape == (1_904_711, 2)
    assert instance.length_of is not None
    assert "Length function" not in instance.metadata
