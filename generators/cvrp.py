"""Wrapper around the official CVRPLIB (Uchoa/XML) CVRP generator.

The generator itself is a self-contained script published with the XML100
dataset at https://galgos.inf.puc-rio.br/cvrplib/index.php/en/xml100.

The generated instances follow the same design as the X, XL and XML100 sets:
depot positioning, customer positioning, demand distribution and average
route size are chosen from fixed families, which makes the output suitable
for training and validation data that matches the CVRPLIB benchmarks.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from enum import IntEnum
from pathlib import Path

from instances import load_path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GENERATOR_SCRIPT = REPOSITORY_ROOT / "vendor" / "xml100" / "generator.py"
_GENERATOR_ENVIRONMENT_VARIABLE = "XML_GENERATOR_SCRIPT"


class XmlGeneratorError(RuntimeError):
    """Raised when the XML generator cannot produce a valid instance."""


class DepotPositioning(IntEnum):
    """Depot placement families understood by the generator."""

    RANDOM = 1
    CENTERED = 2
    CORNERED = 3


class CustomerPositioning(IntEnum):
    """Customer placement families understood by the generator."""

    RANDOM = 1
    CLUSTERED = 2
    RANDOM_CLUSTERED = 3


class DemandDistribution(IntEnum):
    """Demand families understood by the generator."""

    UNITARY = 1
    SMALL_LARGE_VARIATION = 2
    SMALL_SMALL_VARIATION = 3
    LARGE_LARGE_VARIATION = 4
    LARGE_SMALL_VARIATION = 5
    LARGE_BY_QUADRANT = 6
    FEW_LARGE_MANY_SMALL = 7


class RouteSize(IntEnum):
    """Average route length families understood by the generator."""

    VERY_SHORT = 1
    SHORT = 2
    MEDIUM = 3
    LONG = 4
    VERY_LONG = 5
    ULTRA_LONG = 6


def find_generator_script(override: str | Path | None = None) -> Path:
    """Locate the vendored XML generator script.

    Search order: the ``override`` argument, the ``XML_GENERATOR_SCRIPT``
    environment variable, then ``vendor/xml100/generator.py``.

    Raises:
        FileNotFoundError: If no readable script is found.
    """
    candidates: list[Path] = []
    if override is not None:
        candidates.append(Path(override).expanduser())
    else:
        from_environment = os.environ.get(_GENERATOR_ENVIRONMENT_VARIABLE)
        if from_environment:
            candidates.append(Path(from_environment).expanduser())
        candidates.append(DEFAULT_GENERATOR_SCRIPT)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"could not find the XML generator script; searched {searched} "
        f"(set {_GENERATOR_ENVIRONMENT_VARIABLE} or download it from "
        "https://galgos.inf.puc-rio.br/cvrplib/index.php/en/xml100)"
    )


def generate_cvrp(
    *,
    n: int,
    seed: int,
    output_dir: str | Path,
    instance_id: int = 1,
    depot_positioning: DepotPositioning = DepotPositioning.RANDOM,
    customer_positioning: CustomerPositioning = CustomerPositioning.RANDOM,
    demand_distribution: DemandDistribution = DemandDistribution.UNITARY,
    route_size: RouteSize = RouteSize.MEDIUM,
    script: str | Path | None = None,
    validate: bool = True,
) -> Path:
    """Generate one CVRP instance with the official XML generator.

    Args:
        n: Number of customers; the instance has ``n + 1`` nodes.
        seed: Random seed forwarded to the generator.
        output_dir: Directory that receives the ``.vrp`` file.
        instance_id: Two-digit instance index used in the file name.
        depot_positioning: Depot placement family.
        customer_positioning: Customer placement family.
        demand_distribution: Demand family.
        route_size: Average route length family.
        script: Generator script override; defaults to the environment
            variable or the vendored copy.
        validate: Parse the generated file and check its shape.

    Returns:
        Path of the generated ``.vrp`` file.

    Raises:
        XmlGeneratorError: If the generator fails or writes an invalid file.
        FileNotFoundError: If the generator script is missing.
        ValueError: If ``n`` or ``instance_id`` is not positive.
    """
    if n < 1:
        raise ValueError(f"n must be positive, got {n}")
    if instance_id < 1:
        raise ValueError(f"instance_id must be positive, got {instance_id}")
    script_path = find_generator_script(script)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(script_path),
        str(n),
        str(int(depot_positioning)),
        str(int(customer_positioning)),
        str(int(demand_distribution)),
        str(int(route_size)),
        str(instance_id),
        str(seed),
    ]
    completed = subprocess.run(
        command,
        cwd=destination,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise XmlGeneratorError(
            f"the XML generator exited with code {completed.returncode}\n"
            f"stdout:\n{completed.stdout[-2000:]}\n"
            f"stderr:\n{completed.stderr[-2000:]}"
        )

    name = (
        f"XML{n}_"
        f"{int(depot_positioning)}{int(customer_positioning)}"
        f"{int(demand_distribution)}{int(route_size)}_"
        f"{instance_id:02d}.vrp"
    )
    path = destination / name
    if not path.is_file():
        raise XmlGeneratorError(
            f"the XML generator did not write {name}; "
            f"stdout:\n{completed.stdout[-2000:]}"
        )
    if validate:
        _validate_instance(path, n)
    return path


def generate_batch(
    *,
    n: int,
    count: int,
    seed: int,
    output_dir: str | Path,
    depot_positioning: DepotPositioning | None = None,
    customer_positioning: CustomerPositioning | None = None,
    demand_distribution: DemandDistribution | None = None,
    route_size: RouteSize | None = None,
    script: str | Path | None = None,
    validate: bool = True,
) -> tuple[Path, ...]:
    """Generate a batch of CVRP instances with reproducible class sampling.

    Class families that are ``None`` are sampled uniformly at random for
    every instance, using a dedicated random stream derived from ``seed``.

    Returns:
        The generated instance paths in generation order.
    """
    if count < 1:
        raise ValueError(f"count must be positive, got {count}")
    generator = random.Random(seed)
    paths: list[Path] = []
    for index in range(count):
        depot = depot_positioning or generator.choice(list(DepotPositioning))
        customers = customer_positioning or generator.choice(list(CustomerPositioning))
        demands = demand_distribution or generator.choice(list(DemandDistribution))
        route = route_size or generator.choice(list(RouteSize))
        paths.append(
            generate_cvrp(
                n=n,
                seed=generator.randrange(2**31),
                output_dir=output_dir,
                instance_id=index + 1,
                depot_positioning=depot,
                customer_positioning=customers,
                demand_distribution=demands,
                route_size=route,
                script=script,
                validate=validate,
            )
        )
    return tuple(paths)


def _validate_instance(path: Path, n: int) -> None:
    """Parse a generated instance and check its problem family and size.

    Raises:
        XmlGeneratorError: If the file is not a CVRP of ``n + 1`` nodes.
    """
    try:
        instance = load_path(path)
    except (OSError, ValueError) as error:
        raise XmlGeneratorError(
            f"{path.name} is not a valid instance: {error}"
        ) from error
    if instance.kind != "CVRP":
        raise XmlGeneratorError(f"{path.name} has type {instance.kind}, expected CVRP")
    if instance.dimension != n + 1:
        raise XmlGeneratorError(
            f"{path.name} has dimension {instance.dimension}, expected {n + 1}"
        )
