"""Shared plumbing for the bundled Concorde and LKH-3 solver wrappers.

The wrappers run the command-line solvers that live under ``vendor/`` (or
wherever ``CONCORDE_BIN`` / ``LKH_BIN`` point) and translate their file-based
output into the format-agnostic model defined in :mod:`instances`.  Solver
processes always run in a temporary working directory because both binaries
drop scratch files next to their output, never next to the instance.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from instances import Instance, Sequence

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIRECTORY = REPOSITORY_ROOT / "vendor"

_EXECUTABLE_ENVIRONMENT_VARIABLES: Mapping[str, str] = {
    "concorde": "CONCORDE_BIN",
    "lkh": "LKH_BIN",
}
_DEFAULT_EXECUTABLES: Mapping[str, Path] = {
    "concorde": VENDOR_DIRECTORY / "concorde",
    "lkh": VENDOR_DIRECTORY / "LKH",
}


class SolverError(RuntimeError):
    """Raised when a solver wrapper cannot produce a valid solution."""


class SolverTimeoutError(SolverError, TimeoutError):
    """Raised when a solver process exceeds the wall-clock limit."""


class SolverExecutionError(SolverError):
    """Raised when a solver exits unsuccessfully or writes unusable output."""


@dataclass(eq=False, frozen=True, slots=True)
class SolverResult:
    """One solver run's solution in the project's node-index convention.

    Attributes:
        solver: Solver name, ``"concorde"`` or ``"lkh"``.
        instance_path: Instance file that was solved.
        kind: Problem family of the instance, e.g. ``"TSP"`` or ``"CVRP"``.
        routes: Zero-based node-index arrays.  A TSP result has exactly one
            route; every CVRP route starts at the depot.
        length: Route length recomputed from the instance, never copied from
            the solver's own reporting.
        runtime_seconds: Wall-clock time spent in the solver process.
        command: Full command line, useful for logging and bug reports.
        stdout: Captured standard output.
        stderr: Captured standard error.
    """

    solver: str
    instance_path: Path
    kind: str
    routes: tuple[np.ndarray, ...]
    length: float
    runtime_seconds: float
    command: tuple[str, ...]
    stdout: str
    stderr: str

    @property
    def node_indices(self) -> np.ndarray:
        """Return the tour of a single-route (TSP) result.

        Raises:
            SolverError: If the result has more than one route.
        """
        if len(self.routes) != 1:
            raise SolverError(
                f"{self.solver} returned {len(self.routes)} routes; "
                "use routes for multi-vehicle solutions"
            )
        return self.routes[0]

    def to_sequences(self, label: str | None = None) -> tuple[Sequence, ...]:
        """Convert the routes into viewer-ready sequences.

        Args:
            label: Optional label override.  Multi-route results append the
                route number so every label stays unique.
        """
        if label is None:
            label = f"{self.solver} on {self.instance_path.name}"
        sequences = []
        for position, route in enumerate(self.routes):
            if len(self.routes) == 1:
                route_label = label
            else:
                route_label = f"{label} route {position + 1}"
            sequences.append(
                Sequence(node_indices=route, label=route_label, closed=True)
            )
        return tuple(sequences)

    def save_solution(self, path: str | Path) -> Path:
        """Write the solution to a TSPLIB tour or CVRPLIB solution file.

        TSP results are written as a standard TSPLIB ``TOUR_SECTION`` file;
        CVRP results as a CVRPLIB ``.sol`` file with one ``Route #i: ...``
        line per vehicle and a trailing ``Cost`` line.  CVRP routes are
        expected to start with their depot, so the leading node is dropped.
        """
        destination = Path(path)
        name = self.instance_path.stem
        if self.kind == "CVRP":
            _write_cvrp_solution(destination, self.routes, self.length)
        else:
            _write_tsplib_tour(destination, self.node_indices, self.length, name)
        return destination


def find_executable(solver: str, override: str | Path | None = None) -> Path:
    """Locate a solver binary.

    Search order: the ``override`` argument, the solver's environment
    variable (``CONCORDE_BIN`` or ``LKH_BIN``), then the bundled ``vendor/``
    executable.

    Raises:
        ValueError: If the solver name is unknown.
        FileNotFoundError: If no usable executable is found.
    """
    if solver not in _DEFAULT_EXECUTABLES:
        raise ValueError(f"unknown solver {solver!r}")
    variable = _EXECUTABLE_ENVIRONMENT_VARIABLES[solver]
    if override is not None:
        candidate = Path(override).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise FileNotFoundError(
            f"the {solver} executable {candidate} is missing or not executable"
        )
    candidates = []
    from_environment = os.environ.get(variable)
    if from_environment:
        candidates.append(Path(from_environment).expanduser())
    candidates.append(_DEFAULT_EXECUTABLES[solver])
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"could not find the {solver} executable; searched {searched} "
        f"(set {variable} or pass executable=...)"
    )


@dataclass(eq=False, frozen=True, slots=True)
class ProcessOutcome:
    """Captured result of one solver process."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    runtime_seconds: float


def run_process(
    command: Iterable[str | Path],
    *,
    solver: str,
    cwd: Path,
    timeout_seconds: float | None,
) -> ProcessOutcome:
    """Run a solver command in ``cwd`` and capture its output.

    Raises:
        SolverTimeoutError: If the process exceeds ``timeout_seconds``.
        SolverExecutionError: If the process cannot start or exits nonzero.
    """
    arguments = [str(argument) for argument in command]
    logger.debug("running %s in %s", " ".join(arguments), cwd)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SolverTimeoutError(
            f"{solver} did not finish within {timeout_seconds} seconds; "
            f"command: {' '.join(arguments)}"
        ) from error
    except OSError as error:
        raise SolverExecutionError(f"could not start {solver}: {error}") from error
    runtime_seconds = time.perf_counter() - started
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise SolverExecutionError(
            f"{solver} exited with code {completed.returncode}\n"
            f"command: {' '.join(arguments)}\n"
            f"stdout:\n{_tail(stdout)}\n"
            f"stderr:\n{_tail(stderr)}"
        )
    return ProcessOutcome(
        command=tuple(arguments),
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        runtime_seconds=runtime_seconds,
    )


def check_permutation(
    values: np.ndarray,
    expected_size: int,
    *,
    solver: str,
) -> None:
    """Check that 1-based values are exactly a permutation of ``1..size``.

    Raises:
        SolverExecutionError: If the values repeat, fall outside the range,
            or do not match ``expected_size``.
    """
    if values.size != expected_size:
        raise SolverExecutionError(
            f"{solver} wrote {values.size} stops, expected {expected_size}"
        )
    if (
        values.min() < 1
        or values.max() > expected_size
        or np.unique(values).size != values.size
    ):
        raise SolverExecutionError(
            f"{solver} wrote a tour that is not a permutation of 1..{expected_size}"
        )


def require_length(
    instance: Instance,
    node_indices: np.ndarray,
    *,
    closed: bool,
    solver: str,
) -> float:
    """Compute a route length with the instance's own length function.

    Raises:
        SolverExecutionError: If the parser has no length function.
    """
    if instance.length_of is None:
        raise SolverExecutionError(
            f"{solver} solved {instance.source_path.name}, but the parser has "
            "no length function for this edge weight type"
        )
    return float(instance.length_of(node_indices, closed))


def _tail(text: str, max_lines: int = 20) -> str:
    """Return at most the last ``max_lines`` lines of captured output."""
    lines = text.rstrip().splitlines()
    if len(lines) <= max_lines:
        return text.rstrip()
    return "\n".join(["...", *lines[-max_lines:]])


def _write_tsplib_tour(
    path: Path,
    node_indices: np.ndarray,
    length: float,
    name: str,
) -> None:
    """Write one closed tour as a TSPLIB tour file."""
    lines = [
        f"NAME : {name}",
        f"COMMENT : Length = {length:g}",
        "TYPE : TOUR",
        f"DIMENSION : {node_indices.size}",
        "TOUR_SECTION",
        *(str(int(index) + 1) for index in node_indices),
        "-1",
        "EOF",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_cvrp_solution(
    path: Path,
    routes: tuple[np.ndarray, ...],
    length: float,
) -> None:
    """Write depot-anchored routes in the CVRPLIB ``.sol`` format.

    CVRPLIB numbers customers ``1..n-1`` and omits the depot, so a
    zero-based node index is shifted down by one when it follows the depot
    in the file's own numbering.
    """
    lines = []
    for position, route in enumerate(routes, start=1):
        depot_index = int(route[0])
        customers = " ".join(
            str(_customer_number(int(index), depot_index)) for index in route[1:]
        )
        lines.append(f"Route #{position}: {customers}".rstrip())
    lines.append(f"Cost {length:g}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _customer_number(node_index: int, depot_index: int) -> int:
    """Map a zero-based node index to its CVRPLIB customer number."""
    return node_index + 1 if node_index < depot_index else node_index
