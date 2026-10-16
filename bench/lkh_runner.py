"""Parallel execution of the LKH-3 setting pilot.

Unlike :mod:`bench.runner`, which runs solvers one at a time so their
wall-clock metrics stay comparable, the pilot runs the arms of one block
concurrently.  The comparison is between those arms, and running them side
by side exposes them to the same machine load.  Records are written
incrementally to ``runs.jsonl``, with one trace CSV and the raw LKH output
per run.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

from solvers import (
    LkhReport,
    SolverCancelledError,
    SolverError,
    find_executable,
    run_lkh_reported,
)

from .lkh_pilot import TIERS, Block, PilotRun, build_waves
from .metadata import file_sha256, host_metadata

logger = logging.getLogger(__name__)

RUNS_FILE_NAME = "runs.jsonl"
CONFIG_FILE_NAME = "config.json"
STOP_NOW = "now"
STOP_AFTER_BLOCK = "after_block"
KEEP_RUNNING = "continue"
_POLL_SECONDS = 0.2

# Problem types whose LKH tours can be re-checked by the Python-side
# validation in :mod:`solvers.lkh`.
_VALIDATED_KINDS = frozenset({"TSP", "CVRP"})


@dataclass(frozen=True, slots=True)
class PilotRecord:
    """The outcome of one pilot run, ready for JSON serialization.

    ``status`` is ``"ok"`` for a run LKH ended itself, ``"killed"`` for a run
    stopped at the deadline that still reported a solution, and ``"error"``
    otherwise.  ``gap`` is only set for a feasible solution with a known
    best-known cost.  ``validated_cost`` is the cost recomputed in Python
    from LKH's tour after checking coverage and capacity, available for TSP
    and CVRP; ``validation_error`` says why that check failed.  Times are
    LKH's own search seconds except ``runtime_seconds`` and
    ``preprocessing_seconds``, which are wall-clock.
    """

    instance: str
    kind: str
    tier: str
    arm: str
    seed: int
    budget_seconds: int
    deadline_seconds: int
    status: str
    penalty: int | None = None
    cost: int | None = None
    bks: float | None = None
    gap: float | None = None
    reached_bks: bool | None = None
    time_to_bks: float | None = None
    time_to_best: float | None = None
    preprocessing_seconds: float | None = None
    trials: int | None = None
    improvements: int = 0
    validated_cost: float | None = None
    validation_error: str | None = None
    runtime_seconds: float | None = None
    executable: str | None = None
    executable_sha256: str | None = None
    trace_file: str | None = None
    log_file: str | None = None
    command: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return the record as a JSON-serializable dictionary."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PilotProgress:
    """What a pilot session left in its run directory.

    Attributes:
        records: Records of every completed block, earlier sessions included.
        completed_blocks: Blocks whose records are stored.
        total_blocks: Blocks in the plan.
        stopped: Whether the session ended on request before the plan did.
    """

    records: tuple[PilotRecord, ...]
    completed_blocks: int
    total_blocks: int
    stopped: bool

    @property
    def finished(self) -> bool:
        """Whether every planned block is stored."""
        return self.completed_blocks == self.total_blocks


def run_pilot(
    blocks: Sequence[Block],
    output_dir: str | Path,
    *,
    jobs: int = 4,
    executable: str | Path | None = None,
    resume: bool = False,
    on_interrupt: Callable[[], str] | None = None,
) -> PilotProgress:
    """Run every block, ``jobs`` LKH processes at a time, and persist results.

    Blocks run in waves of as many whole blocks as fit into ``jobs``
    processes (see :func:`~bench.lkh_pilot.build_waves`).  A wave's records
    are written together once all its runs have ended, so ``runs.jsonl``
    only ever holds complete blocks and a stopped pilot can be continued
    with ``resume``.

    While the pilot runs, Ctrl+C does not reach the LKH processes.  It calls
    ``on_interrupt``, which answers with one of :data:`STOP_NOW` (kill the
    running blocks and discard them), :data:`STOP_AFTER_BLOCK` or
    :data:`KEEP_RUNNING`.  Without a callback, and on ``SIGTERM``, the pilot
    stops at once.  Signals are only handled when called from the main
    thread.

    Args:
        blocks: Blocks from :func:`~bench.lkh_pilot.build_blocks`.
        output_dir: Directory that receives ``runs.jsonl``, ``config.json``,
            ``traces/`` and ``logs/``.
        jobs: Concurrent LKH processes.
        executable: LKH binary override, mainly for tests.
        resume: Continue a stopped pilot in ``output_dir``, skipping the
            blocks it already holds.
        on_interrupt: Decides what a Ctrl+C does.

    Returns:
        The records of all completed blocks, including resumed ones, and
        how many blocks are done.

    Raises:
        ValueError: If ``jobs`` is not positive, or when resuming a
            directory that was created with a different plan or LKH binary.
        FileExistsError: If the directory already holds a ``runs.jsonl``
            and ``resume`` is not set.
        FileNotFoundError: If no LKH executable can be found.
    """
    if jobs <= 0:
        raise ValueError(f"jobs must be positive, got {jobs}")
    destination = Path(output_dir)
    runs_path = destination / RUNS_FILE_NAME
    blocks = [block for block in blocks if block]
    binary = find_executable("lkh", executable)
    session = _session_metadata(jobs=jobs, binary=binary)
    records: list[PilotRecord] = []
    sessions: list[dict[str, Any]] = []
    if runs_path.exists():
        if not resume:
            raise FileExistsError(
                f"{runs_path} already exists; resume it or choose a new directory"
            )
        sessions = _require_same_plan(destination, blocks, session)
        records = _completed_records(destination, blocks)
    done = {(record.instance, record.seed) for record in records}
    pending = [block for block in blocks if _block_key(block) not in done]
    waves = build_waves(pending, jobs=jobs)

    binary_sha256 = str(session["executable_sha256"])
    (destination / "traces").mkdir(parents=True, exist_ok=True)
    (destination / "logs").mkdir(parents=True, exist_ok=True)
    _write_config(destination, blocks, [*sessions, session])
    # Rewriting the file drops the records of any half-written block.
    runs_path.write_text(
        "".join(json.dumps(record.to_json()) + "\n" for record in records),
        encoding="utf-8",
    )

    signals = _SignalFlags()
    cancel_event = threading.Event()
    stop_after_block = False
    stopped = False
    with (
        _handle_signals(signals),
        runs_path.open("a", encoding="utf-8") as runs_file,
        ThreadPoolExecutor(max_workers=jobs) as executor,
    ):
        started_blocks = len(blocks) - len(pending)
        for wave in waves:
            logger.info(
                "blocks %d-%d of %d: %s",
                started_blocks + 1,
                started_blocks + len(wave),
                len(blocks),
                ", ".join(
                    f"{block[0].instance.name} seed {block[0].seed}" for block in wave
                ),
            )
            started_blocks += len(wave)
            futures = [
                executor.submit(
                    _run_one, run, destination, binary, binary_sha256, cancel_event
                )
                for block in wave
                for run in block
            ]
            while wait(futures, timeout=_POLL_SECONDS).not_done:
                if signals.terminate:
                    cancel_event.set()
                elif signals.interrupt:
                    signals.interrupt = False
                    choice = STOP_NOW if on_interrupt is None else on_interrupt()
                    if choice == STOP_NOW:
                        cancel_event.set()
                    elif choice == STOP_AFTER_BLOCK:
                        stop_after_block = True
            block_records = [future.result() for future in futures]
            if cancel_event.is_set():
                stopped = True
                break
            for record in block_records:
                records.append(record)
                runs_file.write(json.dumps(record.to_json()) + "\n")
            runs_file.flush()
            if stop_after_block or signals.terminate:
                stopped = True
                break
    return PilotProgress(
        records=tuple(records),
        completed_blocks=len({(record.instance, record.seed) for record in records}),
        total_blocks=len(blocks),
        stopped=stopped,
    )


def pilot_progress(output_dir: str | Path) -> tuple[int, int] | None:
    """Return ``(completed, planned)`` blocks of a run directory.

    Returns:
        The counts, or ``None`` when the directory holds no pilot run.

    Raises:
        ValueError: If the stored files are corrupt.
    """
    destination = Path(output_dir)
    runs_path = destination / RUNS_FILE_NAME
    config_path = destination / CONFIG_FILE_NAME
    if not runs_path.is_file() or not config_path.is_file():
        return None
    planned = json.loads(config_path.read_text(encoding="utf-8")).get("runs", [])
    completed = {
        (record["instance"], record["seed"]) for record in _read_record_lines(runs_path)
    }
    return len(completed), len({(run["instance"], run["seed"]) for run in planned})


def archive_run(output_dir: str | Path) -> Path:
    """Move a run directory's results aside so the pilot can start over.

    Nothing is deleted: the records, configuration, traces and logs move to
    a ``superseded-<timestamp>`` folder inside the run directory.

    Returns:
        The folder that now holds the earlier results.
    """
    destination = Path(output_dir)
    archive = destination / f"superseded-{time.strftime('%Y%m%d-%H%M%S')}"
    archive.mkdir(parents=True)
    for name in (RUNS_FILE_NAME, CONFIG_FILE_NAME, "traces", "logs"):
        if (destination / name).exists():
            (destination / name).rename(archive / name)
    return archive


class _SignalFlags:
    """Signals received since the pilot last looked."""

    def __init__(self) -> None:
        self.interrupt = False
        self.terminate = False


@contextmanager
def _handle_signals(flags: _SignalFlags) -> Iterator[None]:
    """Record SIGINT and SIGTERM in ``flags`` instead of raising.

    Outside the main thread Python cannot install handlers, so the signals
    keep their previous behaviour.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def on_interrupt(_signum: int, _frame: FrameType | None) -> None:
        flags.interrupt = True

    def on_terminate(_signum: int, _frame: FrameType | None) -> None:
        flags.terminate = True

    previous_interrupt = signal.signal(signal.SIGINT, on_interrupt)
    previous_terminate = signal.signal(signal.SIGTERM, on_terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous_interrupt)
        signal.signal(signal.SIGTERM, previous_terminate)


def _block_key(block: Block) -> tuple[str, int]:
    """Return the (instance, seed) pair that identifies a block."""
    return block[0].instance.name, block[0].seed


def _planned_runs(blocks: Sequence[Block]) -> list[dict[str, Any]]:
    """Describe every planned run for ``config.json`` and plan comparison."""
    return [
        {
            "instance": run.instance.name,
            "problem": str(run.instance.problem_path),
            "kind": run.instance.kind,
            "tier": run.instance.tier,
            "arm": run.arm.name,
            "seed": run.seed,
            "base_parameters": dict(run.instance.base_parameters),
            "overrides": run.arm.overrides(run.instance.base_parameters),
        }
        for block in blocks
        for run in block
    ]


def _require_same_plan(
    destination: Path, blocks: Sequence[Block], session: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Check that a run directory can be continued by this session.

    The runs, the tiers and the LKH binary must match, because results from
    different plans or binaries cannot be compared.  A different number of
    jobs is allowed but logged, since it changes the machine load.

    Returns:
        The metadata of the earlier sessions.

    Raises:
        ValueError: If the stored configuration differs or cannot be read.
    """
    config_path = destination / CONFIG_FILE_NAME
    if not config_path.is_file():
        raise ValueError(f"{destination} has no {CONFIG_FILE_NAME} to resume from")
    stored = json.loads(config_path.read_text(encoding="utf-8"))
    tiers = {name: asdict(tier) for name, tier in TIERS.items()}
    if stored.get("runs") != _planned_runs(blocks) or stored.get("tiers") != tiers:
        raise ValueError(
            f"{destination} was created with a different plan; start over or "
            "use a new run directory"
        )
    sessions = stored.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError(f"{config_path} records no earlier session")
    first = sessions[0]
    if first.get("executable_sha256") != session["executable_sha256"]:
        raise ValueError(
            f"{destination} was created with a different LKH binary; start over "
            "or use a new run directory"
        )
    if first.get("jobs") != session["jobs"]:
        logger.warning(
            "resuming with %s jobs a run that started with %s; later blocks "
            "run under a different machine load",
            session["jobs"],
            first.get("jobs"),
        )
    return sessions


def _read_record_lines(runs_path: Path) -> list[dict[str, Any]]:
    """Read ``runs.jsonl``, dropping a final line cut off mid-write.

    Raises:
        ValueError: If a line other than the last is not valid JSON.
    """
    lines = [
        line
        for line in runs_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    loaded: list[dict[str, Any]] = []
    for position, line in enumerate(lines, start=1):
        try:
            loaded.append(json.loads(line))
        except json.JSONDecodeError as error:
            if position < len(lines):
                raise ValueError(
                    f"{runs_path} line {position} is not valid JSON"
                ) from error
            logger.warning("%s ends in a cut-off record; ignoring it", runs_path)
    return loaded


def _completed_records(destination: Path, blocks: Sequence[Block]) -> list[PilotRecord]:
    """Load the records of blocks that finished in an earlier session."""
    loaded = [
        PilotRecord(**fields)
        for fields in _read_record_lines(destination / RUNS_FILE_NAME)
    ]
    by_block: dict[tuple[str, int], list[PilotRecord]] = {}
    for record in loaded:
        by_block.setdefault((record.instance, record.seed), []).append(record)
    return [
        record
        for block in blocks
        if len(by_block.get(_block_key(block), [])) == len(block)
        for record in by_block[_block_key(block)]
    ]


def _run_one(
    run: PilotRun,
    destination: Path,
    binary: Path,
    binary_sha256: str,
    cancel_event: threading.Event,
) -> PilotRecord:
    """Execute one run and translate the outcome into a record."""
    tier = TIERS[run.instance.tier]
    common: dict[str, Any] = {
        "instance": run.instance.name,
        "kind": run.instance.kind,
        "tier": run.instance.tier,
        "arm": run.arm.name,
        "seed": run.seed,
        "budget_seconds": tier.budget_seconds,
        "deadline_seconds": tier.deadline_seconds,
        "bks": run.instance.bks,
        "executable": str(binary),
        "executable_sha256": binary_sha256,
    }
    try:
        report = run_lkh_reported(
            run.instance.problem_path,
            time_limit_seconds=tier.budget_seconds,
            base_parameters=run.instance.base_parameters,
            overrides=run.arm.overrides(run.instance.base_parameters),
            seed=run.seed,
            deadline_seconds=tier.deadline_seconds,
            validate=run.instance.kind in _VALIDATED_KINDS,
            cancel_event=cancel_event,
            executable=binary,
        )
    except SolverCancelledError as error:
        return PilotRecord(status="cancelled", error=str(error), **common)
    except (SolverError, OSError) as error:
        logger.warning("%s failed: %s", run.label, error)
        return PilotRecord(status="error", error=str(error), **common)

    bks = run.instance.bks
    feasible = report.penalty == 0
    reaching = [
        point
        for point in report.trace
        if bks is not None and point.penalty == 0 and point.cost <= bks
    ]
    best_point = min(report.trace, key=lambda point: (point.penalty, point.cost))
    first_point = report.trace[0]
    return PilotRecord(
        status="killed" if report.killed else "ok",
        penalty=report.penalty,
        cost=report.cost,
        gap=(report.cost - bks) / bks if bks is not None and feasible else None,
        reached_bks=None if bks is None else bool(reaching),
        time_to_bks=reaching[0].solver_seconds if reaching else None,
        time_to_best=best_point.solver_seconds,
        preprocessing_seconds=max(
            0.0, first_point.wall_seconds - first_point.solver_seconds
        ),
        trials=report.trials,
        improvements=len(report.trace),
        validated_cost=report.validated_cost,
        validation_error=report.validation_error,
        runtime_seconds=report.runtime_seconds,
        trace_file=_write_trace(destination, run.label, report),
        log_file=_write_log(destination, run.label, report),
        command=list(report.command),
        **common,
    )


def _write_trace(destination: Path, label: str, report: LkhReport) -> str:
    """Write the improvement trace as CSV and return its run-relative path."""
    relative = f"traces/{label}.csv"
    lines = ["wall_seconds,solver_seconds,trial,penalty,cost"]
    lines += [
        f"{point.wall_seconds:.6f},{point.solver_seconds:.2f},"
        f"{point.trial},{point.penalty},{point.cost}"
        for point in report.trace
    ]
    (destination / relative).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return relative


def _write_log(destination: Path, label: str, report: LkhReport) -> str:
    """Write the parameter file and raw LKH output of a run."""
    relative = f"logs/{label}.log"
    (destination / relative).write_text(
        f"--- parameters ---\n{report.parameters}"
        f"--- stdout ---\n{report.stdout}\n--- stderr ---\n{report.stderr}\n",
        encoding="utf-8",
    )
    return relative


def _write_config(
    destination: Path, blocks: Sequence[Block], sessions: Sequence[Mapping[str, Any]]
) -> None:
    """Write the plan and the metadata of every session as ``config.json``."""
    configuration = {
        "sessions": list(sessions),
        "tiers": {name: asdict(tier) for name, tier in TIERS.items()},
        "runs": _planned_runs(blocks),
    }
    (destination / CONFIG_FILE_NAME).write_text(
        json.dumps(configuration, indent=2) + "\n", encoding="utf-8"
    )


def _session_metadata(*, jobs: int, binary: Path) -> dict[str, Any]:
    """Describe the host, environment and LKH binary of this session."""
    return {
        **host_metadata(),
        "jobs": jobs,
        "executable": str(binary),
        "executable_sha256": file_sha256(binary),
    }
