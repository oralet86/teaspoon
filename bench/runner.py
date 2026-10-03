"""Anytime benchmark runs for the bundled CVRP solvers.

One run is one (instance, solver, seed, budget) combination.  Results are
written incrementally to ``runs.jsonl`` inside the run directory, with one
trace CSV per run and the raw solver output kept next to it, so a crashed
sweep still leaves usable data behind.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from solvers import (
    SolverError,
    TracePoint,
    find_executable,
    solve_filo2,
    solve_hgs,
)

from .metrics import primal_integral, time_to_target
from .suites import load_bks

logger = logging.getLogger(__name__)

_TARGET_GAPS: Mapping[str, float] = {
    "time_to_target_1pct": 0.01,
    "time_to_target_0_5pct": 0.005,
}


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One planned solver run.

    Attributes:
        instance_path: CVRP instance to solve.
        solver: ``"hgs"`` or ``"filo2"``.
        seed: Random seed passed to the solver.
        budget_seconds: Search budget in seconds.
        arm: Experiment arm; only ``"baseline"`` exists today.
    """

    instance_path: Path
    solver: str
    seed: int
    budget_seconds: int
    arm: str = "baseline"


@dataclass(frozen=True, slots=True)
class RunRecord:
    """The outcome of one solver run, ready for JSON serialization."""

    instance: str
    solver: str
    seed: int
    budget_seconds: int
    arm: str
    status: str
    bks: float | None = None
    best_cost: float | None = None
    gap: float | None = None
    time_to_target_1pct: float | None = None
    time_to_target_0_5pct: float | None = None
    primal_integral: float | None = None
    runtime_seconds: float | None = None
    trace_points: int = 0
    executable: str | None = None
    executable_sha256: str | None = None
    trace_file: str | None = None
    log_file: str | None = None
    command: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return the record as a JSON-serializable dictionary."""
        return asdict(self)


def run_specs(
    specs: Sequence[RunSpec],
    output_dir: str | Path,
    *,
    executables: Mapping[str, Path] | None = None,
    timeout_slack_seconds: float = 120.0,
) -> tuple[RunRecord, ...]:
    """Run every spec sequentially and persist the results.

    Runs are sequential on purpose: wall-clock metrics are only comparable
    when the solvers do not compete for CPU time.

    Args:
        specs: Runs to execute.
        output_dir: Directory that receives ``runs.jsonl``, ``config.json``,
            ``traces/`` and ``logs/``.
        executables: Optional per-solver binary overrides, mainly for tests.
        timeout_slack_seconds: Extra wall-clock seconds allowed on top of
            the search budget before a run is killed.

    Returns:
        One record per spec, in execution order.
    """
    destination = Path(output_dir)
    (destination / "traces").mkdir(parents=True, exist_ok=True)
    (destination / "logs").mkdir(parents=True, exist_ok=True)
    _write_config(destination, specs)

    records: list[RunRecord] = []
    runs_path = destination / "runs.jsonl"
    with runs_path.open("w", encoding="utf-8") as runs_file:
        for spec in specs:
            record = _run_one(spec, destination, executables, timeout_slack_seconds)
            records.append(record)
            runs_file.write(json.dumps(record.to_json()) + "\n")
            runs_file.flush()
    return tuple(records)


def _run_one(
    spec: RunSpec,
    destination: Path,
    executables: Mapping[str, Path] | None,
    timeout_slack_seconds: float,
) -> RunRecord:
    """Execute one run and translate the outcome into a record."""
    label = f"{spec.instance_path.stem}__{spec.solver}__seed{spec.seed}"
    common: dict[str, Any] = {
        "instance": str(spec.instance_path),
        "solver": spec.solver,
        "seed": spec.seed,
        "budget_seconds": spec.budget_seconds,
        "arm": spec.arm,
    }
    override = None if executables is None else executables.get(spec.solver)
    try:
        binary = find_executable(spec.solver, override)
    except (OSError, ValueError) as error:
        logger.warning("%s: %s", label, error)
        return RunRecord(status="error", error=str(error), **common)

    timeout_seconds = spec.budget_seconds + timeout_slack_seconds
    try:
        if spec.solver == "hgs":
            result = solve_hgs(
                spec.instance_path,
                executable=binary,
                time_limit_seconds=float(spec.budget_seconds),
                timeout_seconds=timeout_seconds,
            )
        elif spec.solver == "filo2":
            result = solve_filo2(
                spec.instance_path,
                executable=binary,
                optimization_seconds=spec.budget_seconds,
                timeout_seconds=timeout_seconds,
            )
        else:
            raise ValueError(f"unknown solver {spec.solver!r}")
    except (SolverError, OSError) as error:
        logger.warning("%s failed: %s", label, error)
        return RunRecord(
            status="error",
            executable=str(binary),
            executable_sha256=_sha256(binary),
            error=str(error),
            **common,
        )

    trace_file = _write_trace(destination, label, result.trace)
    log_file = _write_log(destination, label, result.stdout, result.stderr)
    bks = load_bks(spec.instance_path)
    gap = (result.length - bks) / bks if bks is not None else None
    targets: dict[str, float | None] = {name: None for name in _TARGET_GAPS}
    integral: float | None = None
    if bks is not None and result.trace:
        for name, target in _TARGET_GAPS.items():
            targets[name] = time_to_target(
                result.trace,
                bks,
                target,
                budget_seconds=float(spec.budget_seconds),
            )
        integral = primal_integral(
            result.trace,
            bks,
            budget_seconds=float(spec.budget_seconds),
        )
    return RunRecord(
        status="ok",
        bks=bks,
        best_cost=result.length,
        gap=gap,
        time_to_target_1pct=targets["time_to_target_1pct"],
        time_to_target_0_5pct=targets["time_to_target_0_5pct"],
        primal_integral=integral,
        runtime_seconds=result.runtime_seconds,
        trace_points=len(result.trace),
        executable=str(binary),
        executable_sha256=_sha256(binary),
        trace_file=trace_file,
        log_file=log_file,
        command=list(result.command),
        **common,
    )


def _write_trace(
    destination: Path, label: str, trace: Sequence[TracePoint]
) -> str | None:
    """Write the best-cost trace as CSV and return its run-relative path."""
    if not trace:
        return None
    relative = f"traces/{label}.csv"
    path = destination / relative
    lines = ["wall_seconds,best_cost"]
    lines += [f"{point.wall_seconds:.6f},{point.best_cost:g}" for point in trace]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return relative


def _write_log(destination: Path, label: str, stdout: str, stderr: str) -> str:
    """Write the raw solver output and return its run-relative path."""
    relative = f"logs/{label}.log"
    path = destination / relative
    path.write_text(
        f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
        encoding="utf-8",
    )
    return relative


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of a solver binary."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_config(destination: Path, specs: Sequence[RunSpec]) -> None:
    """Write the run configuration and host metadata as ``config.json``."""
    configuration = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "specs": [
            {
                "instance": str(spec.instance_path),
                "solver": spec.solver,
                "seed": spec.seed,
                "budget_seconds": spec.budget_seconds,
                "arm": spec.arm,
            }
            for spec in specs
        ],
    }
    (destination / "config.json").write_text(
        json.dumps(configuration, indent=2) + "\n", encoding="utf-8"
    )
