"""Command-line interface for the anytime CVRP benchmark runner.

Examples::

    uv run python -m bench run --suite smoke --budget 30 \\
        --seeds 0 --out runs/smoke
    uv run python -m bench report runs/smoke
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .fetch_bks import DEFAULT_INDEX_URL, fetch_bks
from .report import load_records, summarize, write_summary_csv
from .runner import RunSpec, run_specs
from .suites import DATA_ROOT, suite_instances

_SUITE_NAMES = ("smoke", "x", "xl", "ags")
_SOLVER_NAMES = ("hgs", "filo2")
_BKS_SUITE_NAMES = ("x", "xl", "ags")


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

    fetch = commands.add_parser(
        "fetch-bks",
        help="download and verify the best-known solutions of a suite",
    )
    fetch.add_argument(
        "--suites",
        nargs="+",
        choices=_BKS_SUITE_NAMES,
        default=list(_BKS_SUITE_NAMES),
        help="suites to adopt, verify or refresh (default: all)",
    )
    fetch.add_argument(
        "--refresh",
        action="store_true",
        help="re-download solution files that already exist",
    )
    fetch.add_argument(
        "--data-root",
        type=Path,
        default=DATA_ROOT,
        help="root that holds the X/, XL/ and AGS/ folders",
    )
    fetch.add_argument(
        "--index-url",
        default=DEFAULT_INDEX_URL,
        help="CVRPLIB instance index, or a local file:// copy",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = parse_arguments(argv)
    if arguments.command == "report":
        return _report(arguments.run_dir, write_csv=arguments.csv)
    if arguments.command == "fetch-bks":
        return _fetch_bks(arguments)
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


def _fetch_bks(arguments: argparse.Namespace) -> int:
    try:
        entries = fetch_bks(
            arguments.suites,
            data_root=arguments.data_root,
            index_url=arguments.index_url,
            refresh=arguments.refresh,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"recorded {len(entries)} best-known solutions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
