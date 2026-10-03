"""HGS-CVRP wrapper for capacitated vehicle routing instances.

HGS-CVRP prints a progress line every ``nbIterTraces`` iterations and writes
the final solution as a CVRPLIB ``.sol`` file.  The wrapper streams the
progress lines so every run also carries an anytime trace with wall-clock
timestamps.

Upstream: https://github.com/vidalt/HGS-CVRP (MIT license).  The vendored
binary is built from commit ``1a927955cd2861a29d978f0d359d6e647db9319c``.
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

# "It  14570  12413 | T(s) 5.00 | Feas 27 27591.00 27636.08 | Inf ..."
_PROGRESS_PATTERN = re.compile(
    r"^\s*It\s+\d+\s+\d+\s+\|\s*T\(s\)\s+[\d.]+\s+\|\s*"
    r"Feas\s+\d+\s+([\d.]+)\s+[\d.]+\s+\|"
)


def solve_hgs(
    instance_path: str | Path,
    *,
    executable: str | Path | None = None,
    time_limit_seconds: float | None = None,
    max_iterations: int | None = None,
    seed: int = 0,
    nb_iter_traces: int = 10,
    round_distances: bool = True,
    extra_arguments: Sequence[str] = (),
    timeout_seconds: float | None = None,
) -> SolverResult:
    """Solve a CVRP instance with HGS-CVRP.

    Args:
        instance_path: VRPLIB instance file, plain or gzipped.
        executable: HGS binary.  Defaults to ``HGS_BIN`` or the bundled
            ``vendor/hgs``.
        time_limit_seconds: Wall-clock budget for the search.  When omitted,
            HGS stops after ``max_iterations`` iterations without improvement.
        max_iterations: Maximum iterations without improvement.
        seed: Random seed passed to ``-seed``.
        nb_iter_traces: Iterations between progress lines, passed to
            ``-nbIterTraces``.  Lower values give finer anytime traces.
        round_distances: Round distances to the nearest integer, the CVRPLIB
            X/XL/AGS convention (``-round 1``); pass ``False`` for sets such
            as CMT or Golden that publish unrounded costs.
        extra_arguments: Additional raw command-line arguments.
        timeout_seconds: Hard wall-clock limit for the process.  The search
            budget is ``time_limit_seconds``; this limit only guards against
            a hanging process.

    Returns:
        The routes, the length recomputed from the instance, the captured
        process output, and the best-feasible-cost trace.

    Raises:
        SolverError: If the instance is not a CVRP or has no dimension.
        SolverTimeoutError: If the process exceeds ``timeout_seconds``.
        SolverExecutionError: If HGS fails or writes no usable solution.
        FileNotFoundError: If no HGS executable can be found.
    """
    path = Path(instance_path)
    instance = load_path(path)
    _require_cvrp(instance, "hgs")
    binary = find_executable("hgs", executable)

    with tempfile.TemporaryDirectory(prefix="teaspoon-hgs-") as directory:
        workdir = Path(directory)
        solution_path = workdir / "solution.sol"
        command = [
            str(binary),
            str(path.resolve()),
            str(solution_path),
            "-seed",
            str(seed),
            "-log",
            "1",
            "-nbIterTraces",
            str(nb_iter_traces),
            "-round",
            "1" if round_distances else "0",
        ]
        if time_limit_seconds is not None:
            command += ["-t", f"{time_limit_seconds:g}"]
        if max_iterations is not None:
            command += ["-it", str(max_iterations)]
        command += [str(argument) for argument in extra_arguments]

        outcome, lines = run_process_streaming(
            command,
            solver="hgs",
            cwd=workdir,
            timeout_seconds=timeout_seconds,
        )
        if not solution_path.is_file():
            raise SolverExecutionError(
                "hgs exited without writing a solution file\n"
                f"stdout:\n{outcome.stdout[-2000:]}"
            )
        routes = _read_routes(instance, solution_path)
        length = sum(
            require_length(instance, route, closed=True, solver="hgs")
            for route in routes
        )

    return SolverResult(
        solver="hgs",
        instance_path=path,
        kind=instance.kind,
        routes=routes,
        length=length,
        runtime_seconds=outcome.runtime_seconds,
        command=outcome.command,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        trace=parse_hgs_trace(lines),
    )


def parse_hgs_trace(lines: Iterable[StreamedLine]) -> tuple[TracePoint, ...]:
    """Extract best feasible costs from HGS progress lines.

    Lines that do not match the progress format (banners, final summaries)
    are ignored.
    """
    points: list[TracePoint] = []
    for line in lines:
        if line.stream != "stdout":
            continue
        match = _PROGRESS_PATTERN.match(line.text)
        if match is None:
            continue
        best_cost = float(match.group(1))
        if not math.isfinite(best_cost):
            continue
        points.append(TracePoint(wall_seconds=line.wall_seconds, best_cost=best_cost))
    return tuple(points)


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
            f"hgs wrote an unusable solution: {error}"
        ) from error
    routes = tuple(sequence.node_indices for sequence in sequences)
    validate_capacity(instance, routes, solver="hgs")
    return routes
