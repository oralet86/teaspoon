"""Command-line interface for synthetic CVRP instance generation.

Examples::

    uv run python -m generators --n 100 --count 20 --seed 0 --out data/generated/train
    uv run python -m generators --n 500 --count 1 --seed 7 --out data/generated/val \\
        --depot 2 --customers 3 --demands 6 --route-size 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cvrp import (
    CustomerPositioning,
    DemandDistribution,
    DepotPositioning,
    RouteSize,
    XmlGeneratorError,
    generate_batch,
)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, required=True, help="number of customers")
    parser.add_argument(
        "--count", type=int, default=1, help="number of instances to generate"
    )
    parser.add_argument("--seed", type=int, default=0, help="sampling seed")
    parser.add_argument(
        "--out", type=Path, required=True, help="directory for the .vrp files"
    )
    parser.add_argument(
        "--depot",
        type=int,
        choices=[int(member) for member in DepotPositioning],
        default=None,
        help="depot positioning family; sampled when omitted",
    )
    parser.add_argument(
        "--customers",
        type=int,
        choices=[int(member) for member in CustomerPositioning],
        default=None,
        help="customer positioning family; sampled when omitted",
    )
    parser.add_argument(
        "--demands",
        type=int,
        choices=[int(member) for member in DemandDistribution],
        default=None,
        help="demand distribution family; sampled when omitted",
    )
    parser.add_argument(
        "--route-size",
        type=int,
        choices=[int(member) for member in RouteSize],
        default=None,
        help="average route size family; sampled when omitted",
    )
    parser.add_argument(
        "--script",
        type=Path,
        default=None,
        help="generator script override (defaults to vendor/xml100/generator.py)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="skip parsing the generated files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        paths = generate_batch(
            n=arguments.n,
            count=arguments.count,
            seed=arguments.seed,
            output_dir=arguments.out,
            depot_positioning=(
                DepotPositioning(arguments.depot)
                if arguments.depot is not None
                else None
            ),
            customer_positioning=(
                CustomerPositioning(arguments.customers)
                if arguments.customers is not None
                else None
            ),
            demand_distribution=(
                DemandDistribution(arguments.demands)
                if arguments.demands is not None
                else None
            ),
            route_size=(
                RouteSize(arguments.route_size)
                if arguments.route_size is not None
                else None
            ),
            script=arguments.script,
            validate=not arguments.no_validate,
        )
    except (XmlGeneratorError, FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
