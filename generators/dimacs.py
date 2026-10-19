"""Wrapper around the DIMACS TSP Challenge instance generators.

The 8th DIMACS Implementation Challenge (Johnson and McGeoch 2002) ships
generators by Lyle McGeoch, building on code by David Johnson and Jon
Bentley.  Two are used:

- ``portgen``: integer points uniform on the 10^6 x 10^6 square (``E``
  instances of the Challenge);
- ``portmgen``: symmetric distance matrices with entries uniform on
  0..10^6 - 1 (``M`` instances), written as ``UPPER_DIAG_ROW``.

The clustered ``portcgen`` is not used: it makes ``n / 100`` clusters, too
few below 1,000 cities; clustered instances come from netgen instead
(:mod:`generators.netgen`).  Both print TSPLIB to stdout, which is written
to disk unchanged, so an instance is byte-identical to the Challenge
generator's output.  The C
sources are vendored under ``vendor/dimacs/`` (from
https://dimacs.rutgers.edu/archive/Challenges/TSP/download.html) and built
with :func:`build_dimacs`, or ``python -m generators.dimacs``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path

LOGGER = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIMACS_DIRECTORY = REPOSITORY_ROOT / "vendor" / "dimacs"
_DIRECTORY_ENVIRONMENT_VARIABLE = "DIMACS_DIR"


class DimacsError(RuntimeError):
    """Raised when a DIMACS generator cannot be built or fails to run."""


class DimacsGenerator(StrEnum):
    """The DIMACS generators used, by program name."""

    PORTGEN = "portgen"
    PORTMGEN = "portmgen"


def find_dimacs_directory(override: str | Path | None = None) -> Path:
    """Locate the directory holding the DIMACS sources and binaries.

    Search order: the ``override`` argument, the ``DIMACS_DIR`` environment
    variable, then ``vendor/dimacs/``.

    Raises:
        FileNotFoundError: If no candidate directory exists.
    """
    candidates: list[Path] = []
    if override is not None:
        candidates.append(Path(override).expanduser())
    else:
        from_environment = os.environ.get(_DIRECTORY_ENVIRONMENT_VARIABLE)
        if from_environment:
            candidates.append(Path(from_environment).expanduser())
        candidates.append(DEFAULT_DIMACS_DIRECTORY)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"could not find the DIMACS generators; searched {searched} (set "
        f"{_DIRECTORY_ENVIRONMENT_VARIABLE} or download the sources from "
        "https://dimacs.rutgers.edu/archive/Challenges/TSP/download.html)"
    )


def build_dimacs(directory: str | Path | None = None) -> list[Path]:
    """Compile the generators next to their sources.

    ``-fwrapv`` is needed: ``portmgen`` hashes city indices with signed
    integer overflow, which C leaves undefined, and the flag fixes it to the
    two's-complement wrap-around the code relies on.

    Returns:
        The paths of the built binaries.

    Raises:
        FileNotFoundError: If the directory, a source file or a C compiler
            is missing.
        DimacsError: If compilation fails.
    """
    source_directory = find_dimacs_directory(directory)
    compiler = os.environ.get("CC") or shutil.which("cc")
    if compiler is None:
        raise FileNotFoundError("no C compiler found; set CC")
    binaries = []
    for generator in DimacsGenerator:
        source = source_directory / f"{generator}.c"
        if not source.is_file():
            raise FileNotFoundError(f"missing DIMACS source {source}")
        binary = source_directory / generator.value
        command = [compiler, "-O", "-fwrapv", "-o", str(binary), str(source), "-lm"]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise DimacsError(
                f"compiling {source.name} failed:\n{completed.stderr[-2000:]}"
            )
        LOGGER.info("built %s", binary)
        binaries.append(binary)
    return binaries


def generate_dimacs(
    generator: DimacsGenerator,
    n: int,
    seed: int,
    output_dir: str | Path,
    directory: str | Path | None = None,
) -> Path:
    """Run one DIMACS generator and write ``<generator>-<n>-<seed>.tsp``.

    The file name matches the ``NAME`` line the generator writes.

    Args:
        generator: Which generator to run.
        n: Number of cities.
        seed: Integer seed passed to the generator.
        output_dir: Directory that receives the instance.
        directory: Override for the DIMACS directory.

    Raises:
        ValueError: If ``n`` is smaller than 8.
        FileNotFoundError: If the binary has not been built.
        DimacsError: If the generator fails or writes something unexpected.
    """
    if n < 8:
        raise ValueError(f"n must be at least 8, got {n}")
    binary = find_dimacs_directory(directory) / generator.value
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        raise FileNotFoundError(
            f"{binary} is not built; run python -m generators.dimacs"
        )
    completed = subprocess.run(
        [str(binary), str(n), str(seed)], capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise DimacsError(
            f"{generator} exited with code {completed.returncode}\n"
            f"stderr:\n{completed.stderr[-2000:]}"
        )
    name = f"{generator}-{n}-{seed}"
    first_line = completed.stdout.split("\n", 1)[0]
    if first_line != f"NAME : {name}":
        raise DimacsError(f"{generator} wrote {first_line!r}, expected NAME {name}")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{name}.tsp"
    path.write_text(completed.stdout)
    return path


def main() -> int:
    """Build the generators; return an exit status."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        build_dimacs()
    except (OSError, DimacsError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
