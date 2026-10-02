"""Concorde wrapper for symmetric TSP instances.

Concorde writes its solution as a node count followed by zero-based node ids,
in whichever working directory it runs, so the wrapper executes it inside a
temporary directory and parses that file.  The ``-x`` flag is deliberately
unused: on the bundled binary it exits 255 even after a successful solve.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from instances import load_path

from .base import (
    SolverError,
    SolverExecutionError,
    SolverResult,
    find_executable,
    require_length,
    run_process,
)


def solve_concorde(
    instance_path: str | Path,
    *,
    executable: str | Path | None = None,
    seed: int | None = None,
    timeout_seconds: float | None = None,
) -> SolverResult:
    """Solve a symmetric TSP instance with Concorde.

    Args:
        instance_path: TSPLIB instance file, plain or gzipped.
        executable: Concorde binary.  Defaults to ``CONCORDE_BIN`` or the
            bundled ``vendor/concorde``.
        seed: Optional random seed; Concorde derives one from the clock when
            omitted.
        timeout_seconds: Wall-clock limit for the solver process.

    Returns:
        The optimal tour, its length recomputed from the instance, and the
        captured process output.

    Raises:
        SolverError: If the instance is not a symmetric TSP.
        SolverTimeoutError: If the process exceeds ``timeout_seconds``.
        SolverExecutionError: If Concorde fails or writes no usable tour.
        FileNotFoundError: If no Concorde executable can be found.
    """
    path = Path(instance_path)
    instance = load_path(path)
    if instance.kind != "TSP":
        raise SolverError(
            f"concorde solves symmetric TSP instances only, "
            f"but {path.name} is {instance.kind}"
        )
    dimension = instance.dimension
    if dimension is None:
        raise SolverError(f"{path.name} has no DIMENSION field")
    binary = find_executable("concorde", executable)

    with tempfile.TemporaryDirectory(prefix="teaspoon-concorde-") as directory:
        workdir = Path(directory)
        tour_path = workdir / "solution.tour"
        command = [str(binary)]
        if seed is not None:
            command += ["-s", str(seed)]
        command += ["-o", str(tour_path), str(path.resolve())]
        outcome = run_process(
            command,
            solver="concorde",
            cwd=workdir,
            timeout_seconds=timeout_seconds,
        )
        if not tour_path.is_file():
            raise SolverExecutionError(
                "concorde exited without writing a tour file\n"
                f"stdout:\n{outcome.stdout[-2000:]}"
            )
        node_indices = _read_concorde_tour(tour_path, dimension)
        length = require_length(instance, node_indices, closed=True, solver="concorde")

    return SolverResult(
        solver="concorde",
        instance_path=path,
        kind=instance.kind,
        routes=(node_indices,),
        length=length,
        runtime_seconds=outcome.runtime_seconds,
        command=outcome.command,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
    )


def _read_concorde_tour(path: Path, dimension: int) -> np.ndarray:
    """Read Concorde's tour file: node count first, then zero-based ids.

    Raises:
        SolverExecutionError: If the file is not numeric, has the wrong
            length, or does not contain every node exactly once.
    """
    try:
        values = np.array(path.read_text(encoding="utf-8").split(), dtype=np.int64)
    except ValueError as error:
        raise SolverExecutionError(f"{path.name} is not a numeric tour file") from error
    if values.size != dimension + 1:
        raise SolverExecutionError(
            f"concorde wrote {values.size - 1} stops, expected {dimension}"
        )
    if int(values[0]) != dimension:
        raise SolverExecutionError(
            f"concorde tour header says {int(values[0])} nodes, expected {dimension}"
        )
    node_indices = values[1:].copy()
    if (
        node_indices.min() < 0
        or node_indices.max() >= dimension
        or np.unique(node_indices).size != dimension
    ):
        raise SolverExecutionError(
            "concorde tour is not a permutation of the instance nodes"
        )
    return node_indices
