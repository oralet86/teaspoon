"""Command-line interface for the anytime CVRP benchmark runner.

Examples::

    uv run python -m bench run --suite smoke --budget 30 \\
        --seeds 0 --out experiments/runs/smoke
    uv run python -m bench report experiments/runs/smoke
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .report import load_records, summarize, write_summary_csv
from .runner import RunSpec, run_specs
from .suites import suite_instances

_SUITE_NAMES = ("smoke", "x", "xl", "ags")
_SOLVER_NAMES = ("hgs", "filo2")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m bench", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="execute benchmark runs")
    run.add_argument(
        "--suite",
        choices=_SUITE_NAMES,
        default="smoke",
        help="named instance suite (default: smoke)",
    )
    run.add_argument(
        "--limit", type=int, default=None, help="use only the first N instances"
    )
    run.add_argument(
        "--solvers",
        nargs="+",
        choices=_SOLVER_NAMES,
        default=list(_SOLVER_NAMES),
        help="solvers to run (default: both)",
    )
    run.add_argument("--seeds", nargs="+", type=int, default=[0], help="random seeds")
    run.add_argument("--budget", type=int, default=60, help="search budget in seconds")
    run.add_argument("--out", type=Path, required=True, help="run directory")
    run.add_argument(
        "--timeout-slack",
        type=float,
        default=120.0,
        help="extra seconds before a process is killed",
    )

    report = commands.add_parser("report", help="summarize a run directory")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--csv", action="store_true", help="also write summary.csv")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = parse_arguments(argv)
    if arguments.command == "report":
        return _report(arguments.run_dir, write_csv=arguments.csv)
    return _run(arguments)


def _run(arguments: argparse.Namespace) -> int:
    if arguments.budget <= 0:
        print("error: --budget must be positive", file=sys.stderr)
        return 1
    try:
        instances = suite_instances(arguments.suite)
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if arguments.limit is not None:
        instances = instances[: arguments.limit]
    specs = [
        RunSpec(
            instance_path=instance,
            solver=solver,
            seed=seed,
            budget_seconds=arguments.budget,
        )
        for instance in instances
        for solver in arguments.solvers
        for seed in arguments.seeds
    ]
    if not specs:
        print("error: no runs selected", file=sys.stderr)
        return 1
    logging.info("running %d specs into %s", len(specs), arguments.out)
    records = run_specs(
        specs,
        arguments.out,
        timeout_slack_seconds=arguments.timeout_slack,
    )
    print(summarize([record.to_json() for record in records]))
    print(f"results written to {arguments.out}")
    return 0


def _report(run_dir: Path, *, write_csv: bool) -> int:
    try:
        records = load_records(run_dir)
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(summarize(records))
    if write_csv:
        path = write_summary_csv(run_dir, records)
        print(f"summary written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
