"""Wrapper around netgen's clustered generator (the NETGEN set).

netgen (Bossek) generates the clustered instances of Kerschke et al.,
"Leveraging TSP Solver Complementarity through Machine Learning" (Evol.
Comput. 26(4), 2018), whose set used n.cluster in {2, 5, 10}.  The package
defaults reproduce that set's procedure (see ``netgen.R``); the instance is
generated on [0, 100]^2 as in the paper and then scaled to [0, 10^6]^2, the
DIMACS range, which keeps its shape.

netgen is an R package and a dependency of tspgen, so it is installed with
it; ``Rscript`` is located as in :mod:`generators.tspgen`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .tspgen import find_rscript

SCRIPT = Path(__file__).resolve().with_name("netgen.R")
DEFAULT_CLUSTERS = 5
"""Clusters per instance: the middle of the NETGEN set's {2, 5, 10}."""
SCALE = 10_000
"""Factor from netgen's [0, 100]^2 to [0, 10^6]^2."""


class NetgenError(RuntimeError):
    """Raised when netgen fails or writes no instance."""


def generate_netgen(
    n: int,
    seed: int,
    output_dir: str | Path,
    clusters: int = DEFAULT_CLUSTERS,
    rscript: str | Path | None = None,
) -> Path:
    """Generate one clustered instance and write ``netgen-<n>-<seed>.tsp``.

    Args:
        n: Number of cities.
        seed: Seed for R's random number generator.
        output_dir: Directory that receives the instance.
        clusters: Number of clusters.
        rscript: Override for the ``Rscript`` executable.

    Raises:
        ValueError: If ``n`` or ``clusters`` is out of range.
        FileNotFoundError: If ``Rscript`` is missing.
        NetgenError: If R fails (for example, netgen is not installed) or
            writes no instance.
    """
    if n < 8:
        raise ValueError(f"n must be at least 8, got {n}")
    if not 1 <= clusters <= n:
        raise ValueError(f"clusters must be in 1..{n}, got {clusters}")
    executable = find_rscript(rscript)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"netgen-{n}-{seed}.tsp"
    command = [
        str(executable),
        str(SCRIPT),
        str(n),
        str(seed),
        str(clusters),
        str(SCALE),
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise NetgenError(
            f"netgen exited with code {completed.returncode} (is the R package "
            "installed? it comes with tspgen)\n"
            f"stderr:\n{completed.stderr[-2000:]}"
        )
    if not path.is_file():
        raise NetgenError(f"netgen did not write {path}")
    return path
