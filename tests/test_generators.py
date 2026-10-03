"""Tests for the vendored XML/Uchoa CVRP generator wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from generators import (
    CustomerPositioning,
    DemandDistribution,
    DepotPositioning,
    RouteSize,
    XmlGeneratorError,
    find_generator_script,
    generate_batch,
    generate_cvrp,
)
from instances import load_path

VENDOR_DIRECTORY = Path(__file__).resolve().parents[1] / "vendor"
GENERATOR_SCRIPT = VENDOR_DIRECTORY / "xml100" / "generator.py"

pytestmark = pytest.mark.skipif(
    not GENERATOR_SCRIPT.is_file(),
    reason="the vendored XML generator script is missing",
)


def test_find_generator_script_resolves_default() -> None:
    assert find_generator_script() == GENERATOR_SCRIPT


def test_generate_cvrp_parses_and_is_deterministic(tmp_path: Path) -> None:
    options = {
        "n": 20,
        "seed": 1234,
        "instance_id": 3,
        "depot_positioning": DepotPositioning.CENTERED,
        "customer_positioning": CustomerPositioning.CLUSTERED,
        "demand_distribution": DemandDistribution.SMALL_SMALL_VARIATION,
        "route_size": RouteSize.SHORT,
    }
    first = generate_cvrp(output_dir=tmp_path / "first", **options)
    second = generate_cvrp(output_dir=tmp_path / "second", **options)
    assert first.name == second.name
    assert first.read_bytes() == second.read_bytes()

    instance = load_path(first)
    assert instance.kind == "CVRP"
    assert instance.dimension == 21


def test_generate_batch_is_reproducible(tmp_path: Path) -> None:
    first = generate_batch(n=10, count=3, seed=7, output_dir=tmp_path / "first")
    second = generate_batch(n=10, count=3, seed=7, output_dir=tmp_path / "second")
    assert [path.name for path in first] == [path.name for path in second]
    assert len({path.name for path in first}) == 3
    for path in first:
        assert load_path(path).dimension == 11


def test_missing_script_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="XML generator script"):
        generate_cvrp(
            n=5,
            seed=0,
            output_dir=tmp_path,
            script=tmp_path / "does_not_exist.py",
        )


def test_invalid_size_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="n must be positive"):
        generate_cvrp(n=0, seed=0, output_dir=tmp_path)


def test_invalid_output_is_reported(tmp_path: Path) -> None:
    """A script that exits successfully without writing a file must fail."""
    broken = tmp_path / "broken_generator.py"
    broken.write_text("import sys\nsys.exit(0)\n")
    with pytest.raises(XmlGeneratorError, match="did not write"):
        generate_cvrp(n=5, seed=0, output_dir=tmp_path, script=broken)
