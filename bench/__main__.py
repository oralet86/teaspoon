"""Command-line interface for the benchmark runner and the LKH-3 pilot.

Examples::

    uv run python -m bench run --suite smoke --budget 30 \\
        --seeds 0 --out runs/smoke
    uv run python -m bench report runs/smoke
    uv run python -m bench lkh-pilot --suite pilot-1h --dry-run
    uv run python -m bench lkh-report runs/pilot
"""

from __future__ import annotations

import argparse
import logging
import sys
import tarfile
from pathlib import Path

from .fetch_bks import DEFAULT_INDEX_URL, fetch_bks
from .fetch_lkh_benchmarks import BENCHMARK_TYPES, fetch_lkh_benchmarks
from .lkh_pilot import PilotSuite, build_blocks, load_suite, worst_case_seconds
from .lkh_report import summarize_pilot
from .lkh_runner import (
    KEEP_RUNNING,
    STOP_AFTER_BLOCK,
    STOP_NOW,
    archive_run,
    pilot_progress,
    run_pilot,
)
from .report import load_records, summarize, write_summary_csv
from .runner import RunSpec, run_specs
from .suites import DATA_ROOT, suite_instances

_SUITE_NAMES = ("smoke", "x", "xl", "ags")
_SOLVER_NAMES = ("hgs", "filo2", "pyvrp")
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
        help="solvers to run (default: all)",
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

    pilot = commands.add_parser(
        "lkh-pilot", help="run LKH-3 under each fixed setting of the pilot"
    )
    pilot.add_argument(
        "--suite", default="pilot-1h", help="pilot suite name or suite file"
    )
    pilot.add_argument(
        "--kinds", nargs="+", default=None, help="only these problem types"
    )
    pilot.add_argument(
        "--seeds", nargs="+", type=int, default=None, help="override the suite seeds"
    )
    pilot.add_argument(
        "--limit", type=int, default=None, help="use only the first N instances"
    )
    pilot.add_argument("--jobs", type=int, default=4, help="concurrent LKH processes")
    pilot.add_argument(
        "--max-minutes",
        type=float,
        default=60.0,
        help="refuse to start when the worst-case wall time is longer",
    )
    pilot.add_argument("--out", type=Path, default=None, help="run directory")
    pilot.add_argument(
        "--dry-run", action="store_true", help="print the plan without running"
    )
    existing = pilot.add_mutually_exclusive_group()
    existing.add_argument(
        "--resume",
        action="store_true",
        help="continue the run in --out without asking",
    )
    existing.add_argument(
        "--restart",
        action="store_true",
        help="start over in --out without asking; earlier results are moved aside",
    )

    pilot_report = commands.add_parser(
        "lkh-report", help="summarize an LKH-3 pilot run directory"
    )
    pilot_report.add_argument("run_dir", type=Path)

    fetch_lkh = commands.add_parser(
        "fetch-lkh", help="download the LKH-3 benchmark archives"
    )
    fetch_lkh.add_argument(
        "--types",
        nargs="+",
        default=list(BENCHMARK_TYPES),
        help="archive names (default: the pilot's problem types)",
    )
    fetch_lkh.add_argument(
        "--refresh", action="store_true", help="download and unpack again"
    )
    fetch_lkh.add_argument("--data-root", type=Path, default=DATA_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = parse_arguments(argv)
    if arguments.command == "report":
        return _report(arguments.run_dir, write_csv=arguments.csv)
    if arguments.command == "fetch-bks":
        return _fetch_bks(arguments)
    if arguments.command == "lkh-pilot":
        return _lkh_pilot(arguments)
    if arguments.command == "lkh-report":
        return _lkh_report(arguments.run_dir)
    if arguments.command == "fetch-lkh":
        return _fetch_lkh(arguments)
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


def _lkh_pilot(arguments: argparse.Namespace) -> int:
    try:
        suite = load_suite(arguments.suite)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    instances = suite.instances
    if arguments.kinds is not None:
        kinds = {kind.upper() for kind in arguments.kinds}
        instances = tuple(item for item in instances if item.kind in kinds)
    if arguments.limit is not None:
        instances = instances[: arguments.limit]
    seeds = suite.seeds if arguments.seeds is None else tuple(arguments.seeds)
    if not instances or not seeds or any(seed <= 0 for seed in seeds):
        print("error: no runs selected, or a seed is not positive", file=sys.stderr)
        return 1
    try:
        blocks = build_blocks(PilotSuite(suite.name, seeds, instances))
        worst_case = worst_case_seconds(blocks, jobs=arguments.jobs)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    counts = ", ".join(
        f"{kind} {sum(item.kind == kind for item in instances)}"
        for kind in sorted({item.kind for item in instances})
    )
    print(
        f"{suite.name}: {len(instances)} instances ({counts}), "
        f"{len(blocks)} blocks, {sum(len(block) for block in blocks)} runs, "
        f"worst case {worst_case / 60:.1f} min on {arguments.jobs} jobs"
    )
    if worst_case > arguments.max_minutes * 60:
        print(
            f"error: worst case exceeds --max-minutes {arguments.max_minutes:g}",
            file=sys.stderr,
        )
        return 1
    if arguments.dry_run:
        return 0
    if arguments.out is None:
        print("error: --out is required unless --dry-run is given", file=sys.stderr)
        return 1
    try:
        resume = _resolve_existing_run(arguments)
        if resume is None:
            return 1
        progress = run_pilot(
            blocks,
            arguments.out,
            jobs=arguments.jobs,
            resume=resume,
            on_interrupt=_ask_how_to_stop if sys.stdin.isatty() else None,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if progress.records:
        print(summarize_pilot([record.to_json() for record in progress.records]))
    print(
        f"{progress.completed_blocks} of {progress.total_blocks} blocks stored "
        f"in {arguments.out}"
    )
    if not progress.finished:
        print("stopped early; run the same command again to continue")
    return 0


def _resolve_existing_run(arguments: argparse.Namespace) -> bool | None:
    """Decide what to do with results already in the run directory.

    Returns:
        ``True`` to continue the earlier run, ``False`` to start fresh, or
        ``None`` when the command should not run.
    """
    progress = pilot_progress(arguments.out)
    if progress is None:
        return False
    completed, planned = progress
    choice = "c" if arguments.resume else "s" if arguments.restart else None
    if choice is None and not sys.stdin.isatty():
        print(
            f"error: {arguments.out} already holds {completed} of {planned} "
            "blocks; pass --resume or --restart",
            file=sys.stderr,
        )
        return None
    if choice is None:
        choice = _ask(
            f"{arguments.out} already holds {completed} of {planned} blocks.\n"
            "  [c] continue from there\n"
            "  [s] start over (the earlier results are moved aside, not deleted)\n"
            "  [q] quit\n"
            "Choice: ",
            "csq",
        )
    if choice == "q":
        return None
    if choice == "s":
        print(f"earlier results moved to {archive_run(arguments.out)}")
        return False
    return True


def _ask_how_to_stop() -> str:
    """Ask what a Ctrl+C should do while the pilot is running."""
    choice = _ask(
        "\nInterrupted. The running LKH processes are still going.\n"
        "  [n] stop now (the blocks in progress are discarded)\n"
        "  [a] stop after the blocks in progress have finished\n"
        "  [c] keep running\n"
        "Choice: ",
        "nac",
    )
    return {"n": STOP_NOW, "a": STOP_AFTER_BLOCK, "c": KEEP_RUNNING}[choice]


def _ask(prompt: str, choices: str) -> str:
    """Prompt until the answer starts with one of ``choices``.

    A closed input stream selects the first choice, which callers order so
    that it is the cautious one.
    """
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return choices[0]
        if answer[:1] in choices and answer:
            return answer[0]


def _lkh_report(run_dir: Path) -> int:
    try:
        records = load_records(run_dir)
        summary = summarize_pilot(records)
    except (FileNotFoundError, ValueError, KeyError) as error:
        print(f"error: cannot report on {run_dir}: {error!r}", file=sys.stderr)
        return 1
    print(summary)
    return 0


def _fetch_lkh(arguments: argparse.Namespace) -> int:
    try:
        entries = fetch_lkh_benchmarks(
            arguments.types,
            data_root=arguments.data_root,
            refresh=arguments.refresh,
        )
    except (OSError, ValueError, EOFError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"recorded {len(entries)} LKH-3 benchmark archives")
    return 0


if __name__ == "__main__":
    sys.exit(main())
