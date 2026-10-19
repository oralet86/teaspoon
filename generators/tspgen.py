"""Wrapper around the tspgen R package (Bossek et al., FOGA 2019).

tspgen starts from a uniform point set and applies randomly chosen mutation
operators (explosion, implosion, rotation, cluster, expansion, compression,
axis and linear projection, grid), which gives topologies far outside the
uniform and clustered sets.  The defaults follow the paper's TSPGEN
benchmark: 1,000 iterations with the "sophisticated" operators.  Points are
scaled to [0, 10^6]^2, the DIMACS range, and written by netgen as EUC_2D
TSPLIB with real coordinates.

Needs R with the tspgen package (``remotes::install_github(
"jakobbossek/tspgen")``).  ``Rscript`` is found via the argument, the
``TSPGEN_RSCRIPT`` environment variable, then ``PATH``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("tspgen.R")
DEFAULT_ITERATIONS = 1000
"""Mutation iterations per instance, as in the paper's TSPGEN set."""
SIDE = 1_000_000
"""Points lie in [0, SIDE]^2."""
_RSCRIPT_ENVIRONMENT_VARIABLE = "TSPGEN_RSCRIPT"


class TspgenError(RuntimeError):
    """Raised when tspgen fails or writes something unexpected."""


def find_rscript(override: str | Path | None = None) -> Path:
    """Locate ``Rscript``.

    Search order: the ``override`` argument, the ``TSPGEN_RSCRIPT``
    environment variable, then ``PATH``.

    Raises:
        FileNotFoundError: If no executable is found.
    """
    if override is not None:
        candidate = Path(override).expanduser()
    elif os.environ.get(_RSCRIPT_ENVIRONMENT_VARIABLE):
        candidate = Path(os.environ[_RSCRIPT_ENVIRONMENT_VARIABLE]).expanduser()
    else:
        found = shutil.which("Rscript")
        if found is None:
            raise FileNotFoundError(
                f"Rscript not found on PATH; install R or set "
                f"{_RSCRIPT_ENVIRONMENT_VARIABLE}"
            )
        candidate = Path(found)
    if not (candidate.is_file() and os.access(candidate, os.X_OK)):
        raise FileNotFoundError(f"{candidate} is missing or not executable")
    return candidate


def generate_tspgen(
    n: int,
    seed: int,
    output_dir: str | Path,
    iterations: int = DEFAULT_ITERATIONS,
    rscript: str | Path | None = None,
) -> Path:
    """Generate one instance and write ``tspgen-<n>-<seed>.tsp``.

    Args:
        n: Number of cities.
        seed: Seed for R's random number generator.
        output_dir: Directory that receives the instance.
        iterations: Mutation iterations.
        rscript: Override for the ``Rscript`` executable.

    Raises:
        ValueError: If ``n`` or ``iterations`` is out of range.
        FileNotFoundError: If ``Rscript`` is missing.
        TspgenError: If R fails (for example, tspgen is not installed) or
            writes no instance.
    """
    if n < 8:
        raise ValueError(f"n must be at least 8, got {n}")
    if iterations < 1:
        raise ValueError(f"iterations must be positive, got {iterations}")
    executable = find_rscript(rscript)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"tspgen-{n}-{seed}.tsp"
    command = [
        str(executable),
        str(SCRIPT),
        str(n),
        str(seed),
        str(iterations),
        str(SIDE),
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise TspgenError(
            f"tspgen exited with code {completed.returncode} (is the R package "
            "installed? remotes::install_github('jakobbossek/tspgen'))\n"
            f"stderr:\n{completed.stderr[-2000:]}"
        )
    if not path.is_file():
        raise TspgenError(f"tspgen did not write {path}")
    return path
