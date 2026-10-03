"""FILO2 wrapper for large-scale capacitated vehicle routing instances.

FILO2 is built here with ``ENABLE_TIMELIMIT`` and ``ENABLE_VERBOSE``, so it
accepts ``--optimization-seconds`` and prints a progress row roughly every
second.  Those rows carry the current best objective and become the anytime
trace; the final routes are read from the CVRPLIB ``.vrp.sol`` file.

Note that FILO2 does not count setup time (instance parsing, initial
solution, move-generator construction) against ``--optimization-seconds``,
so wall-clock timestamps from the streaming layer, not FILO2's own timers,
are the trace axis.

Upstream: https://github.com/acco93/filo2 (GPL-3.0).  The vendored binary is
built from commit ``17b8f844b4779832d673b59d6ba28aedee1cba52``.
"""

from __future__ import annotations

import math
import re
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np

from instances import Instance, TsplibError, load_path
from instances.tsplib import load_solution

from .base import (
    SolverError,
    SolverExecutionError,
    SolverResult,
    StreamedLine,
    TracePoint,
    find_executable,
    require_length,
    run_process_streaming,
    validate_capacity,
)

_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")

# " 40.00  11103  27591  26  5523.00 ..." -> progress, iterations, objective.
_PROGRESS_PATTERN = re.compile(r"^\s*[\d.]+\s+(\d+)\s+(\d+)\s+\d+\s")


def solve_filo2(
    instance_path: str | Path,
    *,
    executable: str | Path | None = None,
    optimization_seconds: int = 60,
    seed: int = 0,
    extra_arguments: Sequence[str] = (),
    timeout_seconds: float | None = None,
) -> SolverResult:
    """Solve a CVRP instance with FILO2.

    Args:
        instance_path: VRPLIB instance file, plain or gzipped.
        executable: FILO2 binary.  Defaults to ``FILO2_BIN`` or the bundled
            ``vendor/filo2``.
        optimization_seconds: Core optimization budget passed to
            ``--optimization-seconds``.  Setup time comes on top.
        seed: Random seed passed to ``--seed``.
        extra_arguments: Additional raw command-line arguments.
        timeout_seconds: Hard wall-clock limit for the process.  Give it
            enough slack for the setup phase on very large instances.

    Returns:
        The routes, the length recomputed from the instance, the captured
        process output, and the best-objective trace.

    Raises:
        SolverError: If the instance is not a CVRP or has no dimension.
        SolverTimeoutError: If the process exceeds ``timeout_seconds``.
        SolverExecutionError: If FILO2 fails or writes no usable solution.
        FileNotFoundError: If no FILO2 executable can be found.
    """
    if optimization_seconds <= 0:
        raise ValueError(
            f"optimization_seconds must be positive, got {optimization_seconds}"
        )
    path = Path(instance_path)
    instance = load_path(path)
    _require_cvrp(instance, "filo2")
    binary = find_executable("filo2", executable)

    with tempfile.TemporaryDirectory(prefix="teaspoon-filo2-") as directory:
        workdir = Path(directory)
        output_directory = workdir / "output"
        command = [
            str(binary),
            str(path.resolve()),
            "--seed",
            str(seed),
            "--optimization-seconds",
            str(optimization_seconds),
            "--outpath",
            f"{output_directory}/",
        ]
        command += [str(argument) for argument in extra_arguments]

        outcome, lines = run_process_streaming(
            command,
            solver="filo2",
            cwd=workdir,
            timeout_seconds=timeout_seconds,
        )
        solution_path = _solution_path(output_directory, path, seed)
        if not solution_path.is_file():
            raise SolverExecutionError(
                "filo2 exited without writing a solution file\n"
                f"stdout:\n{outcome.stdout[-2000:]}"
            )
        routes = _read_routes(instance, solution_path)
        length = sum(
            require_length(instance, route, closed=True, solver="filo2")
            for route in routes
        )

    return SolverResult(
        solver="filo2",
        instance_path=path,
        kind=instance.kind,
        routes=routes,
        length=length,
        runtime_seconds=outcome.runtime_seconds,
        command=outcome.command,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        trace=parse_filo2_trace(lines),
    )


def parse_filo2_trace(lines: Iterable[StreamedLine]) -> tuple[TracePoint, ...]:
    """Extract best objectives from FILO2 progress rows.

    ANSI escape codes and non-numeric lines (banners, summaries) are
    ignored.  Rows are printed roughly once per second.
    """
    points: list[TracePoint] = []
    for line in lines:
        if line.stream != "stdout":
            continue
        text = _ANSI_PATTERN.sub("", line.text)
        match = _PROGRESS_PATTERN.match(text)
        if match is None:
            continue
        best_cost = float(match.group(2))
        if not math.isfinite(best_cost):
            continue
        points.append(TracePoint(wall_seconds=line.wall_seconds, best_cost=best_cost))
    return tuple(points)


def _solution_path(output_directory: Path, instance_path: Path, seed: int) -> Path:
    """Return the path FILO2 writes its solution to."""
    return output_directory / f"{instance_path.name}_seed-{seed}.vrp.sol"


def _require_cvrp(instance: Instance, solver: str) -> None:
    """Check that the instance is a CVRP with a known dimension.

    Raises:
        SolverError: If the instance is not a CVRP or lacks a dimension.
    """
    if instance.kind != "CVRP":
        raise SolverError(
            f"{solver} solves CVRP instances only, but "
            f"{instance.source_path.name} is {instance.kind}"
        )
    if instance.dimension is None:
        raise SolverError(f"{instance.source_path.name} has no DIMENSION field")


def _read_routes(instance: Instance, solution_path: Path) -> tuple[np.ndarray, ...]:
    """Read a CVRPLIB solution and validate coverage and capacity.

    Raises:
        SolverExecutionError: If the file does not cover every customer or a
            route exceeds the vehicle capacity.
    """
    try:
        sequences = load_solution(instance, solution_path)
    except TsplibError as error:
        raise SolverExecutionError(
            f"filo2 wrote an unusable solution: {error}"
        ) from error
    routes = tuple(sequence.node_indices for sequence in sequences)
    validate_capacity(instance, routes, solver="filo2")
    return routes
