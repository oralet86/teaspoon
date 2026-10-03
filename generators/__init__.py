"""Synthetic CVRP instance generation with the official Uchoa/XML generator."""

from .cvrp import (
    CustomerPositioning,
    DemandDistribution,
    DepotPositioning,
    RouteSize,
    XmlGeneratorError,
    find_generator_script,
    generate_batch,
    generate_cvrp,
)

__all__ = [
    "CustomerPositioning",
    "DemandDistribution",
    "DepotPositioning",
    "RouteSize",
    "XmlGeneratorError",
    "find_generator_script",
    "generate_batch",
    "generate_cvrp",
]
